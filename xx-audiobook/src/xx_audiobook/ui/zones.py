"""Zones (M5, ui/zones/ZonesScreen.kt): Home Assistant speakers.

Where the audio goes (this phone or a zone) sits on top. While a zone
plays, its card carries play/pause from the zone's own state, stop,
±30 s, the volume (Quieter / Louder and a drag bar) and "Bring back to
phone", which resumes here only after the zone confirmed the stop.
Below: the locations as chips and their players; casting asks first.
The "Play on…" sheet in Now Playing reuses the same rows and ask.

Everything talks to ctx.zones (ZonesState) on workers; HA errors show
verbatim in one muted line.
"""

from __future__ import annotations

from gi.repository import GObject, Gtk

from xx_audiobook import fmt
from xx_audiobook import zones as zones_module
from xx_audiobook.ui import common
from xx_audiobook.ui.context import NOT_SIGNED_IN_LINE
from xx_ui import widgets

STATE_WORDS = {"playing": "Playing", "paused": "Paused", "idle": "Idle",
               "off": "Off", "standby": "Standby", "buffering": "Starting",
               "unavailable": "Unavailable", "on": "On"}


def state_word(state: str) -> str:
    return STATE_WORDS.get((state or "").lower(),
                           (state or "Unknown").capitalize())


def target_line(target) -> str:
    parts = [state_word(target.state)]
    if target.volume_level is not None:
        parts.append(f"{fmt.percent(target.volume_level)}%")
    if target.media_title and target.state == "playing":
        parts.append(str(target.media_title))
    return "  ·  ".join(parts)


def on_zone(snapshot) -> bool:
    return isinstance(snapshot.output, zones_module.ZoneOutput)


def ask_cast(ctx, parent, target, done=None):
    """Casting asks first: the book, from where, onto which speaker."""
    player = ctx.player
    name = target.name or target.entity_id
    if not player.has_book:
        return common.confirm(
            parent, f"Play on {name}?",
            "Pick a book first: start one in the Library, then send it "
            "here.", [("ok", "OK", "outline")], lambda _r: None)
    where = fmt.format_ms(player.position_ms)
    body = (f"{player.title} continues on {name} from {where}, and this "
            "phone pauses.")

    def _answer(response: str) -> None:
        if response == "cast":
            cast(ctx, target, done)

    return common.confirm(
        parent, f"Play on {name}?", body,
        [("cancel", "Cancel", "outline"), ("cast", "Play there", "primary")],
        _answer)


def cast(ctx, target, done=None) -> None:
    player = ctx.player
    item_id, position = player.item_id, player.position_ms / 1000.0

    def _done(result, error) -> None:
        zone, failure = result if result else (None, error)
        if zone is not None and failure is None:
            player.pause()
        ctx.emit("zones-changed")
        if done is not None:
            done()

    ctx.run(lambda: ctx.zones.play_on(target, item_id, position), _done)
    ctx.emit("zones-changed")


class VolumeBar(Gtk.DrawingArea):
    """A 2 px trough with the level bright and a slim handle; drag or
    arrow keys; emits ``changed`` with 0..1 when let go."""

    __gtype_name__ = "XXAudiobookVolumeBar"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None,
                                (float,))}

    def __init__(self, value: float) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.SLIDER)
        self.value = min(max(value, 0.0), 1.0)
        self._drag: float | None = None
        self._start = 0.0
        self.set_content_height(48)
        self.set_hexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self._draw)
        self._spoken()
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def _spoken(self) -> None:
        self.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.VALUE_MIN,
             Gtk.AccessibleProperty.VALUE_MAX,
             Gtk.AccessibleProperty.VALUE_NOW,
             Gtk.AccessibleProperty.VALUE_TEXT],
            ["Zone volume", 0.0, 100.0, float(fmt.percent(self.value)),
             f"{fmt.percent(self.value)}%"])

    def _at(self, x: float) -> float:
        return min(max(x / max(self.get_width(), 1), 0.0), 1.0)

    def _begin(self, _g, x, _y) -> None:
        self._start = x
        self._drag = self._at(x)
        self.queue_draw()

    def _update(self, _g, dx, _dy) -> None:
        if self._drag is not None:
            self._drag = self._at(self._start + dx)
            self.queue_draw()

    def _end(self, _g, dx, _dy) -> None:
        if self._drag is None:
            return
        self.value = self._at(self._start + dx)
        self._drag = None
        self._spoken()
        self.emit("changed", self.value)
        self.queue_draw()

    def _key(self, _c, keyval, _code, _state) -> bool:
        from gi.repository import Gdk

        step = {Gdk.KEY_Left: -0.05, Gdk.KEY_Right: 0.05}.get(keyval)
        if step is None:
            return False
        self.value = min(max(self.value + step, 0.0), 1.0)
        self._spoken()
        self.emit("changed", self.value)
        self.queue_draw()
        return True

    def _draw(self, widget, cr, width, height) -> None:
        color = widget.get_color()
        rgb = (color.red, color.green, color.blue)
        shown = self._drag if self._drag is not None else self.value
        mid = height / 2
        cr.set_source_rgba(*rgb, 0.22)
        cr.rectangle(0, mid - 1, width, 2)
        cr.fill()
        cr.set_source_rgba(*rgb, 1.0)
        cr.rectangle(0, mid - 1, width * shown, 2)
        cr.fill()
        head = 3 if self._drag is not None else 2
        tall = 20 if self._drag is not None else 14
        x = min(max(width * shown - head / 2, 0), width - head)
        cr.rectangle(x, mid - tall / 2, head, tall)
        cr.fill()


