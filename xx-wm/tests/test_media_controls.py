"""MPRIS media controls: watcher, card, keys, wiring (headless).

The watcher is driven through a FakeBus reached via media_controls.GLib /
Gio (the same seam sibling suites use); the card renders against recording
Gtk fakes. Gio replies mirror the real library: every reply is a TUPLE of
out-args, async callbacks take (conn, result), and signal handlers take
(conn, sender, path, iface, signal, params, user_data).
"""
import struct
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


# --- fake gi ---------------------------------------------------------------

_idle_queue: list = []
_timers: dict = {}
_next_timer_id = [100]


def _drain_idle():
    while _idle_queue:
        cb, args = _idle_queue.pop(0)
        cb(*args)


class _FakeVariant:
    def __init__(self, fmt, value=None):
        self.fmt = fmt
        self.value = value

    def unpack(self):
        return self.value


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.idle_add = lambda cb, *a: (_idle_queue.append((cb, a)), len(_idle_queue))[1]
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.Variant = _FakeVariant
    glib.VariantType = lambda spec: spec

    gio = types.ModuleType('gi.repository.Gio')
    gio.BusType = SimpleNamespace(SYSTEM=1, SESSION=2)
    gio.DBusCallFlags = SimpleNamespace(NONE=0)
    gio.DBusSignalFlags = SimpleNamespace(NONE=0)
    gio.bus_get_sync = lambda *a, **k: None
    gio.DesktopAppInfo = _FakeDesktopAppInfo

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = _FakeDisplay

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.STYLE_PROVIDER_PRIORITY_APPLICATION = 100
    gtk.Orientation = SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    gtk.Align = SimpleNamespace(START=0, END=1, CENTER=2, FILL=3)
    gtk.Box = _FakeBox
    gtk.Label = _FakeLabel
    gtk.Button = _FakeButton
    gtk.ProgressBar = _FakeProgressBar
    gtk.GestureClick = _FakeGesture
    gtk.CssProvider = _FakeCssProvider
    gtk.StyleContext = _FakeStyleContext

    pango = types.ModuleType('gi.repository.Pango')
    pango.EllipsizeMode = SimpleNamespace(END=3)

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')

    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gio'] = gio
    sys.modules['gi.repository.Gdk'] = gdk
    sys.modules['gi.repository.Gtk'] = gtk
    sys.modules['gi.repository.Pango'] = pango


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.children = []
        self.controllers = []
        self.classes = []
        self._visible = True
        self._mapped = True
        self._callbacks = {}

    def append(self, child):
        self.children.append(child)

    def add_css_class(self, css):
        self.classes.append(css)

    def set_visible(self, value):
        self._visible = bool(value)

    def get_visible(self):
        return self._visible

    def is_visible(self):
        return self._visible and self._mapped

    def connect(self, signal, callback, *args):
        self._callbacks.setdefault(signal, []).append(callback)
        return len(self._callbacks[signal])

    def add_controller(self, controller):
        self.controllers.append(controller)

    def set_hexpand(self, value):
        self._hexpand = bool(value)

    def set_valign(self, value):
        self._valign = value

    def set_tooltip_text(self, text):
        self._tooltip = text


class _FakeBox(_FakeWidget):
    def __init__(self, orientation=None, spacing=0, **kwargs):
        super().__init__()
        self.orientation = orientation
        self.spacing = spacing


class _FakeLabel(_FakeWidget):
    def __init__(self, label='', xalign=0, **kwargs):
        super().__init__()
        self._text = label
        self._ellipsize = None

    def set_text(self, text):
        self._text = text

    def get_text(self):
        return self._text

    def set_ellipsize(self, mode):
        self._ellipsize = mode


class _FakeButton(_FakeWidget):
    def __init__(self, label='', **kwargs):
        super().__init__()
        self._label = label
        self._sensitive = True

    def set_label(self, text):
        self._label = text

    def get_label(self):
        return self._label

    def set_icon_name(self, name):
        self._icon = name

    def get_icon_name(self):
        return getattr(self, '_icon', None)

    def set_sensitive(self, value):
        self._sensitive = bool(value)

    def is_sensitive(self):
        return self._sensitive

    def click(self):
        for cb in self._callbacks.get('clicked', []):
            cb(self)


class _FakeProgressBar(_FakeWidget):
    def __init__(self, **kwargs):
        super().__init__()
        self._fraction = 0.0

    def set_fraction(self, value):
        self._fraction = value

    def get_fraction(self):
        return self._fraction

    def set_show_text(self, value):
        pass


