"""config.json robustness: a corrupt file is moved aside and never
overwritten, junk home slots never reach the UI, defaults are never
aliased and gestures.json is written atomically. Pure logic, no display."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))

import config as config_mod  # noqa: E402
from config import DEFAULT_CONFIG, HELPER_PIN_MARKER, ShellConfig  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _config_dir() -> Path:
    path = Path.home() / '.config' / 'xx-wm'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_raw(data: bytes) -> Path:
    path = _config_dir() / 'config.json'
    path.write_bytes(data)
    return path


def _corrupt_copies() -> list[Path]:
    return sorted(_config_dir().glob('config.json.corrupt-*'))


class TestCorruptConfig:
    def test_unparsable_file_is_moved_aside_and_kept(self):
        raw = b'{"theme": "ocean", "pin_hash": "pbkdf2$1$aa$bb",'
        path = _write_raw(raw)
        config = ShellConfig()
        assert config.load_failed
        assert not path.exists()
        copies = _corrupt_copies()
        assert len(copies) == 1
        assert copies[0].read_bytes() == raw

    def test_save_refuses_after_a_failed_load(self):
        _write_raw(b'not json at all')
        config = ShellConfig()
        config.data['theme'] = 'ocean'
        config.save()
        config.set_theme('graphite')
        assert not (_config_dir() / 'config.json').exists()

    def test_undecodable_bytes_do_not_raise(self):
        raw = b'\xff\xfe\x00{garbage'
        _write_raw(raw)
        config = ShellConfig()
        assert config.load_failed
        assert config.data['theme'] == DEFAULT_CONFIG['theme']
        assert _corrupt_copies()[0].read_bytes() == raw

    def test_non_object_root_is_a_failed_load(self):
        _write_raw(b'[1, 2, 3]')
        config = ShellConfig()
        assert config.load_failed
        config.save()
        assert not (_config_dir() / 'config.json').exists()

    def test_later_instances_in_the_process_stay_failed(self):
        # main.py reads the font from a throwaway ShellConfig before the
        # window builds its own; the window must not see "no file".
        _write_raw(b'{oops')
        ShellConfig()
        later = ShellConfig()
        assert later.load_failed
        later.save()
        assert not (_config_dir() / 'config.json').exists()

    def test_failed_load_never_seeds_the_default_layout(self):
        from default_layout import apply_default_layout
        _write_raw(b'{oops')
        config = ShellConfig()

        class Resolver:
            labels: dict = {}
            called = False

            def resolve_slots(self):
                Resolver.called = True
                return [{'type': 'app', 'label': 'X', 'app_id': 'x.desktop'}]

            def resolve_skippy(self):
                return None

        apply_default_layout(config, None, Resolver())
        assert not Resolver.called
        assert config.home_slots == []

    def test_a_fixed_file_clears_the_failed_state(self):
        _write_raw(b'{oops')
        config = ShellConfig()
        good = _config_dir() / 'config.json'
        good.write_text(json.dumps({'theme': 'ocean'}), encoding='utf-8')
        fresh = config.read_fresh()
        assert fresh is not None and fresh['theme'] == 'ocean'
        assert not config.load_failed
        config.data = fresh
        config.set_theme('graphite')
        assert json.loads(good.read_text(encoding='utf-8'))['theme'] == 'graphite'

    def test_hot_reload_with_no_file_stays_failed(self):
        _write_raw(b'{oops')
        config = ShellConfig()
        assert config.read_fresh() is None
        assert config.load_failed
        config.save()
        assert not (_config_dir() / 'config.json').exists()

    def test_read_fresh_never_moves_a_file_being_edited(self):
        config = ShellConfig()
        config.set_theme('ocean')
        path = _config_dir() / 'config.json'
        path.write_text('{"theme": "graph', encoding='utf-8')
        before = dict(config.data)
        assert config.read_fresh() is None
        assert path.exists() and _corrupt_copies() == []
        assert config.data == before
        # The half-edited file is not overwritten by the next save either.
        config.save()
        assert path.read_text(encoding='utf-8') == '{"theme": "graph'

    def test_read_fresh_builds_a_new_dict(self):
        config = ShellConfig()
        config.set_theme('ocean')
        live = config.data
        fresh = config.read_fresh()
        assert fresh is not live
        assert fresh == live


class TestPinSurvivesConfigLoss:
    """The PIN lives in the root helper; config.json only carries a marker.
    Losing or corrupting config.json must never unlock the phone."""

    @pytest.fixture()
    def store(self, tmp_path):
        import xx_wm_helper as helper
        return helper.PinStore(root=str(tmp_path / 'pin'),
                               now=lambda: 1_800_000_000.0, scrypt_n=2 ** 4)

    @pytest.fixture()
    def fake_helper(self, store, monkeypatch):
        import helper_client
        uid = 1000

        def has_pin(on_done):
            on_done(True, store.has_pin(uid))

        def verify_pin(pin, on_done):
            on_done(True, store.verify_pin(uid, pin))

        monkeypatch.setattr(helper_client, 'has_pin', has_pin)
        monkeypatch.setattr(helper_client, 'verify_pin', verify_pin)
        monkeypatch.setenv('XX_WM_PIN_HELPER', '1')
        helper_client._pin_logged.clear()
        store.set_pin(uid, '', '246810')
        return helper_client

    @staticmethod
    def _verify(helper_client, config, pin):
        out = []
        helper_client.pin_verify(config, pin, lambda *a: out.append(a))
        assert len(out) == 1
        return out[0]

    @pytest.mark.parametrize('raw', [b'{"pin_hash": "helper$", oops',
                                     b'\xff\xfe junk', None])
    def test_corrupt_or_deleted_config_still_demands_the_pin(
            self, fake_helper, raw):
        if raw is not None:
            _write_raw(raw)
        config = ShellConfig()
        assert config.pin_hash is None
        healed = []
        fake_helper.pin_sync(config, healed.append)
        assert healed == [True]
        assert config.pin_hash == HELPER_PIN_MARKER
        assert self._verify(fake_helper, config, '000000')[0] is False
        assert self._verify(fake_helper, config, '246810')[0] is True

    def test_deleting_config_while_running_keeps_the_marker(self, fake_helper):
        config = ShellConfig()
        config.set_pin_in_helper()
        (_config_dir() / 'config.json').unlink()
        fresh = config.read_fresh()
        assert fresh is not None
        assert fresh['pin_hash'] == HELPER_PIN_MARKER
        config.data = fresh
        assert self._verify(fake_helper, config, '000000')[0] is False

    def test_dropping_the_marker_in_an_edit_keeps_it(self, fake_helper):
        config = ShellConfig()
        config.set_pin_in_helper()
        path = _config_dir() / 'config.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        del data['pin_hash']
        path.write_text(json.dumps(data), encoding='utf-8')
        assert config.read_fresh()['pin_hash'] == HELPER_PIN_MARKER


class TestDefaultsAreNotAliased:
    def test_mutating_live_lists_leaves_defaults_alone(self):
        config = ShellConfig()
        config.data['pinned'].append('x.desktop')
        config.data['widgets']['time']['enabled'] = False
        config.data['app_labels']['a'] = 'b'
        assert DEFAULT_CONFIG['pinned'] == []
        assert DEFAULT_CONFIG['widgets']['time']['enabled'] is True
        assert DEFAULT_CONFIG['app_labels'] == {}

    def test_appending_to_home_slots_leaves_defaults_alone(self):
        config = ShellConfig()
        slots = config.home_slots
        slots.append({'type': 'app', 'label': 'X', 'app_id': 'x.desktop'})
        assert DEFAULT_CONFIG['home_slots'] == []
        assert ShellConfig().home_slots == []

    def test_fresh_reads_do_not_share_nested_defaults(self):
        config = ShellConfig()
        fresh = config.read_fresh()
        fresh['widgets']['date']['order'] = 99
        assert DEFAULT_CONFIG['widgets']['date']['order'] == 2


_JUNK_SLOTS = [
    'firefox.desktop',
    ['firefox.desktop'],
    None,
    {'type': 'app', 'label': None, 'app_id': 'a.desktop'},
    {'type': 'app', 'label': 7, 'app_id': 42, 'cmd': 'echo hi'},
    {'type': 'bogus', 'label': 'B'},
    {'label': 'no type'},
    {'type': 'folder', 'label': 'F',
     'folder': ['x.desktop', None, {'label': None, 'app_id': 'b.desktop'},
                {'label': 'Phone', 'app_id': None, 'cmd': None}]},
    {'type': 'folder', 'label': 'G', 'folder': 'nope'},
    {'type': 'app', 'label': 'Notes', 'cmd': ['piercing-note', 3]},
]


class TestHomeSlotsSanitised:
    def _config(self):
        path = _config_dir() / 'config.json'
        path.write_text(json.dumps({'home_slots': _JUNK_SLOTS}),
                        encoding='utf-8')
        return ShellConfig()

    def test_getter_drops_and_coerces_junk(self):
        slots = self._config().home_slots
        assert [s['type'] for s in slots] == ['app', 'app', 'folder',
                                              'folder', 'app']
        for slot in slots:
            assert isinstance(slot['label'], str)
            assert slot.get('app_id') is None or isinstance(slot['app_id'], str)
            assert slot.get('cmd') is None or isinstance(slot['cmd'], list)
            if slot['type'] == 'folder':
                assert isinstance(slot['folder'], list)
                for member in slot['folder']:
                    assert isinstance(member, dict)
                    assert isinstance(member['label'], str)
        assert slots[0]['label'] == ''
        assert slots[1]['app_id'] == '42' and slots[1]['cmd'] is None
        assert slots[2]['folder'] == [
            {'label': '', 'app_id': 'b.desktop'},
            {'label': 'Phone', 'app_id': None, 'cmd': None}]
        assert slots[3]['folder'] == []
        assert slots[4]['cmd'] == ['piercing-note', '3']

    def test_clean_slots_round_trip_unchanged(self):
        clean = [
            {'type': 'app', 'label': 'A', 'app_id': 'a.desktop',
             'cmd': None, 'folder': None},
            {'type': 'folder', 'label': 'Comms', 'app_id': None, 'cmd': None,
             'folder': [{'label': 'Phone', 'app_id': None, 'cmd': None},
                        {'label': 'Text', 'cmd': ['chatty']}]},
        ]
        config = ShellConfig()
        config.set_home_slots(clean)
        assert config.home_slots == clean

    def test_drawer_survives_junk_slots(self):
        import drawer_page
        config = self._config()
        win = SimpleNamespace(
            config=config,
            app_index=SimpleNamespace(entries=[
                SimpleNamespace(app_id='a.desktop'),
                SimpleNamespace(app_id='b.desktop')]))
        page = drawer_page.DrawerPage(win)
        assert page._home_slot_app_ids() == {'a.desktop', '42', 'b.desktop'}
        folders = page._drawer_folder_slots()
        assert [slot['label'] for _idx, slot, _m in folders] == ['F']

    def test_set_home_slots_never_stores_none_as_a_label(self):
        config = ShellConfig()
        config.set_home_slots([{'type': 'app', 'label': None,
                                'app_id': 'a.desktop'}])
        assert config.data['home_slots'][0]['label'] == ''


class TestGesturesAtomicWrite:
    @pytest.fixture()
    def gestures_path(self, tmp_path, monkeypatch):
        import gesture_config
        path = tmp_path / 'gestures.json'
        monkeypatch.setattr(gesture_config, '_config_path', lambda: path)
        return path

    def test_failed_write_leaves_previous_file_intact(self, gestures_path,
                                                      monkeypatch):
        from gesture_config import GestureConfig
        gc = GestureConfig()
        gc.set('swipe_up_long', 'home')
        before = gestures_path.read_text(encoding='utf-8')

        def crash(*_args, **_kwargs):
            raise OSError('simulated crash mid-write')

        monkeypatch.setattr(os, 'replace', crash)
        with pytest.raises(OSError):
            gc.set('swipe_up_long', 'back')
        assert gestures_path.read_text(encoding='utf-8') == before
        assert list(gestures_path.parent.glob('*.tmp')) == []

    def test_save_round_trips(self, gestures_path):
        from gesture_config import GestureConfig
        GestureConfig().set('swipe_down_top', 'none')
        assert json.loads(gestures_path.read_text(encoding='utf-8'))[
            'swipe_down_top'] == 'none'
        assert GestureConfig().get('swipe_down_top') == 'none'


def test_load_failed_defaults_false_for_bare_instances():
    bare = ShellConfig.__new__(ShellConfig)
    assert bare.load_failed is False
    assert config_mod.ShellConfig.load_failed is False
