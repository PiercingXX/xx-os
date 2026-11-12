"""Smart Lock settings (UI over smart_lock).

Trusted Bluetooth devices (bonded, via BlueZ), trusted Wi-Fi access
points (the connected one, via NetworkManager), the 4-hour re-auth rule,
and a clear status line. Adding trust asks for the PIN first; removing it
does not. On-body unlock stays hidden: the backend does not support it.
"""
from __future__ import annotations

import settings_lock
import settings_page
import settings_ui as ui
from shell_log import log_exc


def trust_status(win) -> tuple[str, str]:
    """(status, detail) for the header line: 'Unlocked by: X' while a
    trusted device holds trust, else 'Off' with the lock-screen reason
    as a dim detail. Never raises (bare test windows read as Off)."""
    try:
        import smart_lock
        state = smart_lock.current_state(getattr(win, 'config', None))
        line = smart_lock.lock_line_for(state)
    except Exception:
        return ('Off', '')
    prefix = 'Kept unlocked by '
    if (line.startswith(prefix)
            and (state.trusted_bt or state.trusted_ssid)):
        return ('Unlocked by: ' + line[len(prefix):], '')
    if not line or line.startswith(prefix):
        return ('Off', '')
    return ('Off', line)


def reauth_text() -> str:
    try:
        import smart_lock
        hours = int(smart_lock.TRUST_MAX_PIN_AGE_S // 3600)
    except Exception:
        hours = 4
    return 'Even while trusted, the PIN is asked again every %d h.' % hours


def trusted_bt(win) -> list:
    try:
        return list(getattr(win.config, 'smart_lock_bt_devices', None) or ())
    except Exception:
        return []


def trusted_ssids(win) -> list:
    try:
        return list(getattr(win.config, 'smart_lock_wifi_ssids', None) or ())
    except Exception:
        return []


class SmartLockSection(settings_page._Section):
    key = 'smart_lock'
    title = 'Smart Lock'

    def build(self, win) -> list:
        win._slock_row = ui.row(self.title, 'Off')
        status = ui.group(self.title, [win._slock_row], note=reauth_text())
        win._slock_bt_add = ui.nav_row(
            'Trust a device…', 'A paired Bluetooth device',
            lambda r: self._pick_bt_device(win, r))
        devices = ui.group('Trusted devices', [win._slock_bt_add])
        win._slock_bt_box = devices[1]
        win._slock_wifi_add = ui.nav_row(
            'Trust a network…', 'The connected Wi-Fi network',
            lambda r: self._pick_ssid(win, r))
        networks = ui.group('Trusted networks', [win._slock_wifi_add])
        win._slock_wifi_box = networks[1]
        built = status + devices + networks
        try:
            import smart_lock
            if smart_lock.on_body_supported():
                built.extend(ui.group('On body', [ui.switch_row(
                    'Stay unlocked while on body', '', False)]))
        except Exception:
            log_exc('settings_smart_lock.build: smart_lock.on_body_supported')
        self.refresh(win)
        return built

    def refresh(self, win) -> None:
        self._render_status(win)
        self._render_bt(win)
        self._render_wifi(win)

    def _render_status(self, win) -> None:
        status, detail = trust_status(win)
        ui.set_subtitle(getattr(win, '_slock_row', None),
                        '%s · %s' % (status, detail) if detail else status)

    def _trusted_rows(self, win, items, remove, add_row) -> list:
        """``items``: (entry, title, subtitle) per trusted entry."""
        rows = [ui.row(title, subtitle, on_activate=lambda r, i=entry, t=title:
                       win.item_actions._show_action_menu(
                           r, t, [('Remove', lambda: remove(win, i)),
                                  ('Cancel', lambda: None)]))
                for entry, title, subtitle in items]
        return rows + [add_row]

    def _render_bt(self, win) -> None:
        box = getattr(win, '_slock_bt_box', None)
        if box is None:
            return
        items = [(entry, entry, 'Trusted') for entry in trusted_bt(win)]
        ui.fill(box, self._trusted_rows(win, items, self._remove_bt,
                                        win._slock_bt_add))

    def _render_wifi(self, win) -> None:
        box = getattr(win, '_slock_wifi_box', None)
        if box is None:
            return
        items = [(entry, str(entry.get('ssid', '')),
                  str(entry.get('bssid', '')))
                 for entry in trusted_ssids(win) if isinstance(entry, dict)]
        ui.fill(box, self._trusted_rows(win, items, self._remove_ssid,
                                        win._slock_wifi_add))

    def _remove_bt(self, win, address: str) -> None:
        try:
            kept = [entry for entry in trusted_bt(win)
                    if str(entry).strip().upper() != str(address).strip().upper()]
            win.config.set_smart_lock_bt_devices(kept)
        except Exception:
            log_exc('settings_smart_lock._remove_bt: trusted_bt')
        self._render_bt(win)
        self._render_status(win)

    def _remove_ssid(self, win, network: dict) -> None:
        try:
            kept = [entry for entry in trusted_ssids(win) if entry != network]
            win.config.set_smart_lock_wifi_ssids(kept)
        except Exception:
            log_exc('settings_smart_lock._remove_ssid: trusted_ssids')
        self._render_wifi(win)
        self._render_status(win)

    def _pick_bt_device(self, win, anchor) -> None:
        import smart_lock
        smart_lock.query_bonded_bt_devices(
            lambda devices: self._show_bt_picker(win, anchor, devices))

    def _show_bt_picker(self, win, anchor, devices) -> None:
        if devices is None:
            self._announce(win, 'Bluetooth unavailable.')
            return
        if not devices:
            self._announce(win, 'No paired devices — pair in Bluetooth.')
            return
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        known = {str(entry).strip().upper() for entry in trusted_bt(win)}
        choices = [(_device_label(dev),
                    lambda dev=dev: self._confirm_bt(win, anchor, dev))
                   for dev in devices
                   if str(getattr(dev, 'address', '')).strip().upper() not in known]
        if not choices:
            self._announce(win, 'Every paired device is trusted.')
            return
        actions._show_action_menu(anchor, 'Trust a device', choices)

    def _confirm_bt(self, win, anchor, dev) -> None:
        settings_lock.ask_pin(
            win, anchor, 'Trust %s' % _device_label(dev), 'Trust',
            lambda: self._add_bt(win, dev.address))

    def _add_bt(self, win, address: str) -> None:
        try:
            win.config.set_smart_lock_bt_devices(
                trusted_bt(win) + [address])
        except Exception:
            log_exc('_add_bt: config.set_smart_lock_bt_devices')
        self._render_bt(win)
        self._render_status(win)

    def _pick_ssid(self, win, anchor) -> None:
        import smart_lock
        smart_lock.query_connected_wifi(
            lambda ap: self._show_ssid_picker(win, anchor, ap))

    def _show_ssid_picker(self, win, anchor, ap) -> None:
        """Trust the access point the phone is connected to right now."""
        if ap is None:
            self._announce(win, 'NetworkManager unavailable.')
            return
        if not ap:
            self._announce(win, 'Connect to the Wi-Fi network first.')
            return
        if not ap.get('secure'):
            self._announce(win, 'Open networks cannot be trusted.')
            return
        network = {'ssid': ap['ssid'], 'bssid': ap['bssid']}
        if network in trusted_ssids(win):
            self._announce(win, 'This network is trusted.')
            return
        settings_lock.ask_pin(
            win, anchor, 'Trust %s' % network['ssid'], 'Trust',
            lambda: self._add_ssid(win, network))

    def _add_ssid(self, win, network: dict) -> None:
        try:
            win.config.set_smart_lock_wifi_ssids(
                trusted_ssids(win) + [network])
        except Exception:
            log_exc('_add_ssid: config.set_smart_lock_wifi_ssids')
        self._render_wifi(win)
        self._render_status(win)

    @staticmethod
    def _announce(win, text: str) -> None:
        try:
            win._show_status(text)
        except Exception:
            log_exc('settings_smart_lock._announce: win._show_status')


def _device_label(dev) -> str:
    name = str(getattr(dev, 'name', '') or '').strip()
    address = str(getattr(dev, 'address', '') or '').strip()
    if name and address and name != address:
        return '%s (%s)' % (name, address)
    return name or address or '?'
