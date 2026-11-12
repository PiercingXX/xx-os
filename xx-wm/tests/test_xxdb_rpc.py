"""xxdb RPC framing, authorization, path rules, interfaces, timers, CLI."""
import base64
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb
import xxdb_daemon
from xxdb_daemon import XxdbDaemon

REPO = Path(__file__).parent.parent


def _load_host_cli():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader('xxdb_host_cli', str(REPO / 'tools' / 'xxdb'))
    spec = importlib.util.spec_from_loader('xxdb_host_cli', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _daemon(tmp_path, **overrides):
    trust = xxdb.TrustStore(path=tmp_path / 'trusted.json')
    kwargs = dict(
        cert_dir=tmp_path / 'certs',
        trust_store=trust,
        allow_loopback=True,
        shell_runner=lambda argv: (0, 'out', ''),
        screenshot_provider=lambda: b'\x89PNGstub',
        shell_log_path=tmp_path / 'shell.log',
        ipc_forwarder=lambda verb: None,
        input_injector=lambda action, params: (True, 'ok'),
        sudo_check=lambda: False,
        shell_restarter=lambda: 1234,
    )
    kwargs.update(overrides)
    daemon = XxdbDaemon(**kwargs)
    daemon.phone_fp = 'ab' * 32
    return daemon


def _session(daemon, trusted=True):
    session = xxdb_daemon._Session('127.0.0.1')
    if trusted:
        daemon.trust.add('cd' * 32, 'test-host', 'PEM', now=1.0)
        session.tls_fp = 'cd' * 32
    return session


def _run(daemon, session, message):
    return list(daemon.handle_request(session, message))


# --- framing ---------------------------------------------------------------

def test_frame_split_lines_and_batch():
    reader = xxdb.FrameReader()
    first = xxdb.encode_message({'id': 1, 'cmd': 'hello'})
    second = xxdb.encode_message({'id': 2, 'cmd': 'hello'})
    assert reader.feed(first[:5]) == []
    messages = reader.feed(first[5:] + second)
    assert [m['id'] for m in messages] == [1, 2]


def test_frame_rejects_bad_json():
    reader = xxdb.FrameReader()
    with pytest.raises(xxdb.FrameError):
        reader.feed(b'{nope}\n')


def test_frame_rejects_non_object():
    reader = xxdb.FrameReader()
    with pytest.raises(xxdb.FrameError):
        reader.feed(b'[1, 2]\n')


def test_frame_rejects_overlong_line():
    reader = xxdb.FrameReader(max_line=16)
    with pytest.raises(xxdb.FrameError):
        reader.feed(b'{"id": 12345678901234567890}\n')


def test_encode_has_id_and_newline():
    raw = xxdb.encode_message({'id': 7, 'ok': True})
    assert raw.endswith(b'\n')
    assert b'"id":7' in raw


# --- authorization ---------------------------------------------------------

def test_unpaired_shell_refused(tmp_path):
    daemon = _daemon(tmp_path)
    [response] = _run(daemon, _session(daemon, trusted=False),
                      {'id': 1, 'cmd': 'shell', 'argv': ['echo']})
    assert response == {'id': 1, 'ok': False,
                        'error': 'not-paired: pair this host first'}


def test_unpaired_screenshot_pull_ipc_input_refused(tmp_path):
    daemon = _daemon(tmp_path)
    session = _session(daemon, trusted=False)
    for message in ({'id': 1, 'cmd': 'screenshot'},
                    {'id': 2, 'cmd': 'pull', 'path': 'x'},
                    {'id': 3, 'cmd': 'ipc', 'verb': 'lock'},
                    {'id': 4, 'cmd': 'input', 'action': 'tap',
                     'x': 1, 'y': 2},
                    {'id': 5, 'cmd': 'hello'},
                    {'id': 6, 'cmd': 'restart-shell'}):
        [response] = _run(daemon, session, message)
        assert response['ok'] is False
        assert response['error'].startswith('not-paired'), message


def test_unknown_command_needs_auth_first(tmp_path):
    daemon = _daemon(tmp_path)
    [response] = _run(daemon, _session(daemon, trusted=False),
                      {'id': 1, 'cmd': 'frobnicate'})
    assert response['error'].startswith('not-paired')


def test_unknown_command_after_auth(tmp_path):
    daemon = _daemon(tmp_path)
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 1, 'cmd': 'frobnicate'})
    assert response == {'id': 1, 'ok': False, 'error': 'unknown-command'}


