from __future__ import annotations

from gesture_config import IPC_VERBS, VALID_ACTIONS

import settings_ui as ui


# Settings sections a quick-tile long-press can deep-link to (quick_actions
# tile_settings_section emits these); anything else opens the top of the page.
_SETTINGS_SECTION_KEYS = ('wifi', 'bluetooth', 'mobile', 'apps', 'appearance',
                          'display', 'keyboard', 'lock', 'launcher',
                          'standby', 'permissions', 'smart_lock',
                          'charging', 'batteryuse', 'screentime', 'medical',
                          'recording', 'captions', 'nearby', 'findmy', 'about',
                          'storage', 'security', 'services', 'firewall',
                          'nope')

# The top-level menu (Android Settings style): one row per category, each
# opening a subpage that holds only its sections, in this order. Grouped
# the way a phone user looks for things: radios first, then how the phone
# looks and sounds, then power and apps, then the protective and
# maintenance pages, System last. Every section has exactly one home
# (docs/settings-map.md): Network & internet is connectivity only; what
# the phone serves (wireless debugging, services, the incoming-connection
# firewall) sits together on System, where Android keeps Developer
# options and its running services.
SETTINGS_CATEGORIES: tuple = (
    ('network', 'Network & internet', ('wifi', 'mobile')),
    ('devices', 'Connected devices', ('bluetooth', 'nearby')),
    ('display', 'Display & appearance', ('appearance', 'display')),
    # The Android launcher's preference screen (docs/launcher-settings-
    # parity.md); the system-wide edge swipes live under System.
    ('home', 'Home & launcher', ('launcher',)),
    ('sound', 'Sound', ('sound', 'captions', 'recording')),
    ('battery', 'Battery', ('battery', 'standby', 'charging',
                            'batteryuse')),
    ('apps', 'Apps', ('screentime', 'apps', 'nope')),
    ('privacy', 'Privacy & security', ('lock', 'smart_lock', 'permissions',
                                       'security', 'findmy', 'medical')),
    ('storage', 'Storage & backup', ('storage', 'backup')),
    ('system', 'System', ('system', 'keyboard', 'gestures', 'developer',
                          'services', 'firewall', 'about', 'shell')),
)

# Menu cards: categories that belong together share one rounded card.
_MENU_GROUPS = (('network', 'devices'), ('display', 'home', 'sound'),
                ('battery', 'apps', 'storage'), ('privacy', 'system'))


def category_for(key: str | None) -> str | None:
    """The category holding section ``key`` (or ``key`` itself when it
    names a category); None for anything else."""
    for cat, _title, sections in SETTINGS_CATEGORIES:
        if key == cat or key in sections:
            return cat
    return None


def _percent(status: str | None) -> str:
    """'87%' from a battery_status line ('87% — discharging, …')."""
    head = str(status or '').split(' ', 1)[0]
    return head if head.endswith('%') else ''


def quick_summaries(win) -> dict:
    """Menu row summaries that only read config (main loop, instant)."""
    out: dict = {}
    config = getattr(win, 'config', None)
    try:
        from config import THEME_PRESETS
        import settings_launcher
        key = str(config.data.get('theme', 'amoled'))
        theme = 'Custom' if key == 'custom' else THEME_PRESETS[key].name
        out['display'] = '%s · %s' % (theme,
                                       settings_launcher._font_label(win))
    except Exception:
        pass
    try:
        align = str(config.home_alignment).capitalize()
        count = sum(1 for _k, conf in config.ordered_widgets()
                    if conf.get('enabled', True))
        out['home'] = '%s · %d widget%s' % (align, count,
                                             '' if count == 1 else 's')
    except Exception:
        pass
    try:
        count = len(list(win.app_index.entries))
        out['apps'] = '%d installed' % count
    except Exception:
        pass
    try:
        pin = 'PIN on' if config.pin_hash else 'No PIN'
        import settings_smart_lock
        status, _detail = settings_smart_lock.trust_status(win)
        lock = 'Smart Lock on' if status != 'Off' else 'Smart Lock off'
        out['privacy'] = '%s · %s' % (pin, lock)
    except Exception:
        pass
    try:
        from importlib.metadata import version as get_version
        shell_version = get_version('xx-wm')
    except Exception:
        shell_version = '0.1.1'
    out['system'] = 'XX-WM %s' % shell_version
    return out


def slow_summaries() -> dict:
    """Menu summaries that touch the system (worker thread only; every
    probe is read-only and failures read as unavailable)."""
    import system_settings

    out: dict = {}
    try:
        enabled = system_settings.wifi_enabled()
        if enabled is False:
            out['network'] = 'Wi-Fi off'
        elif enabled is None:
            out['network'] = 'Wi-Fi unavailable'
        else:
            used = [net.ssid for net in system_settings.list_wifi() or []
                    if net.in_use]
            out['network'] = used[0] if used else 'Not connected'
    except Exception:
        out['network'] = 'Wi-Fi unavailable'
    try:
        powered = system_settings.bt_powered()
        if powered is None:
            out['devices'] = 'Bluetooth unavailable'
        elif not powered:
            out['devices'] = 'Bluetooth off'
        else:
            linked = [dev for dev in system_settings.list_bt_devices() or []
                      if dev.connected]
            out['devices'] = ('Bluetooth on · %d connected' % len(linked)
                              if linked else 'Bluetooth on')
    except Exception:
        out['devices'] = 'Bluetooth unavailable'
    try:
        sinks = system_settings.list_audio_sinks()
        default = [sink.description for sink in sinks or []
                   if sink.is_default]
        out['sound'] = default[0] if default else 'No output device'
    except Exception:
        out['sound'] = 'Audio unavailable'
    out['battery'] = _percent(system_settings.battery_status()) \
        or 'No battery'
    try:
        import settings_storage
        _total, free = settings_storage.fs_usage(
            settings_storage.home_dir())
        if free is not None:
            out['storage'] = '%s free' % settings_storage.format_bytes(free)
    except Exception:
        pass
    return out


# Home side swipes (Home & launcher › Gestures; drawer pick status text)
_GESTURE_TITLES = {
    'swipe_left_home': 'Swipe Left App',
    'swipe_right_home': 'Swipe Right App',
}

# System-level lisgd slots (design.md "Gestures"): they fire over any
# focused app, so besides actions they accept the IPC verbs that
# gesture_bindings.py materializes at session start.
_SYSTEM_GESTURE_TITLES = {
    'swipe_up_short': 'Swipe up (short)',
    'swipe_up_long': 'Swipe up (long)',
    'swipe_down_top': 'Swipe down (top)',
    'swipe_left_edge': 'Edge swipe',
}

_ACTION_LABELS = {
    'home': 'Home', 'app_switcher': 'App switcher',
    'notification_shade': 'Notification shade', 'back': 'Back',
    'search': 'Search', 'lock_screen': 'Lock screen', 'settings': 'Settings',
    'camera': 'Camera', 'dialer': 'Dialer', 'assistant': 'Assistant',
    'none': 'None',
}

_VERB_LABELS = {
    'gesture.back': 'Back (system)', 'gesture.home': 'Home (system)',
    'gesture.shade': 'Shade (system)', 'gesture.keyboard': 'Keyboard (system)',
    'gesture.switcher': 'Switcher (system)',
}

_UPDATE_NOTIF_ID = 999901
_UPDATE_RESULT_ID = 999902


def _replace_box_children(box, children: list) -> None:
    ui.fill(box, children)


def _section_placeholder(text: str):
    """Muted one-sentence row for an empty or unavailable list."""
    return ui.note_row(text)


def _run_bg(work, on_done) -> None:
    import threading
    from gi.repository import GLib

    def _worker() -> None:
        result = None
        try:
            result = work()
        except Exception:
            pass
        GLib.idle_add(lambda: on_done(result) and False or False)

    threading.Thread(target=_worker, daemon=True).start()


class _Section:
    """One settings section: layout lives here, behavior on SettingsPage.

    Sections live in settings_<feature>.py modules and register in
    SETTINGS_SECTIONS below (and in one SETTINGS_CATEGORIES entry).
    build() returns the widgets to append in order: the caption first
    (deep links scroll to it), then cards and footnotes built with
    settings_ui. refresh() re-queries live state off the main loop.
    """

    key = ''
    title = ''

    def build(self, win) -> list:
        raise NotImplementedError

    def refresh(self, win) -> None:
        return None


class SystemSection(_Section):
    key = 'system'
    title = 'Software update'

    def __init__(self) -> None:
        self._status = None
        self._row = None
        self._reboot = None
        self._log = None
        self._card = None
        self._rows: list = []
        self._log_text = ''

    def build(self, win) -> list:
        self._row = ui.action_row(
            'Software updates', 'Check',
            lambda _r: win._on_check_updates_clicked(None),
            subtitle='Not checked yet.')
        self._status = self._row.subtitle_label
        self._reboot = ui.row('Restart to finish', '')
        self._log = ui.nav_row('Last update log', '',
                               lambda r: self._show_log(win, r))
        self._rows = [
            ui.nav_row('Update now', 'System and Flatpak updates',
                       lambda r: win._on_update_now(r)),
            ui.nav_row('Open in terminal', 'Run the update script',
                       lambda r: win._on_update_clicked(r)),
        ]
        built = ui.group(self.title)
        self._card = built[1]
        self.refresh(win)
        return built

    def refresh(self, win) -> None:
        reason, found = '', None
        try:
            import settings_updates

            reason = settings_updates.reboot_required() or ''
            found = settings_updates.latest_update_log()
        except Exception:
            pass
        rows = [self._row]
        if reason:
            ui.set_subtitle(self._reboot, reason)
            rows.append(self._reboot)
        rows += self._rows
        if found is not None:
            name, tail = found
            self._log_text = tail
            ui.set_subtitle(self._log, name)
            rows.append(self._log)
        if self._card is not None:
            ui.fill(self._card, rows)

    def _show_log(self, win, anchor) -> None:
        win.item_actions._show_action_menu(
            anchor, 'Last update log', [('Close', lambda: None)],
            subtitle=self._log_text or 'Empty.')


