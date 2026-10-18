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

from gi.repository import Adw, Gtk  # noqa: E402

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
        self._privacy_row = widgets.segmented([
            (label, value == self._settings.lock_screen_privacy,
             self._on_privacy_picked(value))
            for value, label in LOCK_PRIVACY_OPTIONS
        ])
        notifications.append(self._privacy_row)
        notifications.append(widgets.caption('Alert style'))
        self._alert_row = widgets.segmented([
            (label, value == self._settings.alert_style,
             self._on_alert_picked(value))
            for value, label in ALERT_STYLE_OPTIONS
        ])
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
        messages.append(widgets.setting_row(
            'Auto-download MMS', 'Off keeps tap-to-fetch',
            self._auto_download_row))
        self._auto_reply_row = widgets.make_switch(
            self._settings.mms_auto_reply, self._on_auto_reply_changed)
        messages.append(widgets.setting_row(
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

    def _on_privacy_picked(self, value: str):
        def _picked() -> None:
            if not self._ready:
                return
            self._settings.lock_screen_privacy = value
            self._save()

        return _picked

    def _on_alert_picked(self, value: str):
        def _picked() -> None:
            if not self._ready:
                return
            self._settings.alert_style = value
            self._save()

        return _picked

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
