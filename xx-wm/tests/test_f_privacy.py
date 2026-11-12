"""Privacy indicators + per-app permissions backend (task f_privacy).

Headless: PipeWire/GeoClue/PermissionStore/cgroup paths are exercised
through real-shaped fixtures and recording fakes — no display, no daemons.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

def _overlay_real_gi():
    """Point sys.modules gi entries at the real PyGObject banked by
    conftest (captured before any suite installed its fakes).

    Earlier-collected modules (e.g. test_call_ui_flow) install fake gi
    modules without restoring them; re-importing gi from scratch is not
    possible, but no re-import is needed — the real module objects are
    reused. Returns the previous entries so callers can restore them.
    """
    banked = dict(getattr(sys.modules.get('conftest'), '_REAL_GI_MODULES',
                          {}) or {})
    previous = {n: sys.modules[n] for n in banked if n in sys.modules}
    sys.modules.update(banked)
    return previous


# Bind the real GLib for the Variant call-shape assertions below, no
# matter which collection-time stubs are installed, then restore the
# ambient entries so later-collected suites see exactly what they would
# without this module.
_previous_gi = _overlay_real_gi()

gi = pytest.importorskip('gi')

from gi.repository import GLib  # noqa: E402

assert hasattr(GLib, 'Variant'), 'privacy tests need real PyGObject'

sys.modules.update(_previous_gi)
del _previous_gi


def _real_quick_actions():
    """Private quick_actions bound to real PyGObject.

    Sibling suites import quick_actions during collection under their
    fake gi, and that fake-flavored instance stays cached in
    sys.modules for the whole run. Exec a private copy under the real
    gi here so the GeoClue tests exercise the real D-Bus call shapes;
    the shared cache is never touched.
    """
    import importlib.util
    previous = _overlay_real_gi()
    try:
        from gi.repository import Gio as _j, GLib as _g  # noqa: F401
        assert hasattr(_g, 'Variant') and hasattr(_j, 'DBusCallFlags')
        src = (Path(__file__).parent.parent / 'launcher' / 'src'
               / 'quick_actions.py')
        spec = importlib.util.spec_from_file_location(
            'quick_actions_f_privacy', str(src))
        module = importlib.util.module_from_spec(spec)
        sys.modules['quick_actions_f_privacy'] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop('quick_actions_f_privacy', None)
        sys.modules.update(previous)

import app_permissions  # noqa: E402
from app_permissions import (  # noqa: E402
    delete_permission,
    enforce_network_policy,
    expected_scope_relpath,
    find_scope_cgroup,
    get_device_permission,
    get_location_permission,
    is_network_denied,
    lookup_permissions,
    parse_proc_cgroup,
    permission_allowed,
    set_device_permission,
    set_location_permission,
    set_permission,
)
from privacy_monitor import (  # noqa: E402
    CAMERA,
    LOCATION,
    MIC,
    PrivacyMonitor,
    PwDumpParser,
    classify_node,
    detail_text,
    display_name_for,
    format_indicator,
)


def _props(**overrides):
    props = {
        'media.class': 'Stream/Input/Audio',
        'application.process.id': 4242,
        'application.name': 'Calls',
    }
    props.update(overrides)
    return props


def _node(nid, props=None, state='running',
          node_type='PipeWire:Interface:Node', with_info=True):
    obj = {'id': nid, 'type': node_type}
    if with_info:
        obj['info'] = {'state': state, 'props': props if props is not None else _props()}
    return obj


def _dump(*nodes):
    return json.dumps(list(nodes))


class TestClassifyNode:
    def test_running_mic_counts(self):
        assert classify_node(_node(11)) == (MIC, 'Calls')

    def test_running_camera_counts(self):
        node = _node(12, _props(**{'media.class': 'Stream/Input/Video'}))
        assert classify_node(node) == (CAMERA, 'Calls')

    def test_suspended_stream_ignored(self):
        assert classify_node(_node(13, state='suspended')) is None

    def test_idle_stream_ignored(self):
        assert classify_node(_node(14, state='idle')) is None

    def test_output_sink_ignored(self):
        node = _node(15, _props(**{'media.class': 'Audio/Sink'}))
        assert classify_node(node) is None

    def test_device_node_ignored(self):
        node = _node(16, _props(**{'media.class': 'Audio/Device'}))
        assert classify_node(node) is None

    def test_client_object_ignored(self):
        node = _node(17, node_type='PipeWire:Interface:Client')
        assert classify_node(node) is None

    def test_missing_info_is_removal_not_stream(self):
        assert classify_node(_node(18, with_info=False)) is None
        assert classify_node({'id': 18, 'info': None}) is None

    def test_unattributable_stream_ignored(self):
        assert classify_node(_node(19, {})) is None


class TestAttributionFallbacks:
    def test_human_name_preferred(self):
        assert display_name_for(_props(**{
            'application.name': 'Firefox',
            'pipewire.access.portal.app_id': 'org.mozilla.firefox',
            'application.id': 'org.mozilla.firefox',
        })) == 'Firefox'

    def test_portal_app_id_before_desktop_id(self):
        assert display_name_for(_props(**{
            'application.name': '',
            'pipewire.access.portal.app_id': 'org.mozilla.firefox',
            'application.id': 'org.mozilla.firefox',
        })) == 'org.mozilla.firefox'

    def test_desktop_id_fallback(self):
        assert display_name_for({
            'application.id': 'org.gnome.Calls',
            'application.process.id': 7,
        }) == 'org.gnome.Calls'

    def test_node_name_fallback(self):
        assert display_name_for({
            'node.name': 'alsa_input.usb-mic',
            'application.process.id': 7,
        }) == 'alsa_input.usb-mic'

    def test_pid_label_last_resort(self):
        assert display_name_for({'application.process.id': 99}) == 'pid 99'

    def test_nothing_attributable_is_none(self):
        assert display_name_for({}) is None
        assert display_name_for(None) is None


class TestPwDumpParser:
    def test_whole_dump_array(self):
        parser = PwDumpParser()
        values = parser.feed(_dump(_node(1), _node(2)))
        assert len(values) == 1 and len(values[0]) == 2

    def test_partial_chunks_reassemble(self):
        parser = PwDumpParser()
        payload = _dump(_node(1), _node(2, _props(**{
            'media.class': 'Stream/Input/Video',
            'application.name': 'Camera',
        })))
        values = []
        for i in range(0, len(payload), 7):
            values.extend(parser.feed(payload[i:i + 7]))
        assert len(values) == 1 and len(values[0]) == 2

    def test_monitor_object_after_dump(self):
        parser = PwDumpParser()
        assert len(parser.feed(_dump(_node(1)))) == 1
        values = parser.feed(json.dumps(_node(2)))
        assert len(values) == 1 and values[0]['id'] == 2

    def test_split_monitor_object_mid_string(self):
        parser = PwDumpParser()
        parser.feed(_dump())
        payload = json.dumps(_node(3, _props(**{'application.name': 'Calls'})))
        assert parser.feed(payload[:20]) == []
        values = parser.feed(payload[20:])
        assert len(values) == 1 and values[0]['id'] == 3

    def test_stray_commas_and_whitespace_skipped(self):
        parser = PwDumpParser()
        values = parser.feed('  ,\n' + json.dumps(_node(4)) + ',\n')
        assert [v['id'] for v in values] == [4]
        assert parser.feed('   ') == []


class TestMonitorSnapshot:
    def _monitor(self, **kwargs):
        seen = []
        kwargs.setdefault('on_change', seen.append)
        monitor = PrivacyMonitor(**kwargs)
        return monitor, seen

    def test_initial_dump_builds_snapshot(self):
        monitor, _seen = self._monitor()
        monitor.feed_data(_dump(
            _node(1),
            _node(2, _props(**{'media.class': 'Stream/Input/Video',
                               'application.name': 'Camera'})),
            _node(3, state='suspended'),
            _node(4, _props(**{'media.class': 'Audio/Sink'})),
        ))
        assert monitor.snapshot == {
            MIC: {'Calls'}, CAMERA: {'Camera'}, LOCATION: set()}

    def test_node_removal_clears_kind(self):
        monitor, seen = self._monitor()
        monitor.feed_data(_dump(_node(1)))
        assert monitor.snapshot[MIC] == {'Calls'}
        monitor.feed_data(json.dumps({'id': 1}))
        assert monitor.snapshot[MIC] == set()
        assert seen[-1][MIC] == set()

    def test_null_info_marks_removal(self):
        monitor, _seen = self._monitor()
        monitor.feed_data(_dump(_node(1)))
        monitor.feed_data(json.dumps({'id': 1, 'type': 'PipeWire:Interface:Node',
                                      'info': None}))
        assert monitor.snapshot[MIC] == set()

    def test_going_idle_removes_stream(self):
        monitor, _seen = self._monitor()
        monitor.feed_data(_dump(_node(1)))
        idle = _node(1, _props(), state='suspended')
        monitor.feed_data(json.dumps(idle))
        assert monitor.snapshot[MIC] == set()

    def test_on_change_only_on_change(self):
        monitor, seen = self._monitor()
        monitor.feed_data(_dump(_node(1)))
        assert len(seen) == 1
        monitor.feed_data(json.dumps(_node(1)))
        assert len(seen) == 1
        monitor.feed_data(json.dumps(_node(
            2, _props(**{'application.name': 'Recorder'}))))
        assert len(seen) == 2
        assert seen[-1][MIC] == {'Calls', 'Recorder'}

    def test_second_dump_array_merges(self):
        monitor, _seen = self._monitor()
        monitor.feed_data(_dump(_node(1)))
        monitor.feed_data(_dump(_node(2, _props(**{
            'application.name': 'Recorder'}))))
        assert monitor.snapshot[MIC] == {'Calls', 'Recorder'}

    def test_location_provider_feeds_snapshot(self):
        monitor, seen = self._monitor(
            location_provider=lambda: ['Maps', 'Maps'])
        monitor.refresh_location()
        assert monitor.snapshot[LOCATION] == {'Maps'}
        assert seen[-1][LOCATION] == {'Maps'}

    def test_location_leaving_clears(self):
        monitor, seen = self._monitor(location_provider=lambda: [])
        monitor.refresh_location()
        assert seen == []
        assert monitor.snapshot[LOCATION] == set()

    def test_snapshot_is_a_copy(self):
        monitor, _seen = self._monitor()
        monitor.feed_data(_dump(_node(1)))
        monitor.snapshot[MIC].add('forged')
        assert monitor.snapshot[MIC] == {'Calls'}

    def test_poll_once_parses_single_shot(self):
        monitor, seen = self._monitor(
            pw_dump_runner=lambda: _dump(_node(9)))
        monitor.poll_once()
        assert monitor.snapshot[MIC] == {'Calls'}
        assert seen[-1][MIC] == {'Calls'}

    def test_poll_once_empty_payload_keeps_state(self):
        monitor, seen = self._monitor(pw_dump_runner=lambda: '')
        monitor.feed_data(_dump(_node(1)))
        before = len(seen)
        monitor.poll_once()
        assert monitor.snapshot[MIC] == {'Calls'}
        assert len(seen) == before

    def test_location_not_asked_while_screen_off(self):
        asked = []
        monitor, _seen = self._monitor(
            location_provider=lambda: asked.append(1) or ['Maps'])
        monitor.set_screen_on(False)
        monitor.refresh_location()
        assert asked == []
        monitor.set_screen_on(True)
        monitor.refresh_location()
        assert asked == [1]

    def test_stop_terminates_the_monitor_child(self, monkeypatch):
        import privacy_monitor
        import subprocess as sp
        procs = []

        def _popen(argv, **kw):
            proc = sp.Popen(['sleep', '30'], stdout=sp.PIPE)
            proc.kw = kw
            procs.append(proc)
            return proc

        monkeypatch.setattr(privacy_monitor.shutil, 'which',
                            lambda name: '/usr/bin/' + name)
        monitor = privacy_monitor.PrivacyMonitor(
            popen_factory=_popen, poll_interval=1.0)
        monitor.start()
        try:
            for _ in range(200):
                if procs:
                    break
                monitor._stop_event.wait(0.01)
            assert procs
            assert procs[0].kw['preexec_fn'] is privacy_monitor._die_with_thread
        finally:
            monitor.stop()
        assert procs[0].poll() is not None

    def test_fallback_tick_gated_by_screen(self):
        calls = []
        monitor, _seen = self._monitor(
            pw_dump_runner=lambda: calls.append(1) or _dump())
        monitor.set_screen_on(False)
        assert monitor._fallback_tick() is False
        assert calls == []
        monitor.set_screen_on(True)
        assert monitor._fallback_tick() is True
        assert len(calls) == 1


class TestIndicatorText:
    def test_empty_snapshot_is_empty(self):
        assert format_indicator({}) == ''
        assert format_indicator(None) == ''
        assert format_indicator({MIC: set(), CAMERA: set(),
                                 LOCATION: set()}) == ''

    def test_only_active_kinds_shown_in_order(self):
        assert format_indicator({CAMERA: {'Camera'}}) == '● cam'
        assert format_indicator({LOCATION: {'Maps'}, MIC: {'Calls'}}) == \
            '● mic · loc'
        assert format_indicator({LOCATION: {'Maps'}, CAMERA: {'C'},
                                 MIC: {'M'}}) == '● mic · cam · loc'

    def test_detail_lists_apps_per_kind(self):
        text = detail_text({MIC: {'Recorder', 'Calls'},
                            CAMERA: set(), LOCATION: {'Maps'}})
        assert text == ('mic in use by: Calls, Recorder\n'
                        'loc in use by: Maps')

    def test_detail_empty_is_empty(self):
        assert detail_text({}) == ''
        assert detail_text(None) == ''


class _Reply:
    """Gio D-Bus replies are a TUPLE of out-args: unpack() -> (value,)."""

    def __init__(self, value):
        self._value = value

    def unpack(self):
        return (self._value,)


class _FakeSyncBus:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def call_sync(self, *args):
        self.calls.append(args)
        return _Reply(self._handler(args))


class TestGeoclueClients:
    def _clients_bus(self):
        def _handler(args):
            method = args[3]
            if method == 'GetClients':
                return ['/org/freedesktop/GeoClue2/Client/1',
                        '/org/freedesktop/GeoClue2/Client/2',
                        '/org/freedesktop/GeoClue2/Client/3']
            path = args[1]
            if path.endswith('/1'):
                return {'DesktopId': 'org.gnome.Maps',
                        'Active': True}
            if path.endswith('/2'):
                return {'DesktopId': 'org.gnome.Weather',
                        'Active': GLib.Variant('b', False)}
            return {'DesktopId': 'org.example.Idle',
                    'Active': False}
        return _FakeSyncBus(_handler)

    def test_only_started_active_clients_listed(self):
        state = _real_quick_actions().LocationState()
        assert state.active_clients(bus=self._clients_bus()) == \
            ['org.gnome.Maps']

    def test_daemon_down_reads_empty(self):
        state = _real_quick_actions().LocationState()
        assert state.active_clients(bus=_FakeSyncBus(
            lambda _args: (_ for _ in ()).throw(Exception('down')))) == []
        assert state.active_clients(bus=_FailBus()) == []

    def test_client_query_never_starts_geoclue(self):
        bus = self._clients_bus()
        state = _real_quick_actions().LocationState()
        state.active_clients(bus=bus)
        from gi.repository import Gio
        assert bus.calls
        assert all(call[6] == Gio.DBusCallFlags.NO_AUTO_START
                   for call in bus.calls)

    def test_duplicate_clients_deduplicated(self):
        def _handler(args):
            if args[3] == 'GetClients':
                return ['/c/1', '/c/2']
            return {'DesktopId': 'org.gnome.Maps', 'Active': True}

        state = _real_quick_actions().LocationState()
        assert state.active_clients(bus=_FakeSyncBus(_handler)) == \
            ['org.gnome.Maps']


class _FailBus:
    def call_sync(self, *args):
        raise Exception('no bus')


def _write_proc_cgroup(proc_root, pid, scope, uid=None):
    uid = os.getuid() if uid is None else uid
    rel = ('user.slice/user-%d.slice/user@%d.service/app.slice/%s'
           % (uid, uid, scope))
    leaf = Path(proc_root) / str(pid)
    leaf.mkdir(parents=True, exist_ok=True)
    (leaf / 'cgroup').write_text('0::/%s\n' % rel, encoding='utf-8')
    return rel


class TestCgroupResolution:
    def test_proc_verified_path(self, tmp_path):
        scope = 'app-xxwm-firefox.desktop-4242.scope'
        rel = _write_proc_cgroup(str(tmp_path / 'proc'), 4242, scope)
        got = find_scope_cgroup(scope, 4242,
                                proc_root=str(tmp_path / 'proc'),
                                cgroup_root=str(tmp_path / 'cg'))
        assert got == str(tmp_path / 'cg') + '/' + rel

    def test_wrong_scope_never_guessed(self, tmp_path):
        _write_proc_cgroup(str(tmp_path / 'proc'), 4242,
                           'app-xxwm-other.desktop-9.scope')
        assert find_scope_cgroup('app-xxwm-firefox.desktop-4242.scope',
                                 4242,
                                 proc_root=str(tmp_path / 'proc'),
                                 cgroup_root=str(tmp_path / 'cg')) is None

    def test_missing_proc_is_none(self, tmp_path):
        assert find_scope_cgroup('app-xxwm-x-1.scope', 4242,
                                 proc_root=str(tmp_path / 'proc'),
                                 cgroup_root=str(tmp_path / 'cg')) is None

    def test_bad_pid_is_none(self, tmp_path):
        assert find_scope_cgroup('s', 0,
                                 proc_root=str(tmp_path / 'proc')) is None
        assert find_scope_cgroup('s', 'nope',
                                 proc_root=str(tmp_path / 'proc')) is None

    def test_v1_hierarchy_lines_parsed(self):
        text = ('2:cpu:/user.slice/user-1000.slice/app.slice/x.scope\n'
                '0::/user.slice/user-1000.slice/app.slice/x.scope\n')
        assert parse_proc_cgroup(text) == [
            '/user.slice/user-1000.slice/app.slice/x.scope',
            '/user.slice/user-1000.slice/app.slice/x.scope']

    def test_expected_relpath_shape(self):
        assert expected_scope_relpath(1000, 'x.scope') == \
            'user.slice/user-1000.slice/user@1000.service/app.slice/x.scope'


class TestPermissionStore:
    def _lookup_bus(self, store):
        def _handler(args):
            assert args[0] == app_permissions.PERMISSION_STORE_BUS
            assert args[1] == app_permissions.PERMISSION_STORE_PATH
            assert args[2] == app_permissions.PERMISSION_STORE_IFACE
            if args[3] != 'Lookup':
                return None
            params = args[4]
            assert params.get_type_string() == '(ss)'
            return dict(store)
        return _FakeSyncBus(_handler)

    def test_lookup_returns_asas_map(self):
        bus = self._lookup_bus({'org.mozilla.firefox': ['yes'],
                                'org.gnome.Calls': ['no']})
        assert lookup_permissions(bus, 'devices', 'camera') == {
            'org.mozilla.firefox': ['yes'], 'org.gnome.Calls': ['no']}

    def test_lookup_unpacks_variant_values(self):
        bus = self._lookup_bus(
            {'org.mozilla.firefox': GLib.Variant('as', ['yes'])})
        assert lookup_permissions(bus, 'devices',
                                  'microphone') == \
            {'org.mozilla.firefox': ['yes']}

    def test_lookup_unknown_reads_empty(self):
        assert lookup_permissions(self._lookup_bus({}), 'devices',
                                  'camera') == {}

    def test_set_permission_shape(self):
        bus = _FakeSyncBus(lambda _args: None)
        set_permission(bus, 'devices', 'camera', 'org.mozilla.firefox',
                       ['yes'])
        args = bus.calls[0]
        assert args[3] == 'SetPermission'
        assert args[4].get_type_string() == '(sbssas)'
        assert args[4].unpack() == ('devices', True, 'camera',
                                    'org.mozilla.firefox', ['yes'])

    def test_delete_permission_shape(self):
        bus = _FakeSyncBus(lambda _args: None)
        delete_permission(bus, 'devices', 'camera', 'org.mozilla.firefox')
        args = bus.calls[0]
        assert args[3] == 'DeletePermission'
        assert args[4].get_type_string() == '(sss)'
        assert args[4].unpack() == ('devices', 'camera',
                                    'org.mozilla.firefox')

    def test_device_grant_deny_ask(self):
        bus = self._lookup_bus({'org.mozilla.firefox': ['yes'],
                                'org.gnome.Calls': ['no']})
        assert get_device_permission(bus, 'camera',
                                     'org.mozilla.firefox.desktop') is True
        assert get_device_permission(bus, 'camera',
                                     'org.gnome.Calls.desktop') is False
        assert get_device_permission(bus, 'camera', 'unknown.app') is None

    def test_set_device_true_false_none(self):
        bus = _FakeSyncBus(lambda _args: None)
        set_device_permission(bus, 'microphone', 'org.example.App', True)
        assert bus.calls[-1][3] == 'SetPermission'
        assert bus.calls[-1][4].unpack()[4] == ['yes']
        set_device_permission(bus, 'microphone', 'org.example.App', False)
        assert bus.calls[-1][4].unpack()[4] == ['no']
        set_device_permission(bus, 'microphone', 'org.example.App', None)
        assert bus.calls[-1][3] == 'DeletePermission'

    def test_location_grant_and_reset(self):
        bus = self._lookup_bus({'org.gnome.Maps': ['yes']})
        assert get_location_permission(bus, 'org.gnome.Maps') is True
        assert get_location_permission(bus, 'someone.else') is None
        set_location_permission(bus, 'org.gnome.Maps', None)
        assert bus.calls[-1][3] == 'DeletePermission'

    def test_permission_allowed_table(self):
        assert permission_allowed(['yes']) is True
        assert permission_allowed(['no']) is False
        assert permission_allowed(['ask']) is None
        assert permission_allowed([]) is None
        assert permission_allowed(None) is None

    def test_location_table_constants(self):
        assert app_permissions.DEVICES_TABLE == 'devices'
        assert app_permissions.LOCATION_TABLE == 'location'
        assert app_permissions.LOCATION_ID == 'location'


class _FakeAsyncBus:
    """systemd session bus stand-in: async call() completes at once.

    The callback gets exactly (conn, result) — no user_data, per the
    runtime traps — and call_finish succeeds.
    """

    def __init__(self):
        self.calls = []

    def call(self, *args):
        self.calls.append(args[3])
        callback = args[-1]
        callback(self, object())

    def call_finish(self, _result):
        return '/org/freedesktop/systemd1/job/1'


class _FailingAsyncBus(_FakeAsyncBus):
    def call_finish(self, _result):
        raise Exception('unit failed')


class TestNetworkEnforcement:
    def _controller(self, tmp_path, **kwargs):
        import app_standby
        from app_standby import scope_name_for
        pid = 4242
        scope = scope_name_for('org.example.App.desktop', pid)
        _write_proc_cgroup(str(tmp_path / 'proc'), pid, scope)
        kwargs.setdefault('session_bus_factory', _FakeAsyncBus)
        kwargs.setdefault('network_denied', ['org.example.App'])
        kwargs.setdefault('proc_root', str(tmp_path / 'proc'))
        kwargs.setdefault('cgroup_root', str(tmp_path / 'cg'))
        controller = app_standby.StandbyController(config=None, **kwargs)
        return controller, pid, scope

    def test_scope_placed_then_block_then_run(self, tmp_path):
        order = []
        bus = _FakeAsyncBus()
        orig_call = bus.call

        def _recording_call(*args):
            order.append('scope-placed')
            return orig_call(*args)

        bus.call = _recording_call
        blocked = []

        def _blocker(path, allowed):
            order.append('network-block')
            blocked.append((path, allowed))

        controller, pid, scope = self._controller(
            tmp_path, session_bus_factory=lambda: bus,
            network_blocker=_blocker)
        controller.track('org.example.App.desktop', pid)
        assert order == ['scope-placed', 'network-block']
        assert blocked == [(controller.cgroup_path_for(
            'org.example.App.desktop'), False)]
        uid = os.getuid()
        assert blocked[0][0] == (
            str(tmp_path / 'cg')
            + '/user.slice/user-%d.slice/user@%d.service/app.slice/%s'
            % (uid, uid, scope))

    def test_allowed_app_not_blocked(self, tmp_path):
        blocked = []
        controller, pid, _scope = self._controller(
            tmp_path, network_denied=[],
            network_blocker=lambda p, a: blocked.append((p, a)))
        controller.track('org.example.App.desktop', pid)
        assert blocked == []
        assert controller.cgroup_path_for(
            'org.example.App.desktop') is not None

    def test_failed_placement_blocks_nothing(self, tmp_path):
        blocked = []
        controller, pid, _scope = self._controller(
            tmp_path, session_bus_factory=_FailingAsyncBus,
            network_blocker=lambda p, a: blocked.append((p, a)))
        controller.track('org.example.App.desktop', pid)
        assert blocked == []
        assert controller.scope_names_for('org.example.App.desktop') == {}

    def test_scope_lookup_and_unknown_app(self, tmp_path):
        controller, pid, scope = self._controller(tmp_path)
        controller.track('org.example.App.desktop', pid)
        assert controller.scope_names_for(
            'org.example.App.desktop') == {pid: scope}
        assert controller.scope_names_for('unknown.app') == {}
        assert controller.cgroup_path_for('unknown.app') is None

    def test_enforce_policy_helper(self):
        calls = []
        record = lambda p, a: calls.append((p, a))  # noqa: E731
        assert enforce_network_policy('a.b', '/cg/x', ['a.b'], record) is True
        assert enforce_network_policy('a.b', None, ['a.b'], record) is False
        assert enforce_network_policy('a.b', '/cg/x', [], record) is False
        assert enforce_network_policy('a.b.desktop', '/cg/x', ['A.B'],
                                      record) is True
        assert calls == [('/cg/x', False), ('/cg/x', False)]

    def test_is_network_denied_normalizes(self):
        assert is_network_denied('Org.Example.App.desktop',
                                 ['org.example.app']) is True
        assert is_network_denied('other.app', ['org.example.app']) is False


class TestNetworkDeniedConfig:
    def test_default_empty(self, tmp_path):
        from config import DEFAULT_CONFIG, ShellConfig
        assert DEFAULT_CONFIG['network_denied_apps'] == []
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        assert config.network_denied_apps == []

    def test_roundtrip(self, tmp_path):
        from config import ShellConfig
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        config.set_network_denied_apps(['org.example.App'])
        assert config.network_denied_apps == ['org.example.App']
        reloaded = ShellConfig()
        reloaded.config_dir = tmp_path / 'config'
        reloaded.config_path = reloaded.config_dir / 'config.json'
        reloaded.load()
        assert reloaded.network_denied_apps == ['org.example.App']