class _FakeGesture:
    @classmethod
    def new(cls):
        return cls()

    def __init__(self, **kwargs):
        self._callbacks = {}

    def connect(self, signal, callback, *args):
        self._callbacks.setdefault(signal, []).append(callback)
        return 1

    def fire(self, signal, *args):
        for cb in self._callbacks.get(signal, []):
            cb(self, *args)


class _FakeCssProvider:
    def __init__(self, **kwargs):
        self.data = None

    def load_from_data(self, data):
        self.data = bytes(data)


class _FakeStyleContext:
    added = []

    @staticmethod
    def add_provider_for_display(display, provider, priority):
        _FakeStyleContext.added.append((display, provider, priority))


class _FakeDisplay:
    @staticmethod
    def get_default():
        return None


class _FakeDesktopAppInfo:
    launched = []

    def __init__(self, desktop_id=''):
        self.desktop_id = desktop_id

    @staticmethod
    def new(desktop_id):
        if desktop_id.startswith('missing'):
            return None
        return _FakeDesktopAppInfo(desktop_id)

    def launch(self, uris, ctx):
        _FakeDesktopAppInfo.launched.append(self.desktop_id)


_saved_gi = {n: m for n, m in sys.modules.items()
             if n == 'gi' or n.startswith('gi.')}
_install_fake_gi()

import display_manager  # noqa: E402
import media_controls  # noqa: E402

for _name in [n for n in sys.modules if n == 'gi' or n.startswith('gi.')]:
    del sys.modules[_name]
sys.modules.update(_saved_gi)
del _saved_gi

import pytest  # noqa: E402

from _theme_contract import hardcoded_leftover  # noqa: E402
from config import THEME_PRESETS  # noqa: E402

VLC = 'org.mpris.MediaPlayer2.vlc'
BROWSER = 'org.mpris.MediaPlayer2.firefox.instance1'


class FakeParams:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeReply:
    # Mirrors Gio: every reply is a tuple of out-args, so a single a{..}
    # out-arg (GetAll, ListNames) unpacks to a 1-tuple, not a bare value.
    def __init__(self, value):
        self._value = value

    def unpack(self):
        if isinstance(self._value, dict):
            return (self._value,)
        return self._value


class FakeBus:
    def __init__(self):
        self.subs = []
        self.handlers = {}
        self.unsubs = []
        self.calls = []  # async dispatches: (bus, path, iface, method, params)
        self.pending = []  # async completion callbacks awaiting transport
        self._finish = []  # queued call_finish replies (values or Exception)
        self._next = 1

    def signal_subscribe(self, sender, iface, signal, path, *rest):
        sid = self._next
        self._next += 1
        self.subs.append({'sid': sid, 'sender': sender, 'iface': iface,
                          'signal': signal, 'path': path})
        self.handlers[sid] = rest[2]  # the subscribed callback
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubs.append(sid)

    def call(self, bus_name, path, iface, method, params=None, reply_type=None,
             flags=None, timeout=-1, cancellable=None, callback=None,
             *user_data):
        """Async Gio.DBusConnection.call with Gio's real positional layout:
        no user_data is ever passed, so callbacks take (conn, result)."""
        assert callback is not None
        assert not user_data, 'watcher must not pass user_data (arity trap)'
        self.calls.append((bus_name, path, iface, method, params))
        self.pending.append((callback, user_data))

    def call_finish(self, _result):
        reply = self._finish.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def complete_next(self, reply) -> None:
        """Deliver the oldest outstanding async reply."""
        self._finish.append(reply)
        cb, user_data = self.pending.pop(0)
        cb(self, object(), *user_data)

    def methods(self):
        return [c[3] for c in self.calls]


ROOT_VLC = {'Identity': 'VLC media player', 'DesktopEntry': 'vlc'}
ROOT_BROWSER = {'Identity': 'Firefox', 'DesktopEntry': 'firefox'}


def _player_props(status='Playing', title='Song', artist=('A', 'B'),
                  album='Album', length=240_000_000, position=60_000_000,
                  nxt=True, prev=True, play=True, pause=True):
    return {
        'PlaybackStatus': status,
        'Metadata': {
            'xesam:title': title,
            'xesam:artist': list(artist),
            'xesam:album': album,
            'mpris:length': length,
        },
        'Position': position,
        'CanGoNext': nxt,
        'CanGoPrevious': prev,
        'CanPlay': play,
        'CanPause': pause,
    }


