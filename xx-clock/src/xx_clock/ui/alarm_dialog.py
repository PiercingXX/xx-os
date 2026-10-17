"""Alarm editor dialog: time, weekday chips, label, vibrate, sound."""

from __future__ import annotations

from .. import alarm_logic

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class AlarmDialog:
    def __init__(self, Adw, Gtk, Pango, widgets, draft: dict | None,
                 on_save, parent) -> None:
        self._Gtk = Gtk
        self._on_save = on_save
        self._draft = dict(draft or {"hour": 7, "minute": 30, "days_mask": 0,
                                     "label": "", "enabled": True,
                                     "vibrate": True, "sound_uri": None})
        dialog = Adw.Dialog()
        dialog.set_content_width(320)
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.set_margin_top(20)
        body.set_margin_bottom(20)
        title = widgets.label("Edit alarm" if draft else "New alarm", "xx-title")
        body.append(title)
        time_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        time_row.set_halign(Gtk.Align.CENTER)
        hour_adj = Gtk.Adjustment.new(self._draft["hour"], 0, 23, 1, 5, 0)
        minute_adj = Gtk.Adjustment.new(self._draft["minute"], 0, 59, 1, 5, 0)
        self._hour = Gtk.SpinButton.new(hour_adj, 1, 0)
        self._minute = Gtk.SpinButton.new(minute_adj, 1, 0)
        for spin in (self._hour, self._minute):
            spin.set_size_request(88, 56)
        time_row.append(self._hour)
        sep = Gtk.Label(label=":")
        sep.add_css_class("xx-display-face")
        time_row.append(sep)
        time_row.append(self._minute)
        body.append(time_row)
        body.append(widgets.section_label("Repeat"))
        days = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 2)
        days.set_halign(Gtk.Align.CENTER)
        self._day_buttons: list = []
        mask = self._draft.get("days_mask", 0)
        for index, name in enumerate(DAY_NAMES):
            btn = Gtk.ToggleButton.new_with_label(name)
            btn.add_css_class("xx-chip")
            btn.set_size_request(36, 40)
            btn.set_active(bool(mask & alarm_logic.bit_for_weekday(index)))
            days.append(btn)
            self._day_buttons.append(btn)
        body.append(days)
        body.append(widgets.section_label("Label"))
        self._label = Gtk.Entry()
        self._label.set_text(self._draft.get("label") or "")
        self._label.set_placeholder_text("Alarm label")
        body.append(self._label)
        vibrate_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        vibrate_row.append(widgets.label("Vibrate", "xx-title"))
        spacer = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        spacer.set_hexpand(True)
        vibrate_row.append(spacer)
        self._vibrate = widgets.make_switch(self._draft.get("vibrate", True))
        vibrate_row.append(self._vibrate)
        body.append(vibrate_row)
        body.append(widgets.section_label("Sound"))
        self._sound = Gtk.Entry()
        self._sound.set_text(self._draft.get("sound_uri") or "")
        self._sound.set_placeholder_text("Default alarm sound")
        body.append(self._sound)
        buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        buttons.set_halign(Gtk.Align.END)
        cancel = widgets.text_button("Cancel", lambda: dialog.close())
        save = widgets.primary_button("Save", self._save)
        buttons.append(cancel)
        buttons.append(save)
        body.append(buttons)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(body)
        scrolled.set_min_content_height(560)
        dialog.set_child(scrolled)
        self.dialog = dialog
        dialog.present(parent)

    def _save(self) -> None:
        mask = 0
        for index, btn in enumerate(self._day_buttons):
            if btn.get_active():
                mask |= alarm_logic.bit_for_weekday(index)
        draft = dict(self._draft)
        draft.update(hour=int(self._hour.get_value()),
                     minute=int(self._minute.get_value()),
                     days_mask=mask,
                     label=self._label.get_text().strip(),
                     vibrate=self._vibrate.get_active(),
                     sound_uri=self._sound.get_text().strip() or None)
        self.dialog.close()
        self._on_save(draft)
