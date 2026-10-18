"""Per-app versions: manifest generation and the version-bump gate.

Drives scripts/app_versions.py against the real tree (manifest matches the
meson versions) and against temp git repos for the bump rule. No network,
no installs.
"""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = importlib.util.spec_from_file_location(
    "app_versions", REPO_ROOT / "scripts" / "app_versions.py")
app_versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_versions)

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="needs git")


def _write_meson(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("project(\n  'x',\n  version: '%s',\n)\n" % version,
                    encoding="utf-8")


def _make_repo(root: Path) -> Path:
    (root / "common").mkdir(parents=True)
    _write_meson(root / "common" / "meson.build", "0.1.0")
    (root / "common" / "lib.py").write_text("X = 1\n", encoding="utf-8")
    (root / "xx-demo").mkdir()
    _write_meson(root / "xx-demo" / "meson.build", "0.2.0")
    (root / "xx-demo" / "main.py").write_text("import xx_ui\n",
                                              encoding="utf-8")
    (root / "xx-plain").mkdir()
    _write_meson(root / "xx-plain" / "meson.build", "0.1.0")
    (root / "xx-plain" / "main.py").write_text("print('hi')\n",
                                               encoding="utf-8")
    (root / "xx-wm" / "launcher").mkdir(parents=True)
    _write_meson(root / "xx-wm" / "launcher" / "meson.build", "0.1.1")
    (root / "xx-wm" / "launcher" / "main.py").write_text("import xx_ui\n",
                                                         encoding="utf-8")
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run([GIT, "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], cwd=root, check=True, capture_output=True)


class TestManifest:
    def test_real_tree_matches_apps_json(self):
        errors = app_versions.check_manifest(str(REPO_ROOT), no_history=True)
        assert errors == []

    def test_generate_discovers_apps_deps_and_paths(self, tmp_path):
        _make_repo(tmp_path)
        manifest = app_versions.build_manifest(str(tmp_path))
        assert manifest["xx-demo"] == {
            "version": "0.2.0", "path": "xx-demo",
            "deps": ["common"], "common": "0.1.0"}
        assert manifest["xx-plain"] == {
            "version": "0.1.0", "path": "xx-plain", "deps": []}
        assert manifest["common"] == {
            "version": "0.1.0", "path": "common", "deps": []}
        assert manifest["xx-wm"]["version"] == "0.1.1"
        assert manifest["xx-wm"]["deps"] == ["common"]

    def test_real_manifest_has_every_app(self):
        manifest = app_versions.load_manifest(str(REPO_ROOT))
        assert manifest is not None
        for app in ("common", "xx-wm", "xx-keyboard", "xx-find",
                    "xx-contacts", "txxt"):
            assert app in manifest
        assert manifest["xx-keyboard"]["version"] == "0.58.0"
        assert manifest["xx-keyboard"]["deps"] == []
        assert manifest["xx-find"]["deps"] == []
        assert manifest["xx-wm"]["version"] == "0.1.1"

    def test_check_flags_a_stale_manifest(self, tmp_path):
        _make_repo(tmp_path)
        assert app_versions.cmd_generate(str(tmp_path)) == 0
        _write_meson(tmp_path / "xx-demo" / "meson.build", "0.3.0")
        errors = app_versions.check_manifest(str(tmp_path), no_history=True)
        assert any("xx-demo" in error for error in errors)

    def test_version_key_orders_release_lines(self):
        assert (app_versions.version_key("0.1.1")
                > app_versions.version_key("0.1.0"))
        assert (app_versions.version_key("0.58.0")
                > app_versions.version_key("0.1.1"))


class TestBump:
    def test_bump_patch_minor_major(self, tmp_path):
        _make_repo(tmp_path)
        assert app_versions.cmd_bump(str(tmp_path), "xx-demo", "patch") == 0
        assert (app_versions.read_meson_version(
            str(tmp_path / "xx-demo" / "meson.build")) == "0.2.1")
        assert app_versions.cmd_bump(str(tmp_path), "xx-demo", "minor") == 0
        assert (app_versions.read_meson_version(
            str(tmp_path / "xx-demo" / "meson.build")) == "0.3.0")
        manifest = app_versions.load_manifest(str(tmp_path))
        assert manifest["xx-demo"]["version"] == "0.3.0"

    def test_bump_major_resets(self, tmp_path):
        _make_repo(tmp_path)
        assert app_versions.cmd_bump(str(tmp_path), "xx-demo", "major") == 0
        assert (app_versions.read_meson_version(
            str(tmp_path / "xx-demo" / "meson.build")) == "1.0.0")

    def test_bump_unknown_app_fails(self, tmp_path):
        _make_repo(tmp_path)
        assert app_versions.cmd_bump(str(tmp_path), "xx-nope", "patch") == 1

    def test_bump_regenerates_the_manifest(self, tmp_path):
        _make_repo(tmp_path)
        assert app_versions.cmd_generate(str(tmp_path)) == 0
        assert app_versions.cmd_bump(str(tmp_path), "xx-plain", "patch") == 0
        manifest = app_versions.load_manifest(str(tmp_path))
        assert manifest["xx-plain"]["version"] == "0.1.1"
        assert (app_versions.check_manifest(
            str(tmp_path), no_history=True) == [])


class TestBumpRule:
    def _committed(self, root: Path) -> None:
        _git(root, "init")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", "base")
        assert app_versions.cmd_generate(str(root)) == 0
        _git(root, "add", "apps.json")
        _git(root, "commit", "-m", "manifest")

    @needs_git
    def test_clean_tree_passes(self, tmp_path):
        _make_repo(tmp_path)
        self._committed(tmp_path)
        assert app_versions.check_bump_rule(str(tmp_path)) == []

    @needs_git
    def test_code_change_without_bump_fails(self, tmp_path):
        _make_repo(tmp_path)
        self._committed(tmp_path)
        (tmp_path / "xx-demo" / "main.py").write_text(
            "import xx_ui\nNEW = 1\n", encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-m", "feature, no bump")
        errors = app_versions.check_bump_rule(str(tmp_path))
        assert len(errors) == 1 and "xx-demo" in errors[0]

    @needs_git
    def test_bump_clears_the_rule(self, tmp_path):
        _make_repo(tmp_path)
        self._committed(tmp_path)
        (tmp_path / "xx-demo" / "main.py").write_text(
            "import xx_ui\nNEW = 1\n", encoding="utf-8")
        assert app_versions.cmd_bump(str(tmp_path), "xx-demo", "minor") == 0
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-m", "feature with bump")
        assert app_versions.check_bump_rule(str(tmp_path)) == []

    @needs_git
    def test_common_change_requires_only_a_common_bump(self, tmp_path):
        _make_repo(tmp_path)
        self._committed(tmp_path)
        (tmp_path / "common" / "lib.py").write_text("X = 2\n",
                                                   encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-m", "common tweak, no bump")
        errors = app_versions.check_bump_rule(str(tmp_path))
        assert len(errors) == 1 and errors[0].startswith("common ")
        assert app_versions.cmd_bump(str(tmp_path), "common", "patch") == 0
        assert app_versions.check_bump_rule(str(tmp_path)) == []

    def test_no_history_reports_skip_not_failure(self, tmp_path):
        _make_repo(tmp_path)
        assert app_versions.cmd_generate(str(tmp_path)) == 0
        result = app_versions.check_bump_rule(str(tmp_path))
        assert result == [
            "no git history for apps.json; version-bump rule skipped"]
        assert os.path.isfile(tmp_path / "apps.json")
