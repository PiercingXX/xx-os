"""Home edit mode fits the phone: the slot rows scroll, the action footer
(Add app, New folder, Rearrange, Settings, Done) stays on screen at
360x720 and 320x640. It used to sit inside the scroller, so Settings and
Done were pushed below the bottom edge. A slot name that fits its row is
shown whole; only one that does not fit ellipsizes."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'

_SCRIPT = r'''
import glob
import json
import os
import sys
import time

src_dir, run_dir, size = sys.argv[1], sys.argv[2], sys.argv[3]
w, h = (int(v) for v in size.split('x'))
os.environ['GDK_BACKEND'] = 'broadway'
home = os.path.join(run_dir, 'home')
os.environ['HOME'] = home
os.environ['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
os.environ['XDG_CACHE_HOME'] = os.path.join(home, '.cache')
os.environ['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
os.environ['XDG_DATA_DIRS'] = os.path.join(home, '.local', 'share')
os.environ['XDG_RUNTIME_DIR'] = run_dir
apps = os.path.join(home, '.local', 'share', 'applications')
os.makedirs(apps, exist_ok=True)
os.makedirs(os.path.join(home, '.config', 'xx-wm'), exist_ok=True)
names = ['Notes', 'Calendar', 'Weather', 'Maps', 'Clocks', 'Files',
         'Camera', 'Contacts']
slots = []
for name in names:
    app_id = name.lower() + '.desktop'
    with open(os.path.join(apps, app_id), 'w') as fh:
        fh.write(f'[Desktop Entry]\nName={name}\nExec=true\nType=Application\n')
    slots.append({'type': 'app', 'label': name, 'app_id': app_id,
                  'cmd': None, 'folder': None})
slots = slots[:int(sys.argv[4])]
text_scale = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
if len(sys.argv) > 6:
    slots.append({'type': 'app', 'label': sys.argv[6],
                  'app_id': 'notes.desktop', 'cmd': None, 'folder': None})
with open(os.path.join(home, '.config', 'xx-wm', 'config.json'), 'w') as fh:
    json.dump({'theme': 'amoled', 'text_size_scale': text_scale,
               'home_slots': slots}, fh)

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
    from gi.repository import Adw, GLib, Gtk

    import window as window_mod

    failures = []
    sys.excepthook = lambda *exc: failures.append(repr(exc[1]))
    app = Adw.Application(application_id='io.test.HomeEditFit')
    win = window_mod.ShellWindow(app)
    win.present()
    win.unfullscreen()
    win.set_default_size(w, h)
    win.set_size_request(w, h)
    win.go_home()
    win._home_launcher.set_edit_mode(True)

    def _walk(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from _walk(child)
            child = child.get_next_sibling()

    def check():
        try:
            if (win.get_width(), win.get_height()) != (w, h):
                failures.append(f'window {win.get_width()}x{win.get_height()}')
            buttons = [b for b in _walk(win)
                       if isinstance(b, Gtk.Button)
                       and b.has_css_class('home-edit-action')]
            labels = [b.get_label() for b in buttons]
            if 'Settings' not in labels or 'Done' not in labels:
                failures.append(f'footer {labels}')
            for b in buttons:
                ok, rect = b.compute_bounds(win)
                if not ok or not b.get_mapped():
                    failures.append(f'{b.get_label()} not on screen')
                    continue
                left, top = rect.get_x(), rect.get_y()
                right, bottom = left + rect.get_width(), top + rect.get_height()
                print('BOUNDS', b.get_label(), left, top, right, bottom)
                if left < 0 or top < 0 or right > w + 0.5 or bottom > h + 0.5:
                    failures.append(f'{b.get_label()} off screen: '
                                    f'{left},{top},{right},{bottom}')
                if b.get_ancestor(Gtk.ScrolledWindow) is not None:
                    failures.append(f'{b.get_label()} scrolls away')
            from gi.repository import Pango
            for b in _walk(win._home_launcher):
                if not (isinstance(b, Gtk.Button)
                        and b.has_css_class('home-edit-label')):
                    continue
                lbl = b.get_child()
                layout = lbl.get_layout().copy()
                layout.set_width(-1)
                text_w = layout.get_extents()[1].width / Pango.SCALE
                ok, rect = b.compute_bounds(win)
                print('LABEL', repr(lbl.get_label()),
                      lbl.get_layout().is_ellipsized(), round(text_w, 2),
                      lbl.get_width(), rect.get_x(),
                      rect.get_x() + rect.get_width())
        except Exception as exc:
            failures.append(repr(exc))
        loop.quit()
        return False

    loop = GLib.MainLoop()
    GLib.timeout_add(1200, check)
    GLib.timeout_add(15000, lambda: (failures.append('timeout'),
                                     loop.quit(), False)[2])
    loop.run()
    print('FAILURES', failures)
    if not failures:
        print('EDIT FIT OK')
finally:
    daemon.terminate()
'''


@pytest.mark.parametrize('size,count', [
    ('360x720', 5), ('360x720', 7), ('320x640', 5), ('320x640', 7),
])
def test_edit_footer_stays_on_screen(tmp_path, size, count):
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('needs gtk4-broadwayd')
    pytest.importorskip('gi')
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    script = tmp_path / 'edit_fit.py'
    script.write_text(_SCRIPT)
    cmd = [sys.executable, str(script), str(SRC), str(run_dir), size,
           str(count)]
    # Private session bus: the real window talks to services.
    if shutil.which('dbus-run-session'):
        cmd = ['dbus-run-session', '--'] + cmd
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                          check=False)
    assert 'EDIT FIT OK' in proc.stdout, (
        proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:])


@pytest.mark.parametrize('size', ['360x720', '320x640'])
def test_edit_names_ellipsize_only_when_they_do_not_fit(tmp_path, size):
    """At text scale 1.5 "Calendar" got exactly its natural width and
    still ellipsized ("Calenda…"): GTK's natural width falls short of the
    laid-out text under the shell's letter-spacing."""
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('needs gtk4-broadwayd')
    pytest.importorskip('gi')
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    script = tmp_path / 'edit_fit.py'
    script.write_text(_SCRIPT)
    long_name = 'A Much Longer Application Name'
    cmd = [sys.executable, str(script), str(SRC), str(run_dir), size, '7',
           '1.5', long_name]
    if shutil.which('dbus-run-session'):
        cmd = ['dbus-run-session', '--'] + cmd
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                          check=False)
    assert 'EDIT FIT OK' in proc.stdout, (
        proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:])
    width = int(size.split('x')[0])
    rows = {}
    for line in proc.stdout.splitlines():
        if line.startswith('LABEL '):
            name, rest = line[6:].rsplit("' ", 1)
            ellipsized, text_w, alloc, left, right = rest.split()
            rows[name.strip("'")] = (ellipsized == 'True', float(text_w),
                                     int(alloc), float(left), float(right))
    assert len(rows) == 8, proc.stdout
    for name, (ellipsized, _text_w, _alloc, left, right) in rows.items():
        assert left >= 0 and right <= width + 0.5, (name, left, right)
        if name == long_name:
            assert ellipsized, rows[name]
        else:
            assert not ellipsized, (name, rows[name])


def test_footer_actions_pair_up_and_done_spans():
    pytest.importorskip('gi')
    from gi.repository import Gtk
    from home_launcher import HomeLauncher
    slots = [{'type': 'app', 'label': n} for n in ('A', 'B', 'C')]
    launcher = HomeLauncher(get_slots_fn=lambda: slots)
    assert launcher.edit_footer.get_visible() is False
    launcher.set_edit_mode(True)
    footer = launcher.edit_footer
    assert footer.get_visible() is True
    cells = {}
    child = footer.get_first_child()
    while child is not None:
        assert isinstance(child, Gtk.Button)
        cells[child.get_label()] = footer.query_child(child)
        child = child.get_next_sibling()
    assert cells == {
        '+ Add app': (0, 0, 1, 1), '+ New folder': (1, 0, 1, 1),
        'Rearrange…': (0, 1, 1, 1), 'Settings': (1, 1, 1, 1),
        'Done': (0, 2, 2, 1),
    }
    launcher.set_edit_mode(False)
    assert footer.get_visible() is False
    assert footer.get_first_child() is None