def test_bad_request_without_cmd(tmp_path):
    daemon = _daemon(tmp_path)
    [response] = _run(daemon, _session(daemon, trusted=True), {'id': 1})
    assert response == {'id': 1, 'ok': False, 'error': 'bad-request'}


def test_authorized_shell_runs(tmp_path):
    daemon = _daemon(tmp_path)
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 1, 'cmd': 'shell', 'argv': ['echo', 'hi']})
    assert response['ok'] is True
    assert response['returncode'] == 0


def test_shell_rejects_bad_argv(tmp_path):
    daemon = _daemon(tmp_path)
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 1, 'cmd': 'shell', 'argv': 'echo hi'})
    assert response['ok'] is False


def test_ipc_allowlists_verbs(tmp_path):
    daemon = _daemon(tmp_path)
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 1, 'cmd': 'ipc', 'verb': 'gesture.back'})
    assert response == {'id': 1, 'ok': True, 'verb': 'gesture.back'}
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 2, 'cmd': 'ipc', 'verb': 'rm -rf /'})
    assert response['ok'] is False


def test_install_refused_without_sudo(tmp_path):
    daemon = _daemon(tmp_path)
    payload = base64.b64encode(b'x = 1\n').decode('ascii')
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 1, 'cmd': 'install',
                       'files': {'main.py': payload}})
    assert response['ok'] is False
    assert 'sudo' in response['error']


def test_install_rejects_escaping_paths(tmp_path):
    daemon = _daemon(tmp_path, sudo_check=lambda: True)
    payload = base64.b64encode(b'x').decode('ascii')
    for bad in ('/etc/passwd', '../evil.py', 'a/../../b.py'):
        [response] = _run(daemon, _session(daemon, trusted=True),
                          {'id': 1, 'cmd': 'install',
                           'files': {bad: payload}})
        assert response['ok'] is False
        assert 'bad tree path' in response['error']


# --- path restriction ------------------------------------------------------

def test_push_pull_inside_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    daemon = _daemon(tmp_path)
    daemon._home = str(home)
    session = _session(daemon, trusted=True)
    payload = base64.b64encode(b'hello').decode('ascii')
    [response] = _run(daemon, session,
                      {'id': 1, 'cmd': 'push', 'path': 'notes/a.txt',
                       'data': payload})
    assert response == {'id': 1, 'ok': True, 'bytes': 5,
                        'path': str(home / 'notes' / 'a.txt')}
    [response] = _run(daemon, session,
                      {'id': 2, 'cmd': 'pull',
                       'path': 'notes/a.txt'})
    assert response['ok'] is True
    assert base64.b64decode(response['data']) == b'hello'


def test_push_outside_home_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    daemon = _daemon(tmp_path)
    daemon._home = str(home)
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 1, 'cmd': 'push', 'path': '/etc/xx',
                       'data': base64.b64encode(b'x').decode('ascii')})
    assert response['ok'] is False
    assert '$HOME' in response['error']


def test_push_dotdot_escape_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    daemon = _daemon(tmp_path)
    daemon._home = str(home)
    [response] = _run(daemon, _session(daemon, trusted=True),
                      {'id': 1, 'cmd': 'push', 'path': '../../etc/x',
                       'data': base64.b64encode(b'x').decode('ascii')})
    assert response['ok'] is False