class ZonesPage:
    def __init__(self, ctx, window_getter) -> None:
        self.ctx = ctx
        self._window = window_getter
        self.loaded = False
        self.loading = False
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        top = common.page_top("Zones", [(
            "view-refresh-symbolic", "Refresh zones", self.refresh)])
        self.widget = widgets.page(top, self.body)
        self._render()
        ctx.connect("zones-changed", self._render)
        ctx.connect("session-changed", self._session)
        ctx.connect("ready", self._session)
        ctx.player.connect(self._player_changed)
        self._seen_book = None
        self.widget.connect("map", lambda _w: self._mapped())

    def _session(self) -> None:
        if self.ctx.signed_in and not self.loaded:
            self.refresh()
        self._render()

    def _mapped(self) -> None:
        if self.ctx.signed_in and not self.loaded and not self.loading:
            self.refresh()

    def _player_changed(self) -> None:
        player = self.ctx.player
        key = (player.item_id, player.title, player.playing)
        if key != self._seen_book:
            self._seen_book = key
            self._render()

    # -- data ---------------------------------------------------------------

    def refresh(self) -> None:
        if not self.ctx.signed_in:
            self._render()
            return
        self.loading = True
        self._render()

        def _done(_result, _error) -> None:
            self.loading = False
            self.loaded = True
            self._render()

        self.ctx.run(self.ctx.zones.refresh, _done)

    def select(self, location_id: str) -> None:
        self.ctx.run(lambda: self.ctx.zones.select(location_id),
                     lambda _r, _e: self._render())
        self._render()

    def transport(self, action: str, value=None) -> None:
        zones = self.ctx.zones

        def _work():
            result = zones.transport(action, value)
            output = zones.repo.output
            if isinstance(output, zones_module.ZoneOutput):
                zones.load_targets(output.location_id)
            return result

        self.ctx.run(_work, lambda _r, _e: self._render())

    def seek(self, forward: bool) -> None:
        self.ctx.run(lambda: self.ctx.zones.seek_step(forward),
                     lambda _r, _e: self._render())

    def set_volume(self, value: float) -> None:
        self.ctx.run(lambda: self.ctx.zones.set_volume(value),
                     lambda _r, _e: self._render())

    def step_volume(self, louder: bool) -> None:
        zones = self.ctx.zones
        self.ctx.run(zones.louder if louder else zones.quieter,
                     lambda _r, _e: self._render())

    def bring_back(self) -> None:
        """Stop the zone; the phone resumes only once that stop landed."""
        self.busy_line = "Stopping the zone…"
        self._render()

        def _done(result, error) -> None:
            self.busy_line = None
            item_id, failure = result if result else (None, error)
            if failure is None and item_id:
                self.ctx.player.open_and_play(item_id)
            self.ctx.emit("zones-changed")

        self.ctx.run(self.ctx.zones.bring_back_to_phone, _done)

    busy_line: str | None = None

    # -- render -------------------------------------------------------------

    def _render(self) -> None:
        body = self.body
        common.clear(body)
        ctx = self.ctx
        if not ctx.signed_in:
            body.append(common.muted(NOT_SIGNED_IN_LINE))
            return
        snap = ctx.zones.snapshot()
        body.append(self._indicator(snap))
        if on_zone(snap):
            body.append(self._zone_card(snap))
        if snap.message:
            body.append(common.muted(snap.message))
        if not snap.locations:
            if self.loading or not self.loaded:
                body.append(common.muted("Looking for zones…"))
            return
        body.append(common.section("Locations"))
        chips = widgets.chip_bar(
            [(loc.name or loc.id, loc.id == snap.selected,
              lambda i=loc.id: self.select(i)) for loc in snap.locations],
            spacing=6)
        chips.add_css_class("xx-ab-chiprow")
        body.append(chips)
        body.append(common.section("Players"))
        targets = snap.targets.get(snap.selected or "", None)
        if targets is None:
            body.append(common.muted("Looking for players…"))
            return
        if not targets:
            body.append(common.muted("No media players found here."))
            return
        output = snap.output
        for index, target in enumerate(targets):
            active = (on_zone(snap)
                      and output.entity_id == target.entity_id)
            body.append(player_row(
                target, active,
                lambda t=target: ask_cast(ctx, self._window(), t),
                last=index == len(targets) - 1))

    def _indicator(self, snap) -> Gtk.Box:
        """Phone vs zone: where the audio is going right now."""
        player = self.ctx.player
        zone = snap.output if on_zone(snap) else None
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
        row.add_css_class("xx-ab-banner")
        row.set_margin_top(8)
        live = zone is not None or player.playing
        row.append(common.glyph("audio-speakers-symbolic" if zone
                                else "phone-symbolic", 22, on=live))
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        if zone is not None:
            head = f"Playing on {zone.name}"
        elif player.playing:
            head = "Playing on this phone"
        else:
            head = "On this phone"
        text.append(common.label(head, "xx-title", lines=2, chars=22))
        if zone is not None:
            sub = self._zone_title(zone, self.ctx.zones.zone_player())
        elif player.has_book:
            sub = player.title if player.playing else (
                f"Paused  ·  {player.title}")
        else:
            sub = "Nothing playing"
        if sub:
            text.append(common.label(sub, "xx-secondary", lines=2,
                                     chars=26))
        row.append(text)
        tag = common.badge("Zone" if zone is not None else "Phone")
        row.append(tag)
        row.update_property([Gtk.AccessibleProperty.LABEL],
                            [f"{head}. {sub}"])
        return row

    def _zone_card(self, snap) -> Gtk.Box:
        zones = self.ctx.zones
        output = snap.output
        target = zones.zone_player()
        card = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        card.add_css_class("xx-ab-card")
        card.set_margin_top(12)
        location = next((loc.name for loc in snap.locations
                         if loc.id == output.location_id), "")
        playing = snap.zone_playing
        state = ("Playing" if playing else "Paused" if playing is False
                 else state_word(target.state) if target else "Starting")
        card.append(common.caps(" · ".join(p for p in (location, state)
                                           if p)))
        card.append(common.label(output.name, "xx-ab-heading", lines=2,
                                 chars=18))
        position = target.media_position_s if target is not None else None
        if position is not None:
            card.append(common.label(
                f"At {fmt.format_ms(int(position * 1000))} in this file",
                "xx-ab-meta xx-ab-num", chars=30))
        transport = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        transport.set_margin_top(16)
        play = Gtk.Button()
        play.add_css_class("xx-ab-play")
        play.set_child(widgets.icon_image(
            "media-playback-pause-symbolic" if playing
            else "media-playback-start-symbolic", 28))
        common.name(play, f"Pause {output.name}" if playing
                    else f"Play on {output.name}")
        play.connect("clicked", lambda _b: self.transport(
            "pause" if playing else "play"))
        play.set_halign(Gtk.Align.CENTER)
        keys = []
        if position is not None:
            keys.append(_key(common.skip_glyph(30, False, 30),
                             "Back 30 seconds", lambda: self.seek(False)))
        keys.append(play)
        keys.append(_key(
            widgets.icon_image("media-playback-stop-symbolic", 20),
            f"Stop {output.name}", lambda: self.transport("stop")))
        if position is not None:
            keys.append(_key(common.skip_glyph(30, True, 30),
                             "Forward 30 seconds", lambda: self.seek(True)))
        for widget in keys:
            widget.set_hexpand(True)
            transport.append(widget)
        card.append(transport)
        volume = snap.volume if snap.volume is not None else (
            target.volume_level if target is not None else None)
        if volume is not None:
            level = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
            level.set_margin_top(14)
            caption = common.caps("Volume")
            caption.set_hexpand(True)
            level.append(caption)
            percent = common.label(f"{fmt.percent(volume)}%",
                                   "xx-ab-label xx-ab-num", xalign=1.0,
                                   chars=6)
            percent.set_ellipsize(0)  # short; never misfire an ellipsis
            level.append(percent)
            card.append(level)
            slider = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
            slider.append(common.icon_button(
                "audio-volume-low-symbolic", "Quieter",
                lambda: self.step_volume(False)))
            bar = VolumeBar(volume)
            bar.connect("changed", lambda _b, v: self.set_volume(v))
            slider.append(bar)
            slider.append(common.icon_button(
                "audio-volume-high-symbolic", "Louder",
                lambda: self.step_volume(True)))
            card.append(slider)
        back = widgets.primary_button(
            "Stopping the zone…" if self.busy_line else
            "Bring back to phone", self.bring_back)
        common.fit(back, 22)
        back.set_margin_top(12)
        back.set_sensitive(not self.busy_line)
        card.append(back)
        note = common.para("The phone picks up after the zone has stopped.",
                           "xx-ab-meta")
        note.set_margin_top(8)
        card.append(note)
        return card

    def _zone_title(self, output, target) -> str:
        player = self.ctx.player
        if output.item_id and output.item_id == player.item_id:
            return player.title
        if target is not None and target.media_title:
            return str(target.media_title)
        return ""


