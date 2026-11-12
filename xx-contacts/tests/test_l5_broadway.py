"""L5 Broadway smoke test: real import/export page over seeded data.

Builds the real contacts window plus the new import/export page against
a seeded temp database, pumps the main loop, and asserts no exceptions.
Runs against ``gtk4-broadwayd`` in a subprocess so this suite's own
process never pins the backend first.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_l5_driver.py")


@pytest.fixture(scope="module")
def broadway_env(tmp_path_factory):
    """A private gtk4-broadwayd: its socket lives in a runtime dir of its
    own, handed to the client, so neither the host's XDG_RUNTIME_DIR nor
    its absence (CI has no login session) decides where GTK looks."""
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    from xx_testing import broadway_display

    run_dir = tmp_path_factory.mktemp("broadway-run")
    run_dir.chmod(0o700)
    try:
        proc, display = broadway_display.start(run_dir=str(run_dir))
    except RuntimeError as exc:
        pytest.fail(f"gtk4-broadwayd did not come up: {exc}")
    env = dict(os.environ)
    env["GDK_BACKEND"] = "broadway"
    env["BROADWAY_DISPLAY"] = display
    env["XDG_RUNTIME_DIR"] = str(run_dir)
    try:
        yield env
    finally:
        broadway_display.stop(proc)


def test_l5_import_export_page_builds_and_pumps(broadway_env, tmp_path):
    base = str(tmp_path / "l5seed")
    os.makedirs(base, exist_ok=True)
    from broadway_run import run_driver

    completed = run_driver(
        [sys.executable, _DRIVER, base],
        env=broadway_env, timeout=120,
    )
    assert "Traceback" not in (completed.stderr or ""), (
        completed.stderr[-3000:])
    assert "CRITICAL" not in (completed.stderr or ""), (
        completed.stderr[-3000:])
    assert completed.returncode == 0, (
        f"driver failed: {(completed.stderr or '')[-3000:]}"
        f"{(completed.stdout or '')[-3000:]}")
    markers = {}
    for line in (completed.stdout or "").splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("PAGES_OK") == "True", markers
    assert markers.get("EXCEPTIONS") == "0", markers
    assert markers.get("LIST_VISIBLE") == "True", markers
    assert markers.get("LIST_HAS_IMPORT_EXPORT") == "True", markers
    assert markers.get("IMPORT_TITLE") == "Import/export", markers
    assert markers.get("IMPORT_VISIBLE") == "True", markers
    assert markers.get("IMPORT_VCF") == "True", markers
    assert markers.get("IMPORT_BACKUP") == "True", markers
    assert markers.get("IMPORT_EDS_SENSITIVE") == "False", markers
    assert markers.get("EXPORT_READY") == "True", markers
    assert markers.get("PUBLISH_STATE") == "False", markers
    assert markers.get("STATUS_EMPTY") == "True", markers
