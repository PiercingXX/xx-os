"""xxdb deploy: the datadir stays root-owned, and the shell restarts even
when the daemon runs inside it."""
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb
import xxdb_daemon
from xxdb_daemon import XxdbDaemon


def _wait_for(pred, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _rpc_daemon(tmp_path, **overrides):
    kwargs = dict(cert_dir=tmp_path / 'certs',
                  trust_store=xxdb.TrustStore(path=tmp_path / 'trusted.json'),
                  shell_runner=lambda argv: (0, 'out', ''))
    kwargs.update(overrides)
    daemon = XxdbDaemon(**kwargs)
    daemon.phone_fp = 'ab' * 32
    return daemon


def _rpc_session(daemon):
    session = xxdb_daemon._Session('127.0.0.1')
    daemon.trust.add('cd' * 32, 'laptop', 'PEM', now=time.time())
    session.tls_fp = 'cd' * 32
    return session


def _install(tmp_path, monkeypatch, rsync):
    calls = []
    monkeypatch.setattr(
        xxdb_daemon.subprocess, 'run',
        lambda cmd, **_kw: calls.append(cmd) or SimpleNamespace(
            returncode=0, stdout='', stderr=''))
    monkeypatch.setattr(xxdb_daemon.shutil, 'which',
                        lambda name: '/usr/bin/rsync'
                        if rsync and name == 'rsync' else None)
    daemon = _rpc_daemon(tmp_path, sudo_check=lambda: True)
    [response] = daemon.handle_request(
        _rpc_session(daemon), {'id': 1, 'cmd': 'install',
                               'files': {'main.py': 'eA=='}})
    assert response['ok'] is True
    return calls[0]


def test_install_keeps_the_datadir_root_owned(tmp_path, monkeypatch):
    cmd = _install(tmp_path, monkeypatch, rsync=True)
    assert cmd[:3] == ['sudo', '-n', 'rsync']
    assert '--chown=root:root' in cmd
    assert '--chmod=D755,F644' in cmd
    cmd = _install(tmp_path, monkeypatch, rsync=False)
    assert cmd[:4] == ['sudo', '-n', 'cp', '-R']
    assert '-a' not in cmd


def test_restarter_restarts_the_shell_it_runs_in(tmp_path):
    proc = tmp_path / 'proc'
    for pid, cmdline in ((100, b'/usr/bin/python3\0/usr/share/xx-wm/main.py\0'),
                         (200, b'bash\0'),
                         (300, b'python3\0/usr/bin/other.py\0')):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / 'cmdline').write_bytes(cmdline)
    sent = []
    kill = lambda pid, sig: sent.append((pid, sig))  # noqa: E731
    pid, fire = xxdb_daemon._default_shell_restarter(
        proc_root=str(proc), self_pid=100, kill=kill)
    assert pid == 100 and sent == []
    fire()
    assert sent == [(100, signal.SIGUSR1)]
    pid, _fire = xxdb_daemon._default_shell_restarter(
        proc_root=str(proc), self_pid=300, kill=kill)
    assert pid == 100


def test_restart_signal_fires_after_the_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(xxdb_daemon, 'RESTART_DELAY_S', 0)
    fired = []
    daemon = _rpc_daemon(tmp_path,
                         shell_restarter=lambda: (42, lambda: fired.append(1)))
    replies = daemon.handle_request(_rpc_session(daemon),
                                    {'id': 1, 'cmd': 'restart-shell'})
    assert next(replies) == {'id': 1, 'ok': True, 'pid': 42}
    assert fired == []
    assert list(replies) == []
    assert _wait_for(lambda: fired == [1])
