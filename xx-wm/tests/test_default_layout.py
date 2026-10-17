"""Tests for default_layout.py - pure logic via injected fake resolvers."""
from pathlib import Path

import pytest

# Add launcher/src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from default_layout import (
    STOCK_HIDDEN_APPS,
    DefaultLayoutResolver,
    apply_default_layout,
    remap_superseded_home,
    seed_default_layout,
)

_ALL_APPS = {
    'org.gnome.Notes',
    'com.audiobookshelf.app',
    'waydroid.com.google.android.apps.youtube.music',
    'sm.puri.Chatty',
    'org.gnome.Geary',
    'waydroid.com.synology.dschat',
    'waydroid.cz.acrobits.softphone.cloudphone',
    'org.gnome.Calendar',
    'org.gnome.Calculator',
    'org.postmarketos.Megapixels',
    'waydroid.com.synology.projectkailash',
}


def _full_resolver() -> DefaultLayoutResolver:
    return DefaultLayoutResolver(
        find_desktop=lambda x: x if x in _ALL_APPS else None,
        find_cmd=lambda x: x == ['piercing-note'],
        find_browser=lambda: ('org.mozilla.firefox.desktop', 'Firefox'),
        find_by_name=lambda name: 'skippy-pwa.desktop' if name == 'Skippy' else None,
    )


def _empty_resolver() -> DefaultLayoutResolver:
    return DefaultLayoutResolver(
        find_desktop=lambda x: None,
        find_cmd=lambda x: False,
        find_browser=lambda: None,
        find_by_name=lambda name: None,
    )


class FakeConfig:
    def __init__(self, slots=None, applied=False):
        self._slots = slots or []
        self._applied = applied
        self._hidden = []
        self._labels = {}
        self.writes = 0

    @property
    def home_slots(self):
        return self._slots

    def set_home_slots(self, slots):
        self._slots = slots
        self.writes += 1

    @property
    def default_layout_applied(self):
        return self._applied

    def set_default_layout_applied(self, value):
        self._applied = value

    @property
    def hidden_apps(self):
        return list(self._hidden)

    def set_hidden_apps(self, app_ids):
        self._hidden = list(app_ids)
        self.writes += 1

    def set_app_label(self, app_id, label):
        self._labels[app_id] = label
        self.writes += 1


class FakeGestures:
    def __init__(self):
        self.bindings = {}

    def set(self, gesture, action):
        self.bindings[gesture] = action


class TestResolver:
    def test_full_resolution_order_and_members(self):
        slots = _full_resolver().resolve_slots()
        assert [s['label'] for s in slots] == ['Notes', 'Audio', 'Comms', 'Calendar', 'Tools']
        assert [s['type'] for s in slots] == ['app', 'folder', 'folder', 'app', 'folder']

        audio = slots[1]
        assert [m['label'] for m in audio['folder']] == ['Audiobook', 'Music']

        comms = slots[2]
        assert [m['label'] for m in comms['folder']] == ['Phone', 'Text', 'Email', 'Chat', 'SoftPhone']
        assert comms['folder'][0]['app_id'] is None  # built-in dialer

        tools = slots[4]
        assert [m['label'] for m in tools['folder']] == ['Firefox', 'Calculator', 'Camera', 'Photos']

    def test_seeded_labels_become_renames(self):
        resolver = _full_resolver()
        resolver.resolve_slots()
        assert resolver.labels['org.gnome.Calendar'] == 'Calendar'
        assert resolver.labels['com.audiobookshelf.app'] == 'Audiobook'
        assert resolver.labels['org.mozilla.firefox.desktop'] == 'Firefox'

    def test_notes_prefers_piercing_note_cmd(self):
        notes = _full_resolver().resolve_notes()
        assert notes == {'type': 'app', 'label': 'Notes', 'cmd': ['piercing-note']}

    def test_no_apps_leaves_only_comms_with_builtin_phone(self):
        slots = _empty_resolver().resolve_slots()
        assert [s['label'] for s in slots] == ['Comms']
        assert [m['label'] for m in slots[0]['folder']] == ['Phone']

    def test_unresolved_members_are_skipped_without_gaps(self):
        resolver = DefaultLayoutResolver(
            find_desktop=lambda x: x if x == 'org.gnome.Calculator' else None,
            find_cmd=lambda x: False,
            find_browser=lambda: None,
            find_by_name=lambda name: None,
        )
        slots = resolver.resolve_slots()
        assert [s['label'] for s in slots] == ['Comms', 'Tools']
        assert [m['label'] for m in slots[1]['folder']] == ['Calculator']

    def test_phone_and_text_bind_system_telephony_apps(self):
        apps = _ALL_APPS | {'org.gnome.Calls'}
        resolver = DefaultLayoutResolver(
            find_desktop=lambda x: x if x in apps else None,
            find_cmd=lambda x: False,
            find_browser=lambda: None,
            find_by_name=lambda name: None,
        )
        comms = resolver.resolve_comms_folder()
        phone, text = comms['folder'][0], comms['folder'][1]
        assert phone == {'label': 'Phone', 'app_id': 'org.gnome.Calls'}
        assert text == {'label': 'Text', 'app_id': 'sm.puri.Chatty'}
        assert resolver.labels['org.gnome.Calls'] == 'Phone'
        assert resolver.labels['sm.puri.Chatty'] == 'Text'

    def test_skippy_resolved_by_label(self):
        assert _full_resolver().resolve_skippy() == 'skippy-pwa.desktop'
        assert _empty_resolver().resolve_skippy() is None