class WifiSection(_Section):
    key = 'wifi'
    title = 'Wi-Fi'

    def build(self, win) -> list:
        row = ui.switch_row('Wi-Fi', 'Checking…')
        win._wifi_switch = row.switch
        win._wifi_switch.connect(
            'notify::active',
            lambda s, p: win._on_wifi_switch_flipped(s, p))
        win._wifi_switch_updating = False
        win._wifi_row = row
        top = ui.group(self.title, [row])
        win._wifi_service_box = top[1]
        win._wifi_scan_rows = [
            ui.nav_row('Scan for networks', '',
                       lambda _r: win._refresh_wifi(scan=True)),
            ui.nav_row('Add hidden network…', '',
                       lambda _r: win._on_add_hidden()),
        ]
        networks = ui.group('Networks', list(win._wifi_scan_rows))
        win._wifi_list_box = networks[1]
        saved = ui.group('Saved networks')
        win._wifi_saved_box = saved[1]
        ui.fill(win._wifi_saved_box, [], hide_empty=True)
        return top + networks + saved

    def refresh(self, win) -> None:
        win._refresh_wifi()


class BluetoothSection(_Section):
    key = 'bluetooth'
    title = 'Bluetooth'

    def build(self, win) -> list:
        row = ui.switch_row('Bluetooth', 'Checking…')
        win._bt_switch = row.switch
        win._bt_switch.connect(
            'notify::active',
            lambda s, p: win._on_bt_switch_flipped(s, p))
        win._bt_switch_updating = False
        win._bt_row = row
        win._bt_unblock_btn = ui.nav_row(
            'Unblock Bluetooth', 'Bluetooth is blocked by rfkill',
            lambda _r: win._on_bt_unblock())
        top = ui.group(self.title, [row])
        win._bt_service_box = top[1]
        win._bt_scan_row = ui.nav_row(
            'Scan for devices', '',
            lambda _r: win._refresh_bluetooth(scan=True))
        devices = ui.group('Devices', [win._bt_scan_row])
        win._bt_list_box = devices[1]
        return top + devices

    def refresh(self, win) -> None:
        win._refresh_bluetooth()


class SoundSection(_Section):
    key = 'sound'
    title = 'Sound'

    def build(self, win) -> list:
        output = ui.group('Output', [ui.note_row('Checking…')])
        mic = ui.group('Microphone', [ui.note_row('Checking…')])
        win._sink_list_box = output[1]
        win._source_list_box = mic[1]
        return output + mic


class BatterySection(_Section):
    key = 'battery'
    title = 'Battery'

    def __init__(self) -> None:
        self._health_box = None
        self._row = None

    def build(self, win) -> list:
        self._row = ui.row('Battery', 'Checking…')
        win._battery_label = self._row.subtitle_label
        built = ui.group(self.title, [self._row])
        self._health_box = built[1]
        return built

    def refresh(self, win) -> None:
        import system_settings

        def _work():
            import settings_device
            try:
                import settings_display
                power = settings_display.probe_power_state()
            except Exception:
                power = None
            return (system_settings.battery_status(),
                    settings_device.format_battery_health(
                        settings_device.battery_health_sync()),
                    power)

        _run_bg(_work, lambda res: self._render(win, res))

    def _render(self, win, res) -> None:
        try:
            status, health, power = res
        except (TypeError, ValueError):
            try:
                status, health = res
            except (TypeError, ValueError):
                return
            power = None
        ui.set_subtitle(
            self._row, self._with_profile(win, status, power)
            or 'No battery detected.')
        if self._health_box is None:
            return
        rows = [self._row]
        for title, key in (('Health', 'health'), ('Charge cycles', 'cycles'),
                           ('Temperature', 'temperature'),
                           ('Voltage', 'voltage')):
            rows.append(ui.row(title, str(health.get(key, 'Unknown'))))
        ui.fill(self._health_box, rows)

    @staticmethod
    def _with_profile(win, status: str | None, power) -> str | None:
        """Battery status plus the live power profile, so the row
        reflects the real state on either backend."""
        try:
            import power_profiles
            import settings_standby
            active = (power or {}).get('active')
            saver = settings_standby.saver_is_on(win)
            label = power_profiles.profile_label(power_profiles.current_label(
                active, win.config, saver))
        except Exception:
            return status
        if not status or label in ('Unknown', ''):
            return status
        return '%s · %s' % (status, label)


_MIRROR_LABELS = ((False, 'Add / update'), (True, 'Mirror · deletes extras'))


class BackupSection(_Section):
    key = 'backup'
    title = 'Backup & restore'

    def __init__(self) -> None:
        self._source = ''
        self._dest = ''
        self._mirror = False
        self._source_btn = None
        self._dest_btn = None
        self._mode_row = None
        self._status = None

    def build(self, win) -> list:
        import filebackup

        win._restore_btn = ui.nav_row(
            'Restore settings', 'From an xx-wm backup file',
            lambda r: win._on_restore_backup(r))
        import settings_launcher
        settings_group = ui.group(self.title, [
            ui.nav_row('Export settings', 'Home, theme, gestures',
                       lambda _r: win._on_export_backup()),
            win._restore_btn,
            # Android keeps "Reset home layout" under Backup & Restore.
            ui.nav_row(settings_launcher.RESET_TITLE,
                       settings_launcher.RESET_SUMMARY,
                       lambda r: settings_launcher.confirm_reset_home(win, r))])
        if not self._source:
            sources = filebackup.backup_sources()
            self._source = sources[0] if sources else ''
        self._source_btn = ui.nav_row(
            'Folder', self._source or 'Choose a folder',
            lambda _r: self._pick_source(win))
        self._dest_btn = ui.nav_row(
            'Destination', self._dest or 'Choose removable storage',
            lambda _r: self._pick_dest(win))
        self._mode_row = ui.nav_row(
            'Mode', '', lambda r: self._pick_mode(win, r))
        self._render_mode(win)
        files = ui.group('File backup', [
            self._source_btn, self._dest_btn, self._mode_row,
            ui.nav_row('Preview', 'Count what would change',
                       lambda _r: self._on_preview(win)),
            ui.nav_row('Back up now', '',
                       lambda _r: self._on_transfer(win, 'Backup')),
            ui.nav_row('Restore now', 'Copy back from there',
                       lambda _r: self._on_transfer(win, 'Restore'))],
            note='Photos, Documents, Videos and app data to removable '
                 'storage.')
        self._status = files[2]
        return settings_group + files

    def _render_mode(self, win) -> None:
        ui.set_subtitle(self._mode_row, dict(_MIRROR_LABELS)[self._mirror])

    def _pick_mode(self, win, anchor) -> None:
        win.item_actions._show_action_menu(
            anchor, 'Mode',
            [(label, lambda m=mirror: self._set_mode(win, m))
             for mirror, label in _MIRROR_LABELS])

    def _set_mode(self, win, mirror: bool) -> None:
        self._mirror = bool(mirror)
        self._render_mode(win)

    def _say(self, text: str) -> None:
        ui.say(self._status, text)

    def _pick_source(self, win) -> None:
        import filebackup
        sources = filebackup.backup_sources()
        if not sources:
            self._say('No source folders found.')
            return
        actions = [(path, lambda p=path: self._set_source(win, p))
                   for path in sources]
        actions.append(('Cancel', lambda: None))
        win.item_actions._show_action_menu(
            self._source_btn, 'Back up which folder?', actions)

    def _set_source(self, win, path: str) -> None:
        self._source = path
        ui.set_subtitle(self._source_btn, path)

    def _pick_dest(self, win) -> None:
        import filebackup
        volumes = filebackup.list_volumes()
        if not volumes:
            self._say('No removable storage found.')
            return
        actions = [(path, lambda p=path: self._set_dest(win, p))
                   for path in volumes]
        actions.append(('Cancel', lambda: None))
        win.item_actions._show_action_menu(
            self._dest_btn, 'Back up to where?', actions)

    def _set_dest(self, win, path: str) -> None:
        self._dest = path
        ui.set_subtitle(self._dest_btn, path)


    def _endpoints(self, label: str):
        import filebackup
        if label == 'Restore':
            src, dest = self._dest, self._source
        else:
            src, dest = self._source, self._dest
        ok, error = filebackup.validate_transfer(src, dest, self._mirror)
        if not ok:
            self._say(error or 'Check source and destination.')
            return None
        return src, dest

    def _on_preview(self, win) -> None:
        import filebackup
        endpoints = self._endpoints('Backup')
        if endpoints is None:
            return
        src, dest = endpoints
        self._say('Previewing…')

        def _work():
            return filebackup.dry_run_counts(src, dest, self._mirror)

        _run_bg(_work, self._on_preview_done)

    def _on_preview_done(self, res) -> None:
        try:
            _ok, text = res
        except (TypeError, ValueError):
            return
        self._say(text)

    def _on_transfer(self, win, label: str) -> None:
        import filebackup
        endpoints = self._endpoints(label)
        if endpoints is None:
            return
        src, dest = endpoints
        self._say('Previewing…')

        def _work():
            return filebackup.dry_run_counts(src, dest, self._mirror)

        _run_bg(_work,
                lambda res: self._confirm_transfer(win, label, src, dest,
                                                   res))

    def _confirm_transfer(self, win, label: str, src: str, dest: str,
                          res) -> None:
        try:
            ok, text = res
        except (TypeError, ValueError):
            return
        if not ok:
            self._say(text)
            return
        if self._mirror:
            prompt = ('%s (mirror): %s. Files in the destination '
                      'that are not in the source will be DELETED. '
                      'Run it?' % (label, text))
        else:
            prompt = '%s: %s. Run it?' % (label, text)
        anchor = self._dest_btn
        win.item_actions._show_action_menu(
            anchor, prompt,
            [('%s now' % label,
              lambda: self._start_transfer(win, label, src, dest)),
             ('Cancel', lambda: None)])

    def _start_transfer(self, win, label: str, src: str, dest: str) -> None:
        import filebackup
        from gi.repository import GLib
        self._say('Starting…')

        def _preview():
            return filebackup.dry_run_counts(src, dest, self._mirror)

        def _on_previewed(res) -> None:
            try:
                ok, _text = res
            except (TypeError, ValueError):
                return
            if not ok:
                self._say(_text)
                return
            self._say('Copying… 0%')

            def _work():
                import re as _re
                match = _re.match(r'(\d+) file', _text)
                total = int(match.group(1)) if match else 0

                def _progress(fraction: float) -> None:
                    GLib.idle_add(self._say,
                                  'Copying… %d%%' % int(fraction * 100))

                return filebackup.run_transfer(src, dest, self._mirror,
                                               total, _progress)

            _run_bg(_work,
                     lambda res: self._on_transferred(label, res))

        _run_bg(_preview, _on_previewed)

    def _on_transferred(self, label: str, res) -> None:
        try:
            ok, error = res
        except (TypeError, ValueError):
            return
        if ok:
            self._say('%s done.' % label)
        else:
            self._say(error or 'Transfer failed.')