def _key(child: Gtk.Widget, spoken: str, callback) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("xx-ab-key")
    button.set_child(child)
    button.set_halign(Gtk.Align.CENTER)
    button.set_valign(Gtk.Align.CENTER)
    button.connect("clicked", lambda _b: callback())
    return common.name(button, spoken)


def player_row(target, active: bool, on_tap, last: bool = False):
    """A cast target: speaker glyph, name, state · volume, 'Play here'."""
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
    box.append(common.glyph("audio-speakers-symbolic", 22,
                            on=active or target.state == "playing"))
    text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)
    head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    head.append(common.label(target.name or target.entity_id, "xx-title",
                             chars=16))
    if active:
        head.append(common.badge("Now"))
    text.append(head)
    text.append(common.label(target_line(target), "xx-secondary", lines=2,
                             chars=24))
    box.append(text)
    box.append(widgets.chevron())
    name = target.name or target.entity_id
    return common.tap_row(
        box, f"Play on {name}. {target_line(target)}", on_tap, last)


def play_on_sheet(ctx, parent):
    """Now Playing's "Play on…": this phone, then each location's
    players; picking one closes the sheet and asks first."""
    dialog, box = common.sheet("Play on…")
    content = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.append(content)

    def _render() -> None:
        common.clear(content)
        if not ctx.signed_in:
            content.append(common.muted(NOT_SIGNED_IN_LINE))
            return
        snap = ctx.zones.snapshot()
        here = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
        here.append(common.glyph("phone-symbolic", 22,
                                 on=not on_zone(snap)))
        label = common.label("This phone", "xx-title", chars=20)
        label.set_hexpand(True)
        here.append(label)
        if not on_zone(snap):
            here.append(common.badge("Now"))
        row = Gtk.Box()
        row.add_css_class("xx-setting")
        row.append(here)
        here.set_hexpand(True)
        content.append(row)
        if snap.message:
            content.append(common.muted(snap.message))
        if not snap.locations:
            content.append(common.muted("Looking for zones…"))
            return
        if len(snap.locations) > 1:
            chips = widgets.chip_bar(
                [(loc.name or loc.id, loc.id == snap.selected,
                  lambda i=loc.id: _select(i)) for loc in snap.locations],
                spacing=6)
            chips.add_css_class("xx-ab-chiprow")
            chips.set_margin_top(8)
            content.append(chips)
        targets = snap.targets.get(snap.selected or "", None)
        if targets is None:
            content.append(common.muted("Looking for players…"))
            return
        if not targets:
            content.append(common.muted("No media players found here."))
        output = snap.output
        for index, target in enumerate(targets):
            active = on_zone(snap) and output.entity_id == target.entity_id
            content.append(player_row(
                target, active, lambda t=target: _pick(t),
                last=index == len(targets) - 1))

    def _select(location_id: str) -> None:
        ctx.run(lambda: ctx.zones.select(location_id),
                lambda _r, _e: _render())

    def _pick(target) -> None:
        dialog.close()
        ask_cast(ctx, parent, target)

    _render()
    if ctx.signed_in and not ctx.zones.snapshot().locations:
        ctx.run(ctx.zones.refresh, lambda _r, _e: _render())
    return common.present(dialog, parent)
