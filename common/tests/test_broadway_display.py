"""The shared Broadway display picker: probes, never collides."""

import os
import shutil
import socket

import pytest

from xx_testing import broadway_display as bd


def test_taken_http_port_marks_display_busy(tmp_path):
    for number in range(bd.FIRST_DISPLAY, bd.LAST_DISPLAY + 1):
        if bd.display_free(number, str(tmp_path)):
            break
    else:
        pytest.skip('no free display to occupy')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('0.0.0.0', 8080 + number))
        sock.listen(1)
        assert not bd.display_free(number, str(tmp_path))


def test_dead_socket_does_not_block_a_display(tmp_path):
    # A file left by a killed daemon is not a live display.
    (tmp_path / 'broadway43.socket').write_text('')
    live = bd._socket_live(bd.socket_path(42, str(tmp_path)))
    assert not live


def test_parallel_starts_get_distinct_live_displays(tmp_path):
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('gtk4-broadwayd is missing')
    procs = []
    try:
        seen = set()
        for _ in range(3):
            proc, display = bd.start(run_dir=str(tmp_path))
            procs.append(proc)
            assert display not in seen
            seen.add(display)
            path = bd.socket_path(int(display[1:]), str(tmp_path))
            assert os.path.exists(path)
            assert proc.poll() is None
    finally:
        for proc in procs:
            bd.stop(proc)