def apn_summary(config) -> str:
    """Row value for the APN: the access point name, never credentials."""
    try:
        apn = str(config.data.get('apn', '') or '').strip()
    except Exception:
        apn = ''
    return apn or 'Not set'


class MobileSection(_Section):
    key = 'mobile'
    title = 'Mobile data'

    def __init__(self) -> None:
        self._row = None

    def build(self, win) -> list:
        self._row = ui.nav_row('Access point (APN)', apn_summary(win.config),
                               lambda r: self._open_sheet(win, r))
        win._apn_row = self._row
        return ui.group(self.title, [self._row])

    def _open_sheet(self, win, anchor):
        data = win.config.data
        return ui.form_sheet(
            win, anchor, 'Access point',
            [('apn', 'APN', data.get('apn', ''), 'text'),
             ('user', 'Username (blank if none)', data.get('apn_user', ''),
              'text'),
             ('password', 'Password (blank if none)',
              data.get('apn_pass', ''), 'password')],
            'Save', lambda values: win._on_apn_save(values))

    def refresh(self, win) -> None:
        ui.set_subtitle(self._row, apn_summary(win.config))


class AppearanceSection(_Section):
    key = 'appearance'
    title = 'Theme'

    def build(self, win) -> list:
        # The swatch picker lives on the window (it owns the live preview
        # provider); mode, font and text size come from the launcher
        # section's appearance rows.
        import settings_launcher

        theme = ui.group(self.title,
                         [ui.wide_row(win._build_appearance_card())])
        return theme + settings_launcher.build_appearance_group(win)


# Interfaces the About row skips (the tap shows them all).
_VIRTUAL_IFACES = ('lo', 'veth', 'br-', 'docker', 'virbr', 'tailscale',
                   'tun', 'wg')


class AboutSection(_Section):
    key = 'about'
    title = 'About phone'

    def __init__(self) -> None:
        self._detail_box = None
        self._head_rows: list = []
        self._modem_props = None
        self._imei_revealed = False
        self._last_res = None

    def build(self, win) -> list:
        from importlib.metadata import version as get_version

        try:
            shell_version = get_version('xx-wm')
        except Exception:
            shell_version = '0.1.1'
        self._head_rows = [ui.row('Version', 'XX-WM %s' % shell_version),
                           ui.row('Device', win._get_device_name())]
        about = ui.group(self.title, list(self._head_rows))
        self._detail_box = about[1]
        # Storage has its own menu entry (Storage & backup); About keeps
        # no second way in.
        logs = ui.group('Logs', [ui.nav_row(
            'Logs', 'Shell and system logs',
            lambda _r: settings_launcher.open_logs(win))])
        return about + logs

    def refresh(self, win) -> None:
        def _work():
            import settings_device
            uptime = settings_device.read_uptime()
            return {
                'os': settings_device.os_summary(),
                'kernel': __import__('platform').release(),
                'uptime': settings_device.format_uptime(uptime)
                if uptime is not None else '?',
                'cpu': settings_device.cpu_summary(),
                'mem': settings_device.mem_summary(),
                'thermal': settings_device.group_thermal(
                    settings_device.read_thermal_zones()),
                'net': settings_device.net_summary(),
                'modem': settings_device.modem_summary(),
            }

        _run_bg(_work, lambda res: self._render(win, res))

    def _render(self, win, res) -> None:
        if not isinstance(res, dict) or self._detail_box is None:
            return
        self._modem_props = res.get('modem')
        ips, macs = res.get('net') or ({}, {})
        net_lines = []
        for iface in sorted(set(ips) | set(macs)):
            detail = ', '.join(ips.get(iface, []))
            if macs.get(iface):
                detail = (detail + ' · ' + macs[iface]
                          if detail else macs[iface])
            net_lines.append('%s: %s' % (iface, detail or '—'))
        thermal = res.get('thermal') or {}
        rows = list(self._head_rows)
        for title, key in (('OS', 'os'), ('Kernel', 'kernel'),
                           ('Uptime', 'uptime'), ('CPU', 'cpu'),
                           ('Memory', 'mem')):
            rows.append(ui.row(title, str(res.get(key, '?'))))
        if thermal:
            rows.append(ui.row(
                'Temperatures',
                ', '.join('%s %s' % (kind, temp)
                          for kind, temp in sorted(thermal.items()))))
        rows.append(self._modem_row(win))
        rows.append(self._imei_row(win))
        self._net_text = '\n'.join(net_lines)
        shown = ['%s  %s' % (iface, ips[iface][0])
                 for iface in sorted(ips)
                 if ips.get(iface) and not iface.startswith(_VIRTUAL_IFACES)
                 ][:3]
        # Android's About phone label; the network settings themselves
        # live on Network & internet.
        rows.append(ui.nav_row(
            'IP address', '\n'.join(shown) if shown else 'Unavailable',
            lambda r: win.item_actions._show_action_menu(
                r, 'IP address', [('Close', lambda: None)],
                subtitle=self._net_text or 'Unavailable')))
        ui.fill(self._detail_box, rows)
        self._last_res = res

    def _modem_row(self, win):
        props = self._modem_props or {}
        parts = [str(props[key]) for key in (
            'Manufacturer', 'Model', 'Revision') if props.get(key)]
        return ui.row('Modem', ' · '.join(parts) if parts else (
            'No modem detected.' if not props else 'Modem detected.'))

    def _imei_row(self, win):
        props = self._modem_props or {}
        imei = props.get('EquipmentIdentifier')
        if not imei:
            return ui.row('IMEI', 'Unavailable')
        if self._imei_revealed:
            return ui.row('IMEI', str(imei))
        return ui.nav_row('IMEI', 'Tap to show',
                          lambda _r: self._reveal_imei(win))

    def _reveal_imei(self, win) -> None:
        self._imei_revealed = True
        if getattr(self, '_last_res', None) is not None:
            self._render(win, self._last_res)


class ShellSection(_Section):
    key = 'shell'
    title = 'Shell'

    def build(self, _win) -> list:
        # Shell preferences live in ~/.config/xx-wm/ (design.md "Settings
        # scope"); the System page ends by saying so.
        return [ui.footnote('More in ~/.config/xx-wm/, reloaded live.')]


class RecordingSection(_Section):
    """Screen recording status (read-only): the Record tile owns capture."""

    key = 'recording'
    title = 'Screen recording'

    def __init__(self) -> None:
        self._row = None

    def build(self, win) -> list:
        self._row = ui.row(self.title, 'Not recording')
        return ui.group(self.title, [self._row],
                        note='Record tile · ~/Videos/Screenrecordings')

    def refresh(self, win) -> None:
        def _state():
            try:
                import capture
                recorder = capture.get_recorder()
                if recorder.is_recording():
                    return 'Recording…'
            except Exception:
                pass
            return 'Not recording'

        _run_bg(_state, self._render)

    def _render(self, status) -> None:
        ui.set_subtitle(self._row, status or 'Not recording')


class CaptionsSection(_Section):
    """Live captions status (read-only): the Captions tile switches it."""

    key = 'captions'
    title = 'Live captions'

    def build(self, win) -> list:
        try:
            import captions
            installed = captions.captions_installed()
        except Exception:
            installed = False
        if installed:
            text = 'Available · switch on from the Captions tile'
        else:
            text = 'Not installed on this device'
        return ui.group(self.title, [ui.row(self.title, text)])


SHARE_APP_ID = 'io.piercingxx.XXShare.desktop'


class NearbySection(_Section):
    key = 'nearby'
    title = 'Nearby share'

    def build(self, win) -> list:
        from gi.repository import Gio
        try:
            installed = Gio.DesktopAppInfo.new(SHARE_APP_ID) is not None
        except Exception:
            installed = False
        if not installed:
            return ui.group(self.title, [ui.row(
                self.title, 'Not installed on this device')])
        return ui.group(self.title, [ui.nav_row(
            self.title, 'Send files to nearby phones and computers '
            '(LocalSend)', lambda _r: win._launch_app_id(SHARE_APP_ID))])


class FindMySection(_Section):
    """Find My Device stays OFF with no enabling control."""

    key = 'findmy'
    title = 'Find My Device'

    def build(self, win) -> list:
        return ui.group(self.title, [ui.row(
            self.title, 'Off')])


class DeveloperSection(_Section):
    key = 'developer'
    title = 'Developer'

    def build(self, win) -> list:
        return [ui.caption(self.title), win._build_developer_card()]

    def refresh(self, win) -> None:
        win._refresh_xxdb_section()


