"""xx-auto drive screen: MPRIS now-playing card, tiles, settings, sheets.

Headless-safe import: Gtk/Adw load lazily so pure tests never need a
display. Worker threads own every D-Bus round trip; results post back
with GLib.idle_add.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

ICON_DIR = Path(__file__).with_name("icons")

# Drive-screen sizes on top of xx_ui: 72 px targets, light weight, and
# theme colors by xx_ui's named colors so every preset (and always_ink)
# repaints live. Landscape variants hang off .xx-auto-landscape.
APP_CSS = """
.xx-auto-title { font-size: 40px; font-weight: 300; }
.xx-auto-landscape .xx-auto-title { font-size: 30px; }
.xx-auto-title.xx-auto-idle { font-size: 32px; color: @insensitive_fg_color; }
.xx-auto-landscape .xx-auto-title.xx-auto-idle { font-size: 28px; }
.xx-auto-sub { font-size: 24px; font-weight: 300; }
.xx-auto-landscape .xx-auto-sub { font-size: 20px; }
.xx-auto-sub.xx-auto-idle { font-size: 17px; }
.xx-auto-time { font-size: 17px; font-feature-settings: "tnum"; }
progressbar.xx-auto-progress trough,
progressbar.xx-auto-progress progress { min-height: 2px; border-radius: 0; }
progressbar.xx-auto-progress trough { background-color: @borders; }
button.xx-auto-key {
  min-width: 72px; min-height: 72px; padding: 0;
  border-radius: 9999px; color: @theme_fg_color;
}
button.xx-auto-key:active { background-color: @card_bg_color; }
button.xx-auto-key:disabled { color: @insensitive_fg_color; filter: none; }
button.xx-auto-key.xx-auto-play {
  min-width: 88px; min-height: 88px; border: 2px solid @theme_fg_color;
}
.xx-auto-landscape button.xx-auto-key.xx-auto-play {
  min-width: 72px; min-height: 72px;
}
.xx-auto-key-text { font-size: 17px; font-weight: 400; }
button.xx-auto-tile {
  min-height: 72px; padding: 10px 14px; border-radius: 6px;
  border: 1px solid @theme_fg_color; color: @theme_fg_color;
}
.xx-auto-landscape button.xx-auto-tile {
  min-width: 104px; min-height: 92px; padding: 12px 14px;
}
button.xx-auto-tile:active { background-color: @card_bg_color; }
button.xx-auto-tile.active {
  background-color: @theme_fg_color; color: @theme_bg_color;
}
button.xx-auto-tile.active .xx-secondary { color: @theme_bg_color; }
button.xx-auto-tile:disabled { border-color: @borders; filter: none; }
button.xx-auto-tile:disabled .xx-auto-tile-name,
button.xx-auto-tile:disabled image { color: @insensitive_fg_color; }
button.xx-auto-tile:disabled .xx-secondary { color: @destructive_color; }
.xx-auto-tile-name { font-size: 22px; font-weight: 300; }
button.xx-auto-gear {
  min-width: 72px; min-height: 56px; padding: 0;
  color: @insensitive_fg_color;
}
button.xx-auto-row {
  min-height: 72px; padding: 6px 0; border-radius: 0;
}
button.xx-auto-row:active { background-color: @card_bg_color; }
.xx-auto-row-title { font-size: 20px; font-weight: 300; }
.xx-auto-row image { color: @insensitive_fg_color; }
.xx-auto-setting { min-height: 72px; }
.xx-auto-section { margin-top: 8px; }
.xx-auto-back { min-width: 56px; min-height: 72px; padding: 0; }
"""


def _require_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: F401

    return Adw, Gdk, Gio, GLib, Gtk, Pango


def _label(Gtk, Pango, text, cls, size_cls=None, chars=20):
    line = Gtk.Label(label=text)
    line.add_css_class(cls)
    if size_cls:
        line.add_css_class(size_cls)
    line.set_xalign(0.0)
    line.set_max_width_chars(chars)
    line.set_ellipsize(Pango.EllipsizeMode.END)
    return line


def _fit(label, Pango, text, chars) -> None:
    label.set_text(text)
    if len(text) > chars:
        label.set_ellipsize(Pango.EllipsizeMode.END)
    else:
        label.set_ellipsize(Pango.EllipsizeMode.NONE)


def _wrap(Gtk, Pango, text, cls, size_cls=None, chars=24):
    line = Gtk.Label(label=text)
    line.add_css_class(cls)
    if size_cls:
        line.add_css_class(size_cls)
    line.set_xalign(0.0)
    line.set_wrap(True)
    line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    line.set_max_width_chars(chars)
    return line


def _texts(Gtk, Pango, title, subtitle, title_cls="xx-auto-row-title"):
    """Title over a muted subtitle, the body of every list row."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_hexpand(True)
    box.set_valign(Gtk.Align.CENTER)
    box.append(_wrap(Gtk, Pango, title, "xx-title", title_cls, chars=30))
    if subtitle:
        box.append(_wrap(Gtk, Pango, subtitle, "xx-secondary", chars=36))
    return box


