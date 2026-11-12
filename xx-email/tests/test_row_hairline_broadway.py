"""Broadway hairline test for the mailbox rows (needs a real display).

List rows carry a 1 px hairline except the final one (``last`` drops
it). ``replace_store`` refills incrementally while the ListView binds
eagerly, so a position check inside the bind factory marks every row
last unless the store rebinds once the refill lands. The driver below
renders three rows through the real refill path and counts the last
marks; skips cleanly only when gtk4-broadwayd is missing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "row_hairline_driver.py")


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
    try:
        yield (display, str(run_dir))
    finally:
        broadway_display.stop(proc)


def _run_driver(broadway: tuple) -> dict:
    display, daemon_runtime = broadway
    env = dict(os.environ)
    env["GDK_BACKEND"] = "broadway"
    env["BROADWAY_DISPLAY"] = display
    env["XDG_RUNTIME_DIR"] = daemon_runtime
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(__file__).resolve().parent.parent / "src"),
        str(Path(__file__).resolve().parent.parent.parent /
            "common"),
    ))
    completed = subprocess.run(
        [sys.executable, _DRIVER],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, (
        f"hairline driver failed: {completed.stderr[-2000:]}")
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers, "hairline driver printed no markers"
    return markers


def test_only_final_row_drops_its_hairline(broadway_env):
    markers = _run_driver(broadway_env)
    assert markers.get("ERRORS") == "none"
    assert markers.get("ROWS") == "3"
    assert markers.get("LASTCOUNT") == "1", \
        "every row counts as last: hairlines never paint"
