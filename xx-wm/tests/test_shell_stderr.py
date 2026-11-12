"""Shell stderr capture (phone has no visible traceback sink).

The wrapper's python child used to inherit phoc's stderr (/dev/tty7), so
tracebacks vanished on a phone. The wrapper now redirects stderr to
~/.local/share/xx-wm/stderr.log, copied to stderr.log.1 and truncated
past 1 MB at each (re)spawn and every few minutes, the README documents it, and xxdb logcat tails it alongside
shell.log.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb
import xxdb_daemon
from xxdb_daemon import XxdbDaemon

REPO = Path(__file__).parent.parent
WRAPPER = REPO / 'launcher' / 'data' / 'xx-wm.in'
README = REPO / 'launcher' / 'README.md'


def _function_body(script, name):
    match = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return match.group(1) if match else ''


class TestWrapperStderr:
    @pytest.fixture(scope='class')
    def wrapper(self):
        return WRAPPER.read_text(encoding='utf-8')

    def test_stderr_goes_to_log_file(self, wrapper):
        assert '.local/share/xx-wm/stderr.log' in wrapper
        assert '2>>' in wrapper

    def test_python_child_redirected(self, wrapper):
        assert '_xx_err_log="$HOME/.local/share/xx-wm/stderr.log"' in wrapper
        spawn = [line for line in wrapper.splitlines()
                 if 'main.py' in line and '@python@ @shell_dir@/main.py' in line]
        assert spawn, 'python spawn line missing'
        assert any('2>>' in line and '_xx_err_log' in line for line in spawn)

    def test_rotation_past_one_megabyte(self, wrapper):
        body = _function_body(wrapper, '_xx_rotate_err_log')
        assert body, 'rotation function missing'
        assert '1048576' in body
        assert '"$_xx_err_log.1"' in body or "'$_xx_err_log.1'" in body

    def test_rotation_runs_at_each_spawn(self, wrapper):
        assert '_xx_rotate_err_log' in wrapper
        loop_at = wrapper.find('while :; do')
        assert loop_at != -1
        assert wrapper.find('_xx_rotate_err_log', loop_at) != -1

    def test_log_dir_created_before_spawn(self, wrapper):
        assert 'mkdir -p' in wrapper
        assert '.local/share/xx-wm' in wrapper


def _run_rotate_harness(home: Path, size: int | None) -> None:
    """Execute the wrapper's real rotation function against a stub HOME.

    Extracts _xx_rotate_err_log verbatim from the template so this tests
    the shipped logic, not a copy of it.
    """
    wrapper = WRAPPER.read_text(encoding='utf-8')
    body = _function_body(wrapper, '_xx_rotate_err_log')
    assert body
    log_dir = home / '.local' / 'share' / 'xx-wm'
    log_dir.mkdir(parents=True, exist_ok=True)
    if size is not None:
        (log_dir / 'stderr.log').write_bytes(b'x' * size)
    harness = home / 'rotate.sh'
    harness.write_text(
        '_xx_err_log="$HOME/.local/share/xx-wm/stderr.log"\n'
        f'_xx_rotate_err_log() {{{body}}}\n'
        '_xx_rotate_err_log\n',
        encoding='utf-8',
    )
    env = {'HOME': str(home), 'PATH': '/usr/bin:/bin'}
    proc = subprocess.run(['sh', str(harness)], capture_output=True,
                          text=True, env=env, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr


class TestRotationBehavior:
    def test_large_log_rotates(self, tmp_path):
        _run_rotate_harness(tmp_path, 1_200_000)
        log_dir = tmp_path / '.local' / 'share' / 'xx-wm'
        assert (log_dir / 'stderr.log.1').stat().st_size == 1_200_000
        # Copy-truncate: apps holding the log keep writing to stderr.log.
        assert (log_dir / 'stderr.log').read_bytes() == b''

    def test_small_log_kept(self, tmp_path):
        _run_rotate_harness(tmp_path, 100)
        log_dir = tmp_path / '.local' / 'share' / 'xx-wm'
        assert (log_dir / 'stderr.log').is_file()
        assert not (log_dir / 'stderr.log.1').exists()

    def test_missing_log_is_noop(self, tmp_path):
        _run_rotate_harness(tmp_path, None)
        log_dir = tmp_path / '.local' / 'share' / 'xx-wm'
        assert not (log_dir / 'stderr.log.1').exists()


class TestLogcatIncludesStderr:
    def _daemon(self, tmp_path):
        shell_log = tmp_path / 'shell.log'
        shell_log.write_text('shell line\n', encoding='utf-8')
        err_log = tmp_path / 'stderr.log'
        err_log.write_text('Traceback: boom\n', encoding='utf-8')
        trust = xxdb.TrustStore(path=tmp_path / 'trusted.json')
        daemon = XxdbDaemon(
            cert_dir=tmp_path / 'certs',
            trust_store=trust,
            allow_loopback=True,
            shell_runner=lambda argv: (0, 'out', ''),
            shell_log_path=shell_log,
            stderr_log_path=err_log,
        )
        daemon.phone_fp = 'ab' * 32
        return daemon

    def _session(self, daemon):
        session = xxdb_daemon._Session('127.0.0.1')
        daemon.trust.add('cd' * 32, 'test-host', 'PEM', now=1.0)
        session.tls_fp = 'cd' * 32
        return session

    def test_stderr_lines_surface_with_source(self, tmp_path):
        daemon = self._daemon(tmp_path)
        session = self._session(daemon)
        out = list(daemon.handle_request(
            session, {'id': 1, 'cmd': 'logcat', 'lines': 200}))
        logs = [(m.get('source'), m.get('line'))
                for m in out if m.get('stream') == 'log']
        assert ('shell.log', 'shell line') in logs
        assert ('stderr.log', 'Traceback: boom') in logs

    def test_missing_stderr_log_still_returns_shell_log(self, tmp_path):
        daemon = self._daemon(tmp_path)
        (tmp_path / 'stderr.log').unlink()
        session = self._session(daemon)
        out = list(daemon.handle_request(
            session, {'id': 1, 'cmd': 'logcat', 'lines': 200}))
        logs = [(m.get('source'), m.get('line'))
                for m in out if m.get('stream') == 'log']
        assert ('shell.log', 'shell line') in logs
        assert not [s for s, _line in logs if s == 'stderr.log']


class TestReadmeDocumentsStderrLog:
    def test_troubleshooting_mentions_stderr_log(self):
        text = README.read_text(encoding='utf-8')
        assert 'roubleshoot' in text
        assert 'stderr.log' in text

    def test_readme_mentions_xxdb_logcat(self):
        text = README.read_text(encoding='utf-8')
        assert 'logcat' in text


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
