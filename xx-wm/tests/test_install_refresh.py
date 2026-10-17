"""Post-install refresh: running suite apps pick up new code without reboot.

Exercises refresh_running_apps (plus its _rra helpers) from
scripts/install.sh by extracting the functions into a temp file and
sourcing that file in `sh -c` — the whole installer never runs, so no
TUI, network, or privilege escalation can trigger. Every run uses a
fake /proc (XX_REFRESH_PROC), fake gdbus/busctl/systemctl/kill on PATH,
and a fake runtime dir, so real processes are never touched.
"""
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'

FUNCS = [
    '_rra_proc_root',
    '_rra_argv_has',
    '_rra_argv_has_path',
    '_rra_session_bus',
    '_rra_window_pids',
    '_rra_player_pids',
    '_rra_shell_pids',
    '_rra_mpris_status',
    '_rra_quit_window',
    '_rra_quit_player',
    'refresh_running_apps',
]

GDBUS_FAKE = """#!/bin/sh
echo "gdbus $*" >> "$REFRESH_LOG/gdbus.log"
case "$*" in
    *xx_radio*PlaybackStatus*) echo "(<'Playing'>,)"; exit 0 ;;
    *xx_audiobook*PlaybackStatus*) echo "(<'Paused'>,)"; exit 0 ;;
    *XXAudiobook*org.gtk.Actions.Activate*) exit 1 ;;
    *) exit 0 ;;
esac
"""

BUSCTL_FAKE = """#!/bin/sh
echo "busctl $*" >> "$REFRESH_LOG/busctl.log"
case "$*" in
    *xx_radio*PlaybackStatus*) echo 's "Playing"'; exit 0 ;;
    *xx_audiobook*PlaybackStatus*) echo 's "Paused"'; exit 0 ;;
    *) exit 0 ;;
esac
"""

SYSTEMCTL_FAKE = """#!/bin/sh
echo "systemctl $*" >> "$REFRESH_LOG/systemctl.log"
exit 0
"""

KILL_FAKE = """#!/bin/sh
echo "kill $*" >> "$REFRESH_LOG/kill.log"
exit 0
"""


def _extract(names, dest):
    script = INSTALL_SH.read_text()
    chunks = []
    for name in names:
        m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
        assert m, f'shell function {name} not found in install.sh'
        chunks.append('%s() {%s}\n' % (name, m.group(1)))
    dest.write_text(''.join(chunks))


def _write_fake(path, body):
    path.write_text('#!/bin/sh\n' + body, encoding='utf-8')
    path.chmod(0o755)


def _log(logdir, name):
    p = logdir / name
    return p.read_text(encoding='utf-8') if p.is_file() else ''


def _make_bus(run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(run_dir / 'bus'))
    finally:
        sock.close()


def _make_proc(root, entries):
    """entries: {pid: [argv...]} written as NUL-separated cmdline files."""
    root.mkdir(parents=True, exist_ok=True)
    for pid, argv in entries.items():
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        d.joinpath('cmdline').write_bytes(
            b'\0'.join(a.encode() for a in argv) + b'\0')


def _harness(tmp_path, *, with_gdbus=True, restricted_path=False):
    """Build (fakebin, logdir); caller adds /proc entries and bus socket."""
    fakebin = tmp_path / 'fakebin'
    fakebin.mkdir(exist_ok=True)
    logdir = tmp_path / 'logs'
    logdir.mkdir(exist_ok=True)
    if with_gdbus:
        _write_fake(fakebin / 'gdbus', GDBUS_FAKE)
    _write_fake(fakebin / 'busctl', BUSCTL_FAKE)
    _write_fake(fakebin / 'systemctl', SYSTEMCTL_FAKE)
    _write_fake(fakebin / 'kill', KILL_FAKE)
    if restricted_path:
        for tool in ('tr', 'grep', 'sed', 'id', 'env'):
            src = shutil.which(tool)
            assert src, f'need {tool} for restricted PATH'
            (fakebin / tool).symlink_to(src)
        path = str(fakebin)
    else:
        path = '%s:/usr/bin:/bin' % fakebin
    return fakebin, logdir, path


def _run_funcs(tmp_path, env_extra, driver='set -u; refresh_running_apps'):
    funcs = tmp_path / 'funcs.sh'
    _extract(FUNCS, funcs)
    env = dict(os.environ)
    env.update(env_extra)
    shell = shutil.which('sh') or 'sh'
    return subprocess.run(
        [shell, '-c', '. "%s"; %s' % (funcs, driver)],
        capture_output=True, text=True, env=env, cwd=str(tmp_path))


def _session_env(tmp_path, path, logdir, proc, run_dir):
    return {
        'PATH': path,
        'REFRESH_LOG': str(logdir),
        'XDG_RUNTIME_DIR': str(run_dir),
        'XX_REFRESH_PROC': str(proc),
        'XX_REFRESH_USER_BASE': str(tmp_path / 'nouser'),
    }


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _refresh_bodies():
    script = INSTALL_SH.read_text()
    bodies = []
    for name in FUNCS:
        m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
        assert m, f'{name} not found'
        bodies.append(m.group(1))
    return '\n'.join(bodies)


def test_no_bashisms():
    body = _refresh_bodies()
    for token in ('[[', ']]', 'function ', '==', 'source ', '&>',
                  '$RANDOM', '<(', 'local ', 'pushd', 'popd', 'declare '):
        assert token not in body, f'bashism {token!r} in refresh code'
    assert re.search(r'(?m)^\s*select\s', body) is None