@pytest.fixture
def hw(monkeypatch):
    """MprisWatcher on a FakeBus with a drainable main-loop queue."""
    _idle_queue.clear()
    bus = FakeBus()
    fired: list = []
    monkeypatch.setattr(media_controls.Gio, 'bus_get_sync',
                        lambda *a, **k: bus)
    monkeypatch.setattr(media_controls.GLib, 'idle_add',
                        lambda cb, *a: (_idle_queue.append((cb, a)), 0)[1])
    watcher = media_controls.MprisWatcher(on_active_changed=fired.append)

    def _drain():
        _drain_idle()

    _drain()  # runs _init_bus: subscribes + queues ListNames
    assert bus.methods() == ['ListNames']
    bus.complete_next(FakeReply(([],)))  # no players at startup
    _drain()
    assert fired == []
    return SimpleNamespace(watcher=watcher, bus=bus, fired=fired,
                           drain=_drain)


def _adopt(hw, name, root, player):
    """Drive NameOwnerChanged-appear through both async GetAll fetches."""
    n_calls = len(hw.bus.calls)
    hw.watcher._on_name_owner_changed(
        None, None, None, None, None, FakeParams((name, '', ':1.7')), None)
    assert len(hw.bus.calls) == n_calls + 2
    assert [c[3] for c in hw.bus.calls[n_calls:]] == ['GetAll', 'GetAll']
    hw.bus.complete_next(FakeReply(dict(root)))
    hw.bus.complete_next(FakeReply(dict(player)))
    hw.drain()


def _changed(hw, name, iface, changed):
    hw.watcher._on_properties_changed(
        name, None, ':1.7', '/org/mpris/MediaPlayer2', None, None,
        FakeParams((iface, dict(changed), [])), None)
    hw.drain()


