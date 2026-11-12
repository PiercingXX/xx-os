"""Throwaway signed-release repositories for the updater and release tests.

Every key is generated in the test's tmp dir; the real release key is never
read. Git runs with no global or system config, so the developer's signing
or hook settings cannot leak in.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

PRINCIPAL = "release@xx-os"

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Release Test",
    "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
    "GIT_COMMITTER_NAME": "Release Test",
    "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
}


def tools_missing() -> list[str]:
    return [tool for tool in ("git", "ssh-keygen")
            if shutil.which(tool) is None]


def git_env() -> dict:
    return dict(os.environ, **GIT_ENV)


def git(cwd, *args, check=True, stdin=None) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), env=git_env(),
                            capture_output=True, text=True, timeout=120,
                            input=stdin, check=False)
    if check and result.returncode != 0:
        raise AssertionError("git %s failed: %s" % (
            " ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def make_key(directory: Path, name: str) -> Path:
    path = directory / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                    "-C", "throwaway " + name, "-f", str(path)],
                   check=True, capture_output=True, timeout=60)
    return path


def signers_line(key: Path, principal: str = PRINCIPAL) -> str:
    public = Path(str(key) + ".pub").read_text(encoding="utf-8").strip()
    return '%s namespaces="git" %s\n' % (principal, public)


def meson_path(work: Path, app: str, path: str) -> Path:
    if app == "xx-wm":
        return work / path / "launcher" / "meson.build"
    return work / path / "meson.build"


class ReleaseRepo:
    """A work clone pushing to a bare ``origin.git``, plus a release key,
    another key and an allowed-signers file naming only the release key."""

    def __init__(self, root: Path, manifest: dict):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.work = root / "work"
        self.origin = root / "origin.git"
        self.key = make_key(root, "release")
        self.other_key = make_key(root, "other")
        self.signers = root / "release-signers"
        self.signers.write_text(signers_line(self.key), encoding="utf-8")
        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(root, "init", "-q", "-b", "main", str(self.work))
        git(self.work, "remote", "add", "origin", str(self.origin))
        self.commit(manifest, "first")

    def write_tree(self, manifest: dict) -> None:
        (self.work / "apps.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        for app, entry in manifest.items():
            meson = meson_path(self.work, app, entry.get("path", app))
            meson.parent.mkdir(parents=True, exist_ok=True)
            meson.write_text("project('%s', version: '%s')\n"
                             % (app, entry["version"]), encoding="utf-8")

    def commit(self, manifest: dict, message: str = "change",
               push: bool = True) -> str:
        self.write_tree(manifest)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "--allow-empty", "-m", message)
        if push:
            git(self.work, "push", "-q", "origin", "HEAD:main")
        return self.head()

    def head(self, rev: str = "HEAD") -> str:
        return git(self.work, "rev-parse", rev + "^{commit}")

    def tag(self, name: str, rev: str = "HEAD", key: Path | None = None,
            sign: bool = True) -> str:
        if sign:
            git(self.work, "-c", "gpg.format=ssh",
                "-c", "user.signingkey=%s" % (key or self.key),
                "tag", "-s", "-m", "xx-os " + name, name, rev)
        else:
            git(self.work, "tag", "-a", "-m", "xx-os " + name, name, rev)
        self.push_ref(name, name)
        return git(self.work, "rev-parse", "refs/tags/" + name)

    def lightweight_tag(self, name: str, rev: str = "HEAD") -> None:
        git(self.work, "tag", name, rev)
        self.push_ref(name, name)

    def push_ref(self, source: str, tag_name: str) -> None:
        git(self.work, "push", "-q", "--force", "origin",
            "%s:refs/tags/%s" % (source, tag_name))

    def tamper(self, name: str, new_commit: str) -> None:
        """Re-point a signed tag object at another commit, keeping its
        signature, and force-push it under the same name."""
        body = git(self.work, "cat-file", "tag", "refs/tags/" + name) + "\n"
        lines = body.split("\n")
        lines[0] = "object " + new_commit
        forged = git(self.work, "mktag", stdin="\n".join(lines))
        git(self.work, "update-ref", "refs/tags/" + name, forged)
        self.push_ref(forged, name)

    def source(self, tmp: Path) -> dict:
        """The updater's origin keyword arguments for this repo."""
        return {"url": str(self.origin), "cache": str(tmp / "cache.git"),
                "signers": str(self.signers)}
