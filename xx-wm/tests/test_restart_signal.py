"""Deploy and xxdb restart the shell with SIGUSR1; the xx-wm wrapper
respawns a child that died of it (wait status 138). A config save still
waiting out its coalescing delay must reach the disk first."""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'launcher' / 'src'
COMMON = ROOT.parent / 'common'

_CHILD = r'''
import os, signal, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from gi.repository import GLib
import config, main
config.enable_deferred_saves(True)
main.install_restart_signal()
config.ShellConfig().set_theme('ocean')
GLib.timeout_add(20, lambda: os.kill(os.getpid(), signal.SIGUSR1))
GLib.timeout_add(5000, lambda: sys.exit(3))
GLib.MainLoop().run()
sys.exit(4)
'''


def test_sigusr1_flushes_a_queued_save_and_still_dies_of_the_signal(
        tmp_path):
    env = dict(os.environ, HOME=str(tmp_path),
               XDG_CONFIG_HOME=str(tmp_path / '.config'),
               XDG_CACHE_HOME=str(tmp_path / '.cache'))
    env.pop('DBUS_SESSION_BUS_ADDRESS', None)
    proc = subprocess.run(
        [sys.executable, '-c', _CHILD, str(SRC), str(COMMON)],
        env=env, capture_output=True, text=True, timeout=60, check=False)
    # Killed by SIGUSR1: the wrapper's `wait` reads 128+10 = 138.
    assert proc.returncode == -signal.SIGUSR1, proc.stderr
    saved = json.loads(
        (tmp_path / '.config' / 'xx-wm' / 'config.json').read_text())
    assert saved['theme'] == 'ocean'
