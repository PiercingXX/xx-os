"""Alarm editor dialog: time, weekday chips, label, sound, vibrate.

Mirrors dialog_alarm_edit.xml: title, a time picker, Repeat day chips,
a Label field, a Sound row (tap to pick a file; Default when unset),
Vibrate, then Cancel / Save.
"""

from __future__ import annotations

from .. import alarm_logic

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

APP_CSS = """
.xx-clock-spin { font-size: 40px; }
.xx-clock-spin text { min-width: 0; }
.xx-clock-spin button { min-width: 48px; min-height: 48px; }
.xx-clock-colon { font-size: 40px; }
.xx-clock-days .xx-chip { padding: 0; min-width: 0; min-height: 48px; letter-spacing: 0; }
.xx-clock-editor .xx-entry { border-color: @borders; }
.xx-clock-editor .xx-entry:focus { border-color: @window_fg_color; }
"""


def sound_name(uri: str | None) -> str:
    """What the Sound row shows: the file's name, or Default."""
    if not uri:
        return "Default"
    path = uri.split("://", 1)[1].split("?", 1)[0] if "://" in uri else uri
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return _percent_decode(name) or "Default"


def _percent_decode(text: str) -> str:
    """%XX escapes back to UTF-8 text (file:// URIs from the picker)."""
    out = bytearray()
    index = 0
    while index < len(text):
        chunk = text[index:index + 3]
        if len(chunk) == 3 and chunk[0] == "%":
            try:
                out.append(int(chunk[1:], 16))
                index += 3
                continue
            except ValueError:
                pass
        out.extend(text[index].encode())
        index += 1
    return out.decode("utf-8", "replace")


def _two_digits(spin) -> bool:
    spin.set_text(f"{int(spin.get_value()):02d}")
    return True


