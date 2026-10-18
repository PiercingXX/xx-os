"""Settings as a navigation page over the txxt JSON config.

Every Android setting with a real Linux effect (``ui/SettingsActivity``,
``ui/SettingsStore``): lock-screen privacy posture, alert style,
default region, MMS auto-download/auto-reply — plus entry rows into
Blocking and Quarantine. Appearance lives in the shell theme alone
(``~/.config/xx-wm/config.json`` via ``xx_ui``), so this page carries
no theme, preset or font controls. Deliberately absent (no Linux
equivalent): the notification sound picker (a sound-theme name would
not ring anything here) and the channel-generation hack.

Phone rules: no entry autofocuses (region stays unfocusable until
tapped); no header-bar chrome — the shared compact header carries the
back chevron.
"""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
import txxt  # noqa: E402
from txxt import settings as settings_mod  # noqa: E402
from txxt.conversations import tap_to_focus  # noqa: E402
from txxt.list_presenter import count_label  # noqa: E402

LOCK_PRIVACY_OPTIONS = (
    ('sender-only', 'Sender only'),
    ('content', 'Content'),
    ('nothing', 'Nothing'),
)

ALERT_STYLE_OPTIONS = (
    ('silent', 'Silent'),
    ('vibrate', 'Vibrate'),
    ('sound', 'Sound'),
)

def setting_row(title: str, subtitle: str = '', trailing=None,
                last: bool = False) -> Gtk.Box:
    """``widgets.setting_row`` whose title and subtitle wrap to two lines
    each (large text, 320 px) instead of ellipsizing at one; past two
    lines they ellipsize, and they never widen the window."""
    row = widgets.setting_row(title, subtitle, trailing, last=last)
    middle = row.get_first_child()
    line = middle.get_first_child() if middle is not None else None
    while isinstance(line, Gtk.Label):
        line.set_wrap(True)
        line.set_wrap_mode(Pango.WrapMode.WORD)
        line.set_lines(2)
        line.set_ellipsize(Pango.EllipsizeMode.END)
        line = line.get_next_sibling()
    return row


def _buttons(box: Gtk.Widget) -> list:
    out = []
    child = box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            out.append(child)
        child = child.get_next_sibling()
    return out


