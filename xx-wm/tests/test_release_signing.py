"""Signed releases: xx-apps-update installs only from release tags signed by
the shipped key, and scripts/release.sh cuts them.

Every repo, remote and key lives in tmp (``_release_repo``); the real
release key is never touched and nothing is pushed anywhere but a local
bare repo. meson is a fake runner, so nothing is built or installed.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import update_checker
import xx_apps_update as updater
from _release_repo import (PRINCIPAL, ReleaseRepo, git, git_env,
                           signers_line, tools_missing)

XX_WM = Path(__file__).resolve().parent.parent
REPO_ROOT = XX_WM.parent
RELEASE_SH = REPO_ROOT / "scripts" / "release.sh"
SHIPPED_SIGNERS = XX_WM / "launcher" / "data" / "release-signers"

MANIFEST = {
    "common": {"version": "0.1.0", "path": "common", "deps": []},
    "xx-demo": {"version": "0.1.0", "path": "xx-demo",
                "deps": ["common"], "common": "0.1.0"},
}

needs_git = pytest.mark.skipif(bool(tools_missing()),
                               reason="needs git and ssh-keygen")


def _bump(manifest: dict, app: str, version: str) -> dict:
    out = json.loads(json.dumps(manifest))
    out[app]["version"] = version
    return out


def _state(path: Path, data: dict) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _installed(manifest: dict, commit: str) -> dict:
    return {app: {"version": entry["version"], "commit": commit,
                  "common": manifest["common"]["version"]}
            for app, entry in manifest.items()}


class _Run:
    def __init__(self):
        self.calls: list = []

    def __call__(self, argv, cwd="/", timeout=0):
        self.calls.append((list(argv), cwd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def repo(tmp_path):
    return ReleaseRepo(tmp_path / "release", MANIFEST)


def _verified(repo, tmp_path):
    return updater.verified_release(**repo.source(tmp_path))


def _apply(repo, tmp_path, monkeypatch, installed):
    monkeypatch.setattr(updater.os, "geteuid", lambda: 0)
    state = _state(tmp_path / "installed.json", installed)
    run = _Run()
    rc = updater.cmd_apply(None, run=run, state=state,
                           **repo.source(tmp_path))
    return rc, run, json.loads(Path(state).read_text(encoding="utf-8"))


@needs_git
class TestVerifiedRelease:
    def test_newest_signed_tag_is_accepted(self, repo, tmp_path):
        repo.tag("v2026.11.01")
        second = repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        repo.tag("v2026.11.12")
        release = _verified(repo, tmp_path)
        assert release.tag == "v2026.11.12"
        assert release.sha == second
        assert release.manifest["xx-demo"]["version"] == "0.2.0"

    def test_unsigned_annotated_tag_is_refused(self, repo, tmp_path):
        repo.tag("v2026.11.01")
        repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        repo.tag("v2026.11.12", sign=False)
        with pytest.raises(updater.UpdateError, match="v2026.11.12"):
            _verified(repo, tmp_path)

    def test_lightweight_tag_is_refused(self, repo, tmp_path):
        repo.lightweight_tag("v2026.11.12")
        with pytest.raises(updater.UpdateError, match="not a signed tag"):
            _verified(repo, tmp_path)

    def test_tag_signed_by_another_key_is_refused(self, repo, tmp_path):
        repo.tag("v2026.11.12", key=repo.other_key)
        with pytest.raises(updater.UpdateError,
                           match="not signed by the release key"):
            _verified(repo, tmp_path)

    def test_other_principal_is_refused(self, repo, tmp_path):
        repo.signers.write_text(signers_line(repo.key, "someone@else"),
                                encoding="utf-8")
        repo.tag("v2026.11.12")
        with pytest.raises(updater.UpdateError):
            _verified(repo, tmp_path)

    def test_key_without_the_git_namespace_is_refused(self, repo,
                                                      tmp_path):
        repo.signers.write_text(
            signers_line(repo.key).replace('namespaces="git"',
                                           'namespaces="file"'),
            encoding="utf-8")
        repo.tag("v2026.11.12")
        with pytest.raises(updater.UpdateError):
            _verified(repo, tmp_path)

    def test_tampered_tag_is_refused(self, repo, tmp_path):
        repo.tag("v2026.11.12")
        evil = repo.commit(_bump(MANIFEST, "xx-demo", "6.6.6"), "evil")
        repo.tamper("v2026.11.12", evil)
        assert repo.head("v2026.11.12") == evil
        with pytest.raises(updater.UpdateError,
                           match="not signed by the release key"):
            _verified(repo, tmp_path)

    def test_old_release_renamed_newer_is_refused(self, repo, tmp_path):
        repo.tag("v2026.11.01")
        repo.push_ref("refs/tags/v2026.11.01", "v2026.12.31")
        with pytest.raises(updater.UpdateError, match="tag name"):
            _verified(repo, tmp_path)

    def test_missing_signers_file_is_refused(self, repo, tmp_path):
        repo.tag("v2026.11.12")
        source = dict(repo.source(tmp_path),
                      signers=str(tmp_path / "absent"))
        with pytest.raises(updater.UpdateError, match="not installed"):
            updater.verified_release(**source)

    def test_no_release_yet_is_an_error(self, repo, tmp_path):
        repo.lightweight_tag("not-a-release")
        with pytest.raises(updater.UpdateError, match="no release"):
            _verified(repo, tmp_path)

    def test_bad_newest_tag_never_falls_back_to_an_older_one(
            self, repo, tmp_path, monkeypatch):
        repo.tag("v2026.11.01")
        repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        repo.tag("v2026.11.12", key=repo.other_key)
        rc, run, _data = _apply(repo, tmp_path, monkeypatch,
                                {"xx-demo": {"version": "0.0.1"}})
        assert rc == 1
        assert run.calls == []

    def test_deleted_tag_is_pruned_from_the_cache(self, repo, tmp_path):
        repo.tag("v2026.11.01")
        repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        repo.tag("v2026.11.12", sign=False)
        with pytest.raises(updater.UpdateError):
            _verified(repo, tmp_path)
        git(repo.work, "push", "-q", "origin", ":refs/tags/v2026.11.12")
        assert _verified(repo, tmp_path).tag == "v2026.11.01"


@needs_git
class TestApplyRefusals:
    def test_signed_release_installs(self, repo, tmp_path, monkeypatch,
                                     capsys):
        first = repo.head()
        repo.tag("v2026.11.01")
        second = repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        repo.tag("v2026.11.12")
        rc, run, data = _apply(repo, tmp_path, monkeypatch,
                               _installed(MANIFEST, first))
        assert rc == 0, capsys.readouterr().out
        assert any("install" in argv for argv, _cwd in run.calls)
        assert data["xx-demo"] == {"version": "0.2.0", "commit": second,
                                   "common": "0.1.0",
                                   "release": "v2026.11.12"}

    @pytest.mark.parametrize("kind", ["unsigned", "other-key", "tampered"])
    def test_bad_tag_installs_nothing(self, repo, tmp_path, monkeypatch,
                                      capsys, kind):
        first = repo.head()
        newer = repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        if kind == "unsigned":
            repo.tag("v2026.11.12", sign=False)
        elif kind == "other-key":
            repo.tag("v2026.11.12", key=repo.other_key)
        else:
            repo.tag("v2026.11.12", rev=first)
            repo.tamper("v2026.11.12", newer)
        installed = _installed(MANIFEST, first)
        rc, run, data = _apply(repo, tmp_path, monkeypatch, installed)
        assert rc == 1
        assert run.calls == []
        assert data == installed
        assert capsys.readouterr().out.startswith("XX app update failed:")

    def test_downgrade_is_refused(self, repo, tmp_path, monkeypatch,
                                  capsys):
        repo.tag("v2026.11.01")
        installed_from = repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        # The installed record claims an old version but was built from a
        # commit newer than every release: installing would go backwards.
        installed = _installed(MANIFEST, installed_from)
        installed["xx-demo"]["version"] = "0.0.1"
        rc, run, data = _apply(repo, tmp_path, monkeypatch, installed)
        assert rc == 1
        assert run.calls == []
        assert data == installed
        assert "refusing a downgrade" in capsys.readouterr().out

    def test_side_branch_release_is_refused(self, repo, tmp_path,
                                            monkeypatch, capsys):
        base = repo.head()
        installed_from = repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        git(repo.work, "checkout", "-q", "-b", "side", base)
        repo.commit(_bump(MANIFEST, "xx-demo", "0.3.0"), push=False)
        repo.tag("v2026.11.12")
        installed = _installed(_bump(MANIFEST, "xx-demo", "0.2.0"),
                               installed_from)
        rc, run, _data = _apply(repo, tmp_path, monkeypatch, installed)
        assert rc == 1
        assert run.calls == []
        assert "refusing a downgrade" in capsys.readouterr().out

    def test_installed_commit_unknown_to_the_releases_is_refused(
            self, repo, tmp_path, monkeypatch, capsys):
        repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        repo.tag("v2026.11.12")
        installed = _installed(MANIFEST, "f" * 40)
        rc, run, _data = _apply(repo, tmp_path, monkeypatch, installed)
        assert rc == 1
        assert run.calls == []
        assert "not in the release history" in capsys.readouterr().out

    def test_check_refuses_a_bad_release_too(self, repo, tmp_path, capsys):
        first = repo.head()
        repo.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        repo.tag("v2026.11.12", key=repo.other_key)
        state = _state(tmp_path / "installed.json",
                       _installed(MANIFEST, first))
        assert updater.cmd_check(state=state, **repo.source(tmp_path)) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "not signed by the release key" in captured.err


class TestTagScheme:
    def test_order(self):
        tags = ["v2026.11.12", "v2026.11.12.1", "v2026.11.02",
                "v2026.11.12.10", "v2026.11.12.2", "v2025.12.31"]
        assert max(tags, key=updater.release_key) == "v2026.11.12.10"
        assert sorted(tags, key=updater.release_key)[0] == "v2025.12.31"

    @pytest.mark.parametrize("name", [
        "2026.11.12", "v2026.11.12-rc1", "v2026.1.12", "v26.11.12",
        "v2026.11.12.", "latest", "xx-wm-0.1.1"])
    def test_non_release_names(self, name):
        assert updater.release_key(name) is None


class TestShippedKey:
    def test_signers_file_names_one_ed25519_key_for_git(self):
        lines = [line for line in SHIPPED_SIGNERS.read_text(
            encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]
        assert len(lines) == 1
        principal, options, keytype, blob, *_comment = lines[0].split()
        assert principal == PRINCIPAL == updater.SIGNER
        assert options == 'namespaces="git"'
        assert keytype == "ssh-ed25519"
        assert re.fullmatch(r"[A-Za-z0-9+/]+=*", blob)

    def test_no_private_key_in_the_tree(self):
        text = SHIPPED_SIGNERS.read_text(encoding="utf-8")
        assert "PRIVATE KEY" not in text

    def test_meson_installs_it_where_the_updater_reads(self):
        meson = (XX_WM / "launcher" / "meson.build").read_text(
            encoding="utf-8")
        block = meson[meson.index("'data/release-signers'"):]
        block = block[:block.index(")")]
        assert "install_dir: shell_datadir" in block
        assert "rw-r--r--" in block
        assert updater.SIGNERS_FILE == "/usr/share/xx-wm/release-signers"

    @needs_git
    def test_ssh_keygen_parses_it(self, tmp_path):
        from _release_repo import make_key

        key = make_key(tmp_path, "stranger")
        message = tmp_path / "msg"
        message.write_text("hello\n", encoding="utf-8")
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-n", "git",
                        "-f", str(key), str(message)],
                       check=True, capture_output=True, timeout=30)
        result = subprocess.run(
            ["ssh-keygen", "-Y", "find-principals", "-f",
             str(SHIPPED_SIGNERS), "-s", str(message) + ".sig"],
            capture_output=True, text=True, timeout=30, check=False)
        # A stranger's signature: no principal matches, and ssh-keygen
        # must not have tripped over a malformed line in the file.
        assert result.returncode != 0
        assert result.stdout == ""
        assert "%s:" % SHIPPED_SIGNERS not in result.stderr, result.stderr


class TestOwnerScriptPinned:
    def test_terminal_run_sets_no_self_update(self, tmp_path):
        script = tmp_path / "update-system.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        kind, _detail, argv = update_checker.plan_update(
            str(script), which=lambda name: "/usr/bin/" + name
            if name in {"kgx", "bash"} else None)
        assert kind == "terminal"
        assert ("PX_NO_SELF_UPDATE=1 " + str(script)) in argv[-1]

    def test_headless_run_sets_no_self_update(self, tmp_path, monkeypatch):
        class _Immediate:
            @staticmethod
            def idle_add(func, *args):
                func(*args)
                return 0
        monkeypatch.setattr(update_checker, "GLib", _Immediate)
        monkeypatch.delenv("PX_NO_SELF_UPDATE", raising=False)
        script = tmp_path / "update-system.sh"
        script.write_text('#!/bin/sh\necho "flag=$PX_NO_SELF_UPDATE"\n',
                          encoding="utf-8")
        script.chmod(0o755)
        seen: list = []
        done: list = []
        thread = update_checker.run_owner_headless(
            str(script), seen.append, done.append)
        thread.join(timeout=30)
        assert seen == ["flag=1"]
        assert done == [0]

    def test_configured_script_button_sets_no_self_update(
            self, tmp_path, monkeypatch):
        script = tmp_path / "update.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(
            update_checker.shutil, "which",
            lambda binary: "/usr/bin/" + binary
            if binary in ("kgx", "bash") else None)
        calls: list = []
        monkeypatch.setattr(update_checker.subprocess, "Popen",
                            lambda cmd, **k: calls.append(cmd))
        assert update_checker.run_update_script(str(script))[0]
        assert "PX_NO_SELF_UPDATE=1 " + str(script) in calls[0][-1]


# --- scripts/release.sh --------------------------------------------------

def _gh_stub(directory: Path, output: str, rc: int = 0) -> Path:
    stub = directory / "gh-stub"
    stub.write_text("#!/bin/sh\necho \"$*\" >> %s\nprintf '%%s' '%s'\n"
                    "exit %d\n" % (directory / "gh-args", output, rc),
                    encoding="utf-8")
    stub.chmod(0o755)
    return stub


@pytest.fixture
def cut(tmp_path):
    """A pushed checkout whose tree ships the throwaway key as its
    release-signers file, and a runner for release.sh inside it."""
    if tools_missing():
        pytest.skip("needs git and ssh-keygen")
    repo = ReleaseRepo(tmp_path / "rel", MANIFEST)
    signers = repo.work / "xx-wm" / "launcher" / "data" / "release-signers"
    signers.parent.mkdir(parents=True)
    signers.write_text(signers_line(repo.key), encoding="utf-8")
    repo.commit(MANIFEST, "ship the release key")
    passing = _gh_stub(tmp_path, "completed success check\n")

    def run(*args, gh=passing, key=None):
        env = dict(git_env(), XX_RELEASE_KEY=str(key or repo.key),
                   XX_RELEASE_GH=str(gh))
        return subprocess.run(["sh", str(RELEASE_SH), *args],
                              cwd=str(repo.work), env=env,
                              capture_output=True, text=True, timeout=120,
                              check=False)
    repo.run = run
    repo.signers_in_tree = signers
    return repo


def _remote_tags(repo) -> list[str]:
    out = git(repo.work, "ls-remote", "--tags", "--refs", "origin")
    return sorted(line.split("refs/tags/")[1]
                  for line in out.splitlines() if line)


def _today() -> str:
    return time.strftime("v%Y.%m.%d", time.gmtime())


@needs_git
class TestReleaseScript:
    def test_cuts_and_pushes_a_tag_the_updater_accepts(self, cut, tmp_path):
        result = cut.run()
        assert result.returncode == 0, result.stderr
        assert _remote_tags(cut) == [_today()]
        assert git(cut.work, "cat-file", "-t",
                   "refs/tags/" + _today()) == "tag"
        args = (tmp_path / "gh-args").read_text(encoding="utf-8")
        assert "run list" in args and cut.head() in args
        source = dict(cut.source(tmp_path), signers=str(cut.signers_in_tree))
        release = updater.verified_release(**source)
        assert (release.tag, release.sha) == (_today(), cut.head())

    def test_second_release_same_day_gets_a_serial(self, cut):
        assert cut.run().returncode == 0
        cut.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        result = cut.run()
        assert result.returncode == 0, result.stderr
        assert _remote_tags(cut) == sorted([_today(), _today() + ".1"])

    def test_dirty_tree_is_refused(self, cut):
        (cut.work / "apps.json").write_text("{}\n", encoding="utf-8")
        result = cut.run()
        assert result.returncode != 0
        assert "uncommitted" in result.stderr
        assert _remote_tags(cut) == []

    def test_unbumped_app_versions_are_refused(self, cut):
        tool = cut.work / "scripts" / "app_versions.py"
        tool.parent.mkdir()
        tool.write_text("import sys\nprint('xx-demo changed without a "
                        "version bump')\nsys.exit(1)\n", encoding="utf-8")
        cut.commit(MANIFEST, "a version gate that fails")
        result = cut.run()
        assert result.returncode != 0
        assert "not bumped" in result.stderr
        assert _remote_tags(cut) == []

    def test_unpushed_commit_is_refused(self, cut):
        cut.commit(_bump(MANIFEST, "xx-demo", "0.2.0"), push=False)
        result = cut.run()
        assert result.returncode != 0
        assert "not on origin main" in result.stderr
        assert _remote_tags(cut) == []

    @pytest.mark.parametrize("runs", [
        "completed failure check\n", "in_progress  check\n", ""])
    def test_ci_not_passed_is_refused_even_with_force(self, cut, tmp_path,
                                                      runs):
        stub = _gh_stub(tmp_path, runs)
        for args in ((), ("--force",)):
            result = cut.run(*args, gh=stub)
            assert result.returncode != 0
            assert "CI" in result.stderr
        assert _remote_tags(cut) == []

    def test_without_gh_force_is_required(self, cut, tmp_path):
        missing = tmp_path / "no-gh-here"
        result = cut.run(gh=missing)
        assert result.returncode != 0
        assert "--force" in result.stderr
        assert _remote_tags(cut) == []
        result = cut.run("--force", gh=missing)
        assert result.returncode == 0, result.stderr
        assert "warning" in result.stderr
        assert _remote_tags(cut) == [_today()]

    def test_failing_gh_counts_as_unavailable(self, cut, tmp_path):
        stub = _gh_stub(tmp_path, "", rc=1)
        result = cut.run(gh=stub)
        assert result.returncode != 0
        assert "--force" in result.stderr
        assert _remote_tags(cut) == []

    def test_key_not_in_signers_is_refused_and_nothing_pushed(self, cut):
        result = cut.run(key=cut.other_key)
        assert result.returncode != 0
        assert "does not verify" in result.stderr
        assert _remote_tags(cut) == []
        assert git(cut.work, "tag", "--list") == ""

    def test_explicit_tag_must_follow_the_scheme_and_be_newest(self, cut):
        result = cut.run("v2026.1.1")
        assert result.returncode != 0
        assert _remote_tags(cut) == []
        assert cut.run("v2099.01.01").returncode == 0
        cut.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        result = cut.run("v2098.01.01")
        assert result.returncode != 0
        assert "newer" in result.stderr
        assert _remote_tags(cut) == ["v2099.01.01"]

    def test_release_must_descend_from_the_previous_one(self, cut):
        base = cut.head()
        cut.commit(_bump(MANIFEST, "xx-demo", "0.2.0"))
        assert cut.run("v2026.11.12").returncode == 0
        git(cut.work, "checkout", "-q", "-b", "side", base)
        cut.commit(_bump(MANIFEST, "xx-demo", "0.3.0"), push=False)
        git(cut.work, "push", "-q", "--force", "origin", "HEAD:main")
        result = cut.run("v2026.11.13")
        assert result.returncode != 0
        assert "descend" in result.stderr
        assert _remote_tags(cut) == ["v2026.11.12"]

    @pytest.mark.skipif(shutil.which("shellcheck") is None,
                        reason="needs shellcheck")
    def test_shellcheck_clean(self):
        result = subprocess.run(["shellcheck", "-s", "sh", str(RELEASE_SH)],
                                capture_output=True, text=True, timeout=60,
                                check=False)
        assert result.returncode == 0, result.stdout

    def test_never_defaults_to_a_key_in_the_repo(self):
        text = RELEASE_SH.read_text(encoding="utf-8")
        assert "$HOME/.ssh/xx-os-release" in text
        assert os.path.basename(str(SHIPPED_SIGNERS)) in text
