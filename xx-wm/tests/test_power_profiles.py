"""Power profiles that work on phones (wm_power_night lane).

Driver detection, governor mapping/fallbacks, the helper allowlist,
persistence, and the tile/row state — all on fake sysfs with a fake
helper. No D-Bus, no root, no network.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))

import power_profiles
import xx_wm_helper as helper

# Page-first: settings_page imports settings_display while it
# initializes, so the page must win the import race (pre-existing cycle;
# the full suite only passed because collection imported another module
# first).
import settings_page  # noqa: F401
import settings_display  # noqa: F401

FLX1_GOVERNORS = 'ondemand userspace powersave performance schedutil'


class FakeConfig:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


@pytest.fixture()
def cpufreq(tmp_path):
    """Fake FLX1-style cpufreq tree under a fake sysfs root."""
    root = tmp_path / 'sys' / 'devices' / 'system' / 'cpu' / 'cpufreq'

    def add_policy(name, available, governor):
        policy = root / name
        policy.mkdir(parents=True, exist_ok=True)
        (policy / 'scaling_available_governors').write_text(
            available + '\n', encoding='utf-8')
        (policy / 'scaling_governor').write_text(
            governor + '\n', encoding='utf-8')
        return policy

    add_policy('policy0', FLX1_GOVERNORS, 'schedutil')
    add_policy('policy4', FLX1_GOVERNORS, 'schedutil')
    return str(tmp_path / 'sys')


def make_core(sysfs, state):
    return helper.HelperCore(sysfs=sysfs,
                             state_file=str(state))


def ppd_props(*entries):
    return {'ActiveProfile': 'balanced',
            'Profiles': [dict(entry) for entry in entries]}


# ------------------------------------------------------- driver detection


class TestDriverDetection:
    def test_placeholder_only_is_not_real(self):
        props = ppd_props(
            {'Profile': 'power-saver', 'CpuDriver': 'placeholder'},
            {'Profile': 'balanced', 'CpuDriver': 'placeholder'},
            {'Profile': 'performance', 'CpuDriver': 'placeholder'})
        assert power_profiles.has_real_driver(props) is False
        assert power_profiles.select_backend(
            'net.hadess.PowerProfiles', props, True) == 'helper'
        assert power_profiles.select_backend(
            'net.hadess.PowerProfiles', props, False) == 'none'

    def test_real_driver_wins(self):
        props = ppd_props(
            {'Profile': 'power-saver', 'CpuDriver': 'placeholder',
             'PlatformDriver': 'placeholder'},
            {'Profile': 'balanced', 'CpuDriver': 'amd_pstate',
             'PlatformDriver': 'placeholder'},
            {'Profile': 'performance', 'CpuDriver': 'amd_pstate',
             'PlatformDriver': 'placeholder'})
        assert power_profiles.has_real_driver(props) is True
        assert power_profiles.select_backend(
            'net.hadess.PowerProfiles', props, True) == 'ppd'

    def test_single_real_driver_counts(self):
        props = ppd_props(
            {'Profile': 'balanced', 'CpuDriver': 'placeholder'},
            {'Profile': 'performance', 'CpuDriver': 'intel_pstate'})
        assert power_profiles.has_real_driver(props) is True

    def test_empty_profiles_unusable(self):
        assert power_profiles.has_real_driver({'Profiles': []}) is False
        assert power_profiles.has_real_driver({}) is False
        assert power_profiles.has_real_driver(None) is False
        assert power_profiles.select_backend(
            'net.hadess.PowerProfiles', {}, True) == 'helper'
        assert power_profiles.select_backend(None, {}, False) == 'none'

    def test_no_driver_detail_assumes_real(self):
        props = ppd_props({'Profile': 'balanced'})
        assert power_profiles.has_real_driver(props) is True

    def test_no_daemon_uses_helper(self):
        props = ppd_props(
            {'Profile': 'balanced', 'CpuDriver': 'placeholder'})
        assert power_profiles.select_backend(None, props, True) == 'helper'
        assert power_profiles.select_backend(None, props, False) == 'none'


# ------------------------------------------------- governor mapping


class TestGovernorMapping:
    @pytest.mark.parametrize(('profile', 'governor'), [
        ('power-saver', 'powersave'),
        ('balanced', 'schedutil'),
        ('performance', 'performance'),
    ])
    def test_flx1_mapping(self, cpufreq, tmp_path, profile, governor):
        core = make_core(cpufreq, tmp_path / 'state.json')
        assert core.set_power_profile(profile) == profile
        for policy in ('policy0', 'policy4'):
            text = Path(cpufreq, 'devices/system/cpu/cpufreq',
                        policy, 'scaling_governor').read_text()
            assert text == governor + '\n'

    def test_fallback_governors(self, tmp_path):
        root = tmp_path / 'sys' / 'devices' / 'system' / 'cpu' / 'cpufreq'
        policy = root / 'policy0'
        policy.mkdir(parents=True)
        (policy / 'scaling_available_governors').write_text(
            'conservative ondemand\n', encoding='utf-8')
        (policy / 'scaling_governor').write_text(
            'ondemand\n', encoding='utf-8')
        core = make_core(str(tmp_path / 'sys'), tmp_path / 's.json')
        assert core.set_power_profile('power-saver') == 'power-saver'
        assert (policy / 'scaling_governor').read_text() == 'conservative\n'
        assert core.set_power_profile('balanced') == 'balanced'
        assert (policy / 'scaling_governor').read_text() == 'ondemand\n'

    def test_unoffered_governor_leaves_policy_alone(self, tmp_path):
        root = tmp_path / 'sys' / 'devices' / 'system' / 'cpu' / 'cpufreq'
        policy = root / 'policy0'
        policy.mkdir(parents=True)
        (policy / 'scaling_available_governors').write_text(
            'ondemand\n', encoding='utf-8')
        (policy / 'scaling_governor').write_text(
            'ondemand\n', encoding='utf-8')
        core = make_core(str(tmp_path / 'sys'), tmp_path / 's.json')
        assert core.set_power_profile('performance') == 'performance'
        assert (policy / 'scaling_governor').read_text() == 'ondemand\n'

    def test_policy_without_offer_file_skipped(self, cpufreq, tmp_path):
        bare = Path(cpufreq, 'devices/system/cpu/cpufreq/policy4',
                    'scaling_available_governors')
        bare.unlink()
        core = make_core(cpufreq, tmp_path / 'state.json')
        assert core.set_power_profile('balanced') == 'balanced'
        assert Path(cpufreq, 'devices/system/cpu/cpufreq/policy0',
                    'scaling_governor').read_text() == 'schedutil\n'

    def test_unknown_profile_rejected(self, cpufreq, tmp_path):
        core = make_core(cpufreq, tmp_path / 'state.json')
        for bad in ('powersave', 'performance;reboot', '', None, 42):
            with pytest.raises(helper.HelperError) as exc:
                core.set_power_profile(bad)
            assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_no_policies_fails(self, tmp_path):
        core = make_core(str(tmp_path / 'nosys'), tmp_path / 's.json')
        with pytest.raises(helper.HelperError) as exc:
            core.set_power_profile('balanced')
        assert exc.value.dbus_name == helper.ERROR_FAILED

    def test_governor_for_profile_pure(self):
        assert helper.HelperCore.governor_for_profile(
            'power-saver', ['conservative']) == 'conservative'
        assert helper.HelperCore.governor_for_profile(
            'balanced', ['ondemand']) == 'ondemand'
        assert helper.HelperCore.governor_for_profile(
            'performance', ['powersave']) is None
        assert helper.HelperCore.governor_for_profile(
            'balanced', []) is None


# ------------------------------------------------------- helper allowlist


class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def test_policy_for_allowlists_profile_non_interactive():
    assert helper.policy_for(
        'SetPowerProfile', ('balanced',)) == (helper.ACTION_POWER, False)
    assert helper.policy_for(
        'SetPowerProfile', ('powersave',)) is None
    assert helper.policy_for('SetPowerProfile', ()) is None


def test_service_dispatches_set_power_profile(cpufreq, tmp_path):
    core = make_core(cpufreq, tmp_path / 'state.json')
    service = helper.HelperService(
        connection=None, core=core,
        authorize=lambda _c, _s, _a, _i, cb: cb(True, 1000, None))
    invocation = FakeInvocation()
    service._on_authorized(invocation, 'SetPowerProfile',
                           ('power-saver',), True, 1000, None)
    assert invocation.error is None
    assert Path(cpufreq, 'devices/system/cpu/cpufreq/policy0',
                'scaling_governor').read_text() == 'powersave\n'


def test_service_rejects_bad_profile_before_core(cpufreq, tmp_path):
    core = make_core(cpufreq, tmp_path / 'state.json')
    service = helper.HelperService(
        connection=None, core=core,
        authorize=lambda _c, _s, _a, _i, cb: cb(True, 1000, None))
    invocation = FakeInvocation()
    service._on_authorized(invocation, 'SetPowerProfile',
                           ('powersave',), True, 1000, None)
    assert invocation.error is not None
    assert invocation.error[0] == helper.ERROR_INVALID_ARGS


def test_introspection_lists_set_power_profile():
    assert '<method name="SetPowerProfile">' in helper.INTROSPECT_XML


def test_polkit_action_is_allow_active():
    text = (Path(__file__).parent.parent / 'launcher' / 'data'
            / 'polkit-1' / 'actions'
            / 'io.piercingxx.XXWM.Helper.policy').read_text()
    assert 'action id="io.piercingxx.XXWM.Helper1.power-profile"' in text


def test_helper_client_sends_profile(monkeypatch):
    import helper_client
    seen = {}

    def fake_call(method, params, on_done):
        seen['method'] = method
        seen['params'] = params.unpack() if params is not None else ()
        on_done(True, ())

    monkeypatch.setattr(helper_client, '_call_helper', fake_call)
    done = []
    helper_client.set_power_profile(
        'performance', lambda ok, _res: done.append(ok))
    assert seen['method'] == 'SetPowerProfile'
    assert seen['params'] == ('performance',)
    assert done == [True]


# ------------------------------------------------------------ persistence


class TestPersistence:
    def test_choice_persists_and_reapplies(self, cpufreq, tmp_path):
        state = tmp_path / 'state.json'
        make_core(cpufreq, state).set_power_profile('power-saver')
        saved = json.loads(state.read_text())
        assert saved['power_profile'] == 'power-saver'
        for policy in ('policy0', 'policy4'):
            Path(cpufreq, 'devices/system/cpu/cpufreq', policy,
                 'scaling_governor').write_text('performance\n')
        applied = make_core(cpufreq, state).apply_saved()
        assert applied['errors'] == []
        assert applied['power_profile'] == 'power-saver'
        for policy in ('policy0', 'policy4'):
            assert Path(cpufreq, 'devices/system/cpu/cpufreq', policy,
                        'scaling_governor').read_text() == 'powersave\n'

    def test_corrupt_profile_degrades_to_none(self, tmp_path):
        state = tmp_path / 's.json'
        state.write_text(json.dumps({'power_profile': 'powersave'}),
                         encoding='utf-8')
        core = make_core(str(tmp_path), state)
        assert core.load_state()['power_profile'] is None

    def test_profile_leg_failure_skips_only_itself(self, tmp_path):
        root = tmp_path / 'sys' / 'devices' / 'system' / 'cpu' / 'cpufreq'
        (root / 'policy0').mkdir(parents=True)
        (root / 'policy0' / 'scaling_available_governors').write_text(
            'powersave\n', encoding='utf-8')
        (root / 'policy0' / 'scaling_governor').write_text(
            'powersave\n', encoding='utf-8')
        state = tmp_path / 's.json'
        state.write_text(json.dumps({'charge_limit': 80,
                                     'power_profile': 'balanced'}),
                         encoding='utf-8')
        core = make_core(str(tmp_path / 'sys'), state)
        applied = core.apply_saved()
        assert applied['power_profile'] == 'balanced'
        assert any('charge' in err.lower() or 'battery' in err.lower()
                   for err in applied['errors'])

    def test_shell_effective_profile(self):
        assert power_profiles.effective_profile(
            FakeConfig({'power_profile': 'performance'}), False) \
            == 'performance'
        assert power_profiles.effective_profile(
            FakeConfig({'power_profile': 'performance'}), True) \
            == 'power-saver'
        assert power_profiles.effective_profile(
            FakeConfig({}), False) == 'balanced'
        assert power_profiles.configured_profile(
            FakeConfig({'power_profile': 'powersave'})) == ''

    def test_available_choices(self):
        assert power_profiles.available_choices(
            'ppd', ['balanced', 'performance', 'bogus']) == [
            'balanced', 'performance']
        assert power_profiles.available_choices('helper', []) == list(
            power_profiles.PROFILES)
        assert power_profiles.available_choices('none', []) == []

    def test_current_label(self):
        assert power_profiles.current_label(
            'performance', FakeConfig({'power_profile': 'balanced'}),
            False) == 'performance'
        assert power_profiles.current_label(
            None, FakeConfig({'power_profile': 'balanced'}),
            True) == 'power-saver'
        assert power_profiles.current_label(
            None, FakeConfig({'power_profile': 'balanced'}),
            False) == 'balanced'
        assert power_profiles.current_label(
            None, FakeConfig({}), False) == 'Unknown'
        assert power_profiles.current_label(
            'bogus', FakeConfig({}), False) == 'Unknown'


# ------------------------------------------------------- tile / row state


class TestTileAndRowState:
    def test_saver_edges_notify_power_hook(self):
        import app_standby
        seen = []
        ctl = app_standby.StandbyController(
            config=None, on_saver_changed=seen.append)
        ctl.set_saver(True)
        ctl.set_saver(True)
        ctl.set_saver(False)
        assert seen == [True, False]

    def test_saver_selects_power_saver(self):
        import app_standby
        seen = []
        ctl = app_standby.StandbyController(
            config=None, on_saver_changed=seen.append)
        ctl.set_saver(True)
        assert power_profiles.effective_profile(
            FakeConfig({'power_profile': 'performance'}),
            ctl.saver_effective) == 'power-saver'

    def test_display_row_prefers_live_state(self):
        import app_standby
        section = settings_display.DisplaySection()
        win = SimpleNamespace(
            config=FakeConfig({'power_profile': 'balanced'}),
            standby=app_standby.StandbyController(config=None))
        section._active_profile = 'performance'
        assert section._current_profile(win) == 'performance'
        win.standby.set_saver(True)
        assert section._current_profile(win) == 'power-saver'
        win.standby.set_saver(False)
        section._active_profile = None
        assert section._current_profile(win) == 'balanced'

    def test_on_profile_persists_and_routes_to_helper(self, monkeypatch):
        section = settings_display.DisplaySection()
        section._backend = 'helper'
        section._profile_name = None
        section._profile_row = object()
        section._status = None
        win = SimpleNamespace(
            config=FakeConfig({'power_profile': 'balanced'}),
            standby=None)
        calls = []
        monkeypatch.setattr(
            settings_display, 'apply_profile_async',
            lambda profile, backend, bus, on_done: (
                calls.append((profile, backend, bus)), on_done(True)))
        section._on_profile(win, 'performance')
        assert win.config.data['power_profile'] == 'performance'
        assert calls == [('performance', 'helper', None)]

    def test_on_profile_rejects_unoffered(self, monkeypatch):
        section = settings_display.DisplaySection()
        section._backend = 'helper'
        section._profile_row = object()
        section._status = None
        win = SimpleNamespace(
            config=FakeConfig({'power_profile': 'balanced'}))
        called = []
        monkeypatch.setattr(
            settings_display, 'apply_profile_async',
            lambda *args: called.append(args))
        section._on_profile(win, 'powersave')
        assert win.config.data['power_profile'] == 'balanced'
        assert called == []

    def test_battery_row_appends_live_profile(self):
        win = SimpleNamespace(
            config=FakeConfig({'power_profile': 'balanced'}),
            standby=None)
        text = settings_page.BatterySection._with_profile(
            win, '87% — discharging', {'active': 'performance'})
        assert text == '87% — discharging · performance'
        assert settings_page.BatterySection._with_profile(
            win, '87% — discharging', {'active': None}) == \
            '87% — discharging · balanced'
        assert settings_page.BatterySection._with_profile(
            win, None, {'active': 'balanced'}) is None

    def test_cpufreq_present_on_fake_sysfs(self, cpufreq, tmp_path):
        root = str(Path(cpufreq, 'devices/system/cpu/cpufreq'))
        assert power_profiles.cpufreq_present(root) is True
        assert power_profiles.cpufreq_present(
            str(tmp_path / 'missing')) is False
        policies = power_profiles.cpufreq_policies(root)
        assert len(policies) == 2
        assert power_profiles.read_available_governors(
            policies[0]) == FLX1_GOVERNORS.split()
        assert power_profiles.read_governor(policies[0]) == 'schedutil'
