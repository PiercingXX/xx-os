"""xx-wm.in keyboard watchdog: if xx-keyboard exits within a few seconds
(e.g. too-new build on an older compositor), start phosh-osk-stevia or
squeekboard instead so the session never ends up without a keyboard.

Exercises the real `_xx_keyboard_start` block from launcher/data/xx-wm.in
(extracted between markers, never copied) under `sh` with fake commands
on PATH. No network, no real session bus, no display.
"""
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
WRAPPER_IN = REPO / 'launcher' / 'data' / 'xx-wm.in'

BEGIN = '# _xx_keyboard_start: BEGIN'
END = '# _xx_keyboard_start: END'


@pytest.fixture(scope='module')
def kb_block():
    text = WRAPPER_IN.read_text(encoding='utf-8')
    start = text.find(BEGIN)
    end = text.find(END)
    assert start != -1 and end != -1 and start < end, 'keyboard block markers moved'
    # Full lines only: skip to the end of the BEGIN line so no comment
    # tail leaks into the extracted shell.
    start = text.find('\n', start) + 1
    block = text[start:end]
    assert 'command -v xx-keyboard' in block
    assert 'elif command -v squeekboard' in block
    assert 'phosh-osk-stevia' in block
    assert 'logger' in block
    return block + '\n'


def _write_fake(path, body):
    path.write_text('#!/bin/sh\n' + body + '\n', encoding='utf-8')
    path.chmod(0o755)


def _run_block(kb_block, tmp_path, kb_body, fallbacks, with_logger=True):
    """Run the extracted block with fakes; return the marker dir."""
    fakebin = tmp_path / 'fakebin'
    markers = tmp_path / 'markers'
    fakebin.mkdir()
    markers.mkdir()
    _write_fake(fakebin / 'xx-keyboard', kb_body)
    for name in fallbacks:
        _write_fake(
            fakebin / name,
            'echo started >> "$XX_TEST_MARKERS/%s.marker"\nexit 0' % name,
        )
    if with_logger:
        _write_fake(fakebin / 'logger', 'echo "$*" >> "$XX_TEST_MARKERS/logger.log"\nexit 0')
    harness = tmp_path / 'harness.sh'
    # Wait for the watchdog (the block's last background job) so the
    # test observes its decision instead of racing it.
    harness.write_text(
        kb_block + '_xx_harness_watchdog=$!\nwait "$_xx_harness_watchdog"\n',
        encoding='utf-8',
    )
    env = {
        'PATH': '%s:/usr/bin:/bin' % fakebin,
        'XX_KB_FALLBACK_DELAY': '1',
        'XX_TEST_MARKERS': str(markers),
    }
    proc = subprocess.run(
        ['sh', str(harness)], capture_output=True, text=True, env=env, timeout=30,
    )
    return proc, markers


def _wait_for(path, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def _kill_long_keyboard(markers):
    pidfile = markers / 'kb.pid'
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def test_wrapper_parses():
    result = subprocess.run(['sh', '-n', str(WRAPPER_IN)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_fast_exit_prefers_phosh_osk_stevia(kb_block, tmp_path):
    proc, markers = _run_block(
        kb_block, tmp_path, 'exit 1', ['phosh-osk-stevia', 'squeekboard'],
    )
    assert proc.returncode == 0, proc.stderr
    assert _wait_for(markers / 'phosh-osk-stevia.marker'), 'fallback never started'
    assert not (markers / 'squeekboard.marker').exists(), 'wrong fallback won'
    assert _wait_for(markers / 'logger.log'), 'fallback was not logged via logger'


def test_fast_exit_falls_back_to_squeekboard(kb_block, tmp_path):
    proc, markers = _run_block(kb_block, tmp_path, 'exit 1', ['squeekboard'])
    assert proc.returncode == 0, proc.stderr
    assert _wait_for(markers / 'squeekboard.marker'), 'squeekboard fallback never started'


def test_healthy_keyboard_gets_no_fallback(kb_block, tmp_path):
    proc, markers = _run_block(
        kb_block, tmp_path,
        # Detach stdio: a long-lived child inherits the harness pipes,
        # and subprocess.run would wait for EOF instead of process exit.
        'echo $$ > "$XX_TEST_MARKERS/kb.pid"\nexec sleep 30 </dev/null >/dev/null 2>&1',
        ['phosh-osk-stevia', 'squeekboard'],
    )
    try:
        assert proc.returncode == 0, proc.stderr
        time.sleep(2)
        assert not (markers / 'phosh-osk-stevia.marker').exists()
        assert not (markers / 'squeekboard.marker').exists()
    finally:
        _kill_long_keyboard(markers)


def test_fast_exit_without_any_fallback_is_quiet(kb_block, tmp_path):
    proc, markers = _run_block(kb_block, tmp_path, 'exit 1', [])
    assert proc.returncode == 0, proc.stderr
    assert list(markers.iterdir()) == []


def test_no_xx_keyboard_starts_squeekboard_directly(kb_block):
    # Static pin: without xx-keyboard installed, squeekboard stays the
    # direct (non-watchdog) fallback, as before.
    assert 'elif command -v squeekboard' in kb_block
    before, _, _ = kb_block.partition('elif command -v squeekboard')
    assert '_xx_kb_pid' in before
    assert 'squeekboard &' in kb_block


@pytest.mark.skipif(shutil.which('sh') is None, reason='no sh')
def test_posix_sh_only(kb_block):
    for bashism in ('[[', 'function ', '&>', 'source '):
        assert bashism not in kb_block, bashism