class GesturesSection(_Section):
    key = 'gestures'
    title = 'Navigation gestures'

    def build(self, win) -> list:
        # The gestures block stays the last section by contract. These
        # are the phone's own navigation swipes (Android keeps them in
        # system settings, not in the launcher).
        return [ui.caption(self.title), win._build_gestures_card()]

    def refresh(self, win) -> None:
        win._refresh_gesture_labels()


# Registry order (tests pin it; the gestures section stays last). Page
# placement comes from SETTINGS_CATEGORIES.
#
# The feature sections below live in their own settings_<feature>.py
# modules (plain module imports, not from-imports: those modules read
# settings_page attributes at call time, so either import order works
# and the no-circular-imports test stays green).
import settings_apps
import settings_battery_use
import settings_charging
import settings_display
import settings_firewall
import settings_keyboard
import settings_launcher
import settings_lock
import settings_medical
import settings_nope
import settings_permissions
import settings_screentime
import settings_security
import settings_services
import settings_smart_lock
import settings_standby
import settings_storage

SETTINGS_SECTIONS: list[tuple[str, type[_Section]]] = [
    ('system', SystemSection),
    ('wifi', WifiSection),
    ('bluetooth', BluetoothSection),
    ('sound', SoundSection),
    ('battery', BatterySection),
    ('charging', settings_charging.ChargingSection),
    ('batteryuse', settings_battery_use.BatteryUseSection),
    ('screentime', settings_screentime.ScreenTimeSection),
    ('backup', BackupSection),
    ('storage', settings_storage.StorageSection),
    ('security', settings_security.SecuritySection),
    ('lock', settings_lock.LockSection),
    ('mobile', MobileSection),
    ('services', settings_services.ServicesSection),
    ('firewall', settings_firewall.FirewallSection),
    ('apps', settings_apps.AppsSection),
    ('nope', settings_nope.NopeSection),
    ('appearance', AppearanceSection),
    ('display', settings_display.DisplaySection),
    ('keyboard', settings_keyboard.KeyboardSection),
    ('launcher', settings_launcher.LauncherSection),
    ('standby', settings_standby.StandbySection),
    ('permissions', settings_permissions.PermissionsSection),
    ('smart_lock', settings_smart_lock.SmartLockSection),
    ('medical', settings_medical.MedicalSection),
    ('recording', RecordingSection),
    ('captions', CaptionsSection),
    ('nearby', NearbySection),
    ('findmy', FindMySection),
    ('about', AboutSection),
    ('shell', ShellSection),
    ('developer', DeveloperSection),
    ('gestures', GesturesSection),
]


