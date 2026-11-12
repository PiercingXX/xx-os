"""Stock Phosh after a startup crash loop (launcher/data/xx-wm-session.in).

The real session script runs under `sh` with a fake phoc and a fake
phosh-session on a minimal PATH. The fake phoc plays the wrapper's part:
on a crash loop it stamps $XX_WM_PHOSH_FALLBACK with the boot id and
exits non-zero, as phoc does when its -E client exits.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

DATA = Path(__file__).parent.parent / 'launcher' / 'data'
SESSION_IN = DATA / 'xx-wm-session.in'

FAKE_PHOC = r'''#!%(python)s
import os, sys, time
state = os.environ['XX_TEST_STATE']
if sys.argv[1:] == ['--help']:
    print('  -S, --shell   Whether to expect a shell to attach')
    sys.exit(0)
mode = os.environ.get('XX_TEST_PHOC', 'ok')
with open(os.path.join(state, 'phoc'), 'w') as fh:
    fh.write('pid=%%d\n' %% os.getpid())
    fh.write('stdin=%%s\n' %% os.readlink('/proc/self/fd/0'))
    fh.write('fallback=%%s\n' %% os.environ.get('XX_WM_PHOSH_FALLBACK', ''))
    fh.write('args=%%s\n' %% ' '.join(sys.argv[1:]))
if mode == 'crashloop':
    marker = os.environ['XX_WM_PHOSH_FALLBACK']
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(os.environ['XX_WM_BOOT_ID_FILE']) as fh:
        boot = fh.read().strip()
    with open(marker, 'w') as fh:
        fh.write(boot + '\nshell exited with status 3 6 times\n')
    sys.exit(3)
if mode == 'hang':
    while True:
        time.sleep(0.1)
sys.exit(int(mode) if mode.isdigit() else 0)
'''

FAKE_PHOSH = r'''#!%(python)s
import os, sys
state = os.environ['XX_TEST_STATE']
keys = ('GDMSESSION', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_DESKTOP',
        'XX_WM_SESSION', 'XX_WM_PHOSH_FALLBACK', 'GTK_THEME')
with open(os.path.join(state, 'phosh'), 'w') as fh:
    for key in keys:
        fh.write('%%s=%%s\n' %% (key, os.environ.get(key, '<unset>')))
sys.exit(int(os.environ.get('XX_TEST_PHOSH_STATUS', '0')))
'''

RECORDER = '#!/bin/sh\necho "$*" >> "$XX_TEST_STATE/%s"\nexit 0\n'

TOOLS = ('cat', 'head', 'grep', 'mkdir', 'sleep')
SH = shutil.which('sh')


@pytest.fixture
def session(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for tool in TOOLS:
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    (bindir / 'python3').symlink_to(sys.executable)
    scripts = {
        'phoc': FAKE_PHOC % {'python': sys.executable},
        'phosh-session': FAKE_PHOSH % {'python': sys.executable},
        'gsettings': '#!/bin/sh\nexit 1\n',
        'systemctl': RECORDER % 'systemctl',
        'logger': RECORDER % 'logger',
    }
    for name, body in scripts.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    text = (SESSION_IN.read_text(encoding='utf-8')
            .replace('@datadir@', str(tmp_path / 'share'))
            .replace('@bindir@', str(tmp_path / 'usr-bin'))
            .replace('@libexecdir@', str(tmp_path / 'libexec')))
    script = tmp_path / 'xx-wm-session'
    script.write_text(text)
    home = tmp_path / 'home'
    home.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    systemd = tmp_path / 'systemd'
    systemd.mkdir()
    boot = tmp_path / 'boot_id'
    boot.write_text('this-boot\n')
    marker = home / '.local' / 'state' / 'xx-wm' / 'phosh-fallback'
    env = {
        'PATH': str(bindir),
        'HOME': str(home),
        'XX_TEST_STATE': str(state),
        'XX_WM_BOOT_ID_FILE': str(boot),
        'XX_WM_SYSTEMD_RUN_DIR': str(systemd),
        'GDMSESSION': 'phosh',
        'XDG_SESSION_DESKTOP': 'xx-wm',
    }

    def read(name):
        path = state / name
        if not path.exists():
            return None
        return dict(line.split('=', 1)
                    for line in path.read_text().splitlines() if '=' in line)

    def run(phoc='ok', remove=(), stdin=None, **extra):
        full = dict(env, XX_TEST_PHOC=phoc, **extra)
        for name in remove:
            full.pop(name, None)
        return subprocess.run([SH, str(script)], env=full, stdin=stdin,
                              capture_output=True, timeout=30, check=False)

    def spawn(phoc='ok', **extra):
        return subprocess.Popen([SH, str(script)],
                                env=dict(env, XX_TEST_PHOC=phoc, **extra),
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

    return {'run': run, 'spawn': spawn, 'read': read, 'state': state,
            'marker': marker, 'bindir': bindir}


def _calls(s, name):
    path = s['state'] / name
    return path.read_text() if path.exists() else ''


class TestFallsBackToPhosh:
    def test_crash_loop_hands_the_login_to_phosh(self, session):
        proc = session['run'](phoc='crashloop')
        assert proc.returncode == 0, proc.stderr
        assert session['read']('phoc') is not None
        phosh = session['read']('phosh')
        assert phosh is not None, 'phosh-session never ran'
        # Phosh starts unlocked only for GDMSESSION=phosh: it must not
        # survive into the fallback, here or in the user manager.
        assert phosh['GDMSESSION'] == '<unset>'
        assert phosh['XDG_CURRENT_DESKTOP'] == 'Phosh:GNOME'
        assert phosh['XDG_SESSION_DESKTOP'] == '<unset>'
        assert phosh['XX_WM_SESSION'] == '<unset>'
        assert phosh['XX_WM_PHOSH_FALLBACK'] == '<unset>'
        assert phosh['GTK_THEME'] == '<unset>'
        systemctl = _calls(session, 'systemctl')
        assert '--user unset-environment GDMSESSION' in systemctl
        assert 'XDG_CURRENT_DESKTOP=Phosh:GNOME' in systemctl
        assert 'crash-looped at startup' in _calls(session, 'logger')
        assert b'starting Phosh' in proc.stderr

    def test_phosh_status_is_the_session_status(self, session):
        proc = session['run'](phoc='crashloop', XX_TEST_PHOSH_STATUS='5')
        assert proc.returncode == 5

    def test_rest_of_the_boot_goes_straight_to_phosh(self, session):
        marker = session['marker']
        marker.parent.mkdir(parents=True)
        marker.write_text('this-boot\nshell exited\n')
        proc = session['run']()
        assert proc.returncode == 0
        assert session['read']('phoc') is None, 'XX-WM was tried again'
        assert session['read']('phosh')['GDMSESSION'] == '<unset>'
        assert 'earlier this boot' in _calls(session, 'logger')

    def test_next_boot_tries_xx_wm_again(self, session):
        marker = session['marker']
        marker.parent.mkdir(parents=True)
        marker.write_text('an-earlier-boot\nshell exited\n')
        proc = session['run']()
        assert proc.returncode == 0
        assert session['read']('phoc') is not None
        assert session['read']('phosh') is None


class TestKeepsXxWm:
    def test_phoc_learns_where_to_ask_for_the_fallback(self, session):
        session['run']()
        phoc = session['read']('phoc')
        assert phoc['fallback'] == str(session['marker'])
        assert '-E' in phoc['args'].split()

    def test_plain_failure_is_passed_through(self, session):
        proc = session['run'](phoc='3')
        assert proc.returncode == 3
        assert session['read']('phosh') is None

    def test_without_phosh_the_session_ends_as_before(self, session):
        (session['bindir'] / 'phosh-session').unlink()
        marker = session['marker']
        marker.parent.mkdir(parents=True)
        marker.write_text('this-boot\nshell exited\n')
        proc = session['run'](phoc='3')
        assert proc.returncode == 3
        phoc = session['read']('phoc')
        assert phoc is not None
        assert phoc['fallback'] == '', \
            'the wrapper was offered a fallback that does not exist'

    def test_phoc_keeps_the_session_stdin(self, session, tmp_path):
        tty = tmp_path / 'fake-tty'
        tty.write_text('')
        with open(tty) as stdin:
            session['run'](stdin=stdin)
        assert session['read']('phoc')['stdin'] == str(tty)


class TestStop:
    def test_term_stops_phoc(self, session):
        proc = session['spawn'](phoc='hang')
        phoc_file = session['state'] / 'phoc'
        end = time.monotonic() + 10
        while not phoc_file.exists() and time.monotonic() < end:
            time.sleep(0.05)
        pid = int(session['read']('phoc')['pid'])
        time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        try:
            assert proc.wait(timeout=10) != 0
            end = time.monotonic() + 5
            while _alive(pid) and time.monotonic() < end:
                time.sleep(0.05)
            assert not _alive(pid), 'phoc outlived a stopped session'
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.kill()
            proc.wait(timeout=5)
        assert session['read']('phosh') is None


def _alive(pid):
    try:
        stat = Path('/proc/%d/stat' % pid).read_text()
    except OSError:
        return False
    return stat.rsplit(') ', 1)[1][0] not in 'ZX'
