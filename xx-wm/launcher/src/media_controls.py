"""MPRIS media controls: player watcher + text-first media card.

Lock-screen embedding recipe (the follow-up lane owns lock_screen.py; do
not duplicate this wiring anywhere else):

    from media_controls import MediaCard

    # ``app`` is the XXWMApplication that owns the shared MprisWatcher
    # (main.py builds it as ``app._mpris`` next to the DisplayManager).
    # On the lock screen pass locked=True: transport buttons keep working
    # but the title tap never raises or launches the player app.
    self._media = MediaCard(watcher=getattr(app, '_mpris', None),
                            locked=True)
    some_vertical_box.append(self._media)

The card hides itself whenever no player exists, re-renders on every
watcher snapshot, and polls Position only while it is visible and the
track is Playing, so the lock screen needs no extra lifecycle code
beyond forwarding theme reloads::

    self._media.apply_theme(preset)

``MediaCard`` is also usable standalone (watcher=None): it stays hidden
until :meth:`MediaCard.set_watcher` attaches a live watcher.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable

from gi.repository import Gio, GLib

try:
    import gi

    gi.require_version('Gtk', '4.0')
    from gi.repository import Gdk, Gtk, Pango

    _HAS_GTK = True
except (ImportError, ValueError):
    _HAS_GTK = False

from config import DEFAULT_CONFIG, THEME_PRESETS, ThemePreset

_MPRIS_PREFIX = 'org.mpris.MediaPlayer2.'
_MPRIS_PATH = '/org/mpris/MediaPlayer2'
_MPRIS_IFACE = 'org.mpris.MediaPlayer2'
_PLAYER_IFACE = 'org.mpris.MediaPlayer2.Player'
_PROPS_IFACE = 'org.freedesktop.DBus.Properties'
_DBUS_IFACE = 'org.freedesktop.DBus'
_DBUS_PATH = '/org/freedesktop/DBus'

_ENUM_TIMEOUT_MS = 8_000
_BUS_TIMEOUT_MS = 5_000

_STATUS_PLAYING = 'Playing'
_STATUS_PAUSED = 'Paused'

# Media-key action names shared with DisplayManager: it maps evdev codes to
# these strings and the shell passes them to MprisWatcher.handle_media_key.
ACTION_PLAY_PAUSE = 'play-pause'
ACTION_NEXT = 'next'
ACTION_PREVIOUS = 'previous'
_MEDIA_ACTIONS = frozenset({ACTION_PLAY_PAUSE, ACTION_NEXT, ACTION_PREVIOUS})


@dataclass(frozen=True)
class PlayerSnapshot:
    """Immutable view of the active player, delivered on the main loop.

    length_us/position_us are MPRIS microseconds (mpris:length, Position);
    None means unknown. Capabilities gate the card's buttons one-to-one.
    """

    bus_name: str
    identity: str
    desktop_entry: str
    status: str
    title: str
    artist: str
    album: str
    can_go_next: bool
    can_go_previous: bool
    can_play: bool
    can_pause: bool
    length_us: int | None = None
    position_us: int | None = None


def _unwrap(value: object) -> object:
    """Unwrap nested GLib.Variant layers; plain values pass through."""
    for _ in range(4):
        unpack = getattr(value, 'unpack', None)
        if not callable(unpack):
            return value
        try:
            value = unpack()
        except Exception:
            return value
    return value


def _as_str(value: object) -> str:
    try:
        return str(_unwrap(value))
    except Exception:
        return ''


def _as_bool(value: object) -> bool:
    try:
        return bool(_unwrap(value))
    except Exception:
        return False


def parse_player_metadata(metadata: object) -> tuple[str, str, str, int | None]:
    """(title, artist, album, length_us) from a Player Metadata dict.

    xesam:artist is a string list per the MPRIS spec and renders comma
    joined; a lone string still works. mpris:length is microseconds; zero
    or negative means unknown (no total), so it maps to None.
    """
    if not isinstance(metadata, dict):
        return '', '', '', None
    title = _as_str(metadata.get('xesam:title', ''))
    raw_artist = _unwrap(metadata.get('xesam:artist', []))
    if isinstance(raw_artist, (list, tuple)):
        artist = ', '.join(a for a in (_as_str(a) for a in raw_artist) if a)
    else:
        artist = _as_str(raw_artist)
    album = _as_str(metadata.get('xesam:album', ''))
    length_us: int | None = None
    try:
        length = int(_unwrap(metadata.get('mpris:length', 0)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        length = 0
    if length > 0:
        length_us = length
    return title, artist, album, length_us


def format_media_subtitle(artist: str, identity: str) -> str:
    """Second-line text: artist + player name, or whichever exists."""
    if artist and identity:
        return f'{artist} \u00b7 {identity}'
    return artist or identity


def play_button_label(status: str) -> str:
    """Text glyph for the play button: pause mark while Playing."""
    return '\u275a\u275a' if status == _STATUS_PLAYING else '\u25b6'


def progress_fraction(
    length_us: int | None, position_us: int | None,
) -> float | None:
    """Playback fraction in [0, 1]; None when the total is unknown.

    Position 0 is a real reading (track start), so only a missing total
    or a missing position hides the line.
    """
    if length_us is None or length_us <= 0 or position_us is None:
        return None
    try:
        return max(0.0, min(1.0, float(position_us) / float(length_us)))
    except (TypeError, ValueError):
        return None


def theme_css(preset: ThemePreset) -> str:
    # Same brand ramp as the shade around it: 3.5% raised card, 10%
    # hairlines, 25% track, foreground fill; the accent is the focus ring.
    fg = preset.foreground
    return f"""