def test_no_pkill_or_sigkill():
    body = _refresh_bodies()
    code = '\n'.join(
        line.split('#', 1)[0] for line in body.splitlines())
    assert 'pkill' not in code
    assert 'kill -9' not in code and 'kill -KILL' not in code


def test_wired_into_build_install():
    script = INSTALL_SH.read_text()
    m = re.search(r'^build_install\(\) \{(.*?)^\}', script, re.M | re.S)
    assert m, 'build_install not found'
    assert 'refresh_running_apps' in m.group(1)


def test_no_session_means_no_action(tmp_path):
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {
        111: ['python3', '-m', 'xx_radio', '--gapplication-service'],
    })
    run_dir = tmp_path / 'run'  # no bus socket: no session
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert 'no user session bus' in result.stdout
    assert _log(logdir, 'gdbus.log') == ''
    assert _log(logdir, 'kill.log') == ''
    assert _log(logdir, 'systemctl.log') == ''


def test_playing_player_skipped_idle_player_restarted(tmp_path):
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {
        101: ['/usr/bin/python3', '-m', 'xx_radio', '--player-service'],
        102: ['/usr/bin/python3', '-m',
              'xx_audiobook.service.player_service'],
    })
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    kill_log = _log(logdir, 'kill.log')
    gdbus_log = _log(logdir, 'gdbus.log')
    assert '101' not in kill_log.split(), 'playing player was touched'
    assert '102' not in kill_log.split(), 'idle quit should use MPRIS'
    assert 'org.mpris.MediaPlayer2.Quit' in gdbus_log
    assert 'xx_audiobook' in gdbus_log
    assert 'xx_radio player is Playing' in result.stdout
    assert 'next login' in result.stdout


def test_pid_match_is_exact(tmp_path):
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {
        111: ['python3', '-m', 'xx_radio', '--gapplication-service'],
        222: ['vim', 'xx_radio_notes.txt'],
        333: ['sh', 'scripts/install.sh', '--unattended'],
        444: ['grep', 'xx_radio'],
    })
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    kill_log = _log(logdir, 'kill.log')
    gdbus_log = _log(logdir, 'gdbus.log')
    for pid in ('222', '333', '444'):
        assert pid not in kill_log.split(), f'decoy pid {pid} touched'
    assert '--dest io.piercingxx.XXRadio' in gdbus_log
    assert 'xx-radio' in result.stdout


def test_launcher_path_form_matches(tmp_path):
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {
        777: ['/usr/bin/xx-radio', '--gapplication-service'],
    })
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert '--dest io.piercingxx.XXRadio' in _log(logdir, 'gdbus.log')


def test_dbus_quit_fallback_is_sigterm(tmp_path):
    # The fake gdbus fails Activate for the audiobook bus (no quit
    # action): that PID must get SIGTERM instead.
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {
        555: ['/usr/bin/python3', '-m', 'xx_audiobook',
              '--gapplication-service'],
    })
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert '-TERM 555' in _log(logdir, 'kill.log')
    assert 'xx-audiobook' in result.stdout


def test_systemd_units_use_try_restart(tmp_path):
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {})
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    sys_log = _log(logdir, 'systemctl.log')
    for unit in ('xx-clock-daemon.service', 'xx-share.service',
                 'xx-auto-watch.service', 'xx-vitals-collect.service'):
        assert f'try-restart {unit}' in sys_log, unit
    assert '--user restart ' not in sys_log
    assert ' start ' not in sys_log


def test_shell_gets_sigusr1(tmp_path):
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {
        666: ['/usr/bin/python3', '/usr/share/xx-wm/main.py'],
    })
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    kill_log = _log(logdir, 'kill.log')
    assert '-USR1 666' in kill_log
    assert '-TERM 666' not in kill_log
    assert 'respawn' in result.stdout


def test_shell_absent_and_ambiguous(tmp_path):
    _, logdir, path = _harness(tmp_path)
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    proc = tmp_path / 'proc'
    _make_proc(proc, {})
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert 'shell is not running' in result.stdout
    assert 'USR1' not in _log(logdir, 'kill.log')

    _make_proc(proc, {
        701: ['/usr/bin/python3', '/usr/share/xx-wm/main.py'],
        702: ['/usr/bin/python3', '/usr/share/xx-wm/main.py'],
    })
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert 'USR1' not in _log(logdir, 'kill.log')
    assert 'several shell processes' in result.stdout


def test_busctl_fallback_reads_status(tmp_path):
    # No gdbus on PATH: the MPRIS status must come from busctl, and the
    # idle player falls back to SIGTERM.
    _, logdir, path = _harness(tmp_path, with_gdbus=False,
                               restricted_path=True)
    proc = tmp_path / 'proc'
    _make_proc(proc, {
        101: ['/usr/bin/python3', '-m', 'xx_radio', '--player-service'],
        102: ['/usr/bin/python3', '-m',
              'xx_audiobook.service.player_service'],
    })
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert 'PlaybackStatus' in _log(logdir, 'busctl.log')
    assert 'xx_radio player is Playing' in result.stdout
    assert '-TERM 102' in _log(logdir, 'kill.log')
    assert '-TERM 101' not in _log(logdir, 'kill.log')


def test_summary_line(tmp_path):
    _, logdir, path = _harness(tmp_path)
    proc = tmp_path / 'proc'
    _make_proc(proc, {})
    run_dir = tmp_path / 'run'
    _make_bus(run_dir)
    env = _session_env(tmp_path, path, logdir, proc, run_dir)
    result = _run_funcs(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert re.search(r'refresh: done — \d+ refreshed, \d+ left until later\.',
                     result.stdout)


if __name__ == '__main__':
    import sys

    sys.exit(pytest.main([__file__, '-v']))
