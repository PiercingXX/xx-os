"""Home & launcher settings: the Linux twin of the Android xx-launcher
preferences screen (Home Screen, Widgets, App Drawer, Gestures), with
the same rows, titles, summaries, choices, defaults and order, all
backed by config.json / gestures.json with hot reload. The row-by-row
map is docs/launcher-settings-parity.md.
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

WIDGET_ORDER = ('time', 'date', 'weather', 'battery')

# Android widget names (its keys are clock/date/weather/battery).
_WIDGET_TITLES = {
    'time': 'Clock',
    'date': 'Date',
    'weather': 'Weather',
    'battery': 'Battery',
}

_TAP_LABELS = {
    'default': 'Default action',
    'none': 'None',
    'refresh': 'Refresh',
}

# Android list preferences: (value, entry label), in Android's order.
_SLOT_COUNT_CHOICES = tuple(
    [(0, 'None')] + [(n, str(n)) for n in range(1, 9)])
_ALIGN_CHOICES = (('left', 'Left'), ('center', 'Center'), ('right', 'Right'))
_DATE_TIME_CHOICES = (('off', 'Off'), ('date_only', 'Date only'),
                      ('date_time', 'Date and time'))
_TEMP_CHOICES = (('fahrenheit', 'Fahrenheit'), ('celsius', 'Celsius'))
_SORT_CHOICES = (('default', 'Default (pinned first)'),
                 ('alphabetical', 'Alphabetical'),
                 ('alphabetical_no_priority', 'Alphabetical (no priority)'),
                 ('install_date', 'Install date'),
                 ('apk_size', 'APK size'))
_SWIPE_DOWN_CHOICES = (('notifications', 'Expand notifications'),
                       ('web_search', 'Web search'),
                       ('disabled', 'Disabled'))
# Linux only: Android follows the system 12/24 h setting.
_CLOCK_CHOICES = ((None, 'System'), (False, '12 h'), (True, '24 h'))
_MODE_CHOICES = (('theme', 'Theme'), ('light', 'Light'), ('dark', 'Dark'),
                 ('system', 'System'))
_FONT_ORDER = ('system-light', 'monospace', 'space-mono', 'jetbrains-mono',
               'jetbrains-mono-nerd', 'custom')
_FONT_LABELS = {
    'system-light': 'System light',
    'monospace': 'Monospace',
    'space-mono': 'Space Mono',
    'jetbrains-mono': 'JetBrains Mono',
    'jetbrains-mono-nerd': 'JetBrains Mono Nerd',
    'custom': 'Custom',
}

# Android strings (res/values/strings.xml, res/xml/preferences.xml).
WIDGETS_SUMMARY = 'Enable, reorder, and set tap actions'
GESTURES_SUMMARY = 'What every swipe, tap, and long-press does'
RESET_TITLE = 'Reset home layout'
RESET_SUMMARY = ('Clear the home screen and folders and seed the suite '
                 'defaults again')
# The Android help text (gestures_help_text); the two lines that work
# differently here say it the Linux way: long-press edits home, and a
# slot is changed from edit mode.
GESTURES_HELP = (
    'Swipe up — open the app drawer\n'
    'Swipe down — notifications or web search\n'
    'Swipe left / right — launch the configured apps\n'
    'Double-tap — lock the screen\n'
    'Long-press the background — edit the home screen\n'
    'Tap a slot — launch the app or open its folder\n'
    'Tap an empty slot — choose an app\n'
    'In edit mode — change, rename, reorder or clear a slot\n'
    'Long-press any app row — actions: add to home, hide, rename, '
    'folders, uninstall\n'
    'In search, start with ! for a web search')

_SWIPE_APP_TITLES = {'swipe_left_home': 'Swipe Left App',
                     'swipe_right_home': 'Swipe Right App'}


def _config_get(win, key: str, default):
    try:
        get = getattr(win.config, 'get', None)
        if callable(get):
            return get(key, default)
        return win.config.data.get(key, default)
    except Exception:
        return default


def _config_set(win, key: str, value) -> None:
    try:
        win.config.set(key, value)
    except Exception:
        pass


def _tap_text(win, key: str) -> str:
    try:
        tap = win.config.widgets.get(key, {}).get('tap', 'default')
    except Exception:
        return _TAP_LABELS['default']
    if isinstance(tap, dict) and tap.get('app'):
        app_id = str(tap['app'])
        try:
            for entry in win.app_index.entries:
                if entry.app_id == app_id:
                    return win.config.label_for(app_id, entry.name)
        except Exception:
            pass
        return app_id
    return _TAP_LABELS.get(tap, _TAP_LABELS['default'])


def location_text(win) -> str:
    """Manual weather location row value: 'Not set' or 'lat, lon'."""
    try:
        lat = win.config.data.get('weather_lat')
        lon = win.config.data.get('weather_lon')
    except Exception:
        return 'Not set'
    if lat is None or lon is None:
        return 'Not set'
    return '%s, %s' % (lat, lon)


def _font_key(win) -> str:
    try:
        key = str(win.config.data.get('font', 'jetbrains-mono-nerd'))
    except Exception:
        return 'jetbrains-mono-nerd'
    return key if key in _FONT_LABELS else 'jetbrains-mono-nerd'


def _font_label(win) -> str:
    key = _font_key(win)
    if key == 'custom':
        try:
            family = win.config.data.get('custom_font_family')
            if family:
                return str(family)
        except Exception:
            pass
    return _FONT_LABELS[key]


def tail_log_lines(path: str, max_lines: int = 400) -> str:
    """Tail of a log file (default 400 lines). Reads from the end so a
    multi-megabyte shell.log never loads fully; missing files read empty."""
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, 2)
            pos = handle.tell()
            data = b''
            while pos > 0 and data.count(b'\n') <= max_lines:
                step = min(8192, pos)
                pos -= step
                handle.seek(pos)
                data = handle.read(step) + data
    except OSError:
        return ''
    lines = data.decode('utf-8', errors='replace').splitlines(keepends=True)
    return ''.join(lines[-max_lines:]) if len(lines) > max_lines else ''.join(lines)


def _visible_text(view) -> str:
    try:
        buf = view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    except Exception:
        return ''


def share_log_text(text: str) -> tuple[bool, str]:
    """Write `text` to a temp file and open it with `xx-share --send`."""
    import os
    import shutil
    import subprocess
    import tempfile
    if shutil.which('xx-share') is None:
        return False, 'Share is not installed on this device.'
    try:
        fd, path = tempfile.mkstemp(prefix='xxwm-log-', suffix='.txt')
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text or '')
    except OSError as err:
        return False, 'Could not stage the log: %s' % err
    try:
        subprocess.Popen(['xx-share', '--send', path], close_fds=True)
        return True, 'Opening Share…'
    except OSError as err:
        return False, 'Could not open Share: %s' % err


def open_logs(win) -> None:
    """Settings → About → Logs: shell tail plus journal/kernel/system
    tabs, with Copy and Share (via xx-share)."""
    from pathlib import Path

    from gi.repository import Gdk, Gtk
    from xx_ui import widgets

    import settings_logs
    from settings_page import _run_bg

    log_path = Path.home() / '.local' / 'share' / 'xx-wm' / 'shell.log'

    dialog = Gtk.Window(title='Logs')
    try:
        dialog.set_transient_for(win)
        dialog.set_modal(True)
    except Exception:
        pass
    dialog.set_decorated(False)
    dialog.set_default_size(360, 720)

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    root.add_css_class('logs-root')
    root.set_margin_top(16)
    root.set_margin_bottom(24)
    root.set_margin_start(20)
    root.set_margin_end(20)

    state = {'tab': 'shell'}

    view = Gtk.TextView()
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_monospace(True)

    status = widgets.wrap_label(
        'Capped output. PINs and passwords are redacted.')

    def _show(text: str) -> None:
        try:
            view.get_buffer().set_text(text or 'No log yet.')
        except Exception:
            pass

    def _fetch(tab: str) -> None:
        state['tab'] = tab
        _show('Loading…')
        if tab == 'shell':
            _run_bg(
                lambda: settings_logs.scrub_secrets(settings_logs.cap_text(
                    tail_log_lines(str(log_path)))),
                _show)
            return
        helper_name = settings_logs.helper_log_name(tab)
        if helper_name is not None:
            _fetch_helper_log(helper_name)
            return

        def _work():
            return settings_logs.fetch_journal(tab)

        def _done(text) -> None:
            if (tab == 'kernel' and isinstance(text, str)
                    and text.startswith('dmesg restricted')):
                _show(text + '\nRetrying as a service…')
                _fetch_helper_log('dmesg')
                return
            _show(text if isinstance(text, str) else 'Log unavailable.')

        _run_bg(_work, _done)

    def _fetch_helper_log(name: str) -> None:
        try:
            import helper_client
        except ImportError:
            _show('Helper client unavailable.')
            return

        def _done(ok: bool, result) -> None:
            if not ok:
                _show('System log unavailable (%s).' % result)
                return
            _show(settings_logs.scrub_secrets(
                settings_logs.cap_text(str(result or 'No entries.'))))

        helper_client.read_log(name, _done)

    def _copy() -> None:
        try:
            copied = _visible_text(view)
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(copied)
                status.set_text('Copied.')
        except Exception:
            pass

    def _share() -> None:
        ok, message = share_log_text(_visible_text(view))
        try:
            status.set_text(message)
        except Exception:
            pass
        _ = ok

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title = widgets.label('Logs', 'xx-title')
    title.set_hexpand(True)
    header.append(title)
    header.append(widgets.text_button('Copy', _copy))
    header.append(widgets.text_button('Share', _share))
    root.append(header)
    tabs = widgets.segmented([
        (label, key == state['tab'],
         lambda key=key: _fetch(key))
        for key, label in settings_logs.TABS])
    tab_scroll = Gtk.ScrolledWindow(hexpand=True)
    tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                          Gtk.PolicyType.NEVER)
    tab_scroll.set_child(tabs)
    root.append(tab_scroll)
    root.append(status)

    scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(view)
    root.append(scroll)

    dialog.set_child(root)
    _fetch('shell')
    try:
        dialog.present()
    except Exception:
        pass


def reset_home_layout(config, gesture_config) -> None:
    """Clear slots, renames and swipe bindings, then reseed the default
    layout. Pure UI helper: re-render stays with the caller."""
    config.set_home_slots([])
    config.clear_app_labels()
    for key in ('swipe_left_home', 'swipe_right_home'):
        try:
            gesture_config.reset(key)
        except Exception:
            pass
    config.set_default_layout_applied(False)
    from default_layout import apply_default_layout
    apply_default_layout(config, gesture_config)


def _install_font(win, path: str):
    from config import install_custom_font
    try:
        return install_custom_font(win.config, path)
    except Exception as error:
        return (False, str(error))


def _choice_label(choices, value, fallback: str) -> str:
    for key, label in choices:
        if key == value:
            return label
    return fallback


def _scale_row(title: str, value_text: str, slider):
    """Card row: title and live value over a full-width slider."""
    from gi.repository import Gtk
    from xx_ui import widgets

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    name = widgets.label(title, 'xx-title')
    name.set_hexpand(True)
    value = Gtk.Label(label=value_text, xalign=1.0)
    value.add_css_class('xx-secondary')
    head.append(name)
    head.append(value)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    column.append(head)
    column.append(slider)
    row = ui.wide_row(column)
    row.value_label = value
    return row


def _attr(win, name: str, default):
    """A ShellConfig property, with ``default`` for bare test doubles."""
    try:
        value = getattr(win.config, name)
    except Exception:
        return default
    return default if callable(value) else value


def _status(win, message: str) -> None:
    try:
        win._show_status(message)
    except Exception:
        pass


def _close(win) -> None:
    host = getattr(win, '_sheet_host', None)
    if host is not None:
        host.close_all()


def _app_names(win) -> dict:
    """{app_id: display name} for every installed app."""
    names = {}
    try:
        for entry in win.app_index.entries:
            names[entry.app_id] = win.config.label_for(entry.app_id,
                                                       entry.name)
    except Exception:
        pass
    return names


def _widget_keys(win) -> list:
    """Widget keys in their home order (enabled or not)."""
    try:
        configs = win.config.widgets
    except Exception:
        return list(WIDGET_ORDER)
    return sorted(configs, key=lambda k: configs[k].get('order', 99))


def _swipe_app_text(win, key: str) -> str:
    """Swipe Left/Right App summary: the bound app's name, else empty
    (Android shows nothing while the swipe opens its default app)."""
    try:
        action = win.gesture_config.get(key) or 'none'
    except Exception:
        return ''
    if action == 'none':
        return ''
    if action.startswith('launch:'):
        app_id = action[len('launch:'):]
        return _app_names(win).get(app_id, app_id)
    return {'camera': 'Camera', 'dialer': 'Phone'}.get(
        action, action.replace('_', ' ').title())


def confirm_reset_home(win) -> None:
    """Android "Reset home layout" (Backup & Restore): confirm, then wipe
    slots, renames and swipe apps and seed the defaults again."""
    from gi.repository import Adw

    dialog = Adw.AlertDialog(heading=RESET_TITLE, body=RESET_SUMMARY + '?')
    dialog.add_response('cancel', 'Cancel')
    dialog.add_response('reset', 'Reset')
    dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def _on_response(_dialog, response: str) -> None:
        win._launcher_dialog = None
        if response != 'reset':
            return
        try:
            reset_home_layout(win.config, win.gesture_config)
        except Exception:
            return
        try:
            win._refresh_after_item_action()
        except Exception:
            pass
        _status(win, 'Home layout reset.')

    dialog.connect('response', _on_response)
    win._launcher_dialog = dialog
    try:
        dialog.present(win)
    except Exception:
        win._launcher_dialog = None


def build_appearance_group(win) -> list:
    """Mode, font and text size: launcher prefs shown on the Display &
    appearance page under the theme swatches."""
    return LauncherSection().build_appearance(win)


class LauncherSection(settings_page._Section):
    key = 'launcher'
    title = 'Home Screen'

    def build(self, win) -> list:
        win._launcher_updating = False
        win._launcher_rows = {}
        rows = win._launcher_rows

        def nav(key, title, subtitle, handler):
            row = ui.nav_row(title, subtitle, handler)
            rows[key] = row
            return row

        def switch(key, title, subtitle, active, handler):
            row = ui.switch_row(title, subtitle, bool(active))
            row.switch.connect('notify::active',
                               lambda sw, _p: self._on_switch(
                                   win, handler, sw))
            rows[key] = row
            return row

        config = win.config
        home = ui.group('Home Screen', [
            # Android asks for the HOME role here; XX-WM is the shell,
            # so it is always the home screen.
            ui.row('Default Launcher', 'XX-WM is always the home screen'),
            nav('slot_count', 'Home Slot Count', '',
                lambda r: self._pick(win, r, 'Home Slot Count',
                                     _SLOT_COUNT_CHOICES,
                                     self._slot_count_value(win),
                                     lambda v: self._set_slot_count(win, v))),
            nav('alignment', 'Text Alignment', '',
                lambda r: self._pick(win, r, 'Text Alignment',
                                     _ALIGN_CHOICES,
                                     _attr(win, 'home_alignment', 'center'),
                                     lambda v: self._set_alignment(win, v))),
            nav('date_time', 'Date/Time Mode', '',
                lambda r: self._pick(win, r, 'Date/Time Mode',
                                     _DATE_TIME_CHOICES,
                                     _attr(win, 'date_time_mode',
                                           'date_time'),
                                     lambda v: self._set_date_time(win, v))),
            # The top status bar's clock and glyphs (status_bar.py); the
            # strip keeps the camera area clear either way.
            switch('status_bar', 'Show Status Bar', '',
                   _attr(win, 'status_bar_visible', False),
                   self._on_status_bar),
            nav('clock', 'Clock Format', '',
                lambda r: self._pick(win, r, 'Clock Format', _CLOCK_CHOICES,
                                     _config_get(win, 'clock_24h', None),
                                     lambda v: self._set_clock(win, v))),
        ])
        widgets = ui.group('Widgets', [
            nav('widgets', 'Widgets', WIDGETS_SUMMARY,
                lambda r: self._open_widgets(win, r)),
            nav('temp_unit', 'Temperature Unit', '',
                lambda r: self._pick(win, r, 'Temperature Unit',
                                     _TEMP_CHOICES,
                                     _config_get(win, 'weather_temp_unit',
                                                 'fahrenheit'),
                                     lambda v: self._set_temp_unit(win, v))),
            switch('auto_location', 'Auto Location', 'For the forecast',
                   _config_get(win, 'weather_auto_location', True),
                   self._on_auto_loc),
            nav('location', 'Manual Location', '',
                lambda r: self._open_location(win, r)),
        ])
        drawer = ui.group('App Drawer', [
            switch('auto_keyboard', 'Auto-show Keyboard', '',
                   _attr(win, 'auto_show_keyboard', False),
                   self._on_auto_keyboard),
            nav('sort', 'Sort Mode', '',
                lambda r: self._pick(win, r, 'Sort Mode', _SORT_CHOICES,
                                     _attr(win, 'drawer_sort', 'default'),
                                     lambda v: self._set_sort(win, v))),
            nav('hidden', 'Hidden Apps', '',
                lambda r: self._open_hidden_apps(win, r)),
            switch('auto_launch', 'Search Auto-launch',
                   'Opens a lone match; a leading space stops it',
                   _attr(win, 'search_auto_launch', False),
                   self._on_auto_launch),
        ])
        gestures = ui.group('Gestures', [
            nav('help', 'Gestures', GESTURES_SUMMARY,
                lambda r: self._open_help(win, r)),
            switch('swipe_left_on', 'Enable Swipe Left', '',
                   _attr(win, 'swipe_left_enabled', True),
                   lambda w, on: self._set_bool(
                       w, 'set_swipe_left_enabled', on)),
            nav('swipe_left_home', 'Swipe Left App', '',
                lambda r: self._pick_swipe_app(win, r, 'swipe_left_home')),
            switch('swipe_right_on', 'Enable Swipe Right', '',
                   _attr(win, 'swipe_right_enabled', True),
                   lambda w, on: self._set_bool(
                       w, 'set_swipe_right_enabled', on)),
            nav('swipe_right_home', 'Swipe Right App', '',
                lambda r: self._pick_swipe_app(win, r, 'swipe_right_home')),
            nav('swipe_down', 'Swipe Down Action', '',
                lambda r: self._pick(win, r, 'Swipe Down Action',
                                     _SWIPE_DOWN_CHOICES,
                                     _attr(win, 'swipe_down_action',
                                           'notifications'),
                                     lambda v: self._set_swipe_down(win, v))),
            switch('double_tap', 'Double Tap to Lock', '',
                   self._double_tap_on(win), self._on_double_tap),
            switch('recents', 'Home Button Opens Recents', '',
                   _config_get(win, 'home_to_recents', False),
                   self._on_recents),
        ])
        _ = config
        self.refresh(win)
        return home + widgets + drawer + gestures

    def refresh(self, win) -> None:
        self._sync_switches(win)
        self._render_values(win)
        self._render_font(win)
        self._render_mode(win)

    # -- shared ---------------------------------------------------------

    def _row(self, win, key):
        return (getattr(win, '_launcher_rows', None) or {}).get(key)

    def _on_switch(self, win, handler, switch) -> None:
        if getattr(win, '_launcher_updating', False):
            return
        handler(win, bool(switch.get_active()))

    def _sync_switches(self, win) -> None:
        win._launcher_updating = True
        try:
            for key, value in (
                    ('status_bar', _attr(win, 'status_bar_visible', False)),
                    ('auto_location', _config_get(
                        win, 'weather_auto_location', True)),
                    ('auto_keyboard', _attr(win, 'auto_show_keyboard',
                                            False)),
                    ('auto_launch', _attr(win, 'search_auto_launch', False)),
                    ('swipe_left_on', _attr(win, 'swipe_left_enabled', True)),
                    ('swipe_right_on', _attr(win, 'swipe_right_enabled',
                                             True)),
                    ('double_tap', self._double_tap_on(win)),
                    ('recents', _config_get(win, 'home_to_recents', False))):
                row = self._row(win, key)
                switch = getattr(row, 'switch', None)
                if switch is not None:
                    try:
                        switch.set_active(bool(value))
                    except Exception:
                        pass
        finally:
            win._launcher_updating = False

    def _render_values(self, win) -> None:
        """Each list row shows its current entry under the title."""
        values = {
            'slot_count': _choice_label(
                _SLOT_COUNT_CHOICES, self._slot_count_value(win), ''),
            'alignment': _choice_label(
                _ALIGN_CHOICES, _attr(win, 'home_alignment', 'center'),
                'Center'),
            'date_time': _choice_label(
                _DATE_TIME_CHOICES, _attr(win, 'date_time_mode',
                                          'date_time'), 'Date and time'),
            'clock': _choice_label(
                _CLOCK_CHOICES, _config_get(win, 'clock_24h', None),
                'System'),
            'temp_unit': _choice_label(
                _TEMP_CHOICES, _config_get(win, 'weather_temp_unit',
                                           'fahrenheit'), 'Fahrenheit'),
            'location': location_text(win),
            'sort': _choice_label(
                _SORT_CHOICES, _attr(win, 'drawer_sort', 'default'),
                _SORT_CHOICES[0][1]),
            'swipe_left_home': _swipe_app_text(win, 'swipe_left_home'),
            'swipe_right_home': _swipe_app_text(win, 'swipe_right_home'),
            'swipe_down': _choice_label(
                _SWIPE_DOWN_CHOICES, _attr(win, 'swipe_down_action',
                                           'notifications'),
                _SWIPE_DOWN_CHOICES[0][1]),
        }
        for key, text in values.items():
            ui.set_subtitle(self._row(win, key), text)

    def _pick(self, win, anchor, title: str, choices, current, apply) -> None:
        """An Android list preference: the entries in a sheet, the
        current one checked."""
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        checked = next((i for i, (value, _label) in enumerate(choices)
                        if value == current and type(value) is type(current)),
                       None)
        actions._show_action_menu(
            anchor, title,
            [(label, lambda value=value: self._apply(win, apply, value))
             for value, label in choices],
            checked=checked)

    def _apply(self, win, apply, value) -> None:
        apply(value)
        self._render_values(win)

    def _set_bool(self, win, setter: str, value: bool) -> None:
        try:
            getattr(win.config, setter)(bool(value))
        except Exception:
            return
        try:
            win._apply_config_change()
        except Exception:
            pass

    # -- Home Screen ----------------------------------------------------

    def _slot_count_value(self, win) -> int:
        try:
            return int(win.config.effective_slot_count())
        except Exception:
            return 4

    def _set_slot_count(self, win, value: int) -> None:
        try:
            win.config.set_home_slot_count(int(value))
        except Exception:
            return
        self._refresh_home(win)

    def _set_alignment(self, win, value: str) -> None:
        try:
            win.config.set_home_alignment(value)
        except Exception:
            return
        try:
            win._apply_config_change()
        except Exception:
            pass

    def _set_date_time(self, win, value: str) -> None:
        try:
            win.config.set_date_time_mode(value)
        except Exception:
            return
        try:
            win._build_widget_block()
        except Exception:
            pass

    def _on_status_bar(self, win, on: bool) -> None:
        self._set_bool(win, 'set_status_bar_visible', on)

    def _set_clock(self, win, value) -> None:
        _config_set(win, 'clock_24h', value)
        try:
            win._refresh_clock()
        except Exception:
            pass

    def _refresh_home(self, win) -> None:
        try:
            win._home_launcher.refresh()
            win._populate_apps(win.apps_search.get_text())
        except Exception:
            pass

    # -- Widgets ----------------------------------------------------------

    def _open_widgets(self, win, anchor) -> None:
        """The Android Widgets dialog: per widget a switch, ↑ ↓ to move
        it, and its tap action; the sheet re-renders in place."""
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        from gi.repository import Gtk
        from xx_ui import widgets

        box = widgets.dialog_box('Widgets')
        box.set_spacing(4)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(rows)

        def rebuild() -> None:
            child = rows.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                rows.remove(child)
                child = nxt
            ordered = _widget_keys(win)
            for index, key in enumerate(ordered):
                rows.append(self._widget_line(
                    win, anchor, key, index, len(ordered), rebuild))

        rebuild()
        box.append(widgets.primary_button(
            'OK', lambda: _close(win)))
        actions._show_popover(anchor, box)

    def _widget_line(self, win, anchor, key: str, index: int, total: int,
                     rebuild):
        from gi.repository import Gtk
        from xx_ui import widgets

        try:
            enabled = bool(win.config.widgets.get(key, {}).get(
                'enabled', True))
        except Exception:
            enabled = True
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        line.add_css_class('xx-widget-line')
        switch = widgets.make_switch(enabled)
        switch.connect('notify::active', lambda sw, _p: self._flip_widget(
            win, key, sw.get_active(), rebuild))
        line.append(switch)
        text = Gtk.Button()
        text.add_css_class('xx-set-press')
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        column.append(widgets.wrap_label(_WIDGET_TITLES.get(key, key),
                                         'xx-title'))
        column.append(widgets.wrap_label('Tap: %s' % _tap_text(win, key)))
        text.set_child(column)
        text.set_hexpand(True)
        text.connect('clicked',
                     lambda _b: self._pick_tap(win, anchor, key))
        line.append(text)
        for glyph, delta in (('↑', -1), ('↓', 1)):
            move = Gtk.Button(label=glyph)
            move.add_css_class('home-edit-ctl')
            move.set_sensitive(0 <= index + delta < total)
            move.connect('clicked', lambda _b, d=delta: self._move_widget(
                win, key, d, rebuild))
            line.append(move)
        return line

    def _flip_widget(self, win, key: str, on: bool, rebuild) -> None:
        try:
            win.config.set_widget_enabled(key, bool(on))
            win._build_widget_block()
        except Exception:
            pass

    def _move_widget(self, win, key: str, delta: int, rebuild) -> None:
        keys = _widget_keys(win)
        if key not in keys:
            return
        index = keys.index(key)
        target = index + delta
        if not 0 <= target < len(keys):
            return
        keys[index], keys[target] = keys[target], keys[index]
        try:
            if win.config.set_widget_order(keys):
                win._build_widget_block()
        except Exception:
            pass
        rebuild()

    def _pick_tap(self, win, anchor, key: str) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        try:
            current = win.config.widgets.get(key, {}).get('tap', 'default')
        except Exception:
            current = 'default'
        values = ['default', 'none'] + (['refresh'] if key == 'weather'
                                        else [])
        choices = [(_TAP_LABELS[value],
                    lambda value=value: self._set_tap(win, anchor, key, value))
                   for value in values]
        choices.append(('Choose App…',
                        lambda: self._pick_tap_app(win, anchor, key)))
        checked = values.index(current) if current in values else (
            len(values) if isinstance(current, dict) else None)
        actions._show_action_menu(
            anchor, 'Tap: %s' % _WIDGET_TITLES.get(key, key), choices,
            checked=checked)

    def _set_tap(self, win, anchor, key: str, tap) -> None:
        try:
            win.config.set_widget_tap(key, tap)
        except Exception:
            pass
        self._open_widgets(win, anchor)

    def _pick_tap_app(self, win, anchor, key: str) -> None:
        self._pick_app(
            win, anchor, lambda app_id: self._set_tap(
                win, anchor, key, {'app': app_id}),
            clear_label=_TAP_LABELS['default'],
            on_clear=lambda: self._set_tap(win, anchor, key, 'default'))

    # -- Weather (Linux: the location Android asks the system for) ---------

    def _set_temp_unit(self, win, value: str) -> None:
        _config_set(win, 'weather_temp_unit', value)
        try:
            win._refresh_weather()
        except Exception:
            pass

    def _on_auto_loc(self, win, on: bool) -> None:
        _config_set(win, 'weather_auto_location', bool(on))
        try:
            win._refresh_weather(force=True)
        except Exception:
            pass

    def _open_location(self, win, anchor):
        try:
            lat = win.config.data.get('weather_lat')
            lon = win.config.data.get('weather_lon')
        except Exception:
            lat, lon = None, None
        return ui.form_sheet(
            win, anchor, 'Manual Location',
            [('lat', 'Latitude', '' if lat is None else lat, 'number'),
             ('lon', 'Longitude', '' if lon is None else lon, 'number')],
            'Save location', lambda values: self._save_location(win, values),
            note='Leave both blank to clear.')

    def _save_location(self, win, values: dict) -> str | None:
        """Commit the location sheet; an error string keeps it open."""
        def _parse(text) -> float | None:
            text = str(text or '').strip()
            if not text:
                return None
            try:
                return float(text)
            except (TypeError, ValueError):
                return None

        lat = _parse(values.get('lat'))
        lon = _parse(values.get('lon'))
        if (lat is None) != (lon is None):
            return 'Set both latitude and longitude.'
        _config_set(win, 'weather_lat', lat)
        _config_set(win, 'weather_lon', lon)
        ui.set_subtitle(self._row(win, 'location'), location_text(win))
        try:
            win._refresh_weather(force=True)
            win._show_status('Location saved.' if lat is not None
                             else 'Location cleared.')
        except Exception:
            pass
        return None

    # -- App Drawer -------------------------------------------------------

    def _on_auto_keyboard(self, win, on: bool) -> None:
        try:
            win.config.set_auto_show_keyboard(bool(on))
        except Exception:
            pass

    def _set_sort(self, win, value: str) -> None:
        try:
            win.config.set_drawer_sort(value)
        except Exception:
            _config_set(win, 'drawer_sort', value)
        try:
            from drawer_page import drawer_sort_label
            win._sort_btn.set_label(drawer_sort_label(value))
        except Exception:
            pass
        try:
            win._populate_apps(win.apps_search.get_text())
        except Exception:
            pass

    def _on_auto_launch(self, win, on: bool) -> None:
        try:
            win.config.set_search_auto_launch(bool(on))
        except Exception:
            pass

    def _open_hidden_apps(self, win, anchor) -> None:
        """Android Hidden Apps: the hidden apps by name; a tap shows one
        in the drawer again."""
        try:
            hidden = sorted(win.config.hidden_apps or ())
        except Exception:
            hidden = []
        if not hidden:
            _status(win, 'No hidden apps')
            return
        names = _app_names(win)
        labels = sorted(((names.get(app_id, app_id), app_id)
                         for app_id in hidden), key=lambda p: p[0].casefold())
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        actions._show_action_menu(
            anchor, 'Hidden Apps',
            [(label, lambda app_id=app_id: self._unhide(win, app_id))
             for label, app_id in labels])

    def _unhide(self, win, app_id: str) -> None:
        try:
            hidden = [h for h in win.config.hidden_apps if h != app_id]
            win.config.set_hidden_apps(hidden)
        except Exception:
            return
        _status(win, 'Shown')
        try:
            win._populate_apps(win.apps_search.get_text())
        except Exception:
            pass

    # -- Gestures ---------------------------------------------------------

    def _open_help(self, win, anchor) -> None:
        ui.info_sheet(win, anchor, 'Gestures', GESTURES_HELP)

    def _pick_swipe_app(self, win, anchor, key: str) -> None:
        """Android opens its app picker; "Clear" (when an app is set)
        returns the swipe to its default app."""
        try:
            current = win.gesture_config.get(key) or 'none'
        except Exception:
            current = 'none'
        self._pick_app(
            win, anchor,
            lambda app_id: self._set_swipe_app(win, key, 'launch:' + app_id),
            clear_label='Clear' if current != 'none' else None,
            on_clear=lambda: self._set_swipe_app(win, key, 'none'))

    def _set_swipe_app(self, win, key: str, action: str) -> None:
        try:
            win.gesture_config.set(key, action)
        except (ValueError, AttributeError):
            return
        self._render_values(win)
        try:
            win._refresh_gesture_labels()
        except Exception:
            pass

    def _set_swipe_down(self, win, value: str) -> None:
        try:
            win.config.set_swipe_down_action(value)
        except Exception:
            pass

    def _double_tap_on(self, win) -> bool:
        try:
            return win.gesture_config.get('double_tap_home') == 'lock_screen'
        except Exception:
            return False

    def _on_double_tap(self, win, on: bool) -> None:
        try:
            win.gesture_config.set('double_tap_home',
                                   'lock_screen' if on else 'none')
        except (ValueError, AttributeError):
            pass

    def _on_recents(self, win, on: bool) -> None:
        _config_set(win, 'home_to_recents', bool(on))

    # -- the shared app picker (Android AppPickerActivity) ----------------

    def _pick_app(self, win, anchor, on_pick, clear_label=None,
                  on_clear=None) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        names = _app_names(win)
        choices = []
        if clear_label and on_clear is not None:
            choices.append((clear_label, on_clear))
        for name, app_id in sorted(((n, a) for a, n in names.items()),
                                   key=lambda p: p[0].casefold()):
            choices.append((name, lambda app_id=app_id: on_pick(app_id)))
        actions._show_action_menu(anchor, 'Select App', choices)

    # -- Appearance (shown on the Display & appearance page) -------------

    def build_appearance(self, win) -> list:
        win._launcher_mode_box = ui.nav_row(
            'Mode', '', lambda r: self._pick_mode(win, r))
        win._launcher_font_box = ui.nav_row(
            'Font', '', lambda r: self._pick_font(win, r))
        rows = [win._launcher_mode_box, win._launcher_font_box,
                ui.nav_row('Import font…', 'A .ttf or .otf file',
                           lambda _r: self._import_font(win)),
                self._build_scale(win)]
        self._render_mode(win)
        self._render_font(win)
        return ui.group('Look', rows)

    def _mode_label(self, win) -> str:
        try:
            mode = win.config.appearance_mode
        except AttributeError:
            mode = 'theme'
        return _choice_label(_MODE_CHOICES, mode, _MODE_CHOICES[0][1])

    def _render_mode(self, win) -> None:
        ui.set_subtitle(getattr(win, '_launcher_mode_box', None),
                        self._mode_label(win))

    def _pick_mode(self, win, anchor) -> None:
        self._pick(win, anchor, 'Mode', _MODE_CHOICES,
                   _attr(win, 'appearance_mode', 'theme'),
                   lambda value: self._set_mode(win, value))

    def _set_mode(self, win, value: str) -> None:
        try:
            win.config.set_appearance_mode(value)
        except Exception:
            return
        self._render_mode(win)
        try:
            win._apply_config_change()
        except Exception:
            pass

    def _build_scale(self, win):
        from gi.repository import Gtk

        adjustment = Gtk.Adjustment(
            value=float(getattr(win.config, 'text_size_scale', 1.0) or 1.0),
            lower=0.5, upper=2.0, step_increment=0.05, page_increment=0.1)
        slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                           adjustment=adjustment)
        slider.set_digits(0)
        slider.set_draw_value(False)
        slider.set_hexpand(True)
        slider.set_size_request(0, 44)
        row = _scale_row('Text size',
                         '%d%%' % round(adjustment.get_value() * 100),
                         slider)
        percent = row.value_label
        win._launcher_scale_label = percent
        slider.connect('value-changed',
                       lambda _s: self._on_scale(win, slider, percent))
        win._launcher_scale = slider
        return row

    def _on_scale(self, win, slider, percent) -> None:
        try:
            value = round(float(slider.get_value()), 2)
        except Exception:
            return
        try:
            current = win.config.text_size_scale
        except Exception:
            current = 1.0
        if abs(value - current) < 0.005:
            return
        try:
            win.config.set_text_size_scale(value)
        except Exception:
            return
        try:
            percent.set_text('%d%%' % round(value * 100))
        except Exception:
            pass
        try:
            win._apply_theme()
        except Exception:
            pass

    def _render_font(self, win) -> None:
        ui.set_subtitle(getattr(win, '_launcher_font_box', None),
                        _font_label(win))

    def _pick_font(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        current = _font_key(win)
        choices = [(_FONT_LABELS[key], lambda key=key: self._set_font(win, key))
                   for key in _FONT_ORDER]
        actions._show_action_menu(anchor, 'Font', choices,
                                  checked=_FONT_ORDER.index(current))

    def _set_font(self, win, key: str) -> None:
        try:
            win.config.set_font(key)
        except Exception:
            return
        try:
            win._apply_theme()
        except Exception:
            pass
        self._render_font(win)

    def _import_font(self, win) -> None:
        from gi.repository import Gio, Gtk

        dialog = Gtk.FileDialog(title='Import font')
        fonts = Gtk.FileFilter()
        fonts.set_name('Font files')
        fonts.add_pattern('*.ttf')
        fonts.add_pattern('*.otf')
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(fonts)
        dialog.set_filters(store)
        dialog.set_default_filter(fonts)

        def _done(_dialog, result) -> None:
            try:
                chosen = dialog.open_finish(result)
            except Exception:
                return
            if chosen is None:
                return
            try:
                path = chosen.get_path()
            except Exception:
                return
            if not path:
                return
            settings_page._run_bg(
                lambda: _install_font(win, path),
                lambda done: self._on_font_installed(win, done))

        try:
            dialog.open(win, None, _done)
        except Exception:
            pass

    def _on_font_installed(self, win, done) -> None:
        ok, message = done if isinstance(done, tuple) else (False, 'Import failed.')
        try:
            win._show_status(
                f'Font installed: {message}' if ok
                else f'Font import failed: {message}')
        except Exception:
            pass
        if not ok:
            return
        try:
            win._apply_theme()
        except Exception:
            pass
        self._render_font(win)