class FitSegmented(Gtk.Widget):
    """A ``widgets.segmented`` row of options that stacks them, one per
    line, where the page is narrower than the row (320 px at large
    text) instead of widening the window. A tap selects the option in
    both layouts and calls ``on_pick(value)``."""

    __gtype_name__ = 'TxxtFitSegmented'

    def __init__(self, options, selected: str, on_pick=None) -> None:
        super().__init__()
        self._values = [value for value, _label in options]
        self._on_pick = on_pick
        self._selected = selected

        def items():
            return [(label, value == selected, self._picker(value))
                    for value, label in options]

        self._row = widgets.segmented(items())
        self._column = widgets.segmented(items())
        self._column.set_orientation(Gtk.Orientation.VERTICAL)
        # Stacked: theme hairlines between the options, not the row's
        # left borders.
        for index, button in enumerate(_buttons(self._column)):
            button.remove_css_class('seg-mid')
            button.remove_css_class('seg-last')
            if index:
                self._column.insert_child_after(
                    Gtk.Separator.new(Gtk.Orientation.HORIZONTAL),
                    button.get_prev_sibling())
        self._row.set_parent(self)
        self._column.set_parent(self)
        self._column.set_child_visible(False)
        self._stacked = False
        self._sync_id = 0

    # ------------------------------------------------------------ state

    @property
    def selected(self) -> str:
        return self._selected

    @property
    def stacked(self) -> bool:
        return self._stacked

    def option_buttons(self) -> list:
        """The buttons of the layout on screen."""
        return _buttons(self._column if self._stacked else self._row)

    def select(self, value: str) -> None:
        self._selected = value
        for box in (self._row, self._column):
            for option, button in zip(self._values, _buttons(box)):
                if option == value:
                    button.add_css_class('seg-selected')
                else:
                    button.remove_css_class('seg-selected')

    def _picker(self, value: str):
        def _picked() -> None:
            self.select(value)
            if self._on_pick is not None:
                self._on_pick(value)

        return _picked

    # ------------------------------------------------------------ layout

    def _row_min(self) -> int:
        return self._row.measure(Gtk.Orientation.HORIZONTAL, -1)[0]

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            row_min, row_nat, _b1, _b2 = self._row.measure(orientation, -1)
            col_min, _col_nat, _b1, _b2 = self._column.measure(
                orientation, -1)
            return (min(row_min, col_min), max(row_nat, col_min), -1, -1)
        stacked = 0 <= for_size < self._row_min()
        child = self._column if stacked else self._row
        minimum, natural, _b1, _b2 = child.measure(orientation, for_size)
        return (minimum, natural, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        stacked = width < self._row_min()
        active, idle = ((self._column, self._row) if stacked
                        else (self._row, self._column))
        active.allocate(width, height, -1, None)
        # The hidden layout still gets a size it accepts (it is neither
        # drawn nor tappable): no allocation warnings either way.
        idle_w = max(width, idle.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        idle_h = idle.measure(Gtk.Orientation.VERTICAL, idle_w)[0]
        idle.allocate(idle_w, idle_h, -1, None)
        if stacked != self._stacked:
            self._stacked = stacked
            if not self._sync_id:
                self._sync_id = GLib.idle_add(self._sync_visible)

    def _sync_visible(self) -> bool:
        self._sync_id = 0
        self._row.set_child_visible(not self._stacked)
        self._column.set_child_visible(self._stacked)
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def do_snapshot(self, snapshot):
        self.snapshot_child(
            self._column if self._stacked else self._row, snapshot)

    def do_dispose(self):
        if self._sync_id:
            GLib.source_remove(self._sync_id)
            self._sync_id = 0
        for child in (self._row, self._column):
            if child is not None and child.get_parent() is self:
                child.unparent()
        self._row = self._column = None


class SettingsPage(Adw.NavigationPage):
    """Settings page over the txxt JSON config + rule-table counts."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, on_changed=None,
        on_open_blocking=None, on_open_quarantine=None,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._on_changed = on_changed
        self._on_open_blocking = on_open_blocking
        self._on_open_quarantine = on_open_quarantine
        self._settings = settings_mod.load()
        self._ready = False
        self.set_title('Settings')

        header = widgets.compact_header(
            self._on_back, 'Settings', 'On this device',
        )
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)

        notifications = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        notifications.append(widgets.section_label('Notifications'))
        notifications.append(widgets.caption('Lock-screen privacy'))
        self._privacy_row = FitSegmented(
            LOCK_PRIVACY_OPTIONS, self._settings.lock_screen_privacy,
            self._on_privacy_picked)
        notifications.append(self._privacy_row)
        notifications.append(widgets.caption('Alert style'))
        self._alert_row = FitSegmented(
            ALERT_STYLE_OPTIONS, self._settings.alert_style,
            self._on_alert_picked)
        notifications.append(self._alert_row)
        body.append(notifications)

        messages = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        messages.append(widgets.section_label('Messages'))
        messages.append(widgets.caption('Default region'))
        self._region_row = widgets.text_entry('US')
        self._region_row.set_text(self._settings.default_region)
        tap_to_focus(self._region_row)
        self._region_row.connect('changed', self._on_region_changed)
        messages.append(self._region_row)
        self._auto_download_row = widgets.make_switch(
            self._settings.mms_auto_download, self._on_auto_download_changed)
        messages.append(setting_row(
            'Auto-download MMS', 'Off keeps tap-to-fetch',
            self._auto_download_row))
        self._auto_reply_row = widgets.make_switch(
            self._settings.mms_auto_reply, self._on_auto_reply_changed)
        messages.append(setting_row(
            'Auto-reply to voice MMS', '',
            self._auto_reply_row, last=True))
        body.append(messages)

        filtering = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        filtering.append(widgets.section_label('Filtering'))
        self._blocking_sub = ''
        self._blocking_row = widgets.list_row(
            'Blocking', '', on_activate=self._on_blocking_activated)
        filtering.append(self._blocking_row)
        self._quarantine_sub = ''
        self._quarantine_row = widgets.list_row(
            'Quarantine', '', on_activate=self._on_quarantine_activated,
            last=True)
        filtering.append(self._quarantine_row)
        body.append(filtering)

        body.append(widgets.about_content(
            'TxxT', getattr(txxt, '__version__', '?'),
            widgets.standard_license_entries('TxxT'),
            self._on_licenses_activated))

        self.set_child(widgets.page(header, body, None))
        self.refresh_subtitles()
        self._ready = True

    # ------------------------------------------------------------ display

    def _on_back(self) -> None:
        try:
            self._navigation.pop()
        except Exception:
            pass

    def blocking_subtitle(self) -> str:
        return self._blocking_sub

    def quarantine_subtitle(self) -> str:
        return self._quarantine_sub

    def refresh_subtitles(self) -> None:
        """Re-read rule-table counts (call after blocking/quarantine edits)."""
        try:
            senders = self._store.blocklist_list('sender')
            keywords = self._store.blocklist_list('keyword')
            phrases = self._store.blocklist_list('phrase')
        except Exception:
            senders, keywords, phrases = [], [], []
        self._blocking_sub = (
            f"{count_label(len(senders), 'number')}, "
            f"{count_label(len(keywords) + len(phrases), 'term')}"
        )
        try:
            held = sum(
                1 for row in self._store.get_all_conversations()
                if row.is_quarantined
            )
        except Exception:
            held = 0
        self._quarantine_sub = f'{held} held' if held else 'Empty'
        _retitle(self._blocking_row, 'Blocking', self._blocking_sub)
        _retitle(self._quarantine_row, 'Quarantine', self._quarantine_sub)

    # ------------------------------------------------------------ persist

    def _save(self) -> None:
        try:
            settings_mod.save(self._settings)
        except Exception:
            return
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:
                pass

    def _on_privacy_picked(self, value: str) -> None:
        if not self._ready:
            return
        self._settings.lock_screen_privacy = value
        self._save()

    def _on_alert_picked(self, value: str) -> None:
        if not self._ready:
            return
        self._settings.alert_style = value
        self._save()

    def _on_region_changed(self, row) -> None:
        if not self._ready:
            return
        region = row.get_text().strip().upper()
        if len(region) == 2 and region.isalpha():
            self._settings.default_region = region
            self._save()

    def _on_auto_download_changed(self, active: bool) -> None:
        if not self._ready:
            return
        self._settings.mms_auto_download = bool(active)
        self._save()

    def _on_auto_reply_changed(self, active: bool) -> None:
        if not self._ready:
            return
        self._settings.mms_auto_reply = bool(active)
        self._save()

    def _on_blocking_activated(self) -> None:
        if self._on_open_blocking is not None:
            try:
                self._on_open_blocking()
            except Exception:
                pass

    def _on_quarantine_activated(self) -> None:
        if self._on_open_quarantine is not None:
            try:
                self._on_open_quarantine()
            except Exception:
                pass

    def _on_licenses_activated(self) -> None:
        try:
            self._navigation.push(widgets.licenses_page(
                self._navigation, 'TxxT',
                widgets.standard_license_entries('TxxT')))
        except Exception:
            pass


def _retitle(row_button: Gtk.Button, title: str, subtitle: str) -> None:
    """Update an ``xx_ui`` list row's two labels in place."""
    box = row_button.get_child()
    if box is None:
        return
    middle = None
    kid = box.get_first_child()
    while kid is not None:
        if (isinstance(kid, Gtk.Box)
                and kid.get_orientation() == Gtk.Orientation.VERTICAL):
            middle = kid
            break
        kid = kid.get_next_sibling()
    if middle is None:
        return
    first = middle.get_first_child()
    if isinstance(first, Gtk.Label):
        first.set_text(title)
    second = first.get_next_sibling() if first is not None else None
    if isinstance(second, Gtk.Label):
        second.set_text(subtitle)
    elif subtitle:
        middle.append(widgets.label(subtitle, 'xx-secondary'))