class AlarmDialog:
    def __init__(self, Adw, Gtk, Pango, widgets, draft: dict | None,
                 on_save, parent) -> None:
        self._Gtk = Gtk
        self._widgets = widgets
        self._parent = parent
        self._on_save = on_save
        self._draft = dict(draft or {"hour": 7, "minute": 30, "days_mask": 0,
                                     "label": "", "enabled": True,
                                     "vibrate": True, "sound_uri": None})
        self._sound_uri = self._draft.get("sound_uri") or None
        dialog = Adw.Dialog()
        dialog.set_content_width(360)
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.set_margin_top(20)
        body.set_margin_bottom(20)
        # The sheet is the raised surface, the same fill as an entry: a
        # hairline keeps the Label field visible on it.
        body.add_css_class("xx-clock-editor")
        is_new = "id" not in self._draft
        body.append(widgets.label("New alarm" if is_new else "Edit alarm",
                                  "xx-dialog-title"))

        time_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        time_row.set_halign(Gtk.Align.CENTER)
        self._hour = self._spin(Gtk, self._draft["hour"], 23)
        self._minute = self._spin(Gtk, self._draft["minute"], 59)
        time_row.append(self._hour)
        colon = Gtk.Label(label=":")
        colon.add_css_class("xx-display-face")
        colon.add_css_class("xx-clock-colon")
        time_row.append(colon)
        time_row.append(self._minute)
        body.append(time_row)

        body.append(widgets.section_label("Repeat"))
        days = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        days.add_css_class("xx-clock-days")
        days.set_homogeneous(True)
        self._day_buttons: list = []
        mask = self._draft.get("days_mask", 0)
        for index, name in enumerate(DAY_NAMES):
            btn = Gtk.ToggleButton.new_with_label(name.upper())
            btn.add_css_class("xx-chip")
            btn.connect("toggled", self._paint_day)
            btn.set_active(bool(mask & alarm_logic.bit_for_weekday(index)))
            self._paint_day(btn)
            days.append(btn)
            self._day_buttons.append(btn)
        body.append(days)

        self._label = widgets.text_entry("Label")
        self._label.set_text(self._draft.get("label") or "")
        self._label.set_margin_top(8)
        body.append(self._label)

        self._sound_value = widgets.label(sound_name(self._sound_uri),
                                          "xx-secondary", xalign=1.0)
        body.append(widgets.list_row("Sound", trailing=self._sound_value,
                                     on_activate=self._pick_sound))
        # A picked file can go back to the default sound (the old free-text
        # field did that by clearing it); shown only while one is picked.
        self._sound_reset = Gtk.Button()
        self._sound_reset.add_css_class("xx-btn")
        self._sound_reset.add_css_class("xx-clock-act")
        reset_text = widgets.caption("Use default sound")
        widgets.fit_text(reset_text, "USE DEFAULT SOUND", 20)
        self._sound_reset.set_child(reset_text)
        self._sound_reset.connect("clicked", lambda _b: self._reset_sound())
        self._sound_reset.set_halign(Gtk.Align.START)
        self._sound_reset.set_visible(self._sound_uri is not None)
        body.append(self._sound_reset)
        self._vibrate = widgets.make_switch(self._draft.get("vibrate", True))
        body.append(widgets.setting_row("Vibrate", trailing=self._vibrate, last=True))

        buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        buttons.set_halign(Gtk.Align.END)
        buttons.append(widgets.text_button("Cancel", lambda: dialog.close()))
        buttons.append(widgets.primary_button("Save", self._save))
        body.append(buttons)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_child(body)
        dialog.set_child(scrolled)
        self.dialog = dialog
        dialog.present(parent)

    @staticmethod
    def _spin(Gtk, value: int, top: int):
        """Vertical hour/minute spinner: + over the two-digit value over -,
        wrapping 23->00 and 59->00 like a time picker."""
        spin = Gtk.SpinButton.new(Gtk.Adjustment.new(value, 0, top, 1, 5, 0), 1, 0)
        spin.set_orientation(Gtk.Orientation.VERTICAL)
        spin.set_wrap(True)
        spin.set_numeric(True)
        spin.set_width_chars(2)
        spin.add_css_class("xx-display-face")
        spin.add_css_class("xx-clock-spin")
        spin.connect("output", _two_digits)
        return spin

    @staticmethod
    def _paint_day(btn) -> None:
        # Selection inverts (xx_ui .xx-chip.selected), never Adwaita's
        # checked tint.
        if btn.get_active():
            btn.add_css_class("selected")
        else:
            btn.remove_css_class("selected")

    def _pick_sound(self) -> None:
        Gtk = self._Gtk
        try:
            chooser = Gtk.FileDialog()
            chooser.set_title("Alarm sound")
            audio = Gtk.FileFilter()
            audio.set_name("Audio")
            audio.add_mime_type("audio/*")
            chooser.set_default_filter(audio)
        except Exception:
            return

        def _picked(source, result) -> None:
            try:
                picked = source.open_finish(result)
            except Exception:
                return  # cancelled: keep the current sound
            if picked is not None:
                self._set_sound(picked.get_uri())

        root = self.dialog.get_root()
        chooser.open(root if root is not None else self._parent, None, _picked)

    def _set_sound(self, uri: str | None) -> None:
        self._sound_uri = uri or None
        self._sound_value.set_text(sound_name(self._sound_uri))
        self._sound_reset.set_visible(self._sound_uri is not None)

    def _reset_sound(self) -> None:
        self._set_sound(None)

    def _save(self) -> None:
        mask = 0
        for index, btn in enumerate(self._day_buttons):
            if btn.get_active():
                mask |= alarm_logic.bit_for_weekday(index)
        self._hour.update()
        self._minute.update()
        draft = dict(self._draft)
        draft.update(hour=int(self._hour.get_value()),
                     minute=int(self._minute.get_value()),
                     days_mask=mask,
                     label=self._label.get_text().strip(),
                     vibrate=self._vibrate.get_active(),
                     sound_uri=self._sound_uri)
        self.dialog.close()
        self._on_save(draft)
