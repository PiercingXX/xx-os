"""Session wrapper and service lifecycle (review P2, session/install).

The real launcher/data/xx-wm.in runs under `sh` with a fake shell
(main.py) and a minimal PATH, so these tests exercise the shipped
wrapper: the stderr log stays capped while the shell and the apps it
started keep writing, SIGHUP tears the session down, and a shell that
crash-loops at startup ends the session with a failure status that
systemd's Restart=on-failure and OpenRC's respawn can see.
"""
import configparser
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
DATA = REPO / 'launcher' / 'data'
WRAPPER_IN = DATA / 'xx-wm.in'
UNIT = DATA / 'systemd' / 'xx-wm.service'
OPENRC = DATA / 'openrc' / 'xx-wm'
INSTALL_SH = REPO / 'scripts' / 'install.sh'

FAKE_MAIN = r'''
import os, sys, time
mode = os.environ['XX_TEST_MODE']
state = os.environ['XX_TEST_STATE']
with open(os.path.join(state, 'runs'), 'a') as fh:
    fh.write('%d\n' % os.getpid())
if mode == 'crash':
    sys.exit(3)
if mode == 'flood':
    sys.stderr.write('x' * 1_200_000 + '\n')
    sys.stderr.flush()
stop = os.path.join(state, 'stop')
while not os.path.exists(stop):
    if mode == 'flood':
        sys.stderr.write('tick\n')
        sys.stderr.flush()
    time.sleep(0.1)
sys.exit(0)
'''

TOOLS = ('id', 'wc', 'tr', 'cp', 'mv', 'mkdir', 'chmod', 'date', 'sleep',
         'cat', 'awk', 'basename', 'rm', 'ps', 'head')