.media-card {{
    background: alpha({fg}, 0.035);
    border: 1px solid alpha({fg}, 0.10);
    border-radius: 16px;
    padding: 14px 16px;
}}
.media-title {{
    font-size: 15px;
    font-weight: 400;
    color: alpha({fg}, 0.90);
}}
.media-sub {{
    font-size: 11px;
    letter-spacing: 0.02em;
    color: {preset.muted};
}}
.media-btn {{
    font-size: 13px;
    color: alpha({fg}, 0.80);
    background: none;
    box-shadow: none;
    border: 1px solid alpha({fg}, 0.10);
    border-radius: 12px;
    /* Fixed-width keys (no theme padding) so the card fits a 320 px
       shade; 44 px tall for the thumb. */
    padding: 0;
    min-width: 64px;
    min-height: 44px;
}}
.media-btn:hover {{
    background: {preset.surface};
}}
.media-btn:active {{
    background: {preset.surface_alt};
}}
.media-btn:focus-visible {{
    outline-color: {preset.accent};
}}
.media-btn:disabled {{
    color: {preset.muted};
}}
.media-progress trough {{
    background: alpha({fg}, 0.25);
    min-height: 2px;
    border-radius: 1px;
}}
.media-progress progress {{
    background: {fg};
    min-height: 2px;
    border-radius: 1px;
}}
"""


class _PlayerState:
    """Mutable per-player accumulation; snapshots freeze it for listeners."""

    __slots__ = (
        'bus_name', 'identity', 'desktop_entry', 'status', 'title',
        'artist', 'album', 'can_go_next', 'can_go_previous', 'can_play',
        'can_pause', 'length_us', 'position_us', 'last_play_seq',
        'last_pause_seq', 'seen_seq', 'sub_id',
    )

    def __init__(self, bus_name: str, seen_seq: int) -> None:
        self.bus_name = bus_name
        self.identity = ''
        self.desktop_entry = ''
        self.status = ''
        self.title = ''
        self.artist = ''
        self.album = ''
        self.can_go_next = False
        self.can_go_previous = False
        self.can_play = False
        self.can_pause = False
        self.length_us: int | None = None
        self.position_us: int | None = None
        self.last_play_seq = 0
        self.last_pause_seq = 0
        self.seen_seq = seen_seq
        self.sub_id: int | None = None

    def snapshot(self) -> PlayerSnapshot:
        identity = self.identity or self.bus_name.removeprefix(_MPRIS_PREFIX)
        return PlayerSnapshot(
            bus_name=self.bus_name,
            identity=identity,
            desktop_entry=self.desktop_entry,
            status=self.status,
            title=self.title,
            artist=self.artist,
            album=self.album,
            can_go_next=self.can_go_next,
            can_go_previous=self.can_go_previous,
            can_play=self.can_play,
            can_pause=self.can_pause,
            length_us=self.length_us,
            position_us=self.position_us,
        )


class MprisWatcher:
    """Track every org.mpris.MediaPlayer2.* name on the session bus.

    Discovery rides NameOwnerChanged plus one async initial ListNames, so
    a wedged bus can never block the GTK loop. Per player the root
    (Identity, DesktopEntry) and Player (PlaybackStatus, Metadata,
    capabilities, Position) property sets are fetched with async
    Properties.GetAll, and a PropertiesChanged subscription keeps each
    player fresh (Position itself is never signaled per the MPRIS spec, so
    the card polls it while visible and Playing).

    The active player is the most recently Playing one, else the most
    recently Paused one, else the most recently seen name. Listeners and
    on_active_changed fire on the main loop with an immutable
    PlayerSnapshot (None when no player exists).
    """

    def __init__(
        self,
        on_active_changed: Callable[[PlayerSnapshot | None], None] | None = None,
    ) -> None:
        self._on_active_changed = on_active_changed
        self._listeners: list[Callable[[PlayerSnapshot | None], None]] = []
        self._bus: Gio.DBusConnection | None = None
        self._players: dict[str, _PlayerState] = {}
        self._fetching: set[tuple[str, str]] = set()
        self._seq = 0
        self._last_emitted: PlayerSnapshot | None | object = object()
        GLib.idle_add(self._init_bus)

    # ------------------------------------------------------------ bus setup

    def _init_bus(self) -> bool:
        from shell_log import get_logger
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._bus.signal_subscribe(
                _DBUS_IFACE, _DBUS_IFACE, 'NameOwnerChanged', _DBUS_PATH,
                None, Gio.DBusSignalFlags.NONE,
                self._on_name_owner_changed, None,
            )
            self._bus.call(
                _DBUS_IFACE, _DBUS_PATH, _DBUS_IFACE, 'ListNames',
                None, GLib.VariantType('(as)'),
                Gio.DBusCallFlags.NONE, _ENUM_TIMEOUT_MS, None,
                self._on_list_names,
            )
        except Exception as err:
            get_logger('media_controls').warning(
                'session bus unavailable: %s', err)
            self._bus = None
        return False

    # ------------------------------------------------------------- public

    def add_listener(
        self, callback: Callable[[PlayerSnapshot | None], None],
    ) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(
        self, callback: Callable[[PlayerSnapshot | None], None],
    ) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def active_snapshot(self) -> PlayerSnapshot | None:
        """Current active player snapshot; None when no player exists."""
        name = self._active_bus_name()
        if name is None:
            return None
        return self._players[name].snapshot()

    def snapshot_for(self, bus_name: str) -> PlayerSnapshot | None:
        player = self._players.get(bus_name)
        return player.snapshot() if player is not None else None

    def players(self) -> list[PlayerSnapshot]:
        """One snapshot per tracked player, for debugging surfaces."""
        return [p.snapshot() for p in self._players.values()]

    def play_pause(self, bus_name: str | None = None) -> bool:
        """Toggle the (active) player; False when there is nothing to drive."""
        return self._control(bus_name, 'PlayPause')

    def next_track(self, bus_name: str | None = None) -> bool:
        return self._control(bus_name, 'Next')

    def previous_track(self, bus_name: str | None = None) -> bool:
        return self._control(bus_name, 'Previous')

    def raise_player(self, bus_name: str | None = None) -> bool:
        """Ask the (active) player to raise its own window (MPRIS Raise)."""
        target = bus_name or self._active_bus_name()
        if target is None or self._bus is None:
            return False
        self._bus.call(
            target, _MPRIS_PATH, _MPRIS_IFACE, 'Raise',
            None, None, Gio.DBusCallFlags.NONE, _BUS_TIMEOUT_MS, None,
            self._on_control_done,
        )
        return True

    def handle_media_key(self, action: str) -> bool:
        """Route a DisplayManager media action to the active player.

        Actions are 'play-pause', 'next', 'previous'; anything else is
        ignored. True only when a player was actually driven.
        """
        if action == ACTION_NEXT:
            return self.next_track()
        if action == ACTION_PREVIOUS:
            return self.previous_track()
        if action == ACTION_PLAY_PAUSE:
            return self.play_pause()
        return False

    def fetch_position(self, bus_name: str | None = None) -> bool:
        """Refresh Position for the (active) player; the merged snapshot is
        emitted like any other update. False when nothing was requested."""
        target = bus_name or self._active_bus_name()
        if target is None or self._bus is None:
            return False
        self._bus.call(
            target, _MPRIS_PATH, _PROPS_IFACE, 'Get',
            GLib.Variant('(ss)', (_PLAYER_IFACE, 'Position')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, _BUS_TIMEOUT_MS, None,
            lambda conn, result: self._on_position(target, conn, result),
        )
        return True

    # ------------------------------------------------------------ internals

    def _control(self, bus_name: str | None, method: str) -> bool:
        target = bus_name or self._active_bus_name()
        if target is None or self._bus is None:
            return False
        self._bus.call(
            target, _MPRIS_PATH, _PLAYER_IFACE, method,
            None, None, Gio.DBusCallFlags.NONE, _BUS_TIMEOUT_MS, None,
            self._on_control_done,
        )
        return True

    def _on_control_done(self, conn, result) -> None:
        from shell_log import get_logger
        try:
            conn.call_finish(result)
        except Exception as err:
            get_logger('media_controls').warning(
                'player control failed: %s', err)

    def _on_position(self, bus_name: str, conn, result) -> None:
        player = self._players.get(bus_name)
        if player is None:
            return
        try:
            position = int(_unwrap(conn.call_finish(result).unpack()[0]))  # type: ignore[arg-type]
        except (TypeError, ValueError, AttributeError, IndexError):
            return
        except Exception:
            return
        if position < 0:
            return
        player.position_us = position
        self._emit()

    def _tick(self) -> int:
        self._seq += 1
        return self._seq

    def _active_bus_name(self) -> str | None:
        playing = [(p.last_play_seq, n) for n, p in self._players.items()
                   if p.status == _STATUS_PLAYING]
        if playing:
            return max(playing)[1]
        paused = [(p.last_pause_seq, n) for n, p in self._players.items()
                  if p.status == _STATUS_PAUSED]
        if paused:
            return max(paused)[1]
        if self._players:
            return max((p.seen_seq, n) for n, p in self._players.items())[1]
        return None

    def _emit(self) -> None:
        snapshot = self.active_snapshot()
        if snapshot == self._last_emitted:
            return
        self._last_emitted = snapshot
        if self._on_active_changed is not None:
            GLib.idle_add(self._on_active_changed, snapshot)
        for listener in list(self._listeners):
            GLib.idle_add(listener, snapshot)

    # ------------------------------------------------------------ tracking

    def _on_list_names(self, conn, result) -> None:
        from shell_log import get_logger
        try:
            names = conn.call_finish(result).unpack()[0]
        except Exception as err:
            get_logger('media_controls').warning(
                'ListNames failed: %s', err)
            return
        for name in names:
            if isinstance(name, str) and name.startswith(_MPRIS_PREFIX):
                self._adopt_player(str(name))

    def _adopt_player(self, bus_name: str) -> None:
        if bus_name in self._players or self._bus is None:
            return
        self._players[bus_name] = _PlayerState(bus_name, self._tick())
        self._players[bus_name].sub_id = self._bus.signal_subscribe(
            bus_name, _PROPS_IFACE, 'PropertiesChanged', _MPRIS_PATH,
            None, Gio.DBusSignalFlags.NONE,
            lambda conn, sender, path, iface, sig, params, _ud, _name=bus_name:
                self._on_properties_changed(
                    _name, conn, sender, path, iface, sig, params, _ud),
            None,
        )
        for iface in (_MPRIS_IFACE, _PLAYER_IFACE):
            key = (bus_name, iface)
            if key in self._fetching:
                continue
            self._fetching.add(key)
            self._bus.call(
                bus_name, _MPRIS_PATH, _PROPS_IFACE, 'GetAll',
                GLib.Variant('(s)', (iface,)),
                GLib.VariantType('(a{sv})'),
                Gio.DBusCallFlags.NONE, _BUS_TIMEOUT_MS, None,
                (lambda conn, result, _name=bus_name, _iface=iface:
                    self._on_get_all(_name, _iface, conn, result)),
            )
        self._emit()

    def _on_get_all(
        self, bus_name: str, iface: str, conn, result,
    ) -> None:
        from shell_log import get_logger
        self._fetching.discard((bus_name, iface))
        player = self._players.get(bus_name)
        if player is None:
            return
        try:
            props = dict(conn.call_finish(result).unpack()[0])
        except Exception as err:
            get_logger('media_controls').warning(
                'GetAll %s on %s failed: %s', iface, bus_name, err)
            return
        if iface == _MPRIS_IFACE:
            player.identity = _as_str(props.get('Identity', ''))
            player.desktop_entry = _as_str(props.get('DesktopEntry', ''))
        elif iface == _PLAYER_IFACE:
            self._apply_player_props(player, props)
        self._emit()

    def _apply_player_props(
        self, player: _PlayerState, props: dict,
    ) -> None:
        status = _as_str(props.get('PlaybackStatus', ''))
        if status and status != player.status:
            player.status = status
            if status == _STATUS_PLAYING:
                player.last_play_seq = self._tick()
            elif status == _STATUS_PAUSED:
                player.last_pause_seq = self._tick()
        if 'Metadata' in props:
            try:
                title, artist, album, length = parse_player_metadata(
                    dict(props['Metadata'])
                    if not hasattr(props['Metadata'], 'unpack')
                    else _unwrap(props['Metadata']))
            except (TypeError, ValueError, AttributeError):
                title, artist, album, length = '', '', '', None
            player.title = title
            player.artist = artist
            player.album = album
            player.length_us = length
        if 'Position' in props:
            try:
                position = int(_unwrap(props['Position']))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                position = -1
            player.position_us = position if position >= 0 else None
        for key, attr in (('CanGoNext', 'can_go_next'),
                          ('CanGoPrevious', 'can_go_previous'),
                          ('CanPlay', 'can_play'),
                          ('CanPause', 'can_pause')):
            if key in props:
                setattr(player, attr, _as_bool(props[key]))

    def _drop_player(self, bus_name: str) -> None:
        player = self._players.pop(bus_name, None)
        if player is None:
            return
        for iface in (_MPRIS_IFACE, _PLAYER_IFACE):
            self._fetching.discard((bus_name, iface))
        if player.sub_id is not None and self._bus is not None:
            try:
                self._bus.signal_unsubscribe(player.sub_id)
            except Exception:
                pass
        self._emit()

    # ------------------------------------------------------------- signals

    def _on_name_owner_changed(
        self, _conn, _sender, _path, _iface, _sig, params, _ud,
    ) -> None:
        try:
            name, _old_owner, new_owner = params.unpack()
        except Exception:
            return
        if not isinstance(name, str) or not name.startswith(_MPRIS_PREFIX):
            return
        if new_owner:
            self._adopt_player(name)
        else:
            self._drop_player(name)

    def _on_properties_changed(
        self, bus_name, _conn, _sender, _path, _iface, _sig, params, _ud,
    ) -> None:
        player = self._players.get(bus_name)
        if player is None:
            return
        try:
            unpacked = params.unpack()
            iface_name, changed = unpacked[0], dict(unpacked[1])
        except (TypeError, ValueError, AttributeError, IndexError):
            return
        except Exception:
            return
        if iface_name == _MPRIS_IFACE:
            if 'Identity' in changed:
                player.identity = _as_str(changed['Identity'])
            if 'DesktopEntry' in changed:
                player.desktop_entry = _as_str(changed['DesktopEntry'])
        elif iface_name == _PLAYER_IFACE:
            self._apply_player_props(player, changed)
        else:
            return
        self._emit()


if _HAS_GTK:

    class MediaCard(Gtk.Box):
        """Text-first now-playing card, theme-colored like every surface.

        Title (ellipsized, tap to raise the player app), artist/player
        subtitle, ‹‹ ▶/❚❚ ›› text buttons gated by the player's own
        capabilities, and a thin progress line while Length + Position are
        known. Hidden entirely when no player exists. Position is polled
        once a second only while the card is visible and the track is
        Playing; the timer never runs otherwise.
        """

        def __init__(
            self,
            watcher: MprisWatcher | None = None,
            on_raise: Callable[[str, str], None] | None = None,
            config: object | None = None,
            locked: bool = False,
        ) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.add_css_class('media-card')
            self._watcher: MprisWatcher | None = None
            self._on_raise = on_raise
            self._config = config
            # Lock-screen embedding: transport controls stay live but the
            # title tap never raises or launches the player app.
            self._locked = bool(locked)
            self._snapshot: PlayerSnapshot | None = None
            self._pos_src: int | None = None

            # Single display-level provider, reloaded by apply_theme so hot
            # reloads never stack a provider per call.
            self._theme_provider = Gtk.CssProvider()
            try:
                display = Gdk.Display.get_default()
            except Exception:
                display = None
            if display is not None:
                try:
                    Gtk.StyleContext.add_provider_for_display(
                        display, self._theme_provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
                    )
                except Exception:
                    pass
            self.apply_theme()

            self._title = Gtk.Label(xalign=0)
            self._title.add_css_class('media-title')
            self._title.set_ellipsize(Pango.EllipsizeMode.END)
            self._title.set_hexpand(True)
            tap = Gtk.GestureClick.new()
            tap.connect('pressed', self._on_title_pressed)
            self._title.add_controller(tap)

            self._subtitle = Gtk.Label(xalign=0)
            self._subtitle.add_css_class('media-sub')
            self._subtitle.set_ellipsize(Pango.EllipsizeMode.END)

            controls = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            self._prev_btn = self._make_button('\u2039\u2039')
            self._play_btn = self._make_button('\u25b6')
            self._next_btn = self._make_button('\u203a\u203a')
            self._prev_btn.connect(
                'clicked', lambda _b: self._control(ACTION_PREVIOUS))
            self._play_btn.connect(
                'clicked', lambda _b: self._control(ACTION_PLAY_PAUSE))
            self._next_btn.connect(
                'clicked', lambda _b: self._control(ACTION_NEXT))
            controls.append(self._prev_btn)
            controls.append(self._play_btn)
            controls.append(self._next_btn)

            self._progress = Gtk.ProgressBar()
            self._progress.add_css_class('media-progress')
            self._progress.set_show_text(False)
            self._progress.set_visible(False)

            self.append(self._title)
            self.append(self._subtitle)
            self.append(controls)
            self.append(self._progress)

            self.connect('map', lambda _w: self._update_polling())
            self.connect('unmap', lambda _w: self._update_polling())
            self.set_visible(False)
            if watcher is not None:
                self.set_watcher(watcher)

        def _make_button(self, text: str) -> Gtk.Button:
            button = Gtk.Button(label=text)
            button.add_css_class('flat')
            button.add_css_class('media-btn')
            return button

        def set_watcher(self, watcher: MprisWatcher | None) -> None:
            """Attach a (new) watcher; None detaches and hides the card."""
            old = self._watcher
            if old is not None:
                try:
                    old.remove_listener(self._on_snapshot)
                except Exception:
                    pass
            self._watcher = watcher
            if watcher is not None:
                watcher.add_listener(self._on_snapshot)
                self._on_snapshot(watcher.active_snapshot())
            else:
                self._on_snapshot(None)

        def refresh(self) -> None:
            """Re-render from the watcher's current snapshot."""
            watcher = self._watcher
            self._on_snapshot(
                watcher.active_snapshot() if watcher is not None else None)

        def apply_theme(self, preset: ThemePreset | None = None) -> None:
            """Reload this card's single display-level sheet."""
            resolved = preset
            if resolved is None:
                config = self._config
                if config is not None:
                    try:
                        resolved = config.theme
                    except Exception:
                        resolved = None
            if resolved is None:
                resolved = THEME_PRESETS[DEFAULT_CONFIG['theme']]
            try:
                self._theme_provider.load_from_data(
                    theme_css(resolved).encode('utf-8'))
            except Exception:
                pass

        def _on_snapshot(self, snapshot: PlayerSnapshot | None) -> None:
            self._snapshot = snapshot
            if snapshot is None:
                self.set_visible(False)
                self._update_polling()
                return
            self.set_visible(True)
            self._title.set_text(snapshot.title or 'Unknown track')
            self._subtitle.set_text(
                format_media_subtitle(snapshot.artist, snapshot.identity))
            playing = snapshot.status == _STATUS_PLAYING
            self._play_btn.set_label(play_button_label(snapshot.status))
            self._prev_btn.set_sensitive(snapshot.can_go_previous)
            self._next_btn.set_sensitive(snapshot.can_go_next)
            self._play_btn.set_sensitive(
                snapshot.can_pause if playing else snapshot.can_play)
            fraction = progress_fraction(
                snapshot.length_us, snapshot.position_us)
            if fraction is None:
                self._progress.set_visible(False)
            else:
                self._progress.set_visible(True)
                self._progress.set_fraction(fraction)
            self._update_polling()

        def _on_title_pressed(
            self, _gesture, _n_press: int, _x: float, _y: float,
        ) -> None:
            if getattr(self, '_locked', False):
                return
            snapshot = self._snapshot
            if snapshot is None:
                return
            if snapshot.desktop_entry:
                if callable(self._on_raise):
                    try:
                        self._on_raise(
                            snapshot.desktop_entry, snapshot.bus_name)
                    except Exception:
                        pass
                    return
                self._launch_desktop_entry(snapshot.desktop_entry)
            elif self._watcher is not None:
                try:
                    self._watcher.raise_player(snapshot.bus_name)
                except Exception:
                    pass

        def _launch_desktop_entry(self, desktop_entry: str) -> None:
            try:
                app_info = Gio.DesktopAppInfo.new(f'{desktop_entry}.desktop')
                if app_info is not None:
                    app_info.launch([], None)
                    return
            except Exception:
                pass
            try:
                subprocess.Popen(['gtk-launch', desktop_entry], close_fds=True)
            except (FileNotFoundError, OSError):
                pass

        def _control(self, action: str) -> None:
            snapshot = self._snapshot
            watcher = self._watcher
            if snapshot is None or watcher is None:
                return
            try:
                if action == ACTION_NEXT:
                    watcher.next_track(snapshot.bus_name)
                elif action == ACTION_PREVIOUS:
                    watcher.previous_track(snapshot.bus_name)
                else:
                    watcher.play_pause(snapshot.bus_name)
            except Exception:
                pass

        def _is_shown(self) -> bool:
            try:
                if not self.get_visible():
                    return False
            except Exception:
                return False
            try:
                return bool(self.is_visible())
            except Exception:
                return True

        def _want_polling(self) -> bool:
            snapshot = self._snapshot
            return (snapshot is not None
                    and snapshot.status == _STATUS_PLAYING
                    and snapshot.length_us is not None
                    and self._is_shown())

        def _update_polling(self) -> None:
            want = self._want_polling()
            if want and self._pos_src is None:
                try:
                    self._pos_src = GLib.timeout_add_seconds(
                        1, self._on_position_tick)
                except Exception:
                    self._pos_src = None
            elif not want and self._pos_src is not None:
                try:
                    GLib.source_remove(self._pos_src)
                except Exception:
                    pass
                self._pos_src = None

        def _on_position_tick(self) -> bool:
            if not self._want_polling():
                self._pos_src = None
                return GLib.SOURCE_REMOVE
            watcher = self._watcher
            snapshot = self._snapshot
            if watcher is not None and snapshot is not None:
                try:
                    watcher.fetch_position(snapshot.bus_name)
                except Exception:
                    pass
            return GLib.SOURCE_CONTINUE

else:

    class MediaCard:  # type: ignore[no-redef]
        """Headless stand-in: importable without GTK, every call a no-op."""

        def __init__(self, *args, **kwargs) -> None:
            self._snapshot = None

        def set_watcher(self, watcher=None) -> None:
            pass

        def refresh(self) -> None:
            pass

        def apply_theme(self, preset=None) -> None:
            pass