class TestDiscovery:
    def test_list_names_adopts_only_mpris_names(self, hw, monkeypatch):
        watchers = []
        monkeypatch.setattr(media_controls.MprisWatcher, '_adopt_player',
                            lambda self, n: watchers.append(n))
        reply = FakeReply(([VLC, 'org.gnome.Shell', BROWSER],))
        conn = SimpleNamespace(call_finish=lambda _result: reply)
        hw.watcher._on_list_names(conn, object())
        hw.drain()
        assert sorted(watchers) == sorted([VLC, BROWSER])

    def test_adopt_fetches_both_property_sets(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        root_calls = [c for c in hw.bus.calls if c[3] == 'GetAll']
        assert len(root_calls) == 2
        assert {c[0] for c in root_calls} == {VLC}
        snap = hw.watcher.active_snapshot()
        assert snap.identity == 'VLC media player'
        assert snap.desktop_entry == 'vlc'
        assert snap.title == 'Song'
        assert hw.fired and hw.fired[-1] == snap

    def test_name_owner_changed_appear_and_vanish(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        assert hw.watcher.active_snapshot() is not None
        n_unsubs = len(hw.bus.unsubs)
        hw.watcher._on_name_owner_changed(
            None, None, None, None, None, FakeParams((VLC, ':1.7', '')), None)
        hw.drain()
        assert hw.watcher.active_snapshot() is None
        assert len(hw.bus.unsubs) == n_unsubs + 1
        assert hw.fired[-1] is None

    def test_unrelated_names_ignored(self, hw):
        hw.watcher._on_name_owner_changed(
            None, None, None, None, None,
            FakeParams(('org.gnome.Shell', '', ':1.9')), None)
        hw.drain()
        assert hw.watcher.active_snapshot() is None
        assert hw.bus.methods() == ['ListNames']
        assert hw.fired == []

    def test_getall_failure_keeps_name_but_stays_usable(self, hw):
        hw.watcher._on_name_owner_changed(
            None, None, None, None, None, FakeParams((VLC, '', ':1.7')), None)
        hw.bus.complete_next(Exception('no such object'))
        hw.bus.complete_next(Exception('no such object'))
        hw.drain()
        assert VLC in [p.bus_name for p in hw.watcher.players()]
        _changed(hw, VLC, media_controls._PLAYER_IFACE,
                 {'PlaybackStatus': 'Playing'})
        assert hw.watcher.active_snapshot().status == 'Playing'

    def test_properties_changed_subscription_per_player(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        subs = [s for s in hw.bus.subs
                if s['signal'] == 'PropertiesChanged']
        assert len(subs) == 1
        sub = subs[0]
        assert sub['sender'] == VLC
        assert sub['path'] == '/org/mpris/MediaPlayer2'

    def test_subscribed_callback_routes_with_signal_arity(self, hw):
        """The per-player lambda must accept Gio's 7 signal args and route
        to its own player (proves the user_data arity trap is avoided)."""
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        _adopt(hw, BROWSER, ROOT_BROWSER, _player_props())
        by_sender = {s['sender']: hw.bus.handlers[s['sid']]
                     for s in hw.bus.subs
                     if s['signal'] == 'PropertiesChanged'}
        assert set(by_sender) == {VLC, BROWSER}
        handler = by_sender[BROWSER]
        handler(None, ':1.7', '/org/mpris/MediaPlayer2', None, None,
                FakeParams((media_controls._PLAYER_IFACE,
                            {'PlaybackStatus': 'Paused'}, [])), None)
        hw.drain()
        assert hw.watcher.snapshot_for(
            BROWSER).status == 'Paused'
        assert hw.watcher.snapshot_for(VLC).status == 'Playing'
        assert hw.watcher.active_snapshot().bus_name == VLC


class TestActiveSelection:
    def test_most_recently_playing_wins(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props(title='VLC song'))
        assert hw.watcher.active_snapshot().bus_name == VLC
        _adopt(hw, BROWSER, ROOT_BROWSER, _player_props(title='Web song'))
        assert hw.watcher.active_snapshot().bus_name == BROWSER

    def test_playing_beats_earlier_playing_paused_later(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        _adopt(hw, BROWSER, ROOT_BROWSER,
               _player_props(status='Paused', title='Web song'))
        assert hw.watcher.active_snapshot().bus_name == VLC
        _changed(hw, BROWSER, media_controls._PLAYER_IFACE,
                 {'PlaybackStatus': 'Playing'})
        assert hw.watcher.active_snapshot().bus_name == BROWSER

    def test_paused_beats_stopped(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props(status='Stopped'))
        _adopt(hw, BROWSER, ROOT_BROWSER, _player_props(status='Paused'))
        assert hw.watcher.active_snapshot().bus_name == BROWSER

    def test_stopped_only_falls_back_to_most_recent(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props(status='Stopped'))
        _adopt(hw, BROWSER, ROOT_BROWSER, _player_props(status='Stopped'))
        assert hw.watcher.active_snapshot().bus_name == BROWSER

    def test_pause_then_resume_tracks_recency(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        _adopt(hw, BROWSER, ROOT_BROWSER, _player_props())
        assert hw.watcher.active_snapshot().bus_name == BROWSER
        _changed(hw, BROWSER, media_controls._PLAYER_IFACE,
                 {'PlaybackStatus': 'Paused'})
        _changed(hw, VLC, media_controls._PLAYER_IFACE,
                 {'PlaybackStatus': 'Paused'})
        assert hw.watcher.active_snapshot().bus_name == VLC

    def test_snapshot_is_immutable(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        snap = hw.watcher.active_snapshot()
        with pytest.raises(Exception):
            snap.title = 'mutated'  # frozen dataclass
        assert hw.watcher.active_snapshot().title == 'Song'


class TestMetadataParsing:
    def test_artist_list_joined_album_and_length(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        snap = hw.watcher.active_snapshot()
        assert (snap.title, snap.artist, snap.album) == (
            'Song', 'A, B', 'Album')
        assert snap.length_us == 240_000_000
        assert snap.position_us == 60_000_000

    def test_variant_wrapped_values_unwrapped(self, hw):
        props = _player_props()
        props['Metadata'] = {
            'xesam:title': _FakeVariant('s', 'Wrapped'),
            'xesam:artist': _FakeVariant('as', ['X', 'Y']),
            'xesam:album': _FakeVariant('s', 'Wrap Album'),
            'mpris:length': _FakeVariant('x', 100_000_000),
        }
        _adopt(hw, VLC, ROOT_VLC, props)
        snap = hw.watcher.active_snapshot()
        assert snap.title == 'Wrapped'
        assert snap.artist == 'X, Y'
        assert snap.album == 'Wrap Album'
        assert snap.length_us == 100_000_000

    def test_lone_string_artist_and_zero_length(self, hw):
        props = _player_props()
        props['Metadata'] = {'xesam:artist': 'Solo', 'mpris:length': 0}
        _adopt(hw, VLC, ROOT_VLC, props)
        snap = hw.watcher.active_snapshot()
        assert snap.artist == 'Solo'
        assert snap.title == ''
        assert snap.length_us is None

    def test_capability_gating(self, hw):
        _adopt(hw, VLC, ROOT_VLC,
               _player_props(nxt=False, prev=True, play=False, pause=True))
        snap = hw.watcher.active_snapshot()
        assert snap.can_go_next is False
        assert snap.can_go_previous is True
        assert snap.can_play is False
        assert snap.can_pause is True

    def test_position_fetch_merges_and_emits(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        hw.fired.clear()
        assert hw.watcher.fetch_position() is True
        assert hw.bus.calls[-1][3] == 'Get'
        hw.bus.complete_next(FakeReply((_FakeVariant('x', 90_000_000),)))
        hw.drain()
        assert hw.watcher.active_snapshot().position_us == 90_000_000
        assert hw.fired and hw.fired[-1].position_us == 90_000_000

    def test_position_fetch_without_player_is_quiet(self, hw):
        assert hw.watcher.fetch_position() is False
        assert hw.bus.methods() == ['ListNames']

    def test_status_flips_via_properties_changed(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        _changed(hw, VLC, media_controls._PLAYER_IFACE,
                 {'PlaybackStatus': 'Paused',
                  'Metadata': {'xesam:title': 'Next song'}})
        snap = hw.watcher.active_snapshot()
        assert snap.status == 'Paused'
        assert snap.title == 'Next song'

    def test_unknown_iface_and_unknown_player_ignored(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        hw.fired.clear()
        _changed(hw, VLC, 'org.freedesktop.DBus.Properties', {'Foo': 1})
        _changed(hw, 'org.mpris.MediaPlayer2.ghost',
                 media_controls._PLAYER_IFACE, {'PlaybackStatus': 'Playing'})
        assert hw.fired == []
        assert hw.watcher.active_snapshot().bus_name == VLC


class TestControls:
    def test_play_pause_next_previous_target_active_player(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        assert hw.watcher.play_pause() is True
        assert hw.watcher.next_track() is True
        assert hw.watcher.previous_track() is True
        assert hw.bus.methods()[-3:] == ['PlayPause', 'Next', 'Previous']
        for bus_name, path, iface, method, _p in hw.bus.calls[-3:]:
            assert bus_name == VLC
            assert path == '/org/mpris/MediaPlayer2'
            assert iface == 'org.mpris.MediaPlayer2.Player'

    def test_controls_without_player_are_quiet(self, hw):
        assert hw.watcher.play_pause() is False
        assert hw.watcher.next_track() is False
        assert hw.watcher.previous_track() is False
        assert hw.watcher.raise_player() is False
        assert hw.bus.methods() == ['ListNames']

    def test_handle_media_key_routes_actions(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        assert hw.watcher.handle_media_key('play-pause') is True
        assert hw.watcher.handle_media_key('next') is True
        assert hw.watcher.handle_media_key('previous') is True
        assert hw.watcher.handle_media_key('stop') is False
        assert hw.watcher.handle_media_key('') is False
        assert hw.bus.methods()[-3:] == ['PlayPause', 'Next', 'Previous']

    def test_handle_media_key_without_player_is_quiet(self, hw):
        assert hw.watcher.handle_media_key('play-pause') is False
        assert hw.bus.methods() == ['ListNames']

    def test_control_completion_failure_is_swallowed(self, hw):
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        hw.watcher.play_pause()
        hw.bus.complete_next(Exception('no reply'))
        hw.drain()

    def test_listeners_get_active_snapshots(self, hw):
        seen = []
        listener = seen.append
        hw.watcher.add_listener(listener)
        hw.watcher.add_listener(listener)  # duplicate ignored
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        assert seen and all(s.bus_name == VLC for s in seen)
        assert hw.watcher._listeners == [listener]
        hw.watcher.remove_listener(listener)
        seen.clear()
        _changed(hw, VLC, media_controls._PLAYER_IFACE,
                 {'Metadata': {'xesam:title': 'Another'}})
        assert seen == []


class TestPureHelpers:
    def test_format_media_subtitle(self):
        fmt = media_controls.format_media_subtitle
        assert fmt('Artist', 'Player') == 'Artist \u00b7 Player'
        assert fmt('', 'Player') == 'Player'
        assert fmt('Artist', '') == 'Artist'
        assert fmt('', '') == ''

    def test_play_button_label(self):
        assert media_controls.play_button_label('Playing') == '\u275a\u275a'
        assert media_controls.play_button_label('Paused') == '\u25b6'
        assert media_controls.play_button_label('Stopped') == '\u25b6'
        assert media_controls.play_button_label('') == '\u25b6'

    def test_progress_fraction(self):
        frac = media_controls.progress_fraction
        assert frac(240_000_000, 60_000_000) == 0.25
        assert frac(100, 0) == 0.0
        assert frac(None, 10) is None
        assert frac(100, None) is None
        assert frac(0, 0) is None
        assert frac(-5, 10) is None
        assert frac(100, 500) == 1.0
        assert frac(100, -50) == 0.0

    def test_parse_non_dict_metadata(self):
        assert media_controls.parse_player_metadata(None) == ('', '', '', None)
        assert media_controls.parse_player_metadata('nope') == ('', '', '', None)


@pytest.fixture
def card(hw, monkeypatch):
    """MediaCard on the hw watcher with recording GLib timers."""
    _FakeStyleContext.added.clear()
    _FakeDesktopAppInfo.launched.clear()
    timers: dict = {}
    removed: list = []
    next_id = [10]

    def _add(secs, cb):
        nid = next_id[0]
        next_id[0] += 1
        timers[nid] = (secs, cb)
        return nid

    def _remove(tid):
        removed.append(tid)
        timers.pop(tid, None)

    monkeypatch.setattr(media_controls.GLib, 'timeout_add_seconds', _add)
    monkeypatch.setattr(media_controls.GLib, 'source_remove', _remove)
    config = SimpleNamespace(theme=THEME_PRESETS['amoled'])
    made = media_controls.MediaCard(watcher=hw.watcher, config=config)
    assert made.get_visible() is False  # hidden with no player
    return SimpleNamespace(card=made, timers=timers, removed=removed, hw=hw)


def _tap_title(card):
    taps = card._title.controllers
    assert len(taps) == 1
    # FakeGesture.fire prepends itself, matching Gtk's (gesture, n, x, y).
    taps[0].fire('pressed', 1, 0.0, 0.0)


class TestMediaCard:
    def test_renders_active_snapshot(self, card):
        hw = card.hw
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        assert card.card.get_visible() is True
        assert card.card._title.get_text() == 'Song'
        assert card.card._subtitle.get_text() == 'A, B \u00b7 VLC media player'
        assert card.card._play_btn.get_icon_name() == \
            media_controls.PAUSE_ICON
        assert card.card._prev_btn.is_sensitive() is True
        assert card.card._next_btn.is_sensitive() is True
        assert card.card._play_btn.is_sensitive() is True
        assert card.card._progress.get_visible() is True
        assert card.card._progress.get_fraction() == 0.25

    def test_paused_card_gates_play_by_can_play(self, card):
        hw = card.hw
        _adopt(hw, VLC, ROOT_VLC,
               _player_props(status='Paused', nxt=False, play=False))
        assert card.card._play_btn.get_icon_name() == \
            media_controls.PLAY_ICON
        assert card.card._play_btn.is_sensitive() is False
        assert card.card._next_btn.is_sensitive() is False
        assert card.card._prev_btn.is_sensitive() is True

    def test_empty_title_falls_back(self, card):
        hw = card.hw
        props = _player_props()
        props['Metadata'] = {}
        _adopt(hw, VLC, ROOT_VLC, props)
        assert card.card._title.get_text() == 'Unknown track'

    def test_no_progress_without_length(self, card):
        hw = card.hw
        props = _player_props(length=0, position=5_000_000)
        del props['Position']
        _adopt(hw, VLC, ROOT_VLC, props)
        assert card.card._progress.get_visible() is False

    def test_controls_drive_watcher(self, card):
        hw = card.hw
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        card.card._next_btn.click()
        card.card._prev_btn.click()
        card.card._play_btn.click()
        assert hw.bus.methods()[-3:] == ['Next', 'Previous', 'PlayPause']

    def test_title_tap_launches_desktop_entry(self, card):
        hw = card.hw
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        _tap_title(card.card)
        assert _FakeDesktopAppInfo.launched == ['vlc.desktop']

    def test_title_tap_uses_on_raise_callback(self, hw):
        _FakeDesktopAppInfo.launched.clear()
        raised = []
        made = media_controls.MediaCard(
            watcher=hw.watcher,
            on_raise=lambda entry, bus: raised.append((entry, bus)),
            config=SimpleNamespace(theme=THEME_PRESETS['amoled']))
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        _tap_title(made)
        assert raised == [('vlc', VLC)]
        assert _FakeDesktopAppInfo.launched == []

    def test_title_tap_without_entry_raises_via_mpris(self, card):
        hw = card.hw
        _adopt(hw, VLC, {'Identity': 'NoEntry'}, _player_props())
        n_calls = len(hw.bus.calls)
        _tap_title(card.card)
        assert hw.bus.methods()[n_calls:] == ['Raise']
        assert hw.bus.calls[-1][0] == VLC

    def test_hides_when_player_vanishes(self, card):
        hw = card.hw
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        assert card.card.get_visible() is True
        assert card.timers, 'playing card must poll position'
        hw.watcher._on_name_owner_changed(
            None, None, None, None, None, FakeParams((VLC, ':1.7', '')), None)
        hw.drain()
        assert card.card.get_visible() is False
        assert card.timers == {}

    def test_set_watcher_none_hides(self, card):
        hw = card.hw
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        assert card.card.get_visible() is True
        card.card.set_watcher(None)
        assert card.card.get_visible() is False

    def test_polling_only_while_visible_and_playing(self, card):
        hw = card.hw
        _adopt(hw, VLC, ROOT_VLC, _player_props(status='Paused'))
        assert card.timers == {}
        _changed(hw, VLC, media_controls._PLAYER_IFACE,
                 {'PlaybackStatus': 'Playing'})
        assert len(card.timers) == 1
        tid, (secs, tick) = next(iter(card.timers.items()))
        assert secs == 1
        n_calls = len(hw.bus.calls)
        assert tick() is True  # GLib.SOURCE_CONTINUE
        assert hw.bus.methods()[n_calls:] == ['Get']
        card.card._mapped = False  # shade hidden: card unmapped
        assert tick() is False  # GLib.SOURCE_REMOVE
        assert card.card._pos_src is None
        # The recording timer dict cannot emulate GLib auto-removal of a
        # SOURCE_REMOVE callback; the cleared _pos_src above is the proof
        # the timer stops.

    def test_no_polling_without_length(self, card):
        hw = card.hw
        props = _player_props(length=0)
        _adopt(hw, VLC, ROOT_VLC, props)
        assert card.card.get_visible() is True
        assert card.timers == {}

    def test_apply_theme_reloads_single_provider(self, card):
        first = card.card._theme_provider
        card.card.apply_theme(THEME_PRESETS['paper'])
        assert card.card._theme_provider is first
        assert THEME_PRESETS['paper'].foreground.encode() in first.data
        card.card.apply_theme(THEME_PRESETS['amoled'])
        assert THEME_PRESETS['amoled'].foreground.encode() in first.data

    def test_title_ellipsizes(self, card):
        assert card.card._title._ellipsize is not None
        assert card.card._subtitle._ellipsize is not None


class _SyncGLib:
    """Synchronous GLib seam for DisplayManager tests."""

    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    @staticmethod
    def idle_add(fn, *a):
        fn(*a)
        return 0

    @staticmethod
    def timeout_add(_ms, _cb, *_a):
        return 1

    timeout_add_seconds = timeout_add

    @staticmethod
    def source_remove(_tid):
        pass


@pytest.fixture
def dm(monkeypatch):
    """DisplayManager with stubbed nodes/backends; blank/wake recorded."""
    monkeypatch.setattr(display_manager, 'GLib', _SyncGLib)
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: ['/dev/input/eventX'])
    monkeypatch.setattr(display_manager, '_watch_evdev',
                        lambda *a, **k: None)
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event9')
    blanks: list = []
    wakes: list = []
    monkeypatch.setattr(display_manager.DisplayManager, '_blank',
                        lambda self: blanks.append(True))
    monkeypatch.setattr(display_manager.DisplayManager, '_wake',
                        lambda self: wakes.append(True))

    def _build(**kwargs):
        return display_manager.DisplayManager(**kwargs), blanks, wakes

    return _build


def _ev_bytes(ev_type, code, value):
    return struct.pack(display_manager._EV_FMT, 0, 0, ev_type, code, value)


class TestMediaKeyFilter:
    def test_media_presses_post_on_any_node(self):
        data = b''.join([
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_PLAYPAUSE, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_NEXTSONG, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_PREVIOUSSONG, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_PLAYCD, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_PAUSECD, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_MEDIA, 1),
        ])
        wanted, _ = display_manager.filter_evdev_data(
            data, '/dev/input/event5', '/dev/input/event9', 1000.0)
        assert [w[2] for w in wanted] == [
            display_manager.KEY_PLAYPAUSE, display_manager.KEY_NEXTSONG,
            display_manager.KEY_PREVIOUSSONG, display_manager.KEY_PLAYCD,
            display_manager.KEY_PAUSECD, display_manager.KEY_MEDIA,
        ]

    def test_media_releases_and_repeats_dropped(self):
        for value in (0, 2):
            data = b''.join([
                _ev_bytes(display_manager.EV_KEY,
                          display_manager.KEY_PLAYPAUSE, value),
                _ev_bytes(display_manager.EV_KEY,
                          display_manager.KEY_MEDIA, value),
                _ev_bytes(display_manager.EV_KEY,
                          display_manager.KEY_NEXTSONG, value),
            ])
            wanted, _ = display_manager.filter_evdev_data(
                data, '/dev/input/event5', '/dev/input/event9', 1000.0)
            assert wanted == [], f'value {value} must not post'

    def test_power_volume_behavior_unchanged(self):
        data = b''.join([
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_POWER, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_POWER, 0),
            _ev_bytes(display_manager.EV_KEY,
                      display_manager.KEY_VOLUMEUP, 1),
        ])
        wanted, _ = display_manager.filter_evdev_data(
            data, '/dev/input/event0', '/dev/input/event9', 1000.0)
        assert [(w[2], w[3]) for w in wanted] == [
            (display_manager.KEY_POWER, 1),
            (display_manager.KEY_POWER, 0),
            (display_manager.KEY_VOLUMEUP, 1),
        ]


class TestMediaKeyRouting:
    def test_press_routes_action(self, dm):
        actions = []
        mgr, _blanks, _wakes = dm(on_media_key=actions.append)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_NEXTSONG, 1)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_PREVIOUSSONG, 1)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_PLAYPAUSE, 1)
        assert actions == ['next', 'previous', 'play-pause']

    def test_cd_and_headset_buttons_toggle(self, dm):
        actions = []
        mgr, _blanks, _wakes = dm(on_media_key=actions.append)
        for code in (display_manager.KEY_PLAYCD, display_manager.KEY_PAUSECD,
                     display_manager.KEY_MEDIA):
            mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                          code, 1)
        assert actions == ['play-pause'] * 3

    def test_release_and_repeat_are_noops(self, dm):
        actions = []
        mgr, _blanks, _wakes = dm(on_media_key=actions.append)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_PLAYPAUSE, 0)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_PLAYPAUSE, 2)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_MEDIA, 0)
        assert actions == []

    def test_missing_callback_never_crashes(self, dm):
        mgr, _blanks, _wakes = dm()
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_PLAYPAUSE, 1)

    def test_raising_callback_is_swallowed(self, dm):
        def _boom(_action):
            raise RuntimeError('shell blew up')
        mgr, _blanks, _wakes = dm(on_media_key=_boom)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_NEXTSONG, 1)

    def test_media_keys_never_blank_or_wake(self, dm):
        mgr, blanks, wakes = dm(on_media_key=lambda _a: None)
        mgr._blanked = True
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_PLAYPAUSE, 1)
        assert blanks == [] and wakes == []

    def test_headset_press_reaches_active_player(self, dm, hw):
        """End to end: evdev press → watcher → MPRIS Next on the player."""
        _adopt(hw, VLC, ROOT_VLC, _player_props())
        mgr, _blanks, _wakes = dm(
            on_media_key=hw.watcher.handle_media_key)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_NEXTSONG, 1)
        mgr._on_event('/dev/input/event5', display_manager.EV_KEY,
                      display_manager.KEY_MEDIA, 1)
        assert hw.bus.methods()[-2:] == ['Next', 'PlayPause']


