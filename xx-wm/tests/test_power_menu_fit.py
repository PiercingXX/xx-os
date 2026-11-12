"""The power menu fits the smallest phone screen (320x640), measured on a
real GTK display. The 420x768 power snapshot was the rig capturing the
lock screen under the menu, not the menu growing."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
TOOLS = Path(__file__).parent.parent / 'tools'
SNAP = Path(__file__).parent.parent.parent / 'scripts' / 'snap.py'

_SCRIPT = r'''
import glob
import os
import sys
import time

src_dir, run_dir = sys.argv[1], sys.argv[2]
os.environ['GDK_BACKEND'] = 'broadway'
home = os.path.join(run_dir, 'home')
os.makedirs(home, exist_ok=True)
os.environ['HOME'] = home
os.environ['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
os.environ['XDG_RUNTIME_DIR'] = run_dir
_common = src_dir
while _common != os.path.dirname(_common) and not os.path.isdir(
        os.path.join(_common, 'common', 'xx_testing')):
    _common = os.path.dirname(_common)
sys.path.insert(0, os.path.join(_common, 'common'))
from xx_testing import broadway_display
daemon, display = broadway_display.start(run_dir=run_dir)
os.environ['BROADWAY_DISPLAY'] = display
try:
    for _i in range(200):
        if glob.glob(os.path.join(run_dir, 'broadway*.socket')):
            break
        time.sleep(0.05)
    sys.path.insert(0, src_dir)
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk
    Gtk.init()
    from power_menu import PowerMenu
    menu = PowerMenu(call_live=lambda: False)
    child = menu.get_child()
    min_w = child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    min_h = child.measure(Gtk.Orientation.VERTICAL, 320)[0]
    print('MIN', min_w, min_h)
    if min_w <= 320 and min_h <= 640:
        print('POWER FIT OK')
finally:
    daemon.terminate()
'''


def test_power_menu_fits_320x640(tmp_path):
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('needs gtk4-broadwayd')
    pytest.importorskip('gi')
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    script = tmp_path / 'power_fit.py'
    script.write_text(_SCRIPT)
    cmd = [sys.executable, str(script), str(SRC), str(run_dir)]
    if shutil.which('dbus-run-session'):
        cmd = ['dbus-run-session', '--'] + cmd
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          check=False)
    assert 'POWER FIT OK' in proc.stdout, (
        proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])


def test_power_snapshot_captures_the_menu():
    nav = (TOOLS / 'snap_overlays.sh').read_text(encoding='utf-8')
    block = nav.split('nav power <<EOF', 1)[1].split('\nEOF', 1)[0]
    assert 'snap_target(pm)' in block
    assert "'snap_target': _target" in SNAP.read_text(encoding='utf-8')
