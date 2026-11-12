"""Settings Gestures card: system lisgd slots exposed for rebinding.

The four system slots (swipe_up_short/swipe_up_long/swipe_down_top/
swipe_left_edge) accept actions AND IPC verbs in gestures.json; the
card must render verb bindings readably and persist choices through
GestureConfig.set/reset. Menu rendering needs live GTK; the choice-set
and persistence seams are driven on a bare instance, the row wiring is
pinned by source inspection.
"""
import inspect
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


@pytest.fixture(scope='module')
def window():
    def require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError('no layer shell')
        return None

    gi = types.ModuleType('gi')
    gi.require_version = require_version
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def make(name):
        return types.ModuleType(f'gi.repository.{name}')

    adw, gdk, glib, gtk, pango, gio = (
        make('Adw'), make('Gdk'), make('GLib'), make('Gtk'),
        make('Pango'), make('Gio'),
    )
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    gtk.Editable = type('Editable', (), {})
    gtk.Box = type('Box', (), {})
    gtk.Label = type('Label', (), {})
    gtk.Button = type('Button', (), {})
    gtk.Widget = type('Widget', (), {})
    gtk.PickFlags = types.SimpleNamespace(DEFAULT=0)
    gtk.Align = types.SimpleNamespace(END=3, FILL=1)

    installed = {
        'gi': gi, 'gi.repository': repo,
        'gi.repository.Adw': adw, 'gi.repository.Gdk': gdk,
        'gi.repository.GLib': glib, 'gi.repository.Gtk': gtk,
        'gi.repository.Pango': pango, 'gi.repository.Gio': gio,
    }
    saved = {name: sys.modules.get(name) for name in installed}
    sys.modules.update(installed)
    try:
        import window
        sys.modules.pop('window', None)
        yield window
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class _FakeGestures:
    def __init__(self):
        self._map = {'swipe_up_short': 'home'}
        self.set_calls = []
        self.reset_calls = []

    def get(self, key):
        return self._map.get(key, 'none')

    def set(self, key, value):
        self.set_calls.append((key, value))
        self._map[key] = value

    def reset(self, key):
        self.reset_calls.append(key)
        self._map[key] = 'home'


def _bare(window):
    w = object.__new__(window.ShellWindow)
    w.gesture_config = _FakeGestures()
    w.app_index = types.SimpleNamespace(entries=[])
    w.config = types.SimpleNamespace(label_for=lambda app_id, name: name)
    w._gesture_binding_labels = {}
    w._system_gesture_labels = {}
    w.item_actions = types.SimpleNamespace(
        _show_action_menu=lambda anchor, title, actions, checked=None:
        captured.append((anchor, title, actions, checked)))
    return w


captured = []


def test_verb_binding_renders_friendly(window):
    w = _bare(window)
    w.gesture_config._map['swipe_up_short'] = 'gesture.keyboard'
    assert w._gesture_binding_text('swipe_up_short') == 'Keyboard (system)'


def test_pick_offers_only_actions_that_work(window):
    # The old picker offered every action; lock screen, camera, dialer,
    # assistant and settings silently fired the slot's default verb, and
    # "Do Nothing" on the short swipe raised the keyboard.
    w = _bare(window)
    anchor = object()
    w._pick_system_gesture(anchor, 'swipe_up_long', 'Swipe up (long)')
    _anchor, title, actions, _checked = captured[-1]
    assert title == 'Swipe up (long)'
    labels = [label for label, _cb in actions]
    assert labels == ['Home', 'App switcher', 'Notification shade', 'Back',
                      'Keyboard', 'Nothing', 'Default']


def test_every_choice_resolves_to_what_it_says(window):
    import settings_page
    from gesture_bindings import resolve_verb
    expected = {'Home': 'gesture.home', 'App switcher': 'gesture.switcher',
                'Notification shade': 'gesture.shade', 'Back': 'gesture.back',
                'Keyboard': 'gesture.keyboard', 'Nothing': None}
    for value, label in settings_page._SYSTEM_GESTURE_CHOICES:
        for slot in settings_page._SYSTEM_GESTURE_TITLES:
            gc = types.SimpleNamespace(get=lambda _k, v=value: v)
            assert resolve_verb(gc, slot) == expected[label], (slot, value)


def test_current_choice_is_checked_and_row_shows_the_real_action(window):
    w = _bare(window)
    w.gesture_config._map['swipe_up_short'] = 'none'
    w._pick_system_gesture(object(), 'swipe_up_short', 'Swipe up (short)')
    assert captured[-1][3] == 5
    texts = []
    w._system_gesture_labels['swipe_up_short'] = types.SimpleNamespace(
        set_text=texts.append)
    w._refresh_gesture_labels()
    assert texts == ['Nothing']
    # An old hand-edited unmapped value shows the verb it really fires.
    w.gesture_config._map['swipe_up_short'] = 'camera'
    w._refresh_gesture_labels()
    assert texts[-1] == 'Keyboard'


def test_choice_persists_and_refreshes(window):
    w = _bare(window)
    label = types.SimpleNamespace(set_text=lambda t: None)
    w._system_gesture_labels['swipe_up_short'] = label
    w._pick_system_gesture(object(), 'swipe_up_short', 'Swipe up (short)')
    _anchor, _title, actions, _checked = captured[-1]
    by_label = dict(actions)
    by_label['Notification shade']()
    assert ('swipe_up_short', 'notification_shade') in w.gesture_config.set_calls


def test_reset_restores_default(window):
    w = _bare(window)
    w._reset_system_gesture('swipe_up_long')
    assert w.gesture_config.reset_calls == ['swipe_up_long']


def test_card_wiring_pins_both_row_sets(window):
    import settings_page
    assert set(window._SYSTEM_GESTURE_TITLES) == {
        'swipe_up_short', 'swipe_up_long', 'swipe_down_top', 'swipe_left_edge'}
    assert set(settings_page._SYSTEM_GESTURE_TITLES) == set(
        window._SYSTEM_GESTURE_TITLES)
    src = inspect.getsource(settings_page.SettingsPage._build_gestures_card)
    assert '_GESTURE_TITLES' in src
    assert '_SYSTEM_GESTURE_TITLES' in src
    assert '_pick_system_gesture' in src
    # Back to the default binding is the last choice of each row's menu.
    pick = inspect.getsource(settings_page.SettingsPage._pick_system_gesture)
    assert '_reset_system_gesture' in pick
    assert "'Default'" in pick


def test_rebind_restarts_lisgd(window):
    src = inspect.getsource(window.ShellWindow._set_system_gesture)
    assert '_restart_system_gestures' in src
    reset = inspect.getsource(window.ShellWindow._reset_system_gesture)
    assert '_restart_system_gestures' in reset
    init = inspect.getsource(window.ShellWindow.__init__)
    assert '_sync_system_gestures' in init
    sync = inspect.getsource(window.ShellWindow._restart_system_gestures)
    assert '_sync_system_gestures' in sync
