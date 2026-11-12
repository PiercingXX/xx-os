"""L3 fix-up round 3: the search entry must not autofocus (Broadway).

Regression: ``set_focusable(False)`` left the ``SearchEntry``'s inner
``GtkText`` focusable, so launch and ``Adw.NavigationView`` pop focused
it and popped the on-screen keyboard. Each test runs one scenario of
``tests/broadway_focus_driver.py`` in a fresh process against a real
GTK display via ``gtk4-broadwayd`` (a subprocess keeps this suite's own
process — whose GTK modules may already be imported — from pinning the
backend first) and skips when the daemon binary is missing.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_focus_driver.py")


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


def _run_scenario(env: dict, name: str) -> dict:
    from broadway_run import run_driver

    completed = run_driver(
        [sys.executable, _DRIVER, name],
        env=env, timeout=90,
    )
    assert completed.returncode == 0, (
        f"driver {name} failed: {completed.stderr[-2000:]}")
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers, f"driver {name} printed no markers"
    return markers


def test_startup_search_entry_takes_no_focus(broadway_env):
    markers = _run_scenario(broadway_env, "startup")
    assert markers["STARTUP_CAN_FOCUS"] == "False"
    assert markers["STARTUP_INSIDE"] == "False"


def test_tap_puts_caret_into_entry_text(broadway_env):
    markers = _run_scenario(broadway_env, "tap")
    assert markers["TAP_BEFORE"] == "False"
    assert markers["TAP_CAN_FOCUS"] == "True"
    assert markers["TAP_INSIDE"] == "True"
    chain = markers["TAP_CHAIN"].split(">")
    assert "SearchEntry" in chain
    assert chain[0] == "Text"


def test_focus_elsewhere_locks_entry_again(broadway_env):
    markers = _run_scenario(broadway_env, "relock")
    assert markers["RELOCK_TAPPED_INSIDE"] == "True"
    assert markers["RELOCK_INSIDE"] == "False"
    assert markers["RELOCK_CAN_FOCUS"] == "False"


def test_nav_pop_does_not_refocus_entry(broadway_env):
    markers = _run_scenario(broadway_env, "navpop")
    assert markers["NAVPOP_STARTUP_INSIDE"] == "False"
    assert markers["NAVPOP_TAPPED_INSIDE"] == "True"
    assert markers["NAVPOP_AFTER_POP_INSIDE"] == "False"