class TestSeedDefaultLayout:
    def test_never_overwrites_existing_slots(self):
        existing = [{'type': 'app', 'label': 'Mine', 'app_id': 'mine.desktop'}]
        config = FakeConfig(slots=existing)
        assert seed_default_layout(config, resolver=_full_resolver()) == existing


class TestApplyDefaultLayout:
    def test_full_apply(self):
        config = FakeConfig()
        gestures = FakeGestures()
        apply_default_layout(config, gestures, resolver=_full_resolver())

        assert [s['label'] for s in config.home_slots] == ['Notes', 'Audio', 'Comms', 'Calendar', 'Tools']
        assert config._labels['org.gnome.Calendar'] == 'Calendar'
        assert config.hidden_apps == STOCK_HIDDEN_APPS
        assert gestures.bindings == {
            'swipe_left_home': 'launch:skippy-pwa.desktop',
            'swipe_right_home': 'camera'}
        assert config.default_layout_applied is True

    def test_flag_set_even_when_nothing_resolves(self):
        config = FakeConfig()
        apply_default_layout(config, resolver=_empty_resolver())
        assert config.default_layout_applied is True

    def test_no_writes_when_flag_already_set(self):
        config = FakeConfig(applied=True)
        apply_default_layout(config, resolver=_full_resolver())
        assert config.writes == 0
        assert config.home_slots == []

    def test_existing_slots_only_get_flag(self):
        existing = [{'type': 'app', 'label': 'Mine', 'app_id': 'mine.desktop'}]
        config = FakeConfig(slots=existing)
        gestures = FakeGestures()
        apply_default_layout(config, gestures, resolver=_full_resolver())
        assert config.home_slots == existing
        assert config.writes == 0
        assert gestures.bindings == {}
        assert config.default_layout_applied is True

    def test_no_skippy_leaves_gesture_unbound(self):
        config = FakeConfig()
        gestures = FakeGestures()
        resolver = DefaultLayoutResolver(
            find_desktop=lambda x: None,
            find_cmd=lambda x: False,
            find_browser=lambda: None,
            find_by_name=lambda name: None,
        )
        apply_default_layout(config, gestures, resolver=resolver)
        # Left stays unbound (its default app opens); right is the camera,
        # as the Android seeder sets it.
        assert gestures.bindings == {'swipe_right_home': 'camera'}


class TestRemapSupersededHome:
    """Startup only remaps superseded stock apps; it never drops a pin."""

    class _Idx:
        def __init__(self, mapping):
            self.mapping = mapping

        def replacement_for(self, app_id):
            return self.mapping.get(app_id)

    def _comms(self):
        return [
            {'type': 'app', 'label': 'Gone', 'app_id': 'missing.desktop'},
            {'type': 'folder', 'label': 'Comms', 'folder': [
                {'label': 'Phone', 'app_id': 'org.gnome.Calls.desktop'},
                {'label': 'Text', 'app_id': 'sm.puri.Chatty.desktop'},
            ]},
        ]

    def test_superseded_members_are_remapped_not_dropped(self):
        config = FakeConfig(slots=self._comms(), applied=True)
        idx = self._Idx({'org.gnome.Calls.desktop': 'io.piercingxx.XXDialer.desktop',
                         'sm.puri.Chatty.desktop': 'io.piercingxx.TxxT.desktop'})
        assert remap_superseded_home(config, idx) is True
        labels = [s['label'] for s in config.home_slots]
        assert labels == ['Gone', 'Comms']  # nothing dropped, unresolved kept
        folder = config.home_slots[1]['folder']
        assert [m['label'] for m in folder] == ['Phone', 'Text']
        assert [m['app_id'] for m in folder] == [
            'io.piercingxx.XXDialer.desktop', 'io.piercingxx.TxxT.desktop']

    def test_write_only_when_something_was_remapped(self):
        config = FakeConfig(slots=self._comms(), applied=True)
        assert remap_superseded_home(config, self._Idx({})) is False
        assert config.writes == 0
        assert config.home_slots == self._comms()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