class TestMediaTheme:
    # The card is an outline on the shade's background, drawn with the
    # brand ramp of the foreground; play/pause inverts (background glyph
    # on a foreground key).
    FIELDS = ('foreground', 'background', 'muted', 'accent')

    def test_every_preset_interpolates_all_fields(self):
        for key, preset in THEME_PRESETS.items():
            css = media_controls.theme_css(preset)
            for field in self.FIELDS:
                assert getattr(preset, field) in css, f'{key}: {field}'

    def test_paper_differs_from_amoled(self):
        assert (media_controls.theme_css(THEME_PRESETS['paper'])
                != media_controls.theme_css(THEME_PRESETS['amoled']))

    def test_no_hardcoded_literals(self):
        for key, preset in THEME_PRESETS.items():
            leftover = hardcoded_leftover(
                media_controls.theme_css(preset), preset)
            assert not leftover, f'{key}: hardcoded {leftover}'

    def test_theme_css_is_pure(self):
        first = media_controls.theme_css(THEME_PRESETS['ocean'])
        second = media_controls.theme_css(THEME_PRESETS['ocean'])
        assert first == second


REPO = Path(__file__).parent.parent


class TestShellWiring:
    def test_main_builds_watcher_and_wires_key_callback(self):
        text = (REPO / 'launcher' / 'src' / 'main.py').read_text()
        assert 'MprisWatcher()' in text
        assert ('on_media_key=self._standby.wrap_media_key(\n'
                '                self._mpris.handle_media_key)') in text

    def test_window_passes_watcher_to_shade(self):
        text = (REPO / 'launcher' / 'src' / 'window.py').read_text()
        assert 'media_watcher=mpris' in text

    def test_shade_embeds_card_under_header(self):
        text = (REPO / 'launcher' / 'src'
                / 'notification_shade.py').read_text()
        assert 'media_watcher' in text
        assert 'MediaCard(' in text
        header = text.index('root.append(top_header)')
        card = text.index('root.append(media_card)')
        quick = text.index('root.append(self.quick_actions)')
        assert header < card < quick

    def test_shade_forwards_theme_to_card(self):
        text = (REPO / 'launcher' / 'src'
                / 'notification_shade.py').read_text()
        assert 'card.apply_theme' in text

    def test_lock_screen_embedding_is_documented(self):
        doc = media_controls.__doc__
        assert 'MediaCard' in doc
        assert 'lock screen' in doc.lower()
        assert 'apply_theme' in doc