@pytest.fixture
def wrapper(tmp_path):
    shell_dir = tmp_path / 'shell'
    shell_dir.mkdir()
    (shell_dir / 'main.py').write_text(FAKE_MAIN)
    (shell_dir / 'gesture_bindings.py').write_text('')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for tool in TOOLS:
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    for stub in ('pkill', 'ldconfig'):
        path = bindir / stub
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    text = WRAPPER_IN.read_text(encoding='utf-8')
    text = (text.replace('@python@', sys.executable)
            .replace('@shell_dir@', str(shell_dir))
            .replace('@bindir@', str(bindir)))
    # The host's real gtk4-layer-shell would be preloaded into the fake.
    text = text.replace('/usr/lib/libgtk4-layer-shell.so.0 /usr/lib/*/',
                        '/nonexistent/')
    script = tmp_path / 'xx-wm'
    script.write_text(text)
    home = tmp_path / 'home'
    home.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    runtime = tmp_path / 'run'
    runtime.mkdir(mode=0o700)
    env = {
        'PATH': str(bindir),
        'HOME': str(home),
        'XDG_RUNTIME_DIR': str(runtime),
        'XX_WM_SYSTEMD_RUN_DIR': str(tmp_path / 'no-systemd'),
        'XX_TEST_STATE': str(state),
    }

    procs = []

    def start(mode, **extra):
        full = dict(env, XX_TEST_MODE=mode, **extra)
        proc = subprocess.Popen(
            [shutil.which('sh'), str(script)], env=full,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        procs.append(proc)
        return proc

    paths = {'home': home, 'state': state,
             'log': home / '.local' / 'share' / 'xx-wm' / 'stderr.log'}
    yield start, paths
    (state / 'stop').touch()
    for proc in procs:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def _wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _group_gone(pgid):
    """No live process left in the group. Zombies don't count: in a CI
    container whose PID 1 never reaps (GitHub runs `tail -f /dev/null`),
    an exited orphan stays a zombie and killpg(pgid, 0) still succeeds."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            text = stat.read_text()
        except OSError:
            continue
        fields = text.rsplit(')', 1)[-1].split()
        if len(fields) > 2 and fields[2] == str(pgid) and fields[0] != 'Z':
            return False
    return True


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path('/proc/%d/stat' % pid)
    try:
        return stat.read_text().rsplit(') ', 1)[1][0] not in 'ZX'
    except (OSError, IndexError):
        return False


class TestStderrLogCap:
    def test_log_is_capped_while_the_shell_keeps_running(self, wrapper):
        start, paths = wrapper
        proc = start('flood', XX_WM_ERR_LOG_CHECK='1')
        rotated = paths['log'].with_name('stderr.log.1')
        assert _wait_for(rotated.exists, timeout=15), \
            'the log was never rotated while the shell ran'
        runs = (paths['state'] / 'runs').read_text().split()
        assert len(runs) == 1, 'rotation must not need a respawn'
        assert rotated.stat().st_size > 1_000_000
        time.sleep(0.5)
        live = paths['log'].read_bytes()
        assert len(live) < 100_000
        # Writers hold the file O_APPEND: after the truncate they carry
        # on at the new end, so no hole of NUL bytes opens up.
        assert live.startswith(b'tick'), live[:40]
        assert b'\0' not in live
        (paths['state'] / 'stop').touch()
        assert proc.wait(timeout=10) == 0
        assert _wait_for(lambda: _group_gone(proc.pid), timeout=15), \
            'the log watcher outlived the session'

    def test_respawn_rotation_keeps_the_file_writers_hold(self, tmp_path):
        """Rotation at respawn copies and truncates instead of renaming:
        apps still holding the old file would otherwise keep growing
        stderr.log.1 forever."""
        text = WRAPPER_IN.read_text(encoding='utf-8')
        body = re.search(r'^_xx_rotate_err_log\(\) \{(.*?)^\}', text,
                         re.M | re.S).group(1)
        log = tmp_path / 'stderr.log'
        log.write_bytes(b'x' * 1_200_000)
        holder = open(log, 'ab')
        try:
            harness = tmp_path / 'rotate.sh'
            harness.write_text('_xx_err_log="%s"\n_xx_rotate_err_log() {%s}\n'
                               '_xx_rotate_err_log\n' % (log, body))
            subprocess.run(['sh', str(harness)], check=True, timeout=30)
            holder.write(b'after\n')
            holder.flush()
        finally:
            holder.close()
        assert log.read_bytes() == b'after\n'
        assert tmp_path.joinpath('stderr.log.1').stat().st_size == 1_200_000


class TestSignals:
    def test_hangup_ends_the_session_and_stops_the_shell(self, wrapper):
        start, paths = wrapper
        proc = start('hang')
        runs = paths['state'] / 'runs'
        assert _wait_for(lambda: runs.exists() and runs.read_text().strip())
        child = int(runs.read_text().split()[0])
        time.sleep(0.3)
        proc.send_signal(signal.SIGHUP)
        proc.wait(timeout=10)
        assert _wait_for(lambda: not _pid_alive(child), timeout=5), \
            'the shell survived SIGHUP to its wrapper'


class TestCrashLoop:
    def test_startup_crash_loop_ends_with_failure_status(self, wrapper):
        start, paths = wrapper
        proc = start('crash', XX_WM_MAX_FAST_CRASHES='2')
        try:
            status = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pytest.fail('the wrapper retried a crash loop forever')
        assert status == 3
        assert len((paths['state'] / 'runs').read_text().split()) == 2
        assert b'ending the session' in proc.stderr.read()

    def test_crash_loop_stamps_the_phosh_fallback_with_the_boot(
            self, wrapper, tmp_path):
        start, paths = wrapper
        boot = tmp_path / 'boot_id'
        boot.write_text('3b1c-boot\n')
        marker = tmp_path / 'state-home' / 'xx-wm' / 'phosh-fallback'
        proc = start('crash', XX_WM_MAX_FAST_CRASHES='2',
                     XX_WM_PHOSH_FALLBACK=str(marker),
                     XX_WM_BOOT_ID_FILE=str(boot))
        assert proc.wait(timeout=20) == 3
        lines = marker.read_text().splitlines()
        assert lines[0] == '3b1c-boot'
        assert 'status 3 2 times in a row' in lines[1]
        err = proc.stderr.read()
        assert b'falling back to Phosh until the next boot' in err
        assert b'ending the session' not in err
        assert b'falling back to Phosh' in paths['log'].read_bytes()

    def test_no_boot_id_keeps_ending_the_session(self, wrapper, tmp_path):
        start, _paths = wrapper
        marker = tmp_path / 'state-home' / 'xx-wm' / 'phosh-fallback'
        proc = start('crash', XX_WM_MAX_FAST_CRASHES='2',
                     XX_WM_PHOSH_FALLBACK=str(marker),
                     XX_WM_BOOT_ID_FILE=str(tmp_path / 'missing'))
        assert proc.wait(timeout=20) == 3
        assert not marker.exists()
        assert b'ending the session' in proc.stderr.read()

    def test_default_limit_is_finite(self):
        text = WRAPPER_IN.read_text(encoding='utf-8')
        assert '${XX_WM_MAX_FAST_CRASHES:-6}' in text


class TestServiceRestartPolicy:
    def test_systemd_unit_gives_up_after_a_few_failures(self):
        unit = configparser.ConfigParser(strict=False, interpolation=None)
        unit.read_string(UNIT.read_text(encoding='utf-8'))
        assert unit['Service']['Restart'] == 'on-failure'
        assert int(unit['Unit']['StartLimitBurst']) > 0
        assert int(unit['Unit']['StartLimitIntervalSec']) >= 300

    def test_openrc_respawn_is_bounded(self):
        text = OPENRC.read_text(encoding='utf-8')
        assert 'respawn_max=0' not in text
        assert re.search(r'^respawn_max=[1-9]', text, re.M)
        assert re.search(r'^respawn_period=\d+', text, re.M)

    def test_openrc_refuses_a_missing_user(self, tmp_path):
        text = OPENRC.read_text(encoding='utf-8')
        body = re.search(r'^start_pre\(\) \{(.*?)^\}', text,
                         re.M | re.S).group(1)
        harness = tmp_path / 'pre.sh'
        harness.write_text(
            'checkpath() { echo "checkpath $*" >> "%s/calls"; }\n'
            'eerror() { echo "$*" >&2; }\n'
            'mkdir() { echo "mkdir $*" >> "%s/calls"; }\n'
            'chown() { :; }\nchmod() { :; }\n'
            'XX_WM_USER=no-such-user-xxwm\n'
            'start_pre() {%s}\nstart_pre\n' % (tmp_path, tmp_path, body))
        proc = subprocess.run(['sh', str(harness)], capture_output=True,
                              text=True, timeout=30, check=False)
        assert proc.returncode != 0
        assert 'no-such-user-xxwm' in proc.stderr
        assert not (tmp_path / 'calls').exists(), \
            'start_pre went on with a fallback uid'


def _extract(names):
    script = INSTALL_SH.read_text(encoding='utf-8')
    chunks = []
    for name in names:
        match = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
        assert match, name
        chunks.append('%s() {%s}\n' % (name, match.group(1)))
    return ''.join(chunks)


class TestInstallStatus:
    STUBS = ('install_apps install_xx_keyboard_deps install_xx_keyboard '
             'link_squeekboard_layouts check_layer_shell install_fonts '
             'install_logind_dropin select_phoc_scale setup_greeter '
             'refresh_icon_caches refresh_running_apps record_installed').split()

    def _run(self, tmp_path, enable_rc, group_rc=0):
        repo = tmp_path / 'repo'
        (repo / 'launcher').mkdir(parents=True)
        (repo / 'scripts').mkdir()
        (repo / 'scripts' / 'bootstrap-dots.sh').write_text('exit 0\n')
        (tmp_path / 'common').mkdir()
        bindir = tmp_path / 'bin'
        bindir.mkdir()
        meson = bindir / 'meson'
        meson.write_text('#!/bin/sh\nexit 0\n')
        meson.chmod(0o755)
        stubs = ''.join('%s() { return 0; }\n' % name for name in self.STUBS)
        stubs += 'enable_service() { return %d; }\n' % enable_rc
        stubs += 'add_input_group() { return %d; }\n' % group_rc
        funcs = tmp_path / 'funcs.sh'
        funcs.write_text(_extract(['build_install']) + stubs)
        env = dict(os.environ, PATH='%s:%s' % (bindir, os.environ['PATH']),
                   SUDO='', REPO_DIR=str(repo))
        return subprocess.run(['sh', '-c', '. "%s"; build_install' % funcs],
                              capture_output=True, text=True, env=env,
                              cwd=str(tmp_path), timeout=30, check=False)

    def test_failed_service_enable_fails_the_install(self, tmp_path):
        assert self._run(tmp_path, enable_rc=1).returncode != 0

    def test_failed_input_group_fails_the_install(self, tmp_path):
        assert self._run(tmp_path, enable_rc=0, group_rc=1).returncode != 0

    def test_clean_run_succeeds(self, tmp_path):
        assert self._run(tmp_path, enable_rc=0).returncode == 0

    def test_enable_service_reports_a_failed_helper_enable(self, tmp_path):
        bindir = tmp_path / 'bin'
        bindir.mkdir()
        stub = bindir / 'systemctl'
        stub.write_text('#!/bin/sh\ncase "$*" in *xx-wm-helper-boot*) exit 1;; esac\nexit 0\n')
        stub.chmod(0o755)
        rundir = tmp_path / 'systemd'
        rundir.mkdir()
        funcs = tmp_path / 'funcs.sh'
        funcs.write_text(_extract(['is_systemd', 'has_openrc', 'enable_service']))
        env = dict(os.environ, PATH='%s:%s' % (bindir, os.environ['PATH']),
                   SUDO='', XX_WM_SYSTEMD_RUN_DIR=str(rundir))
        proc = subprocess.run(['sh', '-c', '. "%s"; enable_service' % funcs],
                              capture_output=True, text=True, env=env,
                              timeout=30, check=False)
        assert proc.returncode != 0
        assert 'xx-wm-helper-boot' in proc.stderr