class SettingsPage:
    """Settings page behavior for ShellWindow.

    Holds only a reference to the window; all widget refs live on the
    window (win._wifi_list_box, win.apn_entry, ...) so bare-instance
    headless tests keep working. Method names mirror the ShellWindow
    delegators in window.py. Every behavior call routes through win so
    test stubs on the instance keep working.
    """

    def __init__(self, win) -> None:
        self._win = win
        self._sections: dict[str, _Section] = {
            key: cls() for key, cls in SETTINGS_SECTIONS
        }

    def section(self, key: str) -> _Section | None:
        return self._sections.get(key)

    def _build_settings_page(self):
        """The settings surface: a stack of the category menu plus one
        subpage per category. Every section builds once, up front, so
        live refreshes and deep links find their widgets."""
        from gi.repository import Gtk

        win = self._win
        built: dict[str, list] = {}
        for key, _cls in SETTINGS_SECTIONS:
            built[key] = self._sections[key].build(win) or []

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('xx-settings')
        root.set_hexpand(True)
        root.set_vexpand(True)
        stack = Gtk.Stack()
        stack.set_vexpand(True)
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_transition_duration(160)
        self._scrolls: dict = {}
        stack.add_named(self._page('menu', 'Settings', self._build_menu()),
                        'menu')
        for key, title, sections in SETTINGS_CATEGORIES:
            body = [widget for name in sections
                    for widget in built.get(name, [])]
            stack.add_named(self._page(key, title, body), 'cat-' + key)
        root.append(stack)

        # Refresh the quick sections whenever the settings page is opened
        win.stack.connect(
            'notify::visible-child-name',
            lambda stack, _p: win._refresh_system_sections()
            if stack.get_visible_child_name() == 'settings' else None,
        )

        win._settings_stack = stack
        win._settings_scroll = self._scrolls['menu']
        win._settings_section_titles = {
            key: widgets[0] for key, widgets in built.items() if widgets}
        self._refresh_menu()
        return root

    def _page(self, key: str, title: str, body_widgets: list):
        """One settings screen: a fixed back bar (its small title fades in
        once the big one scrolls away) over a scrolling column."""
        from gi.repository import Gtk
        from xx_ui import widgets

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.add_css_class('xx-set-bar')
        back = widgets.icon_button('go-previous-symbolic', self.back)
        back.set_tooltip_text('Back')
        bar.append(back)
        small = widgets.label(title, 'xx-set-bar-title')
        small.set_hexpand(True)
        small.set_valign(Gtk.Align.CENTER)
        small.set_opacity(0.0)
        bar.append(small)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class('xx-set-body')
        header = widgets.wrap_label(title, 'xx-set-header')
        body.append(header)
        for widget in body_widgets:
            body.append(widget)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(body)
        # Touch has no focus ring to follow: a focused switch far down a
        # page must not yank the view when the page is shown again.
        scroll.get_child().set_scroll_to_focus(False)
        scroll.get_vadjustment().connect(
            'value-changed',
            lambda adj: small.set_opacity(1.0 if adj.get_value() > 48
                                          else 0.0))
        self._scrolls[key] = scroll

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.append(bar)
        page.append(scroll)
        return page

    def _build_menu(self) -> list:
        titles = {key: title for key, title, _s in SETTINGS_CATEGORIES}
        self._menu_rows: dict = {}
        cards = []
        for group in _MENU_GROUPS:
            rows = []
            for key in group:
                row = ui.nav_row(titles[key], '',
                                 lambda _r, k=key: self.show_category(k))
                self._menu_rows[key] = row
                rows.append(row)
            card = ui.card(rows)
            card.add_css_class('xx-set-menu-card')
            cards.append(card)
        return cards

    def show_category(self, key: str | None,
                      scroll_top: bool = True) -> None:
        """Show one category subpage (None: the menu) and refresh it."""
        win = self._win
        stack = getattr(win, '_settings_stack', None)
        if stack is not None:
            stack.set_visible_child_name('menu' if key is None
                                         else 'cat-' + key)
        scroll = getattr(self, '_scrolls', {}).get(key or 'menu')
        if scroll is not None:
            win._settings_scroll = scroll
            if scroll_top:
                scroll.get_vadjustment().set_value(0.0)
        if key is None:
            self._refresh_menu()
            return
        for _cat, _title, sections in SETTINGS_CATEGORIES:
            if _cat != key:
                continue
            for name in sections:
                try:
                    self._sections[name].refresh(win)
                except Exception:
                    pass

    def back(self) -> None:
        """Back from a subpage returns to the menu; back from the menu
        leaves Settings for wherever it was opened from."""
        win = self._win
        stack = getattr(win, '_settings_stack', None)
        if stack is not None and stack.get_visible_child_name() != 'menu':
            self.show_category(None)
            return
        win._leave_settings()

    def _refresh_menu(self) -> None:
        rows = getattr(self, '_menu_rows', None)
        if not rows:
            return
        win = self._win
        quick = quick_summaries(win)
        status = getattr(self._sections.get('system'), '_status', None)
        try:
            update = status.get_text().rstrip('.') if status else ''
        except Exception:
            update = ''
        if update:
            quick['system'] = '%s · %s' % (quick['system'], update)
        for key, text in quick.items():
            ui.set_subtitle(rows.get(key), text)
        _run_bg(slow_summaries, self._show_summaries)

    def _show_summaries(self, res) -> None:
        rows = getattr(self, '_menu_rows', None) or {}
        if not isinstance(res, dict):
            return
        win = self._win
        saver = ''
        try:
            import settings_standby
            saver = 'saver on' if settings_standby.saver_is_on(win) \
                else 'saver off'
        except Exception:
            pass
        if res.get('battery'):
            res['battery'] = '%s · %s' % (res['battery'], saver) \
                if saver else res['battery']
        for key, text in res.items():
            if text:
                ui.set_subtitle(rows.get(key), text)

    def open_settings(self, section: str | None = None) -> None:
        """Show Settings: the subpage holding ``section`` scrolled to it,
        a whole category for a category key, the menu otherwise.

        Every _SETTINGS_SECTION_KEYS entry resolves (a test walks them).
        The shown subpage refreshes: a WiFi scan on Network, a device
        list on Connected devices, live state for the rest."""
        from gi.repository import GLib

        win = self._win
        category = category_for(section)
        key = section if section in self._sections else None
        win.stack.set_visible_child_name('settings')
        self.show_category(category)
        win.present()
        win._scroll_settings_to(key)
        if key is not None:
            # Showing the page re-lays it out over the next frames, so an
            # early offset reads stale allocations (upper 0 clamps to the
            # top). Retry on a ladder past layout; each pass is idempotent,
            # so late passes are no-ops once the offset sticks.
            GLib.idle_add(win._scroll_settings_to, key)
            for retry_ms in (150, 300, 600, 1000, 1500):
                GLib.timeout_add(retry_ms, win._scroll_settings_to, key)

    def _scroll_settings_to(self, section: str | None) -> bool:
        win = self._win
        titles = getattr(win, '_settings_section_titles', None) or {}
        target = titles.get(section)
        scroll = getattr(win, '_settings_scroll', None)
        if target is None or scroll is None:
            return False
        offset = target.get_allocation().y
        scroll.get_vadjustment().set_value(max(0.0, float(offset)))
        return False

    def _gesture_binding_text(self, key: str) -> str:
        win = self._win
        action = win.gesture_config.get(key) or 'none'
        if action == 'none':
            return 'Not set'
        if action in _VERB_LABELS:
            return _VERB_LABELS[action]
        if action == 'camera':
            return 'Camera'
        if action.startswith('launch:'):
            app_id = action[len('launch:'):]
            for entry in win.app_index.entries:
                if entry.app_id == app_id:
                    return win.config.label_for(app_id, entry.name)
            return app_id
        return action.replace('_', ' ').title()

    def _refresh_gesture_labels(self) -> None:
        win = self._win
        for key, label in win._gesture_binding_labels.items():
            label.set_text(self._long_press_text() if key == 'long_press_home'
                           else win._gesture_binding_text(key))
        for key, label in win._system_gesture_labels.items():
            label.set_text(win._gesture_binding_text(key))

    def _build_gestures_card(self):
        """Long-press on home, then the four system (lisgd) swipes: one
        value row each, the binding as its subtitle; a tap opens the
        choices. The home side swipes are on Home & launcher."""
        win = self._win
        rows = []
        row = ui.nav_row(
            'Long-press home', self._long_press_text(),
            lambda r: self._pick_long_press(r))
        win._gesture_binding_labels['long_press_home'] = row.subtitle_label
        rows.append(row)
        for key, title in _SYSTEM_GESTURE_TITLES.items():
            row = ui.nav_row(
                title, win._gesture_binding_text(key),
                lambda r, k=key, t=title: win._pick_system_gesture(r, k, t))
            win._system_gesture_labels[key] = row.subtitle_label
            rows.append(row)
        return ui.card(rows)

    _LONG_PRESS_CHOICES = (('edit', 'Edit home'), ('settings', 'Settings'),
                           ('none', 'Nothing'))

    def _long_press_text(self) -> str:
        win = self._win
        try:
            action = win.gesture_config.get('long_press_home') or 'edit'
        except Exception:
            action = 'edit'
        return dict(self._LONG_PRESS_CHOICES).get(action, 'Edit home')

    def _pick_long_press(self, anchor) -> None:
        """Long-press on the home background (Linux: Android always
        opens its settings; here edit mode is the default because home
        slots are edited there)."""
        win = self._win
        try:
            current = win.gesture_config.get('long_press_home') or 'edit'
        except Exception:
            current = 'edit'
        values = [value for value, _label in self._LONG_PRESS_CHOICES]

        def _set(value: str) -> None:
            try:
                win.gesture_config.set('long_press_home', value)
            except (ValueError, AttributeError):
                return
            win._refresh_gesture_labels()

        win.item_actions._show_action_menu(
            anchor, 'Long-press home',
            [(label, lambda v=value: _set(v))
             for value, label in self._LONG_PRESS_CHOICES],
            checked=values.index(current) if current in values else None)

    def _pick_system_gesture(self, anchor, key: str, title: str) -> None:
        win = self._win
        choices = [(_ACTION_LABELS[a], lambda a=a: win._set_system_gesture(key, a))
                   for a in sorted(VALID_ACTIONS)]
        choices += [(_VERB_LABELS[v], lambda v=v: win._set_system_gesture(key, v))
                    for v in sorted(IPC_VERBS)]
        choices.append(('Default', lambda: win._reset_system_gesture(key)))
        win.item_actions._show_action_menu(anchor, title, choices)

    def _set_system_gesture(self, key: str, value: str) -> None:
        win = self._win
        win.gesture_config.set(key, value)
        win._refresh_gesture_labels()

    def _reset_system_gesture(self, key: str) -> None:
        win = self._win
        win.gesture_config.reset(key)
        win._refresh_gesture_labels()

    def _pick_gesture_app(self, key: str, title: str) -> None:
        win = self._win
        win._pick_gesture_key = key
        win.stack.set_visible_child_name('apps')
        win._show_status(f'Tap an app to bind to {title}.')

    def _clear_gesture_app(self, key: str) -> None:
        win = self._win
        win.gesture_config.set(key, 'none')
        win._refresh_gesture_labels()

    # -- Developer section: wireless debugging (xxdb) ----------------------

    def _build_developer_card(self):
        win = self._win
        # The flip handler reads the switch itself, so it stays on the
        # raw notify signal instead of the bool-only builder callback.
        row = ui.switch_row('Wireless debugging', 'Off')
        row.switch.connect('notify::active',
                           lambda s, p: win._on_xxdb_switch_flipped(s, p))
        win._xxdb_switch = row.switch
        win._xxdb_status = row.subtitle_label
        win._xxdb_rows = [row, ui.nav_row(
            'Pair with code', 'A one-time code for xxdb',
            lambda _r: win._on_xxdb_pair_clicked())]
        win._xxdb_card = ui.card(win._xxdb_rows)
        win._refresh_xxdb_section()
        return win._xxdb_card

    def _refresh_xxdb_section(self) -> None:
        win = self._win
        if getattr(win, '_xxdb_switch', None) is None:
            return
        ctrl = getattr(win, 'xxdb_controller', None)
        win._xxdb_switch_updating = True
        try:
            win._xxdb_switch.set_active(
                bool(ctrl is not None and (ctrl.enabled or ctrl.starting)))
        finally:
            win._xxdb_switch_updating = False
        if ctrl is None:
            win._xxdb_status.set_text('Off')
        else:
            win._xxdb_status.set_text(ctrl.status_text())
        rows = list(getattr(win, '_xxdb_rows', []))
        if ctrl is not None:
            for entry in ctrl.trusted_hosts():
                rows.append(ui.action_row(
                    entry['name'], 'Forget',
                    lambda _r, fp=entry['fingerprint']:
                    win._on_xxdb_forget(fp),
                    subtitle='Paired · %s…' % entry['fingerprint'][:16]))
            for token, label in ctrl.pending_system_requests():
                rows.append(ui.nav_row(
                    'File access request', label,
                    lambda r, t=token, lb=label: self._ask_xxdb(r, t, lb)))
        card = getattr(win, '_xxdb_card', None)
        if card is not None:
            ui.fill(card, rows)

    def _ask_xxdb(self, anchor, token: str, label: str) -> None:
        win = self._win
        win.item_actions._show_action_menu(
            anchor, 'Allow file access?',
            [('Allow', lambda: win._on_xxdb_confirm(token, True)),
             ('Deny', lambda: win._on_xxdb_confirm(token, False))],
            subtitle=label)

    def _on_xxdb_switch_flipped(self, switch, _pspec) -> None:
        win = self._win
        if win._xxdb_switch_updating:
            return
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is None:
            return
        if bool(switch.get_active()):
            ctrl.request_enable()
        else:
            ctrl.request_disable()
        win._refresh_xxdb_section()

    def _on_xxdb_pair_clicked(self) -> None:
        win = self._win
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is None or not ctrl.enabled:
            win._xxdb_status.set_text('Turn on wireless debugging first.')
            return
        result = ctrl.issue_pairing_code()
        if result is None:
            win._xxdb_status.set_text('Turn on wireless debugging first.')
            return
        code, shown, _expires = result
        win._xxdb_status.set_text(
            f'{shown} — code {code} (valid 2 minutes, single use)')
        win._refresh_xxdb_section()
        win._xxdb_status.set_text(
            f'{shown} — code {code} (valid 2 minutes, single use)')

    def _on_xxdb_forget(self, fingerprint: str) -> None:
        win = self._win
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is not None:
            ctrl.forget_host(fingerprint)
        win._refresh_xxdb_section()

    def _on_xxdb_confirm(self, token: str, allow: bool) -> None:
        win = self._win
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is not None:
            ctrl.confirm_system_request(token, allow)
        win._refresh_xxdb_section()

    # -- system settings sections (design.md "Settings scope") -------------

    @staticmethod
    def _run_bg(work, on_done) -> None:
        return _run_bg(work, on_done)

    @staticmethod
    def _replace_box_children(box, children: list) -> None:
        return _replace_box_children(box, children)

    def _section_placeholder(self, text: str):
        return _section_placeholder(text)

    def _refresh_system_sections(self) -> None:
        win = self._win
        win._refresh_xxdb_section()
        import system_settings
        win._run_bg(system_settings.list_audio_sinks, win._show_sinks)
        win._run_bg(system_settings.list_audio_sources, win._show_sources)
        win._run_bg(system_settings.list_bt_devices, win._show_bt_devices)
        for key in ('system', 'apps', 'battery', 'about', 'display',
                    'launcher', 'standby', 'permissions', 'smart_lock',
                    'recording', 'services', 'firewall'):
            section = self._sections.get(key)
            if section is not None:
                try:
                    section.refresh(win)
                except Exception:
                    pass

    def _show_battery(self, status: str | None) -> None:
        self._win._battery_label.set_text(status or 'No battery detected.')

    def _show_sinks(self, sinks) -> None:
        self._show_audio(getattr(self._win, '_sink_list_box', None), sinks,
                         'output', lambda sink: self._win._on_sink_selected(
                             sink))

    def _on_sink_selected(self, sink) -> None:
        import system_settings

        win = self._win
        win._run_bg(
            lambda: system_settings.set_default_sink(sink.name),
            lambda ok: (win._show_status(
                f'Output set to {sink.description}.' if ok else 'Could not switch output.'),
                win._run_bg(system_settings.list_audio_sinks, win._show_sinks)),
        )

    def _show_sources(self, sources) -> None:
        self._show_audio(getattr(self._win, '_source_list_box', None),
                         sources, 'input',
                         lambda source: self._win._on_source_selected(source))

    @staticmethod
    def _show_audio(box, devices, kind: str, on_pick) -> None:
        """Output/microphone card: one row per device, the default one
        marked in its subtitle; a tap makes that device the default. No
        devices (or no audio system): the card and its caption hide."""
        if box is None:
            return
        rows = [ui.row(device.description,
                       'Default %s' % kind if device.is_default else '',
                       on_activate=lambda _r, d=device: on_pick(d))
                for device in devices or []]
        ui.fill(box, rows, hide_empty=True)

    def _on_source_selected(self, source) -> None:
        import system_settings

        win = self._win
        win._run_bg(
            lambda: system_settings.set_default_source(source.name),
            lambda ok: (win._show_status(
                f'Input set to {source.description}.'
                if ok else 'Could not switch input.'),
                win._run_bg(system_settings.list_audio_sources,
                             win._show_sources)),
        )

    def _refresh_wifi(self, scan: bool = False) -> None:
        win = self._win
        self._fill_wifi_list([ui.note_row('Scanning…' if scan else 'Loading…')])
        win._run_bg(lambda: self._wifi_snapshot(scan), self._show_wifi_full)

    def _fill_wifi_list(self, rows: list) -> None:
        """Networks card: the scan results, then Scan and Add hidden."""
        win = self._win
        box = getattr(win, '_wifi_list_box', None)
        if box is not None:
            ui.fill(box, rows + list(getattr(win, '_wifi_scan_rows', [])))

    @staticmethod
    def _wifi_snapshot(scan: bool = False):
        """Worker thread: radio, daemon and network state. Never raises."""
        import system_settings

        try:
            enabled = system_settings.wifi_enabled()
        except Exception:
            enabled = None
        try:
            running = system_settings.nm_running()
        except Exception:
            running = False
        try:
            networks = system_settings.list_wifi(rescan=scan)
        except Exception:
            networks = None
        try:
            saved = system_settings.saved_wifi_ssids()
        except Exception:
            saved = None
        return {'enabled': enabled, 'nm_running': running,
                'networks': networks, 'saved': saved}

    def _show_wifi_full(self, res) -> None:
        win = self._win
        if not isinstance(res, dict):
            res = {}
        switch = getattr(win, '_wifi_switch', None)
        if switch is not None:
            win._wifi_switch_updating = True
            try:
                switch.set_active(bool(res.get('enabled')))
            except Exception:
                pass
            finally:
                win._wifi_switch_updating = False
        self._show_wifi(res.get('networks'))
        self._show_wifi_saved(res.get('saved'))
        self._show_wifi_service(res)

    def _show_wifi_service(self, res) -> None:
        """Wi-Fi card: the radio row (state in its subtitle) plus a
        Start-service row while NetworkManager is down but installed."""
        win = self._win
        box = getattr(win, '_wifi_service_box', None)
        row = getattr(win, '_wifi_row', None)
        if box is None or row is None:
            return
        networks = res.get('networks')
        rows = [row]
        if res.get('nm_running') or networks is not None:
            used = [net.ssid for net in networks or [] if net.in_use]
            if res.get('enabled') is False:
                state = 'Off'
            elif used:
                state = 'Connected to %s' % used[0]
            else:
                state = 'Not connected'
        else:
            import shutil
            if shutil.which('nmcli') is None:
                state = 'Unavailable on this device'
            else:
                state = 'NetworkManager is not running'
                rows.append(ui.nav_row(
                    'Start network service', 'Starts NetworkManager',
                    lambda _r: win._on_start_network_service()))
        ui.set_subtitle(row, state)
        ui.fill(box, rows)

    def _show_wifi_saved(self, saved) -> None:
        win = self._win
        box = getattr(win, '_wifi_saved_box', None)
        if box is None:
            return
        rows = [ui.nav_row(ssid, 'Saved',
                           lambda r, s=ssid: win._on_saved_wifi_selected(s, r))
                for ssid in saved or []]
        ui.fill(box, rows, hide_empty=True)

    def _on_wifi_switch_flipped(self, switch, _pspec) -> None:
        import system_settings

        win = self._win
        if getattr(win, '_wifi_switch_updating', False):
            return
        enabled = bool(switch.get_active())

        def _work():
            return system_settings.set_wifi_enabled(enabled)

        def _done(ok) -> None:
            if not ok:
                win._show_status('Could not switch WiFi.')
            win._refresh_wifi()

        win._run_bg(_work, _done)

    def _on_start_network_service(self) -> None:
        win = self._win
        win._show_status('Starting NetworkManager…')
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not start service: {err}')
            return

        def _done(ok: bool, detail) -> None:
            win._show_status('NetworkManager started.'
                             if ok else f'Could not start service: {detail}')
            win._refresh_wifi()

        try:
            helper_client.manage_service('network', 'start', _done)
        except Exception as err:
            win._show_status(f'Could not start service: {err}')

    def _on_add_hidden(self) -> None:
        win = self._win
        anchor = getattr(win, '_wifi_list_box', None)
        win.item_actions._show_entry_dialog(
            anchor, 'Network name', '', 'Next',
            lambda ssid: self._on_hidden_password(anchor, ssid))

    def _on_hidden_password(self, anchor, ssid: str) -> None:
        win = self._win
        win.item_actions._show_entry_dialog(
            anchor, f'Password for {ssid}', '', 'Connect',
            lambda password: win._connect_wifi(ssid, password or None,
                                               None, hidden=True),
            password=True)

    def _on_saved_wifi_selected(self, ssid: str, widget) -> None:
        win = self._win
        win.item_actions._show_action_menu(
            widget, ssid,
            [('Connect', lambda: win._connect_wifi(ssid, None)),
             ('Forget', lambda: self._forget_wifi(ssid)),
             ('Cancel', lambda: None)])

    def _forget_wifi(self, ssid: str) -> None:
        """Forget runs straight from the network's menu, as on Android:
        picking Forget there is the decision, so no second popup."""
        import system_settings

        win = self._win
        win._run_bg(
            lambda: system_settings.forget_wifi(ssid),
            lambda result: (
                win._show_status(result[1] if result else 'Forget failed.'),
                win._refresh_wifi()))

    def _show_wifi(self, networks) -> None:
        # Unavailable or nothing found: just the Scan rows (the Wi-Fi
        # row's subtitle already says why), no empty-state line.
        win = self._win
        rows = []
        for net in (networks or [])[:12]:
            if net.in_use:
                detail = 'Connected'
            elif net.security == 'open':
                detail = 'Open'
            else:
                detail = 'Secured'
            if net.signal is not None:
                detail = '%s · %d%%' % (detail, net.signal)
            rows.append(ui.row(
                net.ssid or '(hidden network)', detail,
                on_activate=lambda r, n=net: win._on_wifi_selected(n, r)))
        self._fill_wifi_list(rows)

    def _on_wifi_selected(self, net, widget) -> None:
        win = self._win
        if net.in_use:
            win.item_actions._show_action_menu(
                widget, net.ssid,
                [('Disconnect', lambda: self._on_wifi_disconnect(net)),
                 ('Forget', lambda: self._forget_wifi(net.ssid)),
                 ('Details', lambda: self._on_wifi_details(net, widget))])
            return
        if net.security == 'open':
            win._connect_wifi(net.ssid, None, net.security)
            return
        win.item_actions._show_entry_dialog(
            widget, f'Password for {net.ssid}', '', 'Connect',
            lambda password: win._connect_wifi(net.ssid, password, net.security),
            password=True)

    def _on_wifi_disconnect(self, net) -> None:
        import system_settings

        win = self._win
        win._show_status(f'Disconnecting from {net.ssid}…')
        win._run_bg(
            lambda: system_settings.disconnect_wifi(net.ssid),
            lambda result: (win._show_status(
                result[1] if result else 'Disconnect failed.'),
                win._refresh_wifi()),
        )

    def _on_wifi_details(self, net, widget) -> None:
        import system_settings

        win = self._win
        win._run_bg(
            lambda: system_settings.wifi_details(net.ssid),
            lambda details: win.item_actions._show_action_menu(
                widget,
                system_settings.wifi_details_text(net.ssid, details),
                [('Close', lambda: None)]),
        )

    def _connect_wifi(self, ssid: str, password: str | None,
                      security: str | None = None,
                      hidden: bool = False) -> None:
        import system_settings

        win = self._win
        win._show_status(f'Connecting to {ssid}…')
        win._run_bg(
            lambda: system_settings.connect_wifi(ssid, password,
                                                 security=security,
                                                 hidden=hidden),
            lambda result: (win._show_status(result[1] if result else 'Connection failed.'),
                            win._refresh_wifi()),
        )

    def _refresh_bluetooth(self, scan: bool = False) -> None:
        import system_settings
        from gi.repository import GLib

        win = self._win
        if scan:
            self._fill_bt_list([ui.note_row('Scanning…')])
            system_settings.start_bt_discovery()
            GLib.timeout_add_seconds(
                6, lambda: win._run_bg(system_settings.list_bt_devices,
                                       self._show_bt_full) or False)
            return
        win._run_bg(self._bt_snapshot, self._show_bt_full)

    def _fill_bt_list(self, rows: list) -> None:
        """Devices card: known and found devices, then Scan."""
        win = self._win
        box = getattr(win, '_bt_list_box', None)
        if box is None:
            return
        scan = getattr(win, '_bt_scan_row', None)
        ui.fill(box, rows + ([scan] if scan is not None else []))

    @staticmethod
    def _bt_snapshot():
        """Worker thread: devices, power and rfkill state. Never raises."""
        import system_settings

        try:
            devices = system_settings.list_bt_devices()
        except Exception:
            devices = None
        try:
            powered = system_settings.bt_powered()
        except Exception:
            powered = None
        try:
            blocked = system_settings.bt_soft_blocked()
        except Exception:
            blocked = False
        return {'devices': devices, 'powered': powered, 'blocked': blocked}

    def _show_bt_full(self, res) -> None:
        win = self._win
        if isinstance(res, dict):
            devices = res.get('devices')
        elif isinstance(res, list) or res is None:
            devices = res
        else:
            devices = None
        self._show_bt_devices(devices)
        switch = getattr(win, '_bt_switch', None)
        if switch is not None and isinstance(res, dict):
            win._bt_switch_updating = True
            try:
                switch.set_active(bool(res.get('powered')))
            except Exception:
                pass
            finally:
                win._bt_switch_updating = False
        blocked = bool(isinstance(res, dict) and res.get('blocked'))
        unblock = getattr(win, '_bt_unblock_btn', None)
        if unblock is not None:
            try:
                unblock.set_visible(blocked)
            except Exception:
                pass
        self._show_bt_service(devices, res if isinstance(res, dict) else {})

    def _show_bt_service(self, devices, res=None) -> None:
        """Bluetooth card: the radio row (state in its subtitle), plus
        Unblock while rfkill blocks it and Start while bluetoothd is down."""
        win = self._win
        box = getattr(win, '_bt_service_box', None)
        row = getattr(win, '_bt_row', None)
        if box is None or row is None:
            return
        res = res or {}
        rows = [row]
        if res.get('blocked'):
            state = 'Blocked'
            rows.append(win._bt_unblock_btn)
        elif devices is None:
            state = 'Unavailable · the service may be stopped'
            rows.append(ui.nav_row(
                'Start Bluetooth service', 'Starts bluetoothd',
                lambda _r: win._on_start_bluetooth_service()))
        elif res.get('powered') is False:
            state = 'Off'
        else:
            linked = [dev for dev in devices if dev.connected]
            paired = [dev for dev in devices if dev.paired]
            if linked:
                state = 'Connected to %s' % (linked[0].name
                                             or linked[0].address)
            elif paired:
                state = 'On · %d paired' % len(paired)
            else:
                state = 'On'
        ui.set_subtitle(row, state)
        ui.fill(box, rows)

    def _on_bt_switch_flipped(self, switch, _pspec) -> None:
        import system_settings

        win = self._win
        if getattr(win, '_bt_switch_updating', False):
            return
        enabled = bool(switch.get_active())

        def _work():
            return system_settings.set_bt_powered(enabled)

        def _done(ok) -> None:
            if not ok:
                win._show_status('Could not switch Bluetooth.')
            win._refresh_bluetooth()

        win._run_bg(_work, _done)

    def _on_bt_unblock(self) -> None:
        import system_settings

        win = self._win
        try:
            if system_settings.rfkill_unblock('bluetooth'):
                win._show_status('Bluetooth unblocked.')
                win._refresh_bluetooth()
                return
        except Exception:
            pass
        win._show_status('Unblocking Bluetooth…')
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not unblock: {err}')
            return

        def _done(ok: bool, detail) -> None:
            win._show_status('Bluetooth unblocked.'
                             if ok else f'Could not unblock: {detail}')
            win._refresh_bluetooth()

        try:
            helper_client.set_rfkill('bluetooth', False, _done)
        except Exception as err:
            win._show_status(f'Could not unblock: {err}')

    def _on_start_bluetooth_service(self) -> None:
        win = self._win
        win._show_status('Starting Bluetooth…')
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not start service: {err}')
            return

        def _done(ok: bool, detail) -> None:
            win._show_status('Bluetooth started.'
                             if ok else f'Could not start service: {detail}')
            win._refresh_bluetooth()

        try:
            helper_client.manage_service('bluetooth', 'start', _done)
        except Exception as err:
            win._show_status(f'Could not start service: {err}')

    def _show_bt_devices(self, devices) -> None:
        # Unavailable or none yet: just the Scan row (the Bluetooth row's
        # subtitle already says why), no empty-state line.
        win = self._win
        rows = []
        for dev in (devices or [])[:12]:
            if dev.connected:
                state = 'Connected'
            elif dev.paired:
                state = 'Paired'
            else:
                state = 'Available · tap to pair'
            rows.append(ui.row(dev.name or dev.address, state,
                               on_activate=lambda _r, d=dev:
                               win._on_bt_selected(d)))
        self._fill_bt_list(rows)

    def _on_bt_selected(self, dev) -> None:
        import system_settings

        win = self._win
        if not dev.paired:
            win._show_status(f'Pairing {dev.name}…')
            win._run_bg(
                lambda: system_settings.pair_bt_device(dev.address),
                lambda result: (win._show_status(
                    result[1] if result else 'Bluetooth error.'),
                    win._refresh_bluetooth()),
            )
            return
        link = ('Disconnect' if dev.connected else 'Connect')
        win.item_actions._show_action_menu(
            getattr(win, '_bt_list_box', None),
            dev.name or dev.address,
            [(link, lambda: self._on_bt_link(dev)),
             ('Forget', lambda: self._forget_bt(dev)),
             ('Rename', lambda: self._on_bt_rename(dev))])

    def _on_bt_link(self, dev) -> None:
        import system_settings

        win = self._win
        action = (system_settings.disconnect_bt_device if dev.connected
                  else system_settings.connect_bt_device)
        win._show_status(
            f'{"Disconnecting" if dev.connected else "Connecting"} '
            f'{dev.name}…')
        win._run_bg(
            lambda: action(dev.address),
            lambda result: (win._show_status(
                result[1] if result else 'Bluetooth error.'),
                win._refresh_bluetooth()),
        )

    def _forget_bt(self, dev) -> None:
        """Forget runs straight from the device's menu: picking Forget
        there is the decision, so no second popup."""
        import system_settings

        win = self._win
        win._run_bg(
            lambda: system_settings.forget_bt_device(dev.address),
            lambda result: (
                win._show_status(result[1] if result else 'Forget failed.'),
                win._refresh_bluetooth()))

    def _on_bt_rename(self, dev) -> None:
        import system_settings

        win = self._win
        win.item_actions._show_entry_dialog(
            getattr(win, '_bt_list_box', None), 'Rename',
            dev.name or '', 'Rename',
            lambda alias: win._run_bg(
                lambda: system_settings.rename_bt_device(dev.address, alias),
                lambda result: (
                    win._show_status(result[1] if result else 'Rename failed.'),
                    win._refresh_bluetooth())))

    def _on_apn_save(self, values: dict) -> None:
        """Commit the APN sheet: config first, then nmcli off-thread."""
        win = self._win
        apn = str(values.get('apn', '')).strip()
        user = str(values.get('user', '')).strip()
        pwd = str(values.get('password', ''))
        win.config.data['apn'] = apn
        win.config.data['apn_user'] = user
        win.config.data['apn_pass'] = pwd
        win.config.save()
        win._apply_apn(apn, user, pwd)
        ui.set_subtitle(getattr(win, '_apn_row', None),
                        apn_summary(win.config))
        win._show_status('APN saved.')

    @staticmethod
    def _gsm_connection_name() -> str:
        import subprocess

        try:
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show'],
                capture_output=True, text=True, timeout=5, check=True,
            )
            for line in result.stdout.splitlines():
                # Terse mode escapes ':' inside names as '\:'; TYPE is last
                name, _, ctype = line.rpartition(':')
                if ctype.strip() == 'gsm' and name:
                    return name.replace('\\:', ':')
        except (OSError, subprocess.SubprocessError) as error:
            from shell_log import get_logger
            get_logger('window').warning(
                'GSM connection lookup failed (%s); using default name', error)
        return 'mobile'

    def _apply_apn(self, apn: str, user: str, pwd: str) -> None:
        win = self._win
        if not apn:
            return
        # The gsm lookup shells nmcli (up to 5 s); the modify itself stays a
        # fire-and-forget Popen. Both run off the GTK thread.
        args = (apn, user, pwd)

        def _work() -> None:
            try:
                import subprocess
                conn = win._gsm_connection_name()
                subprocess.Popen(
                    ['nmcli', 'connection', 'modify', conn,
                     'gsm.apn', args[0], 'gsm.username', args[1],
                     'gsm.password', args[2]],
                    close_fds=True,
                )
            except Exception as error:
                from shell_log import get_logger
                get_logger('window').warning('APN update failed: %s', error)

        win._run_bg(_work, lambda _result: None)

    def _update_status_text(self, text: str) -> None:
        win = self._win
        try:
            section = self._sections.get('system')
            if section is not None and section._status is not None:
                section._status.set_text(text)
        except Exception:
            pass
        win._show_status(text)

    def _on_update_clicked(self, _btn=None) -> None:
        """Terminal update path: the configured update script, or the
        native in-shell flow when the script is missing."""
        from update_checker import run_update_script

        win = self._win
        script = win.config.update_script
        from pathlib import Path as _Path

        if not _Path(script).expanduser().exists():
            import system_settings

            if system_settings.detect_backend() == 'apk':
                win._show_status('Update script not found — using in-shell update.')
                win._on_update_now(_btn)
                return
            win._show_status('Update script not found: %s' % script)
            return
        ok, detail = run_update_script(script)
        if ok:
            win._show_status('Update running in terminal.')
        else:
            win._show_status(detail)

    def _on_check_updates_clicked(self, _btn) -> None:
        """Manual check: refresh the index first (helper, harmless),
        then count repo plus Flatpak updates on a worker."""
        import helper_client

        self._update_status_text('Checking for updates…')
        helper_client.refresh_package_index(
            lambda ok, err: self._on_index_refreshed(ok, err))

    def _on_index_refreshed(self, ok: bool, err) -> None:
        refresh_err = None if ok else str(err or 'index refresh failed')

        def _work():
            import system_settings
            import update_checker

            try:
                repo = update_checker.count_updates()
            except Exception:
                repo = None
            try:
                flatpak = system_settings.flatpak_updates_available()
            except Exception:
                flatpak = None
            return repo, flatpak

        _run_bg(_work, lambda result: self._on_counted(result, refresh_err))

    def _on_counted(self, result, refresh_err=None) -> None:
        import settings_updates

        win = self._win
        try:
            repo, flatpak = result
        except Exception:
            repo, flatpak = None, None
        text = settings_updates.format_count(repo, flatpak)
        if refresh_err is not None and (repo is None and flatpak is None):
            text = 'Index refresh failed: %s' % refresh_err
        total = (repo or 0) + (flatpak or 0)
        if repo is None and flatpak is None:
            self._update_status_text(text)
        elif total > 0:
            self._update_status_text(text)
            win._on_updates_available(total)
        else:
            self._update_status_text(text)

    def _on_manual_check_result(self, count: int | None, flatpak=None,
                                refresh_err=None) -> None:
        """Scheduled/snoozed check result (UpdateChecker calls with one
        arg); the manual Check button reports through _on_counted."""
        win = self._win
        if count is None:
            self._update_status_text(
                'Update status unknown — package index is stale.')
        elif count > 0:
            win._on_updates_available(count)
        else:
            self._update_status_text('System is up to date.')

    def _on_updates_available(self, count: int) -> None:
        win = self._win
        plural = 's' if count != 1 else ''
        win._ensure_shade().add_notification(
            _UPDATE_NOTIF_ID,
            'System',
            'Updates available',
            f'{count} package{plural} can be updated.',
            actions=[
                ('Update now', win._on_update_now),
                ('Snooze 1 week', win._snooze_updates),
            ],
        )
        self._update_status_text(
            f'{count} update{plural} available — see notifications.')

    def _on_update_now(self, anchor=None) -> None:
        """'Update now': the owner's update-system.sh when installed (it
        is THE update path); otherwise the native path on apk (in-shell
        upgrade + Flatpak), the terminal script on pacman."""
        import settings_updates

        win = self._win
        try:
            present = settings_updates.owner_script_path(
                win.config.update_script)
        except Exception:
            present = None
        if present is not None:
            self._confirm_owner_update(anchor, present)
            return
        import system_settings

        backend = system_settings.detect_backend()
        if backend == 'apk':
            self._confirm_native_update(anchor)
        elif backend == 'pacman':
            self._confirm_terminal_update(anchor)
        else:
            win._on_update_clicked(anchor)

    def _confirm_owner_update(self, anchor, script: str) -> None:
        win = self._win
        if anchor is None:
            self._execute_owner_update(script)
            return
        try:
            actions = win.item_actions
        except Exception:
            self._execute_owner_update(script)
            return
        actions._show_action_menu(
            anchor, 'Run the system update now? Requires power.',
            [('Run update', lambda: self._execute_owner_update(script)),
             ('Cancel', lambda: None)])

    def _execute_owner_update(self, script: str) -> None:
        """Run the owner script in a terminal when one is installed,
        else headless with output streaming to the update log."""
        import update_checker

        win = self._win
        kind, detail, argv = update_checker.plan_update(script)
        if kind == 'terminal':
            import subprocess

            try:
                subprocess.Popen(argv or [], close_fds=True)
            except OSError:
                self._update_status_text('Could not open a terminal.')
                return
            self._update_status_text('Update running in terminal.')
            return
        if kind == 'missing':
            win._on_update_clicked(None)
            return
        import settings_updates

        log_path = settings_updates.new_log_path()
        self._owner_log_path = log_path
        self._update_status_text(
            'Updating system… (%s)' % detail)
        update_checker.run_owner_headless(
            script, self._on_owner_line,
            lambda rc: self._on_owner_done(rc, log_path))

    def _on_owner_line(self, line: str) -> None:
        import settings_updates

        settings_updates.append_log(
            getattr(self, '_owner_log_path', ''), line)
        if line.strip():
            self._update_status_text(line.strip()[-120:])

    def _on_owner_done(self, returncode: int, log_path: str) -> None:
        import settings_updates

        win = self._win
        try:
            with open(log_path, encoding='utf-8', errors='replace') as handle:
                text = handle.read()
        except OSError:
            text = ''
        summary = settings_updates.parse_owner_summary(text)
        reason = settings_updates.reboot_required()
        if returncode == 0 and summary['finished']:
            body = 'System and Flatpak updates finished.'
            if summary['reboot_reasons']:
                body += ' %s' % '; '.join(summary['reboot_reasons'])
            elif reason:
                body += ' %s' % reason
            body += ' Log: %s' % (summary['log_path'] or log_path)
            try:
                win._ensure_shade().add_notification(
                    _UPDATE_RESULT_ID, 'System', 'System update finished',
                    body)
            except Exception:
                pass
            self._update_status_text('System update finished.')
        elif returncode == -1:
            self._update_status_text(
                'System update failed to start.')
        else:
            detail = '%d step(s) failed.' % summary['failures'] \
                if summary['failures'] else 'see the update log.'
            self._update_status_text('System update failed: %s' % detail)
        self._refresh_updates_section()

    def _confirm_native_update(self, anchor) -> None:
        win = self._win
        if anchor is None:
            self._update_status_text(
                'Tap Update now to install system updates.')
            return
        try:
            actions = win.item_actions
        except Exception:
            self._update_status_text('Cannot confirm here.')
            return
        actions._show_action_menu(
            anchor, 'Install system updates now? Requires power.',
            [('Install updates', lambda: self._execute_native_update()),
             ('Cancel', lambda: None)])

    def _confirm_terminal_update(self, anchor) -> None:
        win = self._win
        if anchor is None:
            win._on_update_clicked(None)
            return
        try:
            actions = win.item_actions
        except Exception:
            win._on_update_clicked(None)
            return
        actions._show_action_menu(
            anchor, 'Open the system update in a terminal?',
            [('Open terminal', lambda: win._on_update_clicked(None)),
             ('Cancel', lambda: None)])

    def _execute_native_update(self) -> None:
        import system_settings

        ok, reason = system_settings.power_for_update()
        if not ok:
            self._update_status_text(
                'Update cancelled: %s' % reason)
            return
        self._update_status_text(
            'Updating system packages… (%s)' % reason)
        import helper_client

        helper_client.upgrade_system(
            lambda ok, output: self._on_apk_upgraded(ok, output))

    def _on_apk_upgraded(self, ok: bool, output) -> None:
        import settings_updates
        import system_settings

        log_path = settings_updates.new_log_path()
        settings_updates.append_log(
            log_path, '== apk upgrade ==\n%s\n' % (output or ''))
        if not ok:
            self._update_status_text(
                'System update failed: %s' % (output or 'unknown error'))
            self._refresh_updates_section()
            return
        self._update_status_text('System packages done — updating Flatpaks…')

        def _work():
            return system_settings.flatpak_update_all()

        _run_bg(_work, lambda result: self._on_flatpak_updated(
            result, log_path))

    def _on_flatpak_updated(self, result, log_path: str) -> None:
        import settings_updates

        win = self._win
        ok, output = result if result else (False, 'Flatpak update failed.')
        settings_updates.append_log(
            log_path, '== flatpak update ==\n%s\n' % (output or ''))
        reason = settings_updates.reboot_required()
        if ok:
            body = 'System and Flatpak updates finished.'
            if reason:
                body += ' %s' % reason
            body += ' Log: %s' % log_path
            try:
                win._ensure_shade().add_notification(
                    _UPDATE_RESULT_ID, 'System', 'System update finished',
                    body)
            except Exception:
                pass
            self._update_status_text('System update finished.')
        else:
            self._update_status_text('Flatpak update failed: %s' % output)
        self._refresh_updates_section()

    def _refresh_updates_section(self) -> None:
        try:
            section = self._sections.get('system')
            if section is not None:
                section.refresh(self._win)
        except Exception:
            pass

    def _refresh_apps_section(self) -> None:
        try:
            section = self._sections.get('apps')
            if section is not None:
                section.refresh(self._win)
        except Exception:
            pass

    def _snooze_updates(self) -> None:
        win = self._win
        win._update_checker.snooze()
        win._show_status('Update checks snoozed for 1 week.')

    def _on_export_backup(self) -> None:
        from backup import backup_export_dir, export_backup
        import json
        from datetime import datetime

        win = self._win
        backup = export_backup(win.config)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'xx-wm-backup-{timestamp}.json'
        filepath = backup_export_dir() / filename

        filepath.write_text(json.dumps(backup, indent=2), encoding='utf-8')
        win._show_status(f'Backup exported to {filepath}')

    def _on_restore_backup(self, anchor=None) -> None:
        # A file chooser is a normal toplevel: it would open UNDER this
        # layer-shell TOP surface. Restore picks from an in-shell menu of
        # the xx-wm-backup-*.json files in ~ and ~/Documents instead.
        from backup import backup_label, discover_backups

        win = self._win
        if anchor is None:
            anchor = getattr(win, '_restore_btn', None)
        backups = discover_backups()
        if not backups:
            win._show_status('No backups found in ~ or ~/Documents')
            return
        actions: list = []
        for path in backups:
            label = backup_label(path)
            actions.append(
                (label,
                 lambda p=path, lb=label: win._confirm_restore_backup(anchor, p, lb)))
        actions.append(('Cancel', lambda: None))
        win.item_actions._show_action_menu(anchor, 'Restore from backup', actions)

    def _confirm_restore_backup(self, anchor, path, label: str) -> None:
        win = self._win
        win.item_actions._show_action_menu(
            anchor,
            f'Restore backup from {label}? This replaces your home layout and settings.',
            [('Restore', lambda: win._restore_from_path(str(path))),
             ('Cancel', lambda: None)])

    def _restore_from_path(self, filepath: str | None) -> None:
        from backup import restore_backup, validate_backup
        import json
        from pathlib import Path

        win = self._win
        if not filepath:
            win._show_status('No backup file selected.')
            return

        try:
            payload = json.loads(Path(filepath).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as e:
            win._show_status(f'Failed to read backup: {e}')
            return

        is_valid, error = validate_backup(payload)
        if not is_valid:
            win._show_status(f'Invalid backup: {error}')
            return

        success = restore_backup(win.config, payload)
        if success:
            win._show_status('Backup restored successfully')
            win._apply_config_change()
        else:
            win._show_status('Failed to restore backup')

    def _get_device_name(self) -> str:
        import settings_device
        return settings_device.read_device_name()
