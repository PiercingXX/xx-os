"""L6 Broadway smoke test: picker, prefilled editor, vcf-open pages.

Builds the real contacts window plus the new pick-mode pages (one per
kind, driven through the real service ``Pick`` path and its real GTK
presenter), a ``--tel``-prefilled editor, and an import page fed with
a ``.vcf`` file, against a seeded temp database; pumps the main loop;
asserts no exceptions. Runs against ``gtk4-broadwayd`` in a subprocess
so this suite's own process never pins the backend first.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_l6_driver.py")


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


def test_l6_pages_build_and_pump_without_exceptions(broadway_env,
                                                    tmp_path):
    base = str(tmp_path / "l6seed")
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
    assert markers.get("PICK_CONTACT_ROWS") == "3", markers
    assert markers.get("PICK_PHONE_ROWS") == "3", markers
    assert markers.get("PICK_EMAIL_ROWS") == "1", markers
    assert markers.get("PICK_CONTACT_WINDOW") == "True", markers
    assert markers.get("PICK_PHONE_WINDOW") == "True", markers
    assert markers.get("PICK_EMAIL_WINDOW") == "True", markers
    assert "+1 555 0100" in markers.get("PICK_PHONE_REPLY", ""), markers
    assert "ada@example.org" in markers.get("PICK_EMAIL_REPLY", ""), (
        markers)
    assert markers.get("PICK_CANCEL_REPLY") == "('', '')", markers
    for kind in ("CONTACT", "PHONE", "EMAIL"):
        focus = markers.get(f"PICK_{kind}_FOCUS", "")
        assert focus and "Entry" not in focus and "Text" not in focus, (
            markers)
    assert markers.get("PREFILL_NUMBER") == "+1 555 0100", markers
    assert markers.get("VCF_PAGE") == "ImportExportPage", markers
    assert markers.get("VCF_PENDING") == "True", markers
    assert markers.get("VCF_UNCHANGED") == "True", markers
    assert markers.get("LAUNCH_NEW_PAGE") == "ContactEditorPage", markers
    assert markers.get("LAUNCH_NEW_NUMBER") == "+1 555 0404", markers
