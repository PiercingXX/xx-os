#!/usr/bin/env python3
"""xx-apps-update: install the XX apps that changed, from signed releases.

Fetches the xx-os repository's release tags into a cache clone, picks the
newest release tag (``vYYYY.MM.DD`` or ``vYYYY.MM.DD.N``) and verifies its
SSH signature against the allowed-signers file the shell installs
(/usr/share/xx-wm/release-signers). An unsigned tag, a tag signed by any
other key, a tampered or renamed tag object, and a release that does not
descend from an installed app's recorded commit are all refused: nothing is
built. Otherwise the installed state (/var/lib/xx-apps/installed.json,
written by install.sh) is compared against that tag's apps.json, and only
what is newer is rebuilt from exactly the verified commit -- dependencies
first.

Network happens only here, only on an explicit check/apply, through git
over HTTPS. Builds run unprivileged in a fresh temp dir; only
``meson install`` runs as root (non-interactive sudo where available, else
pkexec; credentials are never stored). Apply builds everything before
installing anything, so a build failure leaves the installed apps
untouched, and the temp dir is removed either way.

Exit codes: 0 means up to date (check) or applied (apply), 10 means updates
are available (check only), anything else is an error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import NamedTuple

REPO_URL = "https://github.com/PiercingXX/xx-os.git"
SIGNERS_FILE = "/usr/share/xx-wm/release-signers"
SIGNER = "release@xx-os"
RELEASE_TAG_RE = re.compile(r"v(\d{4})\.(\d{2})\.(\d{2})(?:\.(\d+))?")
SHA_RE = re.compile(r"[0-9a-f]{40}")

GIT_TIMEOUT = 600
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


class Release(NamedTuple):
    tag: str
    sha: str
    manifest: dict
    repo: str


def state_dir() -> str:
    return os.environ.get(STATE_ENV) or DEFAULT_STATE_DIR


def installed_path() -> str:
    return os.path.join(state_dir(), "installed.json")


def cache_repo() -> str:
    base = (os.environ.get("XDG_CACHE_HOME")
            or os.path.join(os.path.expanduser("~"), ".cache"))
    return os.path.join(base, "xx-apps-update", "xx-os.git")


def _run_git(argv: list[str], cwd: str, timeout: int):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=False, env=env)


def _git(run, repo: str, *args: str, timeout: int = GIT_TIMEOUT):
    try:
        return run(["git", "-C", repo, *args], cwd="/", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError("git failed: %s" % error) from error


def _last_line(result) -> str:
    lines = (result.stderr or result.stdout or "").strip().splitlines()
    return lines[-1].strip().rstrip(".") if lines else "no output"


def release_key(tag: str) -> tuple | None:
    """Sort key for a release tag name, or None when it is not one."""
    match = RELEASE_TAG_RE.fullmatch(tag)
    if match is None:
        return None
    year, month, day, serial = match.groups()
    return int(year), int(month), int(day), int(serial or 0)


def fetch_releases(repo: str, url: str, run=None) -> None:
    """Bring the cache clone's main and tags up to date with ``url``.
    Tags are force-updated and pruned so the clone mirrors the origin;
    nothing fetched is trusted until verify_release says so."""
    run = run or _run_git
    if not os.path.isdir(os.path.join(repo, "objects")):
        try:
            os.makedirs(os.path.dirname(repo), mode=0o700, exist_ok=True)
            result = run(["git", "init", "--quiet", "--bare", repo],
                         cwd="/", timeout=GIT_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError(
                "could not create the release cache: %s" % error) from error
        if result.returncode != 0:
            raise UpdateError("could not create the release cache: %s"
                              % _last_line(result))
    result = _git(run, repo, "fetch", "--quiet", "--force", "--prune",
                  "--no-tags", url, "+refs/heads/main:refs/heads/main",
                  "+refs/tags/*:refs/tags/*")
    if result.returncode != 0:
        raise UpdateError("could not fetch releases: %s" % _last_line(result))


def newest_release(repo: str, run=None) -> str:
    run = run or _run_git
    result = _git(run, repo, "for-each-ref",
                  "--format=%(refname:strip=2)", "refs/tags/")
    if result.returncode != 0:
        raise UpdateError("could not list releases: %s" % _last_line(result))
    tags = [tag for tag in result.stdout.split()
            if release_key(tag) is not None]
    if not tags:
        raise UpdateError("no release has been published yet")
    return max(tags, key=lambda tag: release_key(tag) or ())


def _tag_headers(text: str) -> dict:
    headers: dict[str, str] = {}
    for line in text.split("\n"):
        if not line:
            break
        key, _sep, value = line.partition(" ")
        headers.setdefault(key, value)
    return headers


def verify_release(repo: str, tag: str, signers: str, run=None) -> str:
    """The commit SHA of release ``tag`` once its tag object is proven to
    be signed by a key in ``signers`` (principal SIGNER, namespace git).
    Raises UpdateError for anything else."""
    run = run or _run_git
    if not os.path.isfile(signers):
        raise UpdateError("the release signing key is not installed (%s)"
                          % signers)
    ref = "refs/tags/" + tag
    result = _git(run, repo, "cat-file", "tag", ref)
    if result.returncode != 0:
        raise UpdateError("release %s is not a signed tag" % tag)
    headers = _tag_headers(result.stdout)
    sha = headers.get("object", "")
    if headers.get("type") != "commit" or not SHA_RE.fullmatch(sha):
        raise UpdateError("release %s does not name a commit" % tag)
    # The signature covers the name inside the tag object, not the ref:
    # a genuine old release re-pushed under a newer name must not pass.
    if headers.get("tag") != tag:
        raise UpdateError("release %s carries the tag name %r"
                          % (tag, headers.get("tag")))
    result = _git(run, repo,
                  "-c", "gpg.format=ssh",
                  "-c", "gpg.ssh.program=ssh-keygen",
                  "-c", "gpg.ssh.allowedSignersFile=" + signers,
                  "-c", "gpg.minTrustLevel=fully",
                  "verify-tag", ref)
    good = 'Good "git" signature for %s with ' % SIGNER
    if result.returncode != 0 or good not in (result.stderr or ""):
        raise UpdateError("release %s is not signed by the release key: %s"
                          % (tag, _last_line(result)))
    return sha


def release_manifest(repo: str, sha: str, run=None) -> dict:
    run = run or _run_git
    result = _git(run, repo, "cat-file", "blob", sha + ":apps.json")
    if result.returncode != 0:
        raise UpdateError("the release has no apps.json")
    try:
        manifest = json.loads(result.stdout)
    except ValueError as error:
        raise UpdateError(
            "the release's apps.json is invalid: %s" % error) from error
    if not isinstance(manifest, dict):
        raise UpdateError("the release's apps.json is invalid")
    return manifest


def verified_release(url: str | None = None, cache: str | None = None,
                     signers: str | None = None, git=None) -> Release:
    """Fetch, pick the newest release tag, verify it, read its apps.json."""
    repo = cache or cache_repo()
    fetch_releases(repo, url or REPO_URL, run=git)
    tag = newest_release(repo, run=git)
    sha = verify_release(repo, tag, signers or SIGNERS_FILE, run=git)
    return Release(tag, sha, release_manifest(repo, sha, run=git), repo)


def check_descends(release: Release, installed: dict, apps: list[str],
                   git=None) -> None:
    """Refuse a release that does not descend from the commit each of
    ``apps`` was installed from (no downgrades, no side branches)."""
    run = git or _run_git
    for app in apps:
        entry = installed.get(app)
        commit = entry.get("commit") if isinstance(entry, dict) else None
        if (not isinstance(commit, str) or not SHA_RE.fullmatch(commit)
                or commit == release.sha):
            continue
        result = _git(run, release.repo, "merge-base", "--is-ancestor",
                      commit, release.sha)
        if result.returncode == 1:
            raise UpdateError(
                "release %s does not descend from the installed %s (%s); "
                "refusing a downgrade" % (release.tag, app, commit[:12]))
        if result.returncode != 0:
            raise UpdateError(
                "the installed %s (%s) is not in the release history"
                % (app, commit[:12]))


def export_tree(release: Release, workdir: str, git=None) -> str:
    """Extract exactly the verified commit under ``workdir``."""
    run = git or _run_git
    tarball = os.path.join(workdir, "tree.tar")
    result = _git(run, release.repo, "archive", "--format=tar",
                  "--prefix=xx-os/", "-o", tarball, release.sha)
    if result.returncode != 0:
        raise UpdateError("could not export the release: %s"
                          % _last_line(result))
    return safe_extract(tarball, workdir)


def load_installed(path: str | None = None) -> dict:
    path = path or installed_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise UpdateError(
            "installed state not found (run install.sh first).") from None
    except (OSError, ValueError) as error:
        raise UpdateError(
            "installed state is unreadable: %s" % error) from error
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
        raise UnsafeTarball("not a readable tarball: %s" % error) from error
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
            raise UpdateError(
                "build failed for %s: %s" % (app, error)) from error
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
        # Absolute: pkexec starts the command in / whatever cwd says.
        build = os.path.join(src, "build")
        try:
            result = run(prefix + [meson, "install", "-C", build],
                         cwd=src, timeout=INSTALL_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError(
                "install failed for %s: %s" % (app, error)) from error
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
                 commit: str, run=None, prefix=None,
                 stage_dir: str | None = None,
                 tag: str | None = None) -> None:
    """Merge the new versions into installed.json.

    The state dir belongs to root (install.sh creates it), so when it is
    not writable the merged file is staged in ``stage_dir`` and copied in
    with the same privilege ``prefix`` that ran ``meson install``;
    otherwise it is replaced atomically in place."""
    parent = os.path.dirname(path)
    if prefix and parent and not _can_write(parent):
        _record_privileged(path,
                           _merged_state(path, apps, manifest, commit, tag),
                           run or _default_run, prefix,
                           stage_dir or tempfile.gettempdir())
        return
    if parent:
        try:
            os.makedirs(parent, mode=0o755, exist_ok=True)
        except OSError as error:
            raise UpdateError(
                "could not write installed state: %s" % error) from error
    data = _merged_state(path, apps, manifest, commit, tag)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except OSError as error:
        raise UpdateError(
            "could not write installed state: %s" % error) from error


def _can_write(directory: str) -> bool:
    """True when ``directory``, or the nearest existing parent it would
    be created in, is writable by this process."""
    probe = directory
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return os.access(probe or ".", os.W_OK)


def _record_privileged(path: str, data: dict, run, prefix: list[str],
                       stage_dir: str) -> None:
    fd, staged = tempfile.mkstemp(prefix="installed-", suffix=".json",
                                  dir=stage_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        try:
            result = run(prefix + ["install", "-D", "-m", "0644", staged,
                                   path], cwd="/", timeout=SETUP_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError(
                "could not write installed state: %s" % error) from error
        if result.returncode != 0:
            raise UpdateError("could not write installed state.")
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass


def _merged_state(path: str, apps: list[str], manifest: dict,
                  commit: str, tag: str | None = None) -> dict:
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
        if tag:
            entry["release"] = tag
        if "common" in _depends_on(manifest, app):
            entry["common"] = common_want
        data[app] = entry
    return data


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


def _plan(state, url, cache, signers, git) -> tuple[Release, dict, list]:
    release = verified_release(url=url, cache=cache, signers=signers,
                               git=git)
    installed = load_installed(state)
    updates = find_updates(installed, release.manifest)
    check_descends(release, installed, [app for app, _h, _w in updates],
                   git=git)
    return release, installed, updates


def cmd_check(state=None, url=None, cache=None, signers=None,
              git=None) -> int:
    try:
        _release, _installed, updates = _plan(state, url, cache, signers,
                                              git)
    except UpdateError as error:
        print("XX app update check failed: %s" % error, file=sys.stderr)
        return 1
    if not updates:
        print(UP_TO_DATE)
        return 0
    for app, have, want in updates:
        print(format_check_line(app, have, want))
    return EXIT_UPDATES


def cmd_apply(wanted: list[str] | None, run=None, state=None, url=None,
              cache=None, signers=None, git=None) -> int:
    run = run or _default_run
    try:
        release = verified_release(url=url, cache=cache, signers=signers,
                                   git=git)
        available = release.manifest
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
        check_descends(release, installed, apps, git=git)
        prefix = privilege_prefix(check=run)
        workdir = tempfile.mkdtemp(prefix="xx-apps-update-")
        try:
            tree = export_tree(release, workdir, git=git)
            ordered = order_for_install(apps, available)
            build_apps(tree, ordered, available, run=run)
            install_built(tree, ordered, available, run=run, prefix=prefix)
            refresh_running(tree, run=run)
            record_state(state or installed_path(), ordered, available,
                         release.sha, run=run, prefix=prefix,
                         stage_dir=workdir, tag=release.tag)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        for app in ordered:
            print("Installed %s %s from %s."
                  % (app, _manifest_version(available, app), release.tag))
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
