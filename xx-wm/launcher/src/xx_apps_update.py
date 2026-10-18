#!/usr/bin/env python3
"""xx-apps-update: install the XX apps that changed.

Compares the installed state (/var/lib/xx-apps/installed.json, written by
install.sh) against the apps.json published on GitHub main at one pinned
commit SHA, then rebuilds only what is newer -- dependencies first.

Network happens only here, only on an explicit check/apply, over HTTPS with
certificate verification and a timeout. Builds run unprivileged in a fresh
temp dir; only ``meson install`` runs as root (non-interactive sudo where
available, else pkexec; credentials are never stored). Apply builds
everything before installing anything, so a build failure leaves the
installed apps untouched, and the temp dir is removed either way.

Exit codes: 0 means up to date (check) or applied (apply), 10 means updates
are available (check only), anything else is an error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

GITHUB_REPO = "PiercingXX/XX-Linux-Phone"
GITHUB_BRANCH = "main"
API_COMMIT_URL = ("https://api.github.com/repos/%s/commits/%s"
                  % (GITHUB_REPO, GITHUB_BRANCH))
RAW_APPS_URL = ("https://raw.githubusercontent.com/%s/{sha}/apps.json"
                % GITHUB_REPO)
TARBALL_URL = ("https://codeload.github.com/%s/tar.gz/{sha}" % GITHUB_REPO)

FETCH_TIMEOUT = 20
SETUP_TIMEOUT = 300
INSTALL_TIMEOUT = 1200
REFRESH_TIMEOUT = 120

EXIT_UPDATES = 10

UP_TO_DATE = "All XX apps are up to date."
FAILED_LINE = "XX app update failed: %s."

STATE_ENV = "XX_APPS_STATE_DIR"
COMMIT_ENV = "XX_APPS_COMMIT"
DEFAULT_STATE_DIR = "/var/lib/xx-apps"


class UpdateError(Exception):
    pass


class UnsafeTarball(UpdateError):
    pass


def state_dir() -> str:
    return os.environ.get(STATE_ENV) or DEFAULT_STATE_DIR


def installed_path() -> str:
    return os.path.join(state_dir(), "installed.json")


def _https_get(url: str, timeout: int = FETCH_TIMEOUT) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "xx-apps-update",
                      "Accept": "application/vnd.github+json"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout,
                                 context=context) as response:
        return response.read()


def pinned_tree(fetch=None) -> tuple[str, dict]:
    """(commit SHA, apps.json) fetched at that exact SHA."""
    fetch = fetch or _https_get
    try:
        commit_doc = json.loads(fetch(API_COMMIT_URL).decode("utf-8"))
        sha = commit_doc["sha"]
    except (ValueError, KeyError, TypeError) as error:
        raise UpdateError("could not read the latest commit: %s" % error)
    except Exception as error:
        raise UpdateError("could not reach GitHub: %s" % error)
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise UpdateError("bad commit SHA from GitHub.")
    try:
        manifest = json.loads(
            fetch(RAW_APPS_URL.format(sha=sha)).decode("utf-8"))
    except ValueError as error:
        raise UpdateError("published apps.json is invalid: %s" % error)
    except Exception as error:
        raise UpdateError("could not download apps.json: %s" % error)
    if not isinstance(manifest, dict):
        raise UpdateError("published apps.json is invalid.")
    return sha, manifest


def load_installed(path: str | None = None) -> dict:
    path = path or installed_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise UpdateError("installed state not found (run install.sh first).")
    except (OSError, ValueError) as error:
        raise UpdateError("installed state is unreadable: %s" % error)
    if not isinstance(data, dict):
        raise UpdateError("installed state is unreadable.")
    return data


def version_key(version: str) -> tuple:
    """Sortable key for dotted numeric versions (0.1.1, 0.58.0)."""
    key = []
    for part in re.split(r"[.\-+_]", str(version)):
        match = re.match(r"(\d+)(.*)$", part)
        if match:
            key.append((0, int(match.group(1)), match.group(2)))
        else:
            key.append((1, 0, part))
    return tuple(key)


def is_newer(have: str | None, want: str | None) -> bool:
    """True when available ``want`` is newer than installed ``have``."""
    if not want:
        return False
    if not have:
        return True
    try:
        return version_key(want) > version_key(have)
    except TypeError:
        return str(want) != str(have)


def _manifest_version(manifest: dict, app: str) -> str | None:
    entry = manifest.get(app)
    if isinstance(entry, dict):
        version = entry.get("version")
        return str(version) if version is not None else None
    return None


def _manifest_common(manifest: dict, app: str) -> str | None:
    entry = manifest.get(app)
    if isinstance(entry, dict):
        common = entry.get("common")
        return str(common) if common is not None else None
    return None


def _depends_on(manifest: dict, app: str) -> list:
    entry = manifest.get(app)
    if isinstance(entry, dict) and isinstance(entry.get("deps"), list):
        return entry["deps"]
    return []


def find_updates(installed: dict, available: dict) -> list[tuple]:
    """[(app, installed_version, available_version)] needing an update.

    An app qualifies when its available version is newer, or when the
    common tree it builds against moved under it (its recorded common is
    older than the available common) even if its own version did not.
    """
    common_have = _manifest_version(installed, "common")
    common_want = _manifest_version(available, "common")
    common_moved = is_newer(common_have, common_want)
    updates = []
    for app in sorted(available):
        want = _manifest_version(available, app)
        entry = installed.get(app)
        have = entry.get("version") if isinstance(entry, dict) else None
        have = str(have) if have is not None else None
        if is_newer(have, want):
            updates.append((app, have or "none", want))
            continue
        if (common_moved and "common" in _depends_on(available, app)
                and isinstance(entry, dict)):
            recorded = entry.get("common")
            recorded = str(recorded) if recorded is not None else None
            if recorded != common_want:
                updates.append((app, have or "none", want))
    return updates


def order_for_install(apps: list[str], manifest: dict) -> list[str]:
    """Dependency-first order (common before its dependents), stable."""
    wanted = set(apps)
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(app: str) -> None:
        if app in visiting or app in ordered:
            return
        if app not in wanted:
            return
        visiting.add(app)
        for dep in _depends_on(manifest, app):
            if dep in wanted:
                visit(dep)
        visiting.discard(app)
        ordered.append(app)

    for app in sorted(wanted):
        visit(app)
    return ordered


def safe_extract(tarball: str, dest: str) -> str:
    """Extract ``tarball`` under ``dest``; return the single top dir.

    Rejects absolute paths, ``..`` escapes, symlinks/hardlinks escaping the
    destination, and device files. Anything else suspicious fails closed.
    """
    dest_real = os.path.realpath(dest)
    try:
        handle = tarfile.open(tarball, "r:*")
    except (tarfile.TarError, OSError) as error:
        raise UnsafeTarball("not a readable tarball: %s" % error)
    with handle:
        tops: set[str] = set()
        for member in handle.getmembers():
            name = member.name
            if not name or os.path.isabs(name):
                raise UnsafeTarball("absolute path: %r" % name)
            parts = name.split("/")
            if ("" in parts[:-1] or ".." in parts
                    or name.startswith("/") or "/../" in name
                    or name.endswith("/..")):
                raise UnsafeTarball("path escapes: %r" % name)
            tops.add(parts[0])
            target = os.path.realpath(os.path.join(dest, name))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise UnsafeTarball("path escapes: %r" % name)
            if member.islnk() or member.issym():
                link_target = os.path.realpath(
                    os.path.join(os.path.dirname(
                        os.path.join(dest, name)), member.linkname))
                if (link_target != dest_real
                        and not link_target.startswith(dest_real + os.sep)):
                    raise UnsafeTarball("link escapes: %r" % name)
            if member.isdev():
                raise UnsafeTarball("device file: %r" % name)
        if len(tops) != 1:
            raise UnsafeTarball("expected one top dir, found %d." % len(tops))
        handle.extractall(dest, filter="data")
    return os.path.join(dest, tops.pop())


def source_dir(tree: str, app: str, manifest: dict) -> str:
    """The app's build dir inside ``tree``. The path comes from the
    downloaded manifest and feeds a root ``meson install``, so it must be a
    plain relative dir that resolves inside the tree."""
    entry = manifest.get(app)
    path = entry.get("path", app) if isinstance(entry, dict) else app
    if (not isinstance(path, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", path)):
        raise UpdateError("bad path for %s in apps.json." % app)
    src = os.path.join(tree, path, "launcher") if app == "xx-wm" \
        else os.path.join(tree, path)
    tree_real = os.path.realpath(tree)
    if not os.path.realpath(src).startswith(tree_real + os.sep):
        raise UpdateError("path for %s escapes the source tree." % app)
    return src


def _default_run(argv: list[str], cwd: str, timeout: int):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=False)


def build_apps(tree: str, apps: list[str], manifest: dict,
               run=None) -> None:
    """Configure and compile every app; install nothing (failure aborts)."""
    run = run or _default_run
    meson = shutil.which("meson") or "meson"
    for app in apps:
        src = source_dir(tree, app, manifest)
        if not os.path.isfile(os.path.join(src, "meson.build")):
            raise UpdateError("no meson.build for %s." % app)
        attempts = [[meson, "setup", "build", "--prefix=/usr",
                     "--reconfigure"],
                    [meson, "setup", "build", "--prefix=/usr"]]
        configured = False
        last_err = ""
        for argv in attempts:
            try:
                result = run(argv, cwd=src, timeout=SETUP_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as error:
                last_err = str(error)
                continue
            if result.returncode == 0:
                configured = True
                break
            last_err = (result.stderr or result.stdout or "")[-200:]
        if not configured:
            raise UpdateError("build failed for %s%s"
                              % (app, (": " + last_err.strip())
                                  if last_err.strip() else "."))
        try:
            result = run([meson, "compile", "-C", "build"], cwd=src,
                         timeout=INSTALL_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError("build failed for %s: %s" % (app, error))
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()[-200:]
            raise UpdateError("build failed for %s%s"
                              % (app, (": " + err) if err else "."))


def privilege_prefix(check=None) -> list[str]:
    """Root for ``meson install``: none, sudo -n, or pkexec. No passwords."""
    if os.geteuid() == 0:
        return []
    run = check or _default_run
    if shutil.which("sudo") is not None:
        try:
            probe = run(["sudo", "-n", "true"], cwd="/",
                        timeout=30)
        except (OSError, subprocess.SubprocessError):
            probe = None
        if probe is not None and probe.returncode == 0:
            return ["sudo", "-n"]
    if shutil.which("pkexec") is not None:
        return ["pkexec"]
    raise UpdateError("need root for install (no sudo or pkexec).")


def install_built(tree: str, apps: list[str], manifest: dict,
                  run=None, prefix=None) -> None:
    run = run or _default_run
    if prefix is None:
        prefix = privilege_prefix()
    meson = shutil.which("meson") or "meson"
    for app in apps:
        src = source_dir(tree, app, manifest)
        try:
            result = run(prefix + [meson, "install", "-C", "build"],
                         cwd=src, timeout=INSTALL_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError("install failed for %s: %s" % (app, error))
        if result.returncode != 0:
            raise UpdateError("install failed for %s." % app)


def refresh_running(tree: str, run=None) -> None:
    """Best-effort refresh_running_apps from the new tree; never raises."""
    run = run or _default_run
    script = os.path.join(tree, "xx-wm", "scripts", "install.sh")
    if not os.path.isfile(script):
        return
    try:
        run(["sh", script, "--refresh-only"], cwd="/",
            timeout=REFRESH_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass


def record_state(path: str, apps: list[str], manifest: dict,
                 commit: str) -> None:
    """Atomically merge the new versions into installed.json."""
    parent = os.path.dirname(path)
    if parent:
        try:
            os.makedirs(parent, mode=0o755, exist_ok=True)
        except OSError as error:
            raise UpdateError("could not write installed state: %s" % error)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    common_want = _manifest_version(manifest, "common")
    for app in apps:
        entry = dict(data.get(app)) if isinstance(data.get(app), dict) else {}
        entry["version"] = _manifest_version(manifest, app)
        entry["commit"] = commit
        if "common" in _depends_on(manifest, app):
            entry["common"] = common_want
        data[app] = entry
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except OSError as error:
        raise UpdateError("could not write installed state: %s" % error)


def format_check_line(app: str, have: str, want: str) -> str:
    return "%s %s \u2192 %s" % (app, have, want)


def parse_check_output(text: str) -> tuple[int, list[str]]:
    """(count, apps) from ``--check`` output for the Settings status row."""
    apps = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line == UP_TO_DATE:
            continue
        head = line.split(" ", 1)[0]
        if head and "/" not in head and "\\" not in head:
            apps.append(head)
    return len(apps), apps


def cmd_check(fetch=None, state=None) -> int:
    try:
        _sha, available = pinned_tree(fetch=fetch)
        installed = load_installed(state)
    except UpdateError as error:
        print("XX app update check failed: %s" % error, file=sys.stderr)
        return 1
    updates = find_updates(installed, available)
    if not updates:
        print(UP_TO_DATE)
        return 0
    for app, have, want in updates:
        print(format_check_line(app, have, want))
    return EXIT_UPDATES


def cmd_apply(wanted: list[str] | None, fetch=None, run=None,
              state=None) -> int:
    fetch = fetch or _https_get
    run = run or _default_run
    try:
        sha, available = pinned_tree(fetch=fetch)
        try:
            installed = load_installed(state)
        except UpdateError as error:
            if "not found" in str(error):
                installed = {}
            else:
                raise
        updates = find_updates(installed, available)
        if wanted:
            unknown = sorted(set(wanted) - set(available))
            if unknown:
                raise UpdateError("unknown app(s): %s" % ", ".join(unknown))
            outdated = {app for app, _have, _want in updates}
            keep = set(wanted) & outdated
            for app in list(keep):
                for dep in _depends_on(available, app):
                    if dep in outdated:
                        keep.add(dep)
            updates = [u for u in updates if u[0] in keep]
        apps = [app for app, _have, _want in updates]
        if not apps:
            print(UP_TO_DATE)
            return 0
        prefix = privilege_prefix(check=run)
        workdir = tempfile.mkdtemp(prefix="xx-apps-update-")
        try:
            tarball = os.path.join(workdir, "tree.tar.gz")
            try:
                payload = fetch(TARBALL_URL.format(sha=sha))
            except Exception as error:
                raise UpdateError("could not download the release: %s"
                                  % error)
            with open(tarball, "wb") as handle:
                handle.write(payload)
            tree = safe_extract(tarball, workdir)
            ordered = order_for_install(apps, available)
            build_apps(tree, ordered, available, run=run)
            install_built(tree, ordered, available, run=run, prefix=prefix)
            refresh_running(tree, run=run)
            record_state(state or installed_path(), ordered, available, sha)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        for app in ordered:
            print("Installed %s %s."
                  % (app, _manifest_version(available, app)))
        return 0
    except (UpdateError, UnsafeTarball) as error:
        print(FAILED_LINE % error)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("apps", nargs="*")
    parser.add_argument("--state", default=None)
    args = parser.parse_args(argv)
    if args.check and (args.apply or args.apps):
        parser.error("--check takes no other arguments.")
    if args.check:
        return cmd_check(state=args.state)
    if args.apply:
        return cmd_apply(args.apps or None, state=args.state)
    parser.error("need --check or --apply.")


if __name__ == "__main__":
    raise SystemExit(main())