def test_system_path_needs_confirm_then_token(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    daemon = _daemon(tmp_path)
    daemon._home = str(home)
    session = _session(daemon, trusted=True)
    payload = base64.b64encode(b'sys').decode('ascii')
    [response] = _run(daemon, session,
                      {'id': 1, 'cmd': 'push', 'path': '/tmp/sys-x.txt',
                       'data': payload, 'system': True})
    assert response['ok'] is False
    assert response['error'] == 'needs-confirm'
    token = response['token']
    assert daemon.system_access.confirm(token, True, now=1000.0) is True
    [response] = _run(daemon, session,
                      {'id': 2, 'cmd': 'push', 'path': '/tmp/sys-x.txt',
                       'data': payload, 'system': True, 'token': token})
    assert response == {'id': 2, 'ok': True, 'bytes': 3,
                        'path': '/tmp/sys-x.txt'}


def test_system_request_denied(tmp_path):
    daemon = _daemon(tmp_path)
    token = daemon.system_access.request('/etc/x', now=1000.0)
    assert daemon.system_access.confirm(token, False, now=1001.0) is True
    assert daemon.system_access.consume(token, now=1001.0) is None


def test_system_token_expires():
    access = xxdb.SystemAccess(clock=lambda: 0.0)
    token = access.request('/etc/x', now=0.0)
    access.confirm(token, True, now=1.0)
    assert access.consume(token, now=xxdb.SYSTEM_CONFIRM_TTL_S + 2) is None


# --- interfaces ------------------------------------------------------------

def test_classify_interfaces():
    assert xxdb.classify_interface('wlan0', True) == 'wifi'
    assert xxdb.classify_interface('wlp2s0') == 'wifi'
    assert xxdb.classify_interface('wwan0') == 'cellular'
    assert xxdb.classify_interface('rmnet_data0') == 'cellular'
    assert xxdb.classify_interface('usb0') == 'cellular'
    assert xxdb.classify_interface('eth0') == 'other'
    assert xxdb.classify_interface('lo') == 'loopback'


def test_bind_excludes_cellular_and_down():
    entries = [('wlan0', True, ['192.168.1.5'], True),
               ('wwan0', False, ['10.61.0.2'], True),
               ('wlan1', True, ['192.168.2.5'], False),
               ('eth0', False, ['192.168.0.9'], True),
               ('lo', False, ['127.0.0.1'], True)]
    assert xxdb.select_bind_addresses(entries) == ['192.168.1.5']
    assert xxdb.select_bind_addresses(entries, allow_loopback=True) == [
        '192.168.1.5', '127.0.0.1']


def test_parse_ip_addr():
    text = ('1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 inet 127.0.0.1/8 '
            'scope host lo\n'
            '3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 '
            'inet 192.168.1.5/24 scope global wlan0\n'
            '4: wwan0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 '
            'inet 10.61.0.2/29 scope global wwan0\n')
    rows = xxdb.parse_ip_addr(text)
    by_name = {name: (addrs, up) for name, _w, addrs, up in rows}
    assert by_name['wlan0'] == (['192.168.1.5'], True)
    assert by_name['wwan0'] == (['10.61.0.2'], True)
    assert xxdb.select_bind_addresses(
        [(n, n == 'wlan0', addrs, up) for n, (addrs, up) in
         ((name, rest) for name, rest in by_name.items())]) == ['192.168.1.5']


def test_port_range():
    import random
    rng = random.Random(42)
    for _ in range(50):
        assert xxdb.PORT_MIN <= xxdb.pick_port(rng) <= xxdb.PORT_MAX


# --- auto-off --------------------------------------------------------------

def test_idle_expired_cases():
    assert xxdb.idle_expired(True, 0, 0.0, 3600.0) is True
    assert xxdb.idle_expired(True, 0, 0.0, 3599.0) is False
    assert xxdb.idle_expired(True, 1, 0.0, 99999.0) is False
    assert xxdb.idle_expired(False, 0, 0.0, 99999.0) is False


def test_controller_idle_disables(tmp_path):
    import xxdb_controller
    now = [1000.0]
    config = _fake_config()
    disabled = []
    ctrl = xxdb_controller.XxdbController(
        config, post=lambda fn: fn(), clock=lambda: now[0],
        home=tmp_path)
    ctrl.enabled = True
    ctrl.daemon = object()
    ctrl.on_unnotify = lambda: disabled.append(True)
    ctrl.last_activity = 1000.0
    now[0] = 1000.0 + xxdb.IDLE_TIMEOUT_S - 1
    assert ctrl.idle_check() is True
    assert ctrl.enabled is True
    now[0] = 1000.0 + xxdb.IDLE_TIMEOUT_S
    assert ctrl.idle_check() is False
    assert ctrl.enabled is False
    assert disabled == [True]


def test_controller_boot_state_resets_unless_persistent(tmp_path):
    import xxdb_controller
    config = _fake_config(xxdb_enabled=True, xxdb_persistent=False)
    ctrl = xxdb_controller.XxdbController(config, post=lambda fn: fn(),
                                          home=tmp_path)
    assert ctrl.ensure_boot_state() is False
    assert config.data['xxdb_enabled'] is False
    config = _fake_config(xxdb_enabled=True, xxdb_persistent=True)
    ctrl = xxdb_controller.XxdbController(config, post=lambda fn: fn(),
                                          home=tmp_path)
    assert ctrl.ensure_boot_state() is True


def _fake_config(**overrides):
    from types import SimpleNamespace
    data = {'xxdb_enabled': False, 'xxdb_persistent': False}
    data.update(overrides)
    return SimpleNamespace(data=data, save=lambda: None)


def test_controller_enable_notifies_and_disable_unnotifies(tmp_path):
    import time
    import xxdb_controller
    from types import SimpleNamespace
    started, stopped = [], []
    daemon = SimpleNamespace(
        port=37234, addrs=['192.168.1.5'], phone_fp='0123456789abcdef' * 4,
        pairing=xxdb.PairingSession(),
        system_access=xxdb.SystemAccess(),
        start=lambda: started.append(True),
        stop=lambda: stopped.append(True))
    notices, unnotices = [], []
    ctrl = xxdb_controller.XxdbController(
        _fake_config(), post=lambda fn: fn(), home=tmp_path,
        daemon_factory=lambda _ctrl: daemon,
        on_notify=lambda title, body: notices.append((title, body)),
        on_unnotify=lambda: unnotices.append(True))
    ctrl.request_enable()
    deadline = time.time() + 5
    while not ctrl.enabled and time.time() < deadline:
        time.sleep(0.01)
    assert ctrl.enabled is True
    assert started == [True]
    assert notices and notices[0][0] == 'Wireless debugging'
    assert 'On' in ctrl.status_text()
    code, shown, fingerprint, _expires = ctrl.issue_pairing_code()
    assert len(code) == 6 and shown == '192.168.1.5:37234'
    assert fingerprint == '0123 4567 89ab cdef'
    ctrl.request_disable()
    assert ctrl.enabled is False
    assert stopped == [True]
    assert unnotices == [True]
    assert ctrl.status_text() == 'Off'


def test_config_defaults():
    sys.path.insert(0, str(REPO / 'launcher' / 'src'))
    import config as config_module
    assert config_module.DEFAULT_CONFIG['xxdb_enabled'] is False
    assert config_module.DEFAULT_CONFIG['xxdb_persistent'] is False


# --- host CLI parsing ------------------------------------------------------

def test_cli_split_hostport():
    cli = _load_host_cli()
    assert cli.split_hostport('phone:37234') == ('phone', 37234)
    assert cli.split_hostport('phone') == ('phone', None)
    assert cli.split_hostport('phone:37234', 1) == ('phone', 37234)


def test_cli_subcommands():
    cli = _load_host_cli()
    parser = cli.build_parser()
    args = parser.parse_args(['pair', 'phone:37234', '123456',
                              '0123', '4567', '89ab', 'cdef'])
    assert (args.command, args.hostport, args.code) == ('pair', 'phone:37234',
                                                        '123456')
    assert args.fingerprint == ['0123', '4567', '89ab', 'cdef']
    with pytest.raises(SystemExit):
        parser.parse_args(['pair', 'phone:37234', '123456'])
    args = parser.parse_args(['shell', '--to', 'phone', '--', 'echo', 'hi'])
    assert args.command == 'shell' and args.cmd == ['--', 'echo', 'hi']
    args = parser.parse_args(['input', 'phone', 'tap', '10', '20'])
    assert (args.action, args.rest) == ('tap', ['10', '20'])
    args = parser.parse_args(['deploy', 'phone', '--src', 'x/src'])
    assert args.src == 'x/src'
    args = parser.parse_args(['logcat', 'phone'])
    assert args.lines == 200 and args.follow is True


def test_cli_collect_deploy_tree(tmp_path):
    cli = _load_host_cli()
    src = tmp_path / 'launcher' / 'src'
    src.mkdir(parents=True)
    (src / 'main.py').write_text('x')
    (src / 'style.css').write_text('y')
    data = tmp_path / 'launcher' / 'data'
    data.mkdir()
    (data / 'wayland.xml').write_text('<xml/>')
    tree = cli.collect_deploy_tree(src)
    assert set(tree) == {'main.py', 'style.css', 'wayland.xml'}
    assert base64.b64decode(tree['main.py']) == b'x'


def test_cli_pairing_mac_matches_phone():
    cli = _load_host_cli()
    assert cli.pairing_mac('482916', 'ab' * 32, 'cd' * 32) == \
        xxdb.pairing_mac('482916', 'ab' * 32, 'cd' * 32)
