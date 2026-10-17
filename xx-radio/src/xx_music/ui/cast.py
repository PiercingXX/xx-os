"""The cast device sheet, the playing-on line and the cast button.

Small and isolated: Now Playing and the mini player call in here, and
everything else stays untouched for the parallel lanes.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_music.ui import common


def cast_button(ui, px: int = 20) -> Gtk.Button:
    """An icon button that opens the device sheet."""
    button = common.icon_button("audio-speakers-symbolic",
                                "Play on another speaker",
                                lambda: open_cast_sheet(ui), px)
    button.add_css_class("xx-mu-cast")
    return button


def playing_line(ui) -> Gtk.Box:
    """The "Playing on <device>" row with Bring back to phone."""
    box = common.hbox(8)
    box.add_css_class("xx-mu-cast-line")
    text = common.label("", "xx-secondary", chars=24)
    text.set_hexpand(True)
    box.append(text)
    back = Gtk.Button.new_with_label("BRING BACK")
    back.add_css_class("xx-mu-act")
    back.connect("clicked", lambda _b: ui.player.stop_casting())
    common.fit(back, 12)
    common.name(back, "Bring back to phone")
    box.append(back)
    box.text = text
    box.back = back
    return box


def sync_line(box: Gtk.Box, cast: dict) -> None:
    name = str(cast.get("connectedName") or "")
    box.set_visible(bool(cast.get("connectedId")))
    if cast.get("connectedId"):
        box.text.set_text(f"Playing on {name or 'speaker'}")
    else:
        box.text.set_text("")


def volume_row(ui) -> Gtk.Box:
    """A small speaker-volume slider, only visible while casting."""
    box = common.hbox(10)
    box.add_css_class("xx-mu-cast-vol")
    glyph = common.label("VOL", "xx-mu-label", chars=4)
    box.append(glyph)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                     0.0, 1.0, 0.05)
    scale.set_hexpand(True)
    scale.set_draw_value(False)
    scale.set_size_request(120, -1)
    common.name(scale, "Speaker volume")
    box.append(scale)
    box.scale = scale
    guard: list = []

    def _changed(_s) -> None:
        if guard:
            return
        ui.player.set_volume(float(scale.get_value()))

    scale.connect("value-changed", _changed)

    def _sync(cast: dict) -> None:
        guard.append(True)
        try:
            scale.set_value(float(cast.get("volume", 1.0)))
        except (TypeError, ValueError):
            pass
        finally:
            guard.pop()
        box.set_visible(bool(cast.get("connectedId")))

    box.sync = _sync
    return box


def open_cast_sheet(ui):
    """This phone + discovered devices + Searching… + errors."""
    from xx_ui import widgets

    player = ui.player
    dialog, box = common.sheet("Play on", "Choose a speaker")
    status = common.vbox(4)
    box.append(status)
    rows = common.vbox(4)
    box.append(rows)
    foot = common.vbox(4)
    foot.set_margin_top(8)
    box.append(foot)

    def refresh() -> None:
        common.clear(status)
        common.clear(rows)
        common.clear(foot)
        cast = player.cast
        if cast.get("busy"):
            status.append(common.muted("Connecting…"))
        elif cast.get("error"):
            status.append(common.label(str(cast["error"]),
                                       "xx-mu-meta xx-mu-error",
                                       lines=3, chars=26))
        if cast.get("connectedId"):
            name = str(cast.get("connectedName") or "speaker")
            rows.append(common.label(f"Playing on {name}",
                                     "xx-mu-strong", chars=24))
            back = widgets.outline_button(
                "Bring back to phone", player.stop_casting)
            common.fit(back, 18)
            rows.append(back)
        phone = common.menu_row(
            "phone-symbolic", "This phone"
            + ("" if cast.get("connectedId") else " · playing here"),
            lambda: (player.stop_casting(), dialog.close()))
        rows.append(phone)
        devices = cast.get("devices") or []
        for device in devices:
            name = str(device.get("name") or "Speaker")
            model = str(device.get("model") or "")
            text = name if not model else f"{name} · {model}"
            rows.append(common.menu_row(
                "audio-speakers-symbolic", text,
                lambda d=device: (
                    player.cast_to(str(d.get("id"))),
                    common.later(400, refresh))))
        if cast.get("searching"):
            rows.append(common.muted("Searching…"))
        elif not devices:
            rows.append(common.muted("No speakers found on this Wi-Fi."))
        again = widgets.outline_button(
            "Search again",
            lambda: (player.refresh_cast(), common.later(600, refresh)))
        common.fit(again, 14)
        foot.append(again)

    player.refresh_cast()
    refresh()
    return common.present(dialog, ui.shell.window)
