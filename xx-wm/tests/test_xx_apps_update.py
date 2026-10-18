"""xx-apps-update: compare, safe extraction, ordering, apply, Settings row.

Fake fetchers and fake installers throughout: no network, no installs,
no sudo. The only real subprocesses are ``tar``-free tarfile builds in
tmp dirs and ``sh`` sourcing of install.sh's record function with an
overridden state dir.
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import settings_updates
import update_checker
import xx_apps_update as updater

SHA = "0123456789abcdef0123456789abcdef01234567"

AVAILABLE = {
    "common": {"version": "0.1.1", "path": "common", "deps": []},
    "xx-demo": {"version": "0.2.0", "path": "xx-demo",
                "deps": ["common"], "common": "0.1.1"},
    "xx-plain": {"version": "0.1.0", "path": "xx-plain", "deps": []},
}

INSTALLED = {
    "common": {"version": "0.1.0", "commit": "old", "common": "0.1.0"},
    "xx-demo": {"version": "0.1.9", "commit": "old", "common": "0.1.0"},
    "xx-plain": {"version": "0.1.0", "commit": "old"},
}


def _api_payload():
    return json.dumps({"sha": SHA}).encode()


def _manifest_payload(manifest=AVAILABLE):
    return json.dumps(manifest).encode()


def _make_tarball(root: Path, top: str, apps: dict) -> bytes:
    """Real tarball bytes with {top}/{app}/meson.build per app."""
    src = root / "src"
    for app, version in apps.items():
        meson = src / top / app / "meson.build"
        meson.parent.mkdir(parents=True, exist_ok=True)
        meson.write_text("project('x', version: '%s')\n" % version)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(src / top), arcname=top)
    return buf.getvalue()


class _Run:
    """Fake subprocess runner: records argv, cwd; behavior per command."""

    def __init__(self, fail_compile=(), fail_setup=()):
        self.calls: list[tuple[list, str]] = []
        self.fail_compile = set(fail_compile)
        self.fail_setup = set(fail_setup)

    def __call__(self, argv, cwd="/", timeout=0):
        self.calls.append((list(argv), cwd))
        app = cwd.rstrip("/").rsplit("/", 1)[-1]
        if "compile" in argv and app in self.fail_compile:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        if "setup" in argv and app in self.fail_setup:
            return SimpleNamespace(returncode=1, stdout="", stderr="nope")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _fetch(mapping):
    def fetch(url, timeout=20):
        for key, payload in mapping.items():
            if url == key:
                return payload
        raise AssertionError("unexpected fetch: %s" % url)
    return fetch


def _check_fetch(tarball: bytes, manifest=AVAILABLE):
    return _fetch({
        updater.API_COMMIT_URL: _api_payload(),
        updater.RAW_APPS_URL.format(sha=SHA): _manifest_payload(manifest),
        updater.TARBALL_URL.format(sha=SHA): tarball,
    })


def _write_state(path: Path, data=INSTALLED) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class TestFindUpdates:
    def test_newer_version_is_an_update(self):
        updates = updater.find_updates(INSTALLED, AVAILABLE)
        assert ("xx-demo", "0.1.9", "0.2.0") in updates
        assert ("common", "0.1.0", "0.1.1") in updates

    def test_unchanged_app_is_not_listed(self):
        updates = updater.find_updates(INSTALLED, AVAILABLE)
        assert all(app != "xx-plain" for app, _h, _w in updates)

    def test_common_move_rebuilds_dependents_without_their_own_bump(self):
        installed = {
            "common": {"version": "0.1.0", "commit": "old",
                       "common": "0.1.0"},
            "xx-demo": {"version": "0.2.0", "commit": "old",
                        "common": "0.1.0"},
        }
        updates = updater.find_updates(installed, AVAILABLE)
        assert ("xx-demo", "0.2.0", "0.2.0") in updates
        assert ("common", "0.1.0", "0.1.1") in updates

    def test_up_to_date_is_empty(self):
        assert updater.find_updates(AVAILABLE, AVAILABLE) == []


class TestOrdering:
    def test_common_comes_first(self):
        assert updater.order_for_install(
            ["xx-demo", "common"], AVAILABLE) == ["common", "xx-demo"]

    def test_independent_apps_sort_stably(self):
        assert updater.order_for_install(
            ["xx-plain", "common"], AVAILABLE) == ["common", "xx-plain"]


class TestSafeExtract:
    def _tar(self, path: Path, members: dict, links=(),
             devs=()) -> str:
        out = str(path / "t.tar")
        with tarfile.open(out, "w") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            for name, target in links:
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = target
                tar.addfile(info)
            for name in devs:
                info = tarfile.TarInfo(name)
                info.type = tarfile.CHRTYPE
                tar.addfile(info)
        return out

    def test_good_tree_extracts(self, tmp_path):
        tarball = self._tar(tmp_path, {"top/a.txt": b"x"})
        dest = tmp_path / "out"
        dest.mkdir()
        assert updater.safe_extract(tarball, str(dest)) == str(dest / "top")
        assert (dest / "top" / "a.txt").is_file()

    def test_absolute_path_rejected(self, tmp_path):
        tarball = self._tar(tmp_path, {"/etc/evil": b"x"})
        with pytest.raises(updater.UnsafeTarball):
            updater.safe_extract(tarball, str(tmp_path))

    def test_dotdot_rejected(self, tmp_path):
        tarball = self._tar(tmp_path, {"top/../../evil": b"x"})
        with pytest.raises(updater.UnsafeTarball):
            updater.safe_extract(tarball, str(tmp_path))

    def test_escaping_symlink_rejected(self, tmp_path):
        tarball = self._tar(tmp_path, {"top/ok.txt": b"x"},
                            links=[("top/link", "/etc/passwd")])
        with pytest.raises(updater.UnsafeTarball):
            updater.safe_extract(tarball, str(tmp_path))

    def test_local_symlink_allowed(self, tmp_path):
        tarball = self._tar(tmp_path, {"top/ok.txt": b"x"},
                            links=[("top/link", "ok.txt")])
        dest = tmp_path / "out"
        dest.mkdir()
        updater.safe_extract(tarball, str(dest))

    def test_device_file_rejected(self, tmp_path):
        tarball = self._tar(tmp_path, {"top/ok.txt": b"x"},
                            devs=["top/dev"])
        with pytest.raises(updater.UnsafeTarball):
            updater.safe_extract(tarball, str(tmp_path))

    def test_scattered_tops_rejected(self, tmp_path):
        tarball = self._tar(tmp_path, {"a/x": b"x", "b/y": b"y"})
        with pytest.raises(updater.UnsafeTarball):
            updater.safe_extract(tarball, str(tmp_path))


class TestCheck:
    def test_updates_listed_with_exit_10(self, tmp_path, capsys):
        state = _write_state(tmp_path / "installed.json")
        fetch = _fetch({
            updater.API_COMMIT_URL: _api_payload(),
            updater.RAW_APPS_URL.format(sha=SHA): _manifest_payload(),
        })
        assert updater.cmd_check(fetch=fetch, state=state) == 10
        out = capsys.readouterr().out
        assert "xx-demo 0.1.9 \u2192 0.2.0" in out
        assert "common 0.1.0 \u2192 0.1.1" in out
        assert "xx-plain" not in out

    def test_up_to_date_with_exit_0(self, tmp_path, capsys):
        state = _write_state(tmp_path / "installed.json", AVAILABLE)
        fetch = _fetch({
            updater.API_COMMIT_URL: _api_payload(),
            updater.RAW_APPS_URL.format(sha=SHA): _manifest_payload(),
        })
        assert updater.cmd_check(fetch=fetch, state=state) == 0
        assert updater.UP_TO_DATE in capsys.readouterr().out

    def test_fetch_failure_is_an_error(self, tmp_path):
        state = _write_state(tmp_path / "installed.json")

        def fetch(_url, timeout=20):
            raise OSError("offline")
        assert updater.cmd_check(fetch=fetch, state=state) == 1

    def test_missing_state_is_an_error(self, tmp_path):
        fetch = _fetch({
            updater.API_COMMIT_URL: _api_payload(),
            updater.RAW_APPS_URL.format(sha=SHA): _manifest_payload(),
        })
        assert updater.cmd_check(
            fetch=fetch, state=str(tmp_path / "nope.json")) == 1


class TestApply:
    def _apply(self, tmp_path, monkeypatch, run, manifest=AVAILABLE,
               installed=INSTALLED, wanted=None):
        tarball = _make_tarball(
            tmp_path, "XX-Linux-Phone-" + SHA[:7],
            {"common": manifest["common"]["version"],
             "xx-demo": manifest["xx-demo"]["version"],
             "xx-plain": manifest["xx-plain"]["version"]})
        state = _write_state(tmp_path / "installed.json", installed)
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.setattr(updater.os, "geteuid", lambda: 0)
        created: list = []
        real_mkdtemp = updater.tempfile.mkdtemp

        def fake_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(
                dir=str(tmp_path), *args[1:], **kwargs)
            created.append(path)
            return path
        monkeypatch.setattr(updater.tempfile, "mkdtemp", fake_mkdtemp)
        fetch = _check_fetch(tarball, manifest)
        rc = updater.cmd_apply(wanted, fetch=fetch, run=run, state=state)
        return rc, state, run, created

    def test_apply_updates_state_and_installs_dep_first(
            self, tmp_path, monkeypatch, capsys):
        run = _Run()
        rc, state, run, created = self._apply(tmp_path, monkeypatch, run)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Installed common 0.1.1." in out
        assert "Installed xx-demo 0.2.0." in out
        data = json.loads(Path(state).read_text(encoding="utf-8"))
        assert data["common"]["version"] == "0.1.1"
        assert data["common"]["commit"] == SHA
        assert data["xx-demo"]["version"] == "0.2.0"
        assert data["xx-demo"]["common"] == "0.1.1"
        assert data["xx-plain"]["version"] == "0.1.0"
        kinds = ["install" if "install" in argv else "build"
                 for argv, _cwd in run.calls]
        assert "install" in kinds
        assert kinds.index("install") > kinds.index("build")
        common_setup = next(i for i, (_a, cwd) in enumerate(run.calls)
                            if cwd.endswith("/common"))
        demo_install = next(i for i, (argv, _c) in enumerate(run.calls)
                            if "install" in argv
                            and run.calls[i][1].endswith("/xx-demo"))
        assert common_setup < demo_install
        assert created and not any(
            os.path.exists(path) for path in created)

    def test_build_failure_leaves_system_untouched(
            self, tmp_path, monkeypatch, capsys):
        run = _Run(fail_compile=("xx-demo",))
        rc, state, run, created = self._apply(tmp_path, monkeypatch, run)
        assert rc == 1
        assert created and not any(
            os.path.exists(path) for path in created)
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 1
        assert lines[0].startswith("XX app update failed:")
        assert json.loads(Path(state).read_text(
            encoding="utf-8")) == INSTALLED
        assert not any("install" in argv for argv, _cwd in run.calls)

    def test_unknown_app_is_one_plain_line(
            self, tmp_path, monkeypatch, capsys):
        run = _Run()
        rc, _state, _run, _created = self._apply(
            tmp_path, monkeypatch, run, wanted=["xx-nope"])
        assert rc == 1
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines == ["XX app update failed: unknown app(s): xx-nope."]

    def test_named_subset_keeps_needed_deps(
            self, tmp_path, monkeypatch, capsys):
        run = _Run()
        rc, _state, run, _created = self._apply(
            tmp_path, monkeypatch, run, wanted=["xx-demo"])
        assert rc == 0
        apps = sorted(cwd.rsplit("/", 1)[-1]
                      for argv, cwd in run.calls if "install" in argv)
        assert apps == ["common", "xx-demo"]
        capsys.readouterr()


class TestPrivilege:
    def test_root_needs_no_prefix(self, monkeypatch):
        monkeypatch.setattr(updater.os, "geteuid", lambda: 0)
        assert updater.privilege_prefix() == []

    def test_noninteractive_sudo_preferred(self, monkeypatch):
        monkeypatch.setattr(updater.os, "geteuid", lambda: 1000)
        monkeypatch.setattr(updater.shutil, "which",
                            lambda name: "/usr/bin/" + name)
        calls: list = []

        def run(argv, cwd="/", timeout=0):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert updater.privilege_prefix(check=run) == ["sudo", "-n"]

    def test_pkexec_fallback(self, monkeypatch):
        monkeypatch.setattr(updater.os, "geteuid", lambda: 1000)
        monkeypatch.setattr(
            updater.shutil, "which",
            lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)

        def run(argv, cwd="/", timeout=0):
            raise AssertionError("no sudo probe expected")
        assert updater.privilege_prefix(check=run) == ["pkexec"]

    def test_no_privilege_is_an_error(self, monkeypatch):
        monkeypatch.setattr(updater.os, "geteuid", lambda: 1000)
        monkeypatch.setattr(updater.shutil, "which", lambda _n: None)
        with pytest.raises(updater.UpdateError):
            updater.privilege_prefix()


class TestSettingsRow:
    def test_parse_counts_names(self):
        count, apps = settings_updates.parse_xx_check_output(
            "xx-demo 0.1.9 \u2192 0.2.0\ncommon 0.1.0 \u2192 0.1.1\n")
        assert (count, apps) == (2, ["xx-demo", "common"])

    def test_parse_ignores_up_to_date_line(self):
        assert settings_updates.parse_xx_check_output(
            settings_updates.XX_APPS_UP_TO_DATE + "\n") == (0, [])

    def test_titles(self):
        assert settings_updates.xx_status_title(0, "") == (
            "XX apps: up to date", "")
        assert settings_updates.xx_status_title(10, "a 1 \u2192 2\n") == (
            "XX apps: 1 update", "a")
        title, subtitle = settings_updates.xx_status_title(
            10, "b 1 \u2192 2\nc 1 \u2192 3\n")
        assert title == "XX apps: 2 updates"
        assert subtitle == "b, c"
        assert settings_updates.xx_status_title(None, "") == (
            "XX apps: status unknown", "")
        assert settings_updates.xx_status_title(1, "boom") == (
            "XX apps: status unknown", "")
        assert settings_updates.xx_status_title(10, "") == (
            "XX apps: status unknown", "")

    def test_run_check_uses_binary_and_timeout(self):
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["timeout"] = kwargs.get("timeout")
            return SimpleNamespace(returncode=10, stdout="a 1 \u2192 2\n")
        rc, out = settings_updates.run_xx_apps_check(
            which=lambda _n: "/usr/bin/xx-apps-update", run=fake_run)
        assert rc == 10
        assert seen["argv"] == ["/usr/bin/xx-apps-update", "--check"]
        assert seen["timeout"] == settings_updates.XX_APPS_CHECK_TIMEOUT
        assert "a 1" in out

    def test_run_check_without_binary_is_unknown(self):
        assert settings_updates.run_xx_apps_check(
            which=lambda _n: None) == (None, "")

    def test_run_check_failure_is_unknown(self):
        def fake_run(argv, **kwargs):
            raise OSError("gone")
        assert settings_updates.run_xx_apps_check(
            which=lambda _n: "/usr/bin/xx-apps-update",
            run=fake_run) == (None, "")


class _Label:
    def __init__(self):
        self.text = ""
        self.visible = True

    def set_text(self, text):
        self.text = text

    def set_visible(self, value):
        self.visible = bool(value)


def _page_self():
    import settings_page

    row = SimpleNamespace(
        title_label=_Label(), subtitle_label=_Label())
    section = SimpleNamespace(_xx_row=row)
    statuses: list = []
    self = SimpleNamespace(
        _sections={"system": section},
        _update_status_text=statuses.append,
        _refresh_updates_section=lambda: statuses.append("refresh"),
    )
    return settings_page, self, statuses, row


class TestPageWiring:
    def test_xx_checked_paints_row(self):
        settings_page, self, _statuses, row = _page_self()
        settings_page.SettingsPage._on_xx_checked(
            self, (10, "xx-demo 0.1.9 \u2192 0.2.0\n"))
        assert row.title_label.text == "XX apps: 1 update"
        assert row.subtitle_label.text == "xx-demo"

    def test_xx_checked_unknown(self):
        settings_page, self, _statuses, row = _page_self()
        settings_page.SettingsPage._on_xx_checked(self, (None, ""))
        assert row.title_label.text == "XX apps: status unknown"

    def test_counted_carries_xx_result(self):
        settings_page, self, statuses, row = _page_self()
        win_calls: list = []
        self._win = SimpleNamespace(
            _on_updates_available=win_calls.append,
            _show_status=lambda _t: None)
        self._on_xx_checked = types.MethodType(
            settings_page.SettingsPage._on_xx_checked, self)
        settings_page.SettingsPage._on_counted(
            self, (0, 0, (10, "xx-demo 0.1.9 \u2192 0.2.0\n")))
        assert row.title_label.text == "XX apps: 1 update"
        assert statuses

    def test_counted_accepts_legacy_pair(self):
        settings_page, self, statuses, row = _page_self()
        self._win = SimpleNamespace(
            _on_updates_available=lambda _c: None,
            _show_status=lambda _t: None)
        settings_page.SettingsPage._on_counted(self, (0, 0))
        assert row.title_label.text == ""

    def test_terminal_plan_chains_apply(self, tmp_path):
        script = tmp_path / "update-system.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")

        def which(name):
            if name in {"kgx", "bash", "xx-apps-update"}:
                return "/usr/bin/" + name
            return None
        kind, _detail, argv = update_checker.plan_update(str(script),
                                                         which=which)
        assert kind == "terminal"
        assert argv and "xx-apps-update --apply" in argv[-1]

    def test_terminal_plan_without_updater_is_unchanged(self, tmp_path):
        script = tmp_path / "update-system.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")

        def which(name):
            if name in {"kgx", "bash"}:
                return "/usr/bin/" + name
            return None
        kind, _detail, argv = update_checker.plan_update(str(script),
                                                         which=which)
        assert kind == "terminal"
        assert argv and "xx-apps-update" not in argv[-1]

    def test_missing_script_runs_xx_update_alone(self, monkeypatch):
        import settings_page

        opened: list = []
        monkeypatch.setattr(
            update_checker, "plan_update",
            lambda _s, which=None: ("missing", "gone", None))
        monkeypatch.setattr(
            settings_updates, "xx_apply_command",
            lambda which=None: ["/usr/bin/xx-apps-update", "--apply"])
        monkeypatch.setattr(
            update_checker, "update_terminal_argv",
            lambda cmd, which=None: ("kgx", ["kgx", "-e", "sh", "-c", cmd]))
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda argv, **_k: opened.append(argv)
            or SimpleNamespace())
        statuses: list = []
        self = SimpleNamespace(
            _win=SimpleNamespace(
                _on_update_clicked=lambda _a: statuses.append("clicked")),
            _update_status_text=statuses.append,
        )
        settings_page.SettingsPage._execute_xx_update_alone(self)
        assert opened == [["kgx", "-e", "sh", "-c", "xx-apps-update --apply"]]
        assert statuses == ["Updating XX apps in terminal."]

    def test_owner_done_chains_apply_headless(self, tmp_path, monkeypatch):
        import settings_page

        log = tmp_path / "update-1.log"
        log.write_text(
            "Update summary\n  Flatpak                     ok\n"
            "Full log: %s\nSystem update finished.\n" % log,
            encoding="utf-8")
        monkeypatch.setattr(settings_updates, "xx_apply_command",
                            lambda which=None: ["x", "--apply"])
        monkeypatch.setattr(settings_updates, "reboot_required",
                            lambda **_k: None)
        monkeypatch.setattr(settings_page, "_run_bg",
                            lambda work, done: done(work()))
        statuses: list = []
        notes: list = []

        class _Shade:
            def add_notification(self, *args):
                notes.append(args)
        row = SimpleNamespace(
            title_label=_Label(), subtitle_label=_Label())
        self = SimpleNamespace(
            _win=SimpleNamespace(_ensure_shade=_Shade),
            _update_status_text=statuses.append,
            _refresh_updates_section=lambda: statuses.append("refresh"),
            _run_xx_apply=lambda _p: (0, "Installed xx-demo 0.2.0."),
            _sections={"system": SimpleNamespace(_xx_row=row)},
            _on_xx_checked=lambda _x: statuses.append("row"),
        )
        self._on_chained_xx_done = types.MethodType(
            settings_page.SettingsPage._on_chained_xx_done, self)
        self._announce_owner_finished = types.MethodType(
            settings_page.SettingsPage._announce_owner_finished, self)
        monkeypatch.setattr(settings_updates, "run_xx_apps_check",
                            lambda *a, **k: (0, ""))
        settings_page.SettingsPage._on_owner_done(self, 0, str(log))
        assert statuses[0] == "System done — updating XX apps…"
        assert "System update finished." in statuses
        assert notes and notes[0][2] == "System update finished"
        assert "row" in statuses


class TestInstallRecord:
    INSTALL = (Path(__file__).resolve().parent.parent
               / "scripts" / "install.sh")

    def _source_funcs(self, tmp_path) -> Path:
        text = self.INSTALL.read_text(encoding="utf-8")
        start = text.index("_xx_state_dir()")
        end = text.index('XX_APPS="xx-contacts')
        funcs = tmp_path / "xx-funcs.sh"
        funcs.write_text("REPO_DIR=%s\n%s" % (
            REPO_ROOT / "xx-wm", text[start:end]), encoding="utf-8")
        return funcs

    def test_record_writes_versions_atomically(self, tmp_path):
        funcs = self._source_funcs(tmp_path)
        state = tmp_path / "state"
        env = dict(os.environ, XX_APPS_STATE_DIR=str(state),
                   XX_APPS_COMMIT="deadbeef")
        script = ("REPO_DIR=%s; . %s; record_installed xx-find; "
                  "record_installed common; ls %s" % (
                      REPO_ROOT / "xx-wm", funcs, state))
        result = subprocess.run(["sh", "-c", script], capture_output=True,
                                text=True, timeout=60, env=env)
        assert result.returncode == 0, result.stderr
        assert ".installed.json.tmp." not in result.stdout
        data = json.loads((state / "installed.json").read_text(
            encoding="utf-8"))
        assert data["xx-find"]["commit"] == "deadbeef"
        assert data["xx-find"]["version"] == "0.1.0"
        assert data["common"]["version"] == "0.1.0"

    def test_installer_marks_every_app_install(self):
        text = self.INSTALL.read_text(encoding="utf-8")
        assert text.count('record_installed "') >= 4
        assert 'if [ "${REFRESH_ONLY:-0}" = 1 ]; then' in text

    def test_refresh_only_needs_no_network_or_sudo(self):
        text = self.INSTALL.read_text(encoding="utf-8")
        assert ('[ "${REFRESH_ONLY:-0}" != 1 ] && ! net_ok'
                in text)


def test_manifest_paths_cannot_leave_the_tree(tmp_path):
    # The path feeds a root meson install: it must stay inside the tree.
    import pytest
    import xx_apps_update as u
    (tmp_path / 'xx-radio').mkdir()
    assert u.source_dir(str(tmp_path), 'xx-radio', {'xx-radio': {'path': 'xx-radio'}})
    for bad in ('../etc', '/etc', 'a/../../b', '', '..', 'x/y'):
        with pytest.raises(u.UpdateError):
            u.source_dir(str(tmp_path), 'xx-radio', {'xx-radio': {'path': bad}})


def test_commit_sha_must_be_a_full_hex_sha():
    import json
    import pytest
    import xx_apps_update as u
    for bad in ('abc1234', '../../x', 'G' * 40, ''):
        def fetch(url, _b=bad):
            return json.dumps({'sha': _b}).encode()
        with pytest.raises(u.UpdateError):
            u.pinned_tree(fetch)
