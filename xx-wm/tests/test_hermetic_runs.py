"""Snapshot and test runs never reach the host's services.

A snap run of the shell registered a polkit agent on the host's system
bus ("An authentication agent already exists"), and a GTK window on a
test's private session bus activated the host's xdg-desktop-portal
(which started xdg-desktop-portal-hyprland against the real compositor).
XX_WM_NO_POLKIT_AGENT=1 keeps the agent off; GDK_DEBUG=no-portals,
ADW_DISABLE_PORTAL=1 and GTK_USE_PORTAL=0 keep GTK and libadwaita from
asking a portal at all. conftest.py sets them for every test and its
children, scripts/snap.py for every snapshot.

The private bus here can only activate stand-in portal services that
append their name to a marker file, so a portal request is seen without
starting anything real."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'launcher' / 'src'
REPO = ROOT.parent

PORTAL_NAMES = ('org.freedesktop.portal.Desktop',
                'org.freedesktop.portal.Documents',
                'org.freedesktop.impl.portal.PermissionStore')

HERMETIC = {'XX_WM_NO_POLKIT_AGENT': '1', 'ADW_DISABLE_PORTAL': '1',
            'GTK_USE_PORTAL': '0'}


def _strip_switches(env: dict) -> dict:
    env = dict(env)
    for key in HERMETIC:
        env.pop(key, None)
    flags = [f for f in env.get('GDK_DEBUG', '').split(',')
             if f and f != 'no-portals']
    if flags:
        env['GDK_DEBUG'] = ','.join(flags)
    else:
        env.pop('GDK_DEBUG', None)
    return env


class TestPolkitSwitch:
    def test_switch_keeps_the_agent_off_the_system_bus(self, monkeypatch):
        import polkit_agent
        calls = []
        monkeypatch.setattr(polkit_agent, '_HAVE_AGENT', True)
        monkeypatch.setattr(polkit_agent, 'ShellListener',
                            lambda: calls.append('listener') or object(),
                            raising=False)
        monkeypatch.setattr(polkit_agent, 'agent_subject',
                            lambda: calls.append('subject') or object())
        monkeypatch.setattr(polkit_agent, 'register_listener',
                            lambda *a: calls.append('register') or True)
        monkeypatch.setenv('XX_WM_NO_POLKIT_AGENT', '1')
        assert polkit_agent.start_agent() is None
        assert calls == []
        monkeypatch.delenv('XX_WM_NO_POLKIT_AGENT')
        assert polkit_agent.start_agent() is not None
        assert calls == ['listener', 'subject', 'register']
        polkit_agent._agent = None


def test_tests_and_their_children_run_with_the_switches():
    for key, value in HERMETIC.items():
        assert os.environ.get(key) == value, key
    assert 'no-portals' in os.environ.get('GDK_DEBUG', '').split(',')


# ------------------------------------------------ real GTK, marker bus

_BUS_CONF = '''<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:dir={run}</listen>
  <servicedir>{services}</servicedir>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
'''

_WINDOW = r'''
import glob, os, sys, time
sys.path.insert(0, sys.argv[1])
run_dir = sys.argv[2]
from xx_testing import broadway_display
daemon, display = broadway_display.start(run_dir=run_dir)
os.environ.update(GDK_BACKEND='broadway', BROADWAY_DISPLAY=display,
                  XDG_RUNTIME_DIR=run_dir)
try:
    for _i in range(200):
        if glob.glob(os.path.join(run_dir, 'broadway*.socket')):
            break
        time.sleep(0.05)
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, GLib, Gtk
    app = Adw.Application(application_id='io.test.Hermetic')

    def _activate(app):
        win = Adw.ApplicationWindow(application=app)
        win.set_content(Gtk.Label(label='hermetic'))
        win.present()
        GLib.timeout_add(1000, app.quit)

    app.connect('activate', _activate)
    app.run([])
    print('WINDOW OK')
finally:
    daemon.terminate()
'''


@pytest.fixture
def marker_bus(tmp_path):
    if shutil.which('dbus-daemon') is None:
        pytest.skip('needs dbus-daemon')
    services = tmp_path / 'services'
    services.mkdir()
    marker = tmp_path / 'activated'
    for name in PORTAL_NAMES:
        (services / f'{name}.service').write_text(
            '[D-BUS Service]\n'
            f'Name={name}\n'
            f"Exec=/bin/sh -c 'echo {name} >> {marker}'\n")
    run = tmp_path / 'bus'
    run.mkdir()
    conf = tmp_path / 'bus.conf'
    conf.write_text(_BUS_CONF.format(run=run, services=services))
    proc = subprocess.Popen(
        ['dbus-daemon', f'--config-file={conf}', '--nofork',
         '--print-address=1'], stdout=subprocess.PIPE, text=True)
    try:
        address = proc.stdout.readline().strip()
        yield address, marker
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _activated(marker: Path) -> list[str]:
    return marker.read_text().split() if marker.exists() else []


def test_a_gtk_window_in_a_test_never_asks_a_portal(marker_bus, tmp_path):
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('needs gtk4-broadwayd')
    pytest.importorskip('gi')
    address, marker = marker_bus
    run_dir = tmp_path / 'run'
    run_dir.mkdir(mode=0o700)
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address)
    proc = subprocess.run(
        [sys.executable, '-c', _WINDOW, str(REPO / 'common'), str(run_dir)],
        env=env, capture_output=True, text=True, timeout=120)
    assert 'WINDOW OK' in proc.stdout, proc.stderr[-2000:]
    assert _activated(marker) == []


_APP = r'''
import os, sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
with open(sys.argv[1], 'w') as fh:
    for key in ('XX_WM_NO_POLKIT_AGENT', 'ADW_DISABLE_PORTAL',
                'GTK_USE_PORTAL', 'GDK_DEBUG'):
        fh.write(f'{key}={os.environ.get(key, "")}\n')
app = Adw.Application(application_id='io.test.HermeticSnap')

def _activate(app):
    win = Adw.ApplicationWindow(application=app)
    win.set_content(Gtk.Label(label='snap'))
    win.present()

app.connect('activate', _activate)
app.run([])
'''


def test_snap_py_runs_its_app_with_the_switches(marker_bus, tmp_path):
    """A developer's shell has none of the switches: snap.py adds them."""
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('needs gtk4-broadwayd')
    pytest.importorskip('gi')
    address, marker = marker_bus
    app = tmp_path / 'app.py'
    app.write_text(_APP)
    seen = tmp_path / 'env.txt'
    env = _strip_switches(os.environ)
    env['DBUS_SESSION_BUS_ADDRESS'] = address
    proc = subprocess.run(
        [sys.executable, str(REPO / 'scripts' / 'snap.py'),
         '--out', str(tmp_path / 'shot.png'), '--delay', '0.5',
         '--', str(app), str(seen)],
        env=env, capture_output=True, text=True, timeout=120)
    assert (tmp_path / 'shot.png').exists(), proc.stderr[-2000:]
    got = dict(line.split('=', 1) for line in seen.read_text().splitlines())
    assert got['XX_WM_NO_POLKIT_AGENT'] == '1'
    assert got['ADW_DISABLE_PORTAL'] == '1'
    assert got['GTK_USE_PORTAL'] == '0'
    assert 'no-portals' in got['GDK_DEBUG'].split(',')
    assert _activated(marker) == []
