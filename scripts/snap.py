#!/usr/bin/env python3
"""Render a GTK4 app's real window to a PNG, headless.

Runs the app's own entry point under a private Broadway display, waits for
its first window, optionally runs a navigation script against it, then
renders the window at phone size (2x) and exits.

    scripts/snap.py --out shot.png [--size 360x720] [--delay 1.5]
                    [--path DIR ...] [--script nav.py] -- -m xx_contacts

The --script file runs with ``win`` (the window) and ``app`` in scope,
after --delay; the capture follows another --delay. With SNAP_MULTI=1
in the environment the script captures its own shots instead
(``snap_render(path)`` per shot, then ``snap_done()`` for --out and
exit). Point HOME/XDG_* at a
seeded temp dir yourself so the app shows real-looking data.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

CHILD = r'''
import os, runpy, sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import GLib, Graphene, Gtk

out, w, h, delay, script = (os.environ['SNAP_OUT'], int(os.environ['SNAP_W']),
                            int(os.environ['SNAP_H']), float(os.environ['SNAP_DELAY']),
                            os.environ.get('SNAP_SCRIPT', ''))
multi = os.environ.get('SNAP_MULTI') == '1'
state = {'win': None}
_present = Gtk.Window.present


def _main_window():
    wins = [x for x in Gtk.Window.list_toplevels() if x.get_visible()]
    if state['win'] in wins:
        return state['win']
    return wins[0] if wins else None


def _render(path):
    """Paint the main window to ``path`` (2x). Scripts get it as
    ``snap_render`` to capture several shots in one run (SNAP_MULTI)."""
    win = _main_window()
    if win is None:
        print('snap: no visible window', file=sys.stderr)
        os._exit(3)
    # Paint at the real allocation: forcing w x h would squash a window
    # whose content refuses to fit, hiding exactly the bug worth seeing.
    aw, ah = win.get_width(), win.get_height()
    if (aw, ah) != (w, h):
        print(f'snap: WARNING window is {aw}x{ah}, not {w}x{h}: '
              'content does not fit a phone screen', file=sys.stderr, flush=True)
    snap = Gtk.Snapshot()
    snap.scale(2, 2)
    Gtk.WidgetPaintable.new(win).snapshot(snap, aw, ah)
    node = snap.to_node()
    rect = Graphene.Rect().init(0, 0, aw * 2, ah * 2)
    win.get_native().get_renderer().render_texture(node, rect).save_to_png(path)
    print('snap: wrote', path, flush=True)


def _capture():
    try:
        _render(out)
    except TypeError:
        # A tick (a clock label, a timer) invalidated the window's render
        # node just before the capture: headless Broadway then paints an
        # empty node. Try again on a later main-loop turn.
        state['empty'] = state.get('empty', 0) + 1
        if state['empty'] > 20:
            raise
        GLib.timeout_add(150, _capture)
        return False
    os._exit(0)


def _navigate():
    if script:
        win = _main_window()
        code = compile(open(script).read(), script, 'exec')
        exec(code, {'win': win, 'app': win.get_application() if win else None,
                    'Gtk': Gtk, 'GLib': GLib, 'snap_render': _render,
                    'snap_done': _capture})
    if not multi:
        GLib.timeout_add(int(delay * 1000), _capture)
    return False


def present(self, *a):
    if state['win'] is None:
        state['win'] = self
        # The app's own floor (Adw windows default to 360 px wide) is
        # replaced below, so judge it first: a real compositor cannot
        # shrink the window under it, whatever the content allows.
        rw, rh = self.get_size_request()
        if rw > w or rh > h:
            print(f'snap: WARNING window requests at least {rw}x{rh}, '
                  f'more than {w}x{h}: it cannot shrink to a phone screen',
                  file=sys.stderr, flush=True)
        self.set_default_size(w, h)
        self.set_size_request(w, h)
        GLib.timeout_add(int(delay * 1000), _navigate)
    return _present(self, *a)


Gtk.Window.present = present
GLib.timeout_add(int(delay * 1000) * 6 + (900000 if multi else 20000),
                 lambda: os._exit(4))
args = sys.argv[1:]
sys.argv = [args[1] if args[0] == '-m' else args[0]] + args[2 if args[0] == '-m' else 1:]
if args[0] == '-m':
    runpy.run_module(args[1], run_name='__main__', alter_sys=True)
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(args[0])))
    runpy.run_path(args[0], run_name='__main__')
'''


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--out', required=True)
    p.add_argument('--size', default='360x720')
    p.add_argument('--delay', type=float, default=1.5)
    p.add_argument('--path', action='append', default=[], help='prepend to PYTHONPATH')
    p.add_argument('--script', default='')
    p.add_argument('cmd', nargs=argparse.REMAINDER)
    a = p.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ['--'] else a.cmd
    if not cmd:
        p.error('give the app after --, e.g. -- -m xx_contacts')
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', 'common'))
    from xx_testing import broadway_display
    if shutil.which('gtk4-broadwayd') is None:
        print('snap: gtk4-broadwayd not installed', file=sys.stderr)
        return 2
    w, h = (int(v) for v in a.size.split('x'))
    # Hermetic Broadway runtime: every other lane's daemons share the
    # machine's runtime dir, where stale sockets pile up and steal display
    # numbers (Gtk then cannot initialize). Run this daemon under a
    # private XDG_RUNTIME_DIR instead — nothing shared to clean, and the
    # child inherits it so client and daemon agree. The display and HTTP
    # port are probed free (parallel snaps once collided on pid % 50),
    # and a daemon that exits early is retried on another display.
    import tempfile as _tempfile
    run_dir = _tempfile.mkdtemp(prefix='snap-broadway-')
    os.chmod(run_dir, 0o700)
    try:
        daemon, display = broadway_display.start(run_dir=run_dir)
    except RuntimeError as error:
        shutil.rmtree(run_dir, ignore_errors=True)
        print(f'snap: {error}', file=sys.stderr)
        return 2
    env = dict(os.environ, GDK_BACKEND='broadway', BROADWAY_DISPLAY=display,
               GSK_RENDERER='cairo', XDG_RUNTIME_DIR=run_dir,
               SNAP_OUT=os.path.abspath(a.out), SNAP_W=str(w),
               SNAP_H=str(h), SNAP_DELAY=str(a.delay),
               SNAP_SCRIPT=os.path.abspath(a.script) if a.script else '')
    if a.path:
        env['PYTHONPATH'] = os.pathsep.join(
            [os.path.abspath(x) for x in a.path] + [env.get('PYTHONPATH', '')])
    try:
        return subprocess.run([sys.executable, '-c', CHILD, *cmd], env=env).returncode
    finally:
        broadway_display.stop(daemon)
        shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