def _icon(Gtk, name, px):
    img = Gtk.Image.new_from_icon_name(name)
    img.set_pixel_size(px)
    return img


class DriveSnapshot:
    """What the drive page shows; live MPRIS or seeded demo data."""

    def __init__(self, tracks=None, installed=None, maps_title="Maps",
                 maps_sub="xx-maps", note=None) -> None:
        from .mpris import sort_players

        self.tracks = sort_players(list(tracks or []))
        self.installed = dict(installed or {})
        self.maps_title = maps_title
        self.maps_sub = maps_sub
        self.note = note


def _active_index(tracks) -> int | None:
    from .session_pick import PlayerState, active

    return active([PlayerState(t.is_playing, t.has_item) for t in tracks])


class AutoWindow:
    """Main window; built inside an Adw.ApplicationWindow."""

    def __init__(self, app, settings) -> None:
        Adw, Gdk, Gio, GLib, Gtk, Pango = _require_gtk()
        self._Adw, self._Gdk, self._Gio = Adw, Gdk, Gio
        self._GLib, self._Gtk, self._Pango = GLib, Gtk, Pango
        self.app = app
        self.settings = settings
        self.snapshot = DriveSnapshot()
        self._demo = False
        self._favourites: list = []
        self._fav_note: str | None = None
        self._players_rows: list = []
        self._car_devices: list = []
        self._inhibit_cookie = 0
        self._poll_timer = 0
        self._landscape = False

        import xx_ui

        self._xx_ui = xx_ui
        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-auto")
        self.win.set_default_size(360, 720)
        self._apply_theme()
        self._load_app_css()
        try:
            self._theme_unsub = xx_ui.on_change(self._on_suite_theme)
        except Exception:
            self._theme_unsub = None

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        self._build_drive()
        self._build_settings()
        self._build_sheet_players()
        self._build_sheet_favourites()
        self._build_sheet_car()
        self._build_sheet_licenses()
        self.win.set_content(self.stack)
        self.show("drive")
        use_landscape = not settings.follow_rotation
        self.set_landscape(use_landscape)
        if settings.keep_screen_on:
            self.win.connect("map", lambda *_: self._update_inhibit())
            self.win.connect("unmap", lambda *_: self._update_inhibit())
        self._start_poll()

    # -- theme ------------------------------------------------------
    def _apply_theme(self) -> None:
        try:
            import xx_ui
            from xx_ui import theme as theme_mod

            if self.settings.always_ink:
                suite = theme_mod.read_config()
                preset = theme_mod.THEME_PRESETS["amoled"]
                active = theme_mod.Theme(
                    key="amoled", background=preset.background,
                    surface=preset.surface, surface_alt=preset.surface_alt,
                    border=preset.border, foreground=preset.foreground,
                    muted=preset.muted, accent=preset.accent,
                    font_family=theme_mod.resolve_font_family(suite),
                    scale=theme_mod.resolve_scale(suite), dark=True)
                xx_ui.css.install(active)
            else:
                xx_ui.apply(self.win)
        except Exception:
            pass

    def _on_suite_theme(self, _theme) -> None:
        if self.settings.always_ink:
            self._apply_theme()

    def _load_app_css(self) -> None:
        try:
            Gtk = self._Gtk
            Gdk = self._Gdk
            provider = Gtk.CssProvider()
            provider.load_from_data(APP_CSS.encode("utf-8"))
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                Gtk.IconTheme.get_for_display(display).add_search_path(
                    str(ICON_DIR))
        except Exception:
            pass

    # -- navigation ---------------------------------------------------
    def show(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def set_landscape(self, on: bool) -> None:
        try:
            Gtk = self._Gtk
            self.win.set_default_size(*((720, 360) if on else (360, 720)))
            self.drive_columns.set_orientation(
                Gtk.Orientation.HORIZONTAL if on else Gtk.Orientation.VERTICAL)
            self.drive_columns.set_spacing(28 if on else 0)
            if on:
                self.drive_page.add_css_class("xx-auto-landscape")
            else:
                self.drive_page.remove_css_class("xx-auto-landscape")
            for tile in self._tiles.values():
                tile._icon.set_visible(on)
            self.title_label.set_lines(1 if on else 2)
            self._landscape = on
            self.refresh()
        except Exception:
            pass

    # -- drive page ----------------------------------------------------
    def _build_drive(self) -> None:
        from .drive_tiles import Tile

        Gtk, Pango = self._Gtk, self._Pango
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(12)
        self.drive_page = page

        self.drive_columns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.drive_columns.set_vexpand(True)

        # The card centers in whatever the tiles leave, so an idle or a
        # short card sits balanced instead of hugging the top.
        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card_box.set_vexpand(True)
        self.card_box.set_hexpand(True)
        self.card_box.set_valign(Gtk.Align.CENTER)
        self.source_label = _label(Gtk, Pango, "", "xx-caption")
        self.source_label.set_margin_bottom(8)
        self.title_label = Gtk.Label(label="")
        self.title_label.add_css_class("xx-display")
        self.title_label.add_css_class("xx-auto-title")
        self.title_label.set_xalign(0.0)
        self.title_label.set_wrap(True)
        self.title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title_label.set_lines(2)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_max_width_chars(18)
        self.sub_label = _wrap(Gtk, Pango, "", "xx-secondary", "xx-auto-sub")
        self.sub_label.set_margin_top(6)
        self.sub_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.progress = Gtk.ProgressBar()
        self.progress.add_css_class("xx-auto-progress")
        self.progress.set_hexpand(True)
        self.progress.set_margin_top(16)
        self.time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.time_row.set_margin_top(8)
        self.elapsed_label = _label(Gtk, Pango, "", "xx-time", "xx-auto-time")
        self.elapsed_label.set_max_width_chars(7)
        self.elapsed_label.set_hexpand(True)
        self.duration_label = _label(Gtk, Pango, "", "xx-time", "xx-auto-time")
        self.duration_label.set_max_width_chars(7)
        self.duration_label.set_xalign(1.0)
        self.time_row.append(self.elapsed_label)
        self.time_row.append(self.duration_label)
        # One control system: equal columns, 72 px round keys, the play
        # key outlined. Custom actions reuse the same keys and columns.
        self.transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 homogeneous=True)
        self.transport.set_margin_top(16)
        self.prev_btn = self._key("media-skip-backward-symbolic", self._on_prev)
        self.play_btn = self._key("media-playback-start-symbolic",
                                  self._on_play_pause)
        self.play_btn.add_css_class("xx-auto-play")
        self.next_btn = self._key("media-skip-forward-symbolic", self._on_next)
        for btn in (self.prev_btn, self.play_btn, self.next_btn):
            self.transport.append(btn)
        self.actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   homogeneous=True)
        self.actions_row.set_margin_top(4)
        self.note_label = _wrap(Gtk, Pango, "", "xx-secondary")
        self.note_label.set_margin_top(12)
        for widget in (self.source_label, self.title_label, self.sub_label,
                       self.progress, self.time_row, self.transport,
                       self.actions_row, self.note_label):
            self.card_box.append(widget)

        self.tiles_grid = Gtk.Grid(column_spacing=12, row_spacing=12,
                                   column_homogeneous=True,
                                   row_homogeneous=True)
        # Tile labels expand inside their tile; keep that from leaking up.
        self.tiles_grid.set_vexpand(False)
        self._tiles = {
            Tile.RADIO: self._tile("audio-x-generic-symbolic",
                                   self._on_tile_radio),
            Tile.AUDIOBOOK: self._tile("accessories-dictionary-symbolic",
                                       self._on_tile_audio),
            Tile.MAPS: self._tile("mark-location-symbolic",
                                  self._on_tile_maps),
            Tile.CALLS: self._tile("call-start-symbolic",
                                   self._on_tile_calls),
        }
        self.tile_radio = self._tiles[Tile.RADIO]
        self.tile_audio = self._tiles[Tile.AUDIOBOOK]
        self.tile_maps = self._tiles[Tile.MAPS]
        self.tile_calls = self._tiles[Tile.CALLS]
        for index, tile in enumerate(self._tiles.values()):
            self.tiles_grid.attach(tile, index % 2, index // 2, 1, 1)

        self.gear = Gtk.Button()
        self.gear.add_css_class("xx-auto-gear")
        self.gear.set_tooltip_text("Settings")
        gear_img = _icon(Gtk, "emblem-system-symbolic", 24)
        # Glyph flush with the tiles' right edge; the target stays 72 px.
        gear_img.set_halign(Gtk.Align.END)
        gear_img.set_margin_end(4)
        self.gear.set_child(gear_img)
        self.gear.set_halign(Gtk.Align.END)
        self.gear.set_valign(Gtk.Align.END)
        self.gear.set_vexpand(True)
        self.gear.connect("clicked", lambda *_: self.show("settings"))

        self.right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.right_box.set_hexpand(False)
        self.right_box.set_vexpand(False)
        self.right_box.append(self.tiles_grid)
        self.right_box.append(self.gear)

        self.drive_columns.append(self.card_box)
        self.drive_columns.append(self.right_box)
        page.append(self.drive_columns)
        self.stack.add_named(page, "drive")

    def _key(self, child, callback, *args):
        """A 72 px round key; ``child`` is an icon name or a widget."""
        Gtk = self._Gtk
        btn = Gtk.Button()
        btn.add_css_class("xx-auto-key")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_child(_icon(Gtk, child, 32) if isinstance(child, str)
                      else child)
        btn.connect("clicked", callback, *args)
        return btn

    def _action_key(self, action, bus_name):
        """A custom action as a transport key: its icon, else its name."""
        Gtk = self._Gtk
        display = self.win.get_display()
        if action.icon_name and display is not None and \
                Gtk.IconTheme.get_for_display(display).has_icon(
                    action.icon_name):
            child = action.icon_name
        else:
            child = _label(Gtk, self._Pango, action.label,
                           "xx-auto-key-text", chars=9)
            child.set_xalign(0.5)
        btn = self._key(child, self._on_custom_action, bus_name,
                        action.action_id)
        btn.set_tooltip_text(action.label)
        btn.set_sensitive(action.enabled)
        return btn

    def _tile(self, icon, callback):
        Gtk, Pango = self._Gtk, self._Pango
        btn = Gtk.Button()
        btn.add_css_class("xx-auto-tile")
        btn.set_hexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_valign(Gtk.Align.FILL)
        btn._icon = _icon(Gtk, icon, 20)
        btn._icon.set_halign(Gtk.Align.START)
        btn._icon.set_valign(Gtk.Align.START)
        btn._icon.set_vexpand(True)
        btn._icon.set_visible(False)
        name = _label(Gtk, Pango, "", "xx-auto-tile-name", chars=9)
        name.set_valign(Gtk.Align.END)
        name.set_vexpand(True)
        sub = _label(Gtk, Pango, "", "xx-secondary", chars=12)
        sub.set_valign(Gtk.Align.START)
        sub.set_vexpand(True)
        box.append(btn._icon)
        box.append(name)
        box.append(sub)
        btn.set_child(box)
        btn.connect("clicked", callback)
        btn._title_label = name
        btn._sub_label = sub
        return btn

    def _set_tile(self, btn, title, subtitle, enabled=True,
                  active=False) -> None:
        btn._title_label.set_text(title)
        btn._sub_label.set_text(subtitle)
        btn.set_sensitive(enabled)
        if active and enabled:
            btn.add_css_class("active")
        else:
            btn.remove_css_class("active")

    def _set_idle(self, idle: bool) -> None:
        for widget in (self.title_label, self.sub_label):
            if idle:
                widget.add_css_class("xx-auto-idle")
            else:
                widget.remove_css_class("xx-auto-idle")
        self.sub_label.set_lines(4 if idle else (1 if self._landscape else 2))

    # -- drive refresh -------------------------------------------------
    def refresh(self) -> None:
        from . import elapsed as elapsed_mod
        from .drive_tiles import Tile, tile_for, visible
        from .mpris import display_name

        snap = self.snapshot
        shown = visible(self.settings.surface_quick_pick,
                        self.settings.surface_nav, self.settings.surface_calls)
        idx = _active_index(snap.tracks)
        track = snap.tracks[idx] if idx is not None else None
        show_card = self.settings.surface_now_playing

        for child in list(self._iter_children(self.actions_row)):
            self.actions_row.remove(child)
        playing = track is not None and show_card
        self._set_idle(not playing)
        self.transport.set_visible(playing)
        self.source_label.set_visible(playing)
        if playing:
            _fit(self.source_label, self._Pango,
                 display_name(track).lower() or "playing", 20)
            self.title_label.set_text(track.title or "Unknown title")
            sub = track.artist or ""
            self.sub_label.set_text(sub)
            self.sub_label.set_visible(bool(sub))
            seekable = track.length_us > 0
            self.progress.set_visible(seekable)
            self.time_row.set_visible(seekable)
            if seekable:
                pos_ms = track.position_us // 1000
                dur_ms = track.length_us // 1000
                _fit(self.elapsed_label, self._Pango,
                     elapsed_mod.format_ms(min(pos_ms, dur_ms)), 7)
                _fit(self.duration_label, self._Pango,
                     elapsed_mod.format_ms(dur_ms), 7)
                try:
                    self.progress.set_fraction(
                        min(1.0, pos_ms / max(1, dur_ms)))
                except Exception:
                    self.progress.set_fraction(0.0)
            icon = ("media-playback-pause-symbolic" if track.is_playing
                    else "media-playback-start-symbolic")
            self.play_btn.set_child(_icon(self._Gtk, icon, 32))
            for action in (track.actions or [])[:3]:
                self.actions_row.append(
                    self._action_key(action, track.bus_name))
            self.actions_row.set_visible(bool(track.actions))
        else:
            self.actions_row.set_visible(False)
            self.progress.set_visible(False)
            self.time_row.set_visible(False)
            self.title_label.set_text("Nothing playing" if show_card else "")
            hint = ""
            if show_card and not snap.tracks:
                if not snap.installed.get("radio") and not snap.installed.get(
                        "audiobook"):
                    hint = ("Install the radio or xx-audiobook from "
                            "xx-apps, and its controls show up here.")
                else:
                    hint = ("Start something in the radio or "
                            "xx-audiobook, or press Radio.")
            # Word joiners keep "xx-audiobook" whole across line breaks.
            self.sub_label.set_text(hint.replace("-", "-\u2060"))
            self.sub_label.set_visible(bool(hint))
        self.title_label.set_visible(show_card)
        self.note_label.set_text(snap.note or "")
        self.note_label.set_visible(bool(snap.note) and not self._landscape)

        radio_on = snap.installed.get("radio", bool(snap.tracks))
        audio_on = snap.installed.get("audiobook", bool(snap.tracks))
        current = tile_for(track.desktop_entry, track.identity) \
            if track is not None else None
        content = {
            Tile.RADIO: ("Radio", "Stations" if radio_on else "not installed",
                         radio_on),
            Tile.AUDIOBOOK: ("Audiobook",
                             "xx-audiobook" if audio_on else "not installed",
                             audio_on),
            Tile.MAPS: (snap.maps_title, snap.maps_sub, True),
            Tile.CALLS: ("Calls", "Favourites", True),
        }
        for kind, tile in self._tiles.items():
            tile.set_visible(kind in shown)
            title, sub, enabled = content[kind]
            self._set_tile(tile, title, sub, enabled, active=kind is current)

    def _iter_children(self, box):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            yield child
            child = nxt

    # -- transport actions (worker thread) -------------------------------
    def _run_worker(self, func) -> None:
        def _runner():
            try:
                func()
            except Exception as exc:
                from .applog import LOG

                LOG.w("drive", f"media call failed: {type(exc).__name__}")

        threading.Thread(target=_runner, daemon=True).start()

    def _bus_name_of_active(self):
        idx = _active_index(self.snapshot.tracks)
        if idx is None:
            return None
        return self.snapshot.tracks[idx].bus_name

    def _on_play_pause(self, _btn) -> None:
        bus = self._bus_name_of_active()
        if bus is None:
            return

        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            mpris_mod.send_player_method(conn, bus, "PlayPause")

        self._run_worker(_work)

    def _on_prev(self, _btn) -> None:
        bus = self._bus_name_of_active()
        if bus is None:
            return

        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            mpris_mod.send_player_method(conn, bus, "Previous")

        self._run_worker(_work)

    def _on_next(self, _btn) -> None:
        bus = self._bus_name_of_active()
        if bus is None:
            return

        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            mpris_mod.send_player_method(conn, bus, "Next")

        self._run_worker(_work)

    def _on_custom_action(self, _btn, bus_name, action_id) -> None:
        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            try:
                mpris_mod.activate_action(conn, bus_name, action_id)
            except Exception as exc:
                from .applog import LOG

                LOG.w("drive", f"custom action failed: {type(exc).__name__}")
                self._GLib.idle_add(self._set_note,
                                    "That button did not respond.")

        self._run_worker(_work)

    def _set_note(self, text) -> bool:
        self.snapshot.note = text
        self.refresh()
        return False

    # -- tile actions ------------------------------------------------------
    def _on_tile_radio(self, _btn) -> None:
        from .drive_tiles import Tile

        self._open_tile_app(Tile.RADIO)

    def _on_tile_audio(self, _btn) -> None:
        from .drive_tiles import Tile

        self._open_tile_app(Tile.AUDIOBOOK)

    def _open_tile_app(self, tile) -> None:
        """A media tile opens its player when one is on the bus, else it
        launches the tile's app (Radio -> xx-radio when nothing radio is
        playing). The lookup is by tile, never by app id, so another app
        built on the shared music core takes the sheet path as well."""
        from .drive_tiles import Tile, tile_for

        for track in self.snapshot.tracks:
            if tile_for(track.desktop_entry, track.identity) is tile:
                self._open_players_sheet()
                return

        def _work():
            from . import media_apps as media_mod

            if tile is Tile.RADIO:
                ok = media_mod.launch_radio()
                note = "Radio is not installed."
            else:
                ok = media_mod.launch_audiobook()
                note = "Audiobook is not installed."
            if not ok:
                self._GLib.idle_add(self._set_note, note)

        self._run_worker(_work)

    def _on_tile_maps(self, _btn) -> None:
        def _work():
            from . import maps_launch as maps_mod

            target, desktop_id = maps_mod.resolve_maps_app()
            ok = maps_mod.launch_target(target, desktop_id)
            if not ok:
                maps_mod.open_store_listing()
                self._GLib.idle_add(self._set_note, "Maps is not installed.")

        self._run_worker(_work)

    def _on_tile_calls(self, _btn) -> None:
        self.show("favourites")
        self._load_favourites()

    def _open_players_sheet(self) -> None:
        rows = []
        for track in self.snapshot.tracks:
            from .mpris import display_name

            rows.append({"bus": track.bus_name,
                         "title": track.title or display_name(track),
                         "sub": track.artist or display_name(track)})
        self._players_rows = rows
        self._render_players_sheet()
        self.show("players")

    # -- settings page ---------------------------------------------------
    def _build_settings(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        body = self._sheet_shell("settings", "Settings", back_to="drive")

        def _section(text, first=False):
            if not first:
                sep = Gtk.Separator()
                sep.set_margin_top(12)
                body.append(sep)
            caption = self._xx_ui.widgets.section_label(text)
            caption.add_css_class("xx-auto-section")
            body.append(caption)

        _section("Car", first=True)
        self._setting_row(body, "Open when the car connects",
                          "Pick a Bluetooth device", "auto_launch")
        self._car_sub = _label(Gtk, Pango, "Pick a device", "xx-secondary")
        car_texts = _texts(Gtk, Pango, "Car", "", "xx-title")
        car_texts.append(self._car_sub)
        body.append(self._nav_row(car_texts, self._open_car_sheet))
        _section("On the drive screen")
        self._setting_row(body, "Now playing", "", "surface_now_playing")
        self._setting_row(body, "Radio and audiobook tiles", "",
                          "surface_quick_pick")
        self._setting_row(body, "Maps tile", "Opens xx-maps in drive mode",
                          "surface_nav")
        self._setting_row(body, "Calls tile", "Your starred contacts.",
                          "surface_calls")
        _section("Screen")
        self._setting_row(body, "Keep the screen on",
                          "Only while this screen is in front",
                          "keep_screen_on")
        self._setting_row(body, "Rotate with the phone", "Off = landscape",
                          "follow_rotation")
        self._setting_row(body, "Always black",
                          "Off = follow the suite theme", "always_ink")
        _section("About")
        body.append(self._nav_row(
            _texts(Gtk, Pango, "Open-source licenses",
                   "Libraries and fonts in xx-auto", "xx-title"),
            lambda: self.show("licenses")))
        from . import licenses as lic_mod

        about = _texts(Gtk, Pango, lic_mod.VERSION_LINE, lic_mod.LOCAL_ONLY,
                       "xx-title")
        about.set_margin_top(12)
        body.append(about)

    def _nav_row(self, texts, callback):
        """A full-width row button: texts plus a trailing chevron."""
        Gtk = self._Gtk
        btn = Gtk.Button()
        btn.add_css_class("xx-auto-row")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(texts)
        row.append(_icon(Gtk, "go-next-symbolic", 20))
        btn.set_child(row)
        btn.connect("clicked", lambda *_: callback())
        return btn

    def _setting_row(self, body, title, subtitle, key) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.add_css_class("xx-auto-setting")
        row.append(_texts(Gtk, Pango, title, subtitle, "xx-title"))
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(bool(getattr(self.settings, key)))
        switch.connect("notify::active",
                       lambda sw, _ps, k=key: self._on_toggle(k, sw))
        row.append(switch)
        body.append(row)

    def _on_toggle(self, key, switch) -> None:
        from .settings_store import save_settings

        value = bool(switch.get_active())
        if key == "surface_calls" and value:
            value = True
        setattr(self.settings, key, value)
        if key == "auto_launch":
            self._apply_watch_unit(value)
        if key == "always_ink":
            self._apply_theme()
        if key == "follow_rotation":
            self.set_landscape(not value)
        if key == "keep_screen_on":
            self._update_inhibit()
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self._sync_car_sub()
        self.refresh()

    def _sync_car_sub(self) -> None:
        try:
            device = self.settings.auto_launch_device or "Pick a device"
            _fit(self._car_sub, self._Pango, device, 20)
        except Exception:
            pass

    def _apply_watch_unit(self, on: bool) -> None:
        def _work():
            import shutil
            import subprocess

            systemctl = shutil.which("systemctl")
            if not systemctl:
                return
            action = ["enable", "--user", "--now", "xx-auto-watch.service"] \
                if on else ["disable", "--user", "--now",
                            "xx-auto-watch.service"]
            try:
                subprocess.run([systemctl, *action], timeout=15,
                               check=False, capture_output=True)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    # -- sheets ------------------------------------------------------------
    def _sheet_shell(self, name, heading, back_to="drive"):
        """Back chevron + title over a scrolling body, clamped for landscape."""
        Gtk, Pango = self._Gtk, self._Pango
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.set_margin_top(4)
        back = Gtk.Button()
        back.add_css_class("xx-auto-back")
        back.set_tooltip_text("Back")
        back_img = _icon(Gtk, "go-previous-symbolic", 24)
        back_img.set_halign(Gtk.Align.START)
        back.set_child(back_img)
        back.connect("clicked", lambda *_: self.show(back_to))
        header.append(back)
        header.append(_label(Gtk, Pango, heading, "xx-display", chars=16))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.set_margin_bottom(16)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.set_margin_start(20)
        column.set_margin_end(20)
        column.append(header)
        column.append(body)
        clamp = self._Adw.Clamp(maximum_size=600, tightening_threshold=600)
        clamp.set_child(column)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(clamp)
        self.stack.add_named(scroll, name)
        return body

    def _list_row(self, title, subtitle, icon, callback, *args):
        """A 72 px sheet row: optional leading glyph, title, subtitle."""
        Gtk, Pango = self._Gtk, self._Pango
        btn = Gtk.Button()
        btn.add_css_class("xx-auto-row")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        if icon:
            row.append(_icon(Gtk, icon, 24))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        texts.append(_label(Gtk, Pango, title, "xx-title",
                            "xx-auto-row-title"))
        if subtitle:
            texts.append(_label(Gtk, Pango, subtitle, "xx-secondary",
                                chars=28))
        row.append(texts)
        btn.set_child(row)
        btn.connect("clicked", callback, *args)
        return btn

    def _empty(self, body, title, detail="") -> None:
        Gtk, Pango = self._Gtk, self._Pango
        box = _texts(Gtk, Pango, title, detail)
        box.set_margin_top(16)
        body.append(box)

    def _clear(self, body) -> None:
        for child in list(self._iter_children(body)):
            body.remove(child)

    def _build_sheet_players(self) -> None:
        self._players_body = self._sheet_shell("players", "Players")

    def _render_players_sheet(self) -> None:
        body = self._players_body
        self._clear(body)
        if not self._players_rows:
            self._empty(body, "No players",
                        "Start the radio or xx-audiobook.")
            return
        for item in self._players_rows:
            body.append(self._list_row(item["title"], item["sub"],
                                       "media-playback-start-symbolic",
                                       self._on_pick_player, item["bus"]))

    def _on_pick_player(self, _btn, bus_name) -> None:
        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                mpris_mod.send_player_method(conn, bus_name, "Raise")
            except Exception:
                pass
            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                mpris_mod.send_player_method(conn, bus_name, "Play")
            except Exception as exc:
                from .applog import LOG

                LOG.w("drive", f"quick pick failed: {type(exc).__name__}")

        self._run_worker(_work)
        self.show("drive")

    def _build_sheet_favourites(self) -> None:
        self._fav_body = self._sheet_shell("favourites", "Favourites")

    def _load_favourites(self) -> None:
        def _work():
            from gi.repository import Gio

            from . import contacts_client as cc

            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                favourites = cc.list_starred_sync(conn)
                self._GLib.idle_add(self._render_favourites, favourites, "")
            except Exception as exc:
                from .applog import LOG

                LOG.w("calls", "starred query failed")
                self._GLib.idle_add(self._render_favourites, [], str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _render_favourites(self, favourites, error) -> bool:
        body = self._fav_body
        self._clear(body)
        self._favourites = favourites
        if error and not favourites:
            self._empty(body, "Contacts are not available",
                        "xx-auto reads only your starred contacts, "
                        "and only to show them here.")
            return False
        if not favourites:
            self._empty(body, "No starred contacts",
                        "Star someone in xx-contacts and they show up here.")
            return False
        for fav in favourites:
            body.append(self._list_row(fav.name, fav.number,
                                       "call-start-symbolic",
                                       self._on_call_favourite, fav.number))
        return False

    def _on_call_favourite(self, _btn, number) -> None:
        def _work():
            try:
                import gi

                gi.require_version("Gio", "2.0")
                from gi.repository import Gio

                from .contacts_client import tel_uri

                Gio.AppInfo.launch_default_for_uri(tel_uri(number), None)
            except Exception:
                from .applog import LOG

                LOG.w("calls", "dial handoff failed")
                self._GLib.idle_add(self._set_note, "Could not place call.")

        self._run_worker(_work)

    def _build_sheet_car(self) -> None:
        self._car_body = self._sheet_shell("car", "Which car?",
                                           back_to="settings")

    def _open_car_sheet(self) -> None:
        self.show("car")
        self._render_car_sheet()
        self._load_car_devices()

    def _render_car_sheet(self) -> None:
        body = self._car_body
        self._clear(body)
        if not self._car_devices:
            self._empty(body, "Bluetooth is off", "Turn it on to see your car.")
            return
        for dev in self._car_devices:
            body.append(self._list_row(dev.name or dev.address, dev.address,
                                       "bluetooth-active-symbolic",
                                       self._on_pick_car, dev.address))

    def _load_car_devices(self) -> None:
        def _work():
            from . import bluez as bluez_mod

            devices = bluez_mod.list_paired_devices()
            self._GLib.idle_add(self._got_car_devices, devices)

        threading.Thread(target=_work, daemon=True).start()

    def _got_car_devices(self, devices) -> bool:
        self._car_devices = devices
        if self.stack.get_visible_child_name() == "car":
            self._render_car_sheet()
        return False

    def _on_pick_car(self, _btn, address) -> None:
        from .settings_store import save_settings

        self.settings.auto_launch_device = address
        self.settings.auto_launch = True
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self._sync_car_sub()
        self.show("settings")

    def _build_sheet_licenses(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        body = self._sheet_shell("licenses", "Licenses", back_to="settings")
        from . import licenses as lic_mod

        for title, detail in lic_mod.license_rows():
            row = _texts(Gtk, Pango, title, detail, "xx-title")
            row.add_css_class("xx-auto-setting")
            row.set_margin_bottom(4)
            body.append(row)

    # -- live poll + inhibit -----------------------------------------------
    def _start_poll(self) -> None:
        try:
            self._poll_timer = self._GLib.timeout_add_seconds(2, self._poll_tick)
            self._GLib.timeout_add_seconds(1, self._first_poll)
        except Exception:
            pass

    def _first_poll(self) -> bool:
        self._poll_tick()
        self._run_worker(self._resolve_maps)
        return False

    def _resolve_maps(self) -> None:
        from . import maps_launch as maps_mod

        sub = maps_mod.tile_subtitle(*maps_mod.resolve_maps_app())
        self._GLib.idle_add(self._set_maps_sub, sub)

    def _set_maps_sub(self, sub: str) -> bool:
        if not self._demo:
            self.snapshot.maps_sub = sub
            self.refresh()
        return False

    def _poll_tick(self) -> bool:
        def _work():
            try:
                from gi.repository import Gio

                from . import media_apps as media_mod
                from . import mpris as mpris_mod

                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                names = mpris_mod.list_player_names(conn)
                tracks = []
                import time as _time

                now_ms = int(_time.time() * 1000)
                for name in names[:8]:
                    try:
                        track = mpris_mod.read_track(conn, name)
                        track.last_active_ms = now_ms
                        tracks.append(track)
                    except Exception:
                        continue
                try:
                    installed = media_mod.installed()
                except Exception:
                    installed = dict(self.snapshot.installed)
                self._GLib.idle_add(self._got_tracks, tracks, installed)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()
        return True

    def _got_tracks(self, tracks, installed=None) -> bool:
        if installed is None:
            installed = dict(self.snapshot.installed)
        if not tracks and self.snapshot.tracks:
            if installed != self.snapshot.installed:
                self.snapshot = DriveSnapshot(
                    tracks=self.snapshot.tracks, installed=installed,
                    maps_title=self.snapshot.maps_title,
                    maps_sub=self.snapshot.maps_sub,
                    note=self.snapshot.note)
                self.refresh()
            return False
        if tracks:
            self.snapshot = DriveSnapshot(
                tracks=tracks, installed=installed,
                maps_title=self.snapshot.maps_title,
                maps_sub=self.snapshot.maps_sub, note=self.snapshot.note)
            self.refresh()
        elif installed != self.snapshot.installed:
            self.snapshot = DriveSnapshot(
                tracks=[], installed=installed,
                maps_title=self.snapshot.maps_title,
                maps_sub=self.snapshot.maps_sub, note=self.snapshot.note)
            self.refresh()
        return False

    def _update_inhibit(self) -> None:
        try:
            app = self.app
            if self.settings.keep_screen_on and self.win.get_visible():
                if not self._inhibit_cookie:
                    self._inhibit_cookie = app.inhibit(
                        self.win, self._Gtk.ApplicationInhibitFlags.IDLE,
                        "driving")
            elif self._inhibit_cookie:
                app.uninhibit(self._inhibit_cookie)
                self._inhibit_cookie = 0
        except Exception:
            pass

    def present(self) -> None:
        self.refresh()
        self._sync_car_sub()
        self.win.present()

    # -- seed hooks for screenshots/tests ------------------------------------
    def seed_demo(self, scenario: str) -> None:
        from . import demo

        self._demo = True
        self.settings = demo.demo_settings()
        self.snapshot = demo.snapshot_for(scenario)
        if scenario == "favourites":
            self._favourites = demo.demo_favourites()
            self._render_favourites(self._favourites, "")
        if scenario == "car":
            self._car_devices = demo.demo_car_devices()
            self._render_car_sheet()
        if scenario in ("settings", "licenses"):
            pass
        self.refresh()

    def open_sheet(self, name: str) -> None:
        if name == "players" and not self._players_rows:
            self._open_players_sheet()
        else:
            self.show(name)


class AutoApp:
    def __init__(self) -> None:
        Adw, _Gdk, Gio, _GLib, _Gtk, _Pango = _require_gtk()
        from . import __app_id__

        self._app = Adw.Application(application_id=__app_id__,
                                    flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._app.connect("activate", self._on_activate)
        self.window = None

    def _on_activate(self, app) -> None:
        from .settings_store import load_settings

        app._auto_window = self.window
        settings = load_settings()
        if self.window is None:
            self.window = AutoWindow(app, settings)
            app._auto_window = self.window
        else:
            self.window.settings = settings
            app._auto_window = self.window
        scenario = os.environ.get("XX_AUTO_SCENARIO", "")
        sheet = os.environ.get("XX_AUTO_SHEET", "")
        landscape = os.environ.get("XX_AUTO_LANDSCAPE", "")
        if scenario:
            self.window.seed_demo(scenario)
        if landscape == "1":
            self.window.set_landscape(True)
        self.window.present()
        if sheet:
            self.window.open_sheet(sheet)

    def run(self, argv=None) -> int:
        return self._app.run(argv if argv is not None else [])


def main(argv=None) -> int:
    args = list(argv) if argv is not None else None
    import sys

    if args is None:
        args = sys.argv[1:]
    if "--watch" in args:
        from .car_watch import run_watch

        return run_watch(args)
    app = AutoApp()
    return app.run(args)
