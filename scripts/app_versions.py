#!/usr/bin/env python3
"""Per-app versions for the XX-Linux-Phone monorepo.

The ``version:`` in each app dir's meson.build is THE version; apps roll
independently (see docs/versioning.md). This tool generates and checks the
``apps.json`` manifest at the repo root::

    {app: {"version": ..., "path": ..., "deps": [...], ...}, ...}

Usage:
    app_versions.py generate [--root DIR]
    app_versions.py check [--root DIR] [--no-history]
    app_versions.py bump <app> [patch|minor|major] [--root DIR]

Only the standard library is used so the installer and the gate can run it
anywhere python3 exists. Network is never touched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

REPO_APPS_EXCLUDE = frozenset({"scripts", "docs", "ref"})

_VERSION_RE = re.compile(r"""version\s*:\s*['"]([^'"]+)['"]""")

GITHUB_REPO = "PiercingXX/XX-Linux-Phone"
GITHUB_BRANCH = "main"


def repo_root(start: str | None = None) -> str:
    """Repo root: the parent of scripts/, or ``start`` itself."""
    base = os.path.abspath(start or os.path.dirname(__file__))
    if os.path.basename(base) == "scripts":
        return os.path.dirname(base)
    return base


def meson_file_for(root: str, app: str) -> str:
    if app == "xx-wm":
        return os.path.join(root, "xx-wm", "launcher", "meson.build")
    return os.path.join(root, app, "meson.build")


def read_meson_version(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def discover_apps(root: str) -> list[str]:
    """Every top-level dir with its own meson ``version:``, including xx-wm."""
    apps = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        if name in REPO_APPS_EXCLUDE or name.startswith("."):
            continue
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        if name == "xx-wm":
            if read_meson_version(meson_file_for(root, name)) is not None:
                apps.append(name)
            continue
        meson = os.path.join(full, "meson.build")
        if os.path.isfile(meson) and read_meson_version(meson) is not None:
            apps.append(name)
    return apps


_DEP_PATTERNS = ("xx_ui", "xx_phone")


def detect_deps(root: str, app: str) -> list[str]:
    """['common'] when the app builds on the shared design system."""
    if app in ("common", "xx-keyboard"):
        return []
    if app == "xx-wm":
        top = os.path.join(root, "xx-wm", "launcher")
    else:
        top = os.path.join(root, app)
    for dirpath, _dirnames, filenames in os.walk(top):
        if "build" in dirpath.split(os.sep):
            continue
        for filename in filenames:
            if not filename.endswith((".py", ".build")):
                continue
            try:
                with open(os.path.join(dirpath, filename),
                          encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            if any(mark in text for mark in _DEP_PATTERNS):
                return ["common"]
    return []


def build_manifest(root: str) -> dict:
    manifest: dict[str, dict] = {}
    common_version = read_meson_version(meson_file_for(root, "common"))
    for app in discover_apps(root):
        version = read_meson_version(meson_file_for(root, app))
        if version is None:
            continue
        if app == "xx-wm":
            path = "xx-wm"
        else:
            path = app
        deps = detect_deps(root, app)
        entry: dict = {"version": version, "path": path, "deps": deps}
        if "common" in deps and common_version is not None:
            entry["common"] = common_version
        manifest[app] = entry
    return manifest


def manifest_path(root: str) -> str:
    return os.path.join(root, "apps.json")


def _atomic_write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def cmd_generate(root: str) -> int:
    _atomic_write_json(manifest_path(root), build_manifest(root))
    return 0


def load_manifest(root: str) -> dict | None:
    try:
        with open(manifest_path(root), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def version_key(version: str) -> tuple:
    """Sortable key for dotted numeric versions (0.1.1, 0.58.0)."""
    parts = re.split(r"[.\-+_]", str(version))
    key = []
    for part in parts:
        match = re.match(r"(\d+)(.*)$", part)
        if match:
            key.append((0, int(match.group(1)), match.group(2)))
        else:
            key.append((1, 0, part))
    return tuple(key)


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=60)


def last_manifest_commit(root: str) -> str | None:
    result = _git(root, "log", "-1", "--format=%H", "--", "apps.json")
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def changed_paths_since(root: str, base: str) -> set[str] | None:
    """Committed (base..HEAD) plus working-tree paths, repo-relative."""
    result = _git(root, "diff", "--name-only", base + "..HEAD")
    if result.returncode != 0:
        return None
    paths = set(result.stdout.split())
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        return None
    for line in status.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path != "apps.json":
            paths.add(path)
    return paths


def unit_for_path(path: str, apps: list[str]) -> str | None:
    top = path.split("/", 1)[0]
    if top in apps:
        return top
    if top == "xx-wm" and "xx-wm" in apps:
        return "xx-wm"
    return None


def check_bump_rule(root: str, base: str | None = None) -> list[str]:
    """Units changed since the last apps.json commit without a version bump."""
    errors = []
    if base is None:
        base = last_manifest_commit(root)
    if base is None:
        return ["no git history for apps.json; version-bump rule skipped"]
    show = _git(root, "show", base + ":apps.json")
    if show.returncode != 0:
        return ["cannot read apps.json at " + base]
    try:
        old = json.loads(show.stdout)
    except ValueError:
        return ["apps.json at " + base + " is not valid JSON"]
    paths = changed_paths_since(root, base)
    if paths is None:
        return ["git diff failed; version-bump rule skipped"]
    apps = discover_apps(root)
    changed_units: set[str] = set()
    for path in paths:
        unit = unit_for_path(path, apps)
        if unit is not None:
            changed_units.add(unit)
    for unit in sorted(changed_units):
        old_entry = old.get(unit)
        if not isinstance(old_entry, dict) or "version" not in old_entry:
            continue
        new_version = read_meson_version(meson_file_for(root, unit))
        if new_version == old_entry["version"]:
            errors.append(
                "%s changed since %s without a version bump (still %s)"
                % (unit, base[:12], new_version))
    return errors


def check_manifest(root: str, no_history: bool = False) -> list[str]:
    errors = []
    manifest = load_manifest(root)
    if manifest is None:
        return ["apps.json is missing or invalid; run app_versions.py generate"]
    expected = build_manifest(root)
    if set(manifest) != set(expected):
        errors.append("apps.json apps %s != tree apps %s"
                      % (sorted(manifest), sorted(expected)))
    for app, entry in expected.items():
        got = manifest.get(app)
        if got != entry:
            errors.append("%s: apps.json has %s, tree says %s"
                          % (app, got, entry))
    if not no_history:
        history = check_bump_rule(root)
        if history == ["no git history for apps.json; version-bump rule skipped"]:
            print(history[0], file=sys.stderr)
        else:
            errors.extend(history)
    return errors


def bump_version(version: str, part: str) -> str:
    nums = str(version).split(".")
    while len(nums) < 3:
        nums.append("0")
    try:
        major, minor, patch = (int(nums[0]), int(nums[1]), int(nums[2]))
    except ValueError:
        raise SystemExit("cannot bump non-numeric version %r" % (version,))
    if part == "major":
        return "%d.0.0" % (major + 1,)
    if part == "minor":
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def cmd_bump(root: str, app: str, part: str) -> int:
    apps = discover_apps(root)
    if app not in apps:
        print("unknown app %r (known: %s)" % (app, ", ".join(apps)),
              file=sys.stderr)
        return 1
    meson = meson_file_for(root, app)
    try:
        with open(meson, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1
    old = read_meson_version(meson)
    if old is None:
        print("no version: found in %s" % meson, file=sys.stderr)
        return 1
    new = bump_version(old, part)
    updated, count = _VERSION_RE.subn("version: '%s'" % new, text, count=1)
    if count != 1:
        print("could not rewrite the version line in %s" % meson,
              file=sys.stderr)
        return 1
    with open(meson, "w", encoding="utf-8") as handle:
        handle.write(updated)
    return cmd_generate(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate")
    check = sub.add_parser("check")
    check.add_argument("--no-history", action="store_true")
    bump = sub.add_parser("bump")
    bump.add_argument("app")
    bump.add_argument("part", nargs="?", default="patch",
                      choices=("patch", "minor", "major"))
    args = parser.parse_args(argv)
    root = repo_root(args.root)
    if args.command == "generate":
        return cmd_generate(root)
    if args.command == "check":
        errors = check_manifest(root, no_history=args.no_history)
        for error in errors:
            print("app_versions: %s" % error, file=sys.stderr)
        return 1 if errors else 0
    return cmd_bump(root, args.app, args.part)


if __name__ == "__main__":
    raise SystemExit(main())
