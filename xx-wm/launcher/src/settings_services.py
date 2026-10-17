"""Curated services section (ref service-manager.sh).

A fixed list, never a unit browser: state reads unprivileged (systemd
`GetUnit`/`ActiveState`/`GetUnitFileState`, OpenRC `rc-service status`
and `rc-update show` in a worker thread); state changes go through the
root helper's `ManageService`, whose allowlist holds exactly these
units — xx-wm itself, dbus and logind are never touched. Stop uses the
power-menu arm-then-confirm (two taps, the arm resetting on any other
action); failed units list read-only.

The ssh row also shows its listening address and whether the daemon
config is key-only (the helper never writes sshd_config, so enabling
SSH preserves whatever is configured and never flips password auth on
silently). The adbd row (wireless debugging over TCP) renders only
when the adbd binary is present and says nothing otherwise.

KDE Connect and Syncthing are user services (`systemctl --user`,
unprivileged, no helper): state and pairing read on the worker thread,
failures land inline in the row subtitle, and Syncthing's menu opens
its web UI in the browser.
"""
from __future__ import annotations

from dataclasses import dataclass

import settings_page
import settings_ui as ui

try:
    from power_menu import ConfirmState
except Exception:
    # Collection-time stub gi packages (installed by some test modules
    # at import) cannot build power_menu's Gtk.Window subclass. The
    # fallback is the same two-tap machine, pinned by test; window.py
    # imports PowerMenu lazily for the same reason.
    class ConfirmState:
        """First tap arms, second tap confirms (power-menu pattern)."""

        def __init__(self) -> None:
            self.pending = None

        def tap(self, action: str) -> bool:
            if self.pending == action:
                self.pending = None
                return True
            self.pending = action
            return False

        def reset(self) -> None:
            self.pending = None


@dataclass(frozen=True)
class ServiceEntry:
    key: str
    title: str
    detail: str
    systemd_units: tuple
    openrc_names: tuple


# Titles name the daemon, not the feature: Wi-Fi, Bluetooth and Mobile
# data have their own settings pages, so a 'Network' or 'Bluetooth' row
# here read as a second copy of them (docs/settings-map.md).
SERVICES: tuple = (
    ServiceEntry('modem', 'ModemManager', 'Mobile data modem',
                 ('ModemManager.service',), ('modemmanager',)),
    ServiceEntry('eg25', 'EG25 modem helper', 'Quectel manager, when present',
                 ('eg25-manager.service',), ('eg25-manager',)),
    ServiceEntry('network', 'NetworkManager', 'Wi-Fi and wired connections',
                 ('NetworkManager.service',), ('networkmanager',)),
    ServiceEntry('bluetooth', 'BlueZ', 'Bluetooth daemon',
                 ('bluetooth.service',), ('bluetooth',)),
    ServiceEntry('ssh', 'SSH server', 'Remote shell access',
                 ('sshd.service', 'ssh.service'), ('sshd',)),
    ServiceEntry('adbd', 'Wireless debugging (ADB)',
                 'Android debug bridge over TCP, when installed',
                 ('adbd.service',), ('adbd',)),
    ServiceEntry('tailscale', 'Tailscale', 'Private mesh VPN',
                 ('tailscaled.service',), ('tailscale', 'tailscaled')),
    ServiceEntry('waydroid', 'Waydroid container', 'Android compatibility',
                 ('waydroid-container.service',), ('waydroid',)),
)

USER_SERVICES: tuple = (
    ServiceEntry('kdeconnect', 'KDE Connect', 'Phone integration',
                 ('kdeconnectd.service',), ()),
    ServiceEntry('syncthing', 'Syncthing', 'File synchronization',
                 ('syncthing.service',), ()),
)

SERVICE_ACTIONS = ('Start', 'Stop', 'Restart', 'Enable', 'Disable')


def visible_system_entries(eg25: bool = False, adbd: bool = False) -> list:
    """System entries to render: eg25 only where its manager exists and
    adbd only where its binary is installed (silence, never a dead
    toggle, everywhere else)."""
    return [entry for entry in SERVICES
            if (entry.key != 'eg25' or eg25)
            and (entry.key != 'adbd' or adbd)]


def state_text(state) -> str:
    """One-line row subtitle for an (active, enabled) pair."""
    try:
        active, enabled = state
    except (TypeError, ValueError):
        return 'Unavailable'
    if active is None:
        return 'Unavailable'
    if active == 'active' or active == 'activating' or active is True:
        base = 'On'
    elif active == 'failed':
        base = 'Failed'
    else:
        base = 'Off'
    if enabled == 'enabled' or enabled is True:
        base += ', starts at boot'
    return base


class ServicesSection(settings_page._Section):
    """Settings section: the curated service list plus failed units."""

    key = 'services'
    title = 'Services'

    def __init__(self) -> None:
        self._rows_box = None
        self._failed_box = None
        self._confirm = ConfirmState()
        self._user_rows: dict = {}
        self._user_base: dict = {}
        self._user_info: dict = {}

    def build(self, win) -> list:
        services = ui.group(self.title, [ui.note_row('Checking…')],
                            note='Tap a service to start, stop, restart, '
                                 'enable or disable it.')
        self._rows_box = services[1]
        failed = ui.group('Failed units')
        self._failed_box = failed[1]
        ui.fill(self._failed_box, [], hide_empty=True)
        return services + failed

    def refresh(self, win) -> None:
        from settings_page import _run_bg
        _run_bg(self._probe, lambda res: self._render(win, res))

    # ------------------------------------------------------------ behavior

    def _probe(self):
        """Worker thread: init, per-service state, failed units. Never raises."""
        import system_settings

        try:
            init = system_settings.detect_init()
        except Exception:
            init = 'unknown'
        states: dict = {}
        try:
            if init == 'systemd':
                for entry in SERVICES:
                    states[entry.key] = self._systemd_state(
                        system_settings, entry)
            elif init == 'openrc':
                for entry in SERVICES:
                    states[entry.key] = self._openrc_state(
                        system_settings, entry)
        except Exception:
            pass
        try:
            eg25 = self._eg25_present(system_settings, init)
        except Exception:
            eg25 = False
        try:
            failed = system_settings.list_failed_units(init)
        except Exception:
            failed = []
        try:
            import settings_connect as connect
        except Exception:
            connect = None
        ssh_net: dict = {}
        ssh_pw = None
        user: dict = {}
        adbd = False
        if connect is not None:
            try:
                ssh_net = connect.ssh_net_probe() or {}
            except Exception:
                ssh_net = {}
            try:
                ssh_pw = connect.ssh_password_allowed()
            except Exception:
                ssh_pw = None
            try:
                adbd = bool(connect.adbd_available())
            except Exception:
                adbd = False
            for key, probe in (('kdeconnect', connect.kdeconnect_probe),
                               ('syncthing', connect.syncthing_probe)):
                try:
                    user[key] = probe()
                except Exception:
                    user[key] = {'installed': False, 'active': None,
                                 'enabled': None, 'active_state': None,
                                 'enabled_state': None, 'devices': [],
                                 'error': None}
        return {'init': init, 'states': states, 'eg25': eg25,
                'failed': failed, 'ssh_net': ssh_net, 'ssh_pw': ssh_pw,
                'user': user, 'adbd': adbd}

    @staticmethod
    def _systemd_state(system_settings, entry):
        for unit in entry.systemd_units:
            try:
                state = system_settings.systemd_unit_state(unit)
            except Exception:
                state = None
            if state is not None:
                return state
        return None

    @staticmethod
    def _openrc_state(system_settings, entry):
        import os
        for name in entry.openrc_names:
            path = os.path.join('/etc', 'init.d', name)
            try:
                if not os.path.isfile(path):
                    continue
            except Exception:
                continue
            try:
                running, enabled = system_settings.openrc_service_state(name)
            except Exception:
                return None
            active = ('active' if running else 'inactive'
                      if running is not None else None)
            file_state = ('enabled' if enabled else 'disabled'
                          if enabled is not None else None)
            return (active, file_state)
        return None

    @staticmethod
    def _eg25_present(system_settings, init: str) -> bool:
        import os
        if init == 'systemd':
            try:
                return system_settings.systemd_unit_state(
                    'eg25-manager.service') is not None
            except Exception:
                return False
        if init == 'openrc':
            try:
                return os.path.isfile('/etc/init.d/eg25-manager')
            except Exception:
                return False
        return False

    def _render(self, win, res) -> None:
        if self._rows_box is None or self._failed_box is None:
            return
        try:
            states = res.get('states', {})
            eg25 = bool(res.get('eg25'))
            failed = res.get('failed', [])
            ssh_net = res.get('ssh_net') or {}
            ssh_pw = res.get('ssh_pw')
            user = res.get('user') or {}
            adbd = bool(res.get('adbd'))
        except AttributeError:
            return
        try:
            import settings_connect as connect
        except Exception:
            connect = None
        rows = []
        self._user_rows = {}
        self._user_base = {}
        self._user_info = dict(user)
        for entry in visible_system_entries(eg25, adbd):
            subtitle = state_text(states.get(entry.key))
            if entry.key == 'ssh' and connect is not None:
                try:
                    subtitle = connect.describe_ssh(
                        subtitle,
                        bool(ssh_net.get('listening')),
                        ssh_net.get('address'), ssh_pw)
                except Exception:
                    pass
            row = ui.nav_row(
                entry.title, subtitle,
                lambda r, e=entry: self._on_service_menu(win, e, r))
            rows.append(row)
        for entry in USER_SERVICES:
            info = user.get(entry.key) or {}
            subtitle = self._user_subtitle(connect, info)
            row = ui.nav_row(
                entry.title, subtitle,
                lambda r, e=entry: self._on_user_menu(win, e, r))
            self._user_rows[entry.key] = row
            self._user_base[entry.key] = subtitle
            rows.append(row)
        ui.fill(self._rows_box, rows)
        self._show_failed(win, failed)

    @staticmethod
    def _user_subtitle(connect, info: dict) -> str:
        if connect is None:
            return 'Unavailable'
        try:
            return connect.describe_user_service(
                bool(info.get('installed')), info.get('active_state'),
                info.get('enabled_state'), info.get('devices'),
                info.get('error'))
        except Exception:
            return 'Unavailable'

    def _show_failed(self, win, failed) -> None:
        if self._failed_box is None:
            return
        items = [unit for unit in (failed or []) if unit]
        ui.fill(self._failed_box, [ui.row(unit, 'Failed') for unit in items],
                hide_empty=True)

    def _on_service_menu(self, win, entry, anchor=None) -> None:
        actions = []
        for action in SERVICE_ACTIONS:
            if action == 'Stop':
                actions.append(
                    ('Stop', lambda e=entry: self._on_stop_tapped(win, e)))
            else:
                actions.append(
                    (action,
                     lambda a=action.lower(), e=entry: self._apply(
                         win, e, a)))
        actions.append(('Cancel', lambda: None))
        win.item_actions._show_action_menu(
            anchor or self._rows_box, entry.title, actions)

    def _on_user_menu(self, win, entry, anchor=None) -> None:
        actions = []
        if entry.key == 'syncthing':
            actions.append(
                ('Open web UI', lambda: self._open_syncthing(win, entry)))
        for action in SERVICE_ACTIONS:
            if action == 'Stop':
                actions.append(
                    ('Stop',
                     lambda e=entry: self._on_user_stop_tapped(win, e)))
            else:
                actions.append(
                    (action,
                     lambda a=action.lower(), e=entry,
                     r=anchor: self._apply_user(win, e, a, r)))
        actions.append(('Cancel', lambda: None))
        subtitle = None
        if entry.key == 'kdeconnect':
            devices = (self._user_info.get('kdeconnect') or {}).get(
                'devices') or []
            if devices:
                subtitle = 'Paired: %s' % ', '.join(devices[:5])
        if subtitle:
            win.item_actions._show_action_menu(
                anchor or self._rows_box, entry.title, actions,
                subtitle=subtitle)
        else:
            win.item_actions._show_action_menu(
                anchor or self._rows_box, entry.title, actions)

    def _open_syncthing(self, win, entry) -> None:
        row = self._user_rows.get(entry.key)
        try:
            from gi.repository import Gio
            import settings_connect as connect
            Gio.AppInfo.launch_default_for_uri(connect.SYNCTHING_URL, None)
        except Exception as err:
            if row is not None:
                base = self._user_base.get(entry.key, entry.title)
                ui.set_subtitle(row, '%s · %s' % (base, err))

    def _stop_key(self, key: str) -> str:
        return 'stop:%s' % key

    def _on_stop_tapped(self, win, entry) -> str:
        return self._on_stop(win, self._stop_key(entry.key))

    def _on_user_stop_tapped(self, win, entry) -> str:
        return self._on_user_stop(win, 'user-stop:%s' % entry.key)

    def _on_stop(self, win, stop_key: str) -> str:
        """Arm-then-confirm: the first tap arms, the second executes. The
        arm resets on any other service action (menus close between taps,
        so no timer can expire it). Returns 'armed' or 'confirmed'."""
        if self._confirm.tap(stop_key):
            _key = stop_key.split(':', 1)[1]
            entry = next((item for item in SERVICES if item.key == _key),
                         None)
            if entry is not None:
                self._apply(win, entry, 'stop')
            return 'confirmed'
        try:
            win._show_status('Tap Stop again to stop the service.')
        except Exception:
            pass
        return 'armed'

    def _on_user_stop(self, win, stop_key: str) -> str:
        """Arm-then-confirm for user services (same shared machine)."""
        if self._confirm.tap(stop_key):
            _key = stop_key.split(':', 1)[1]
            entry = next((item for item in USER_SERVICES
                          if item.key == _key), None)
            if entry is not None:
                self._apply_user(win, entry, 'stop',
                                 self._user_rows.get(_key))
            return 'confirmed'
        try:
            win._show_status('Tap Stop again to stop the service.')
        except Exception:
            pass
        return 'armed'

    def _apply(self, win, entry, action: str) -> None:
        self._confirm.reset()
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not {action} {entry.title}: {err}')
            return

        def _done(ok: bool, detail) -> None:
            if ok:
                win._show_status(f'{entry.title}: {action}ed.')
            else:
                win._show_status(f'Could not {action} {entry.title}: '
                                 f'{detail}')
            self.refresh(win)

        try:
            helper_client.manage_service(entry.key, action, _done)
        except Exception as err:
            win._show_status(f'Could not {action} {entry.title}: {err}')

    def _apply_user(self, win, entry, action: str, row=None) -> None:
        """Run one user-unit action on a worker; failures land inline in
        the row subtitle, successes refresh."""
        self._confirm.reset()
        from settings_page import _run_bg
        unit = entry.systemd_units[0] if entry.systemd_units else ''
        target = row if row is not None else self._user_rows.get(entry.key)
        base = self._user_base.get(entry.key, entry.title)

        def _work():
            try:
                import settings_connect as connect
                connect.set_user_unit(unit, action)
            except Exception as err:
                return {'error': str(err)}
            return {}

        def _done(res) -> None:
            if not isinstance(res, dict) or not res.get('error'):
                self.refresh(win)
                return
            if target is not None:
                ui.set_subtitle(target, '%s · %s' % (base, res['error']))

        _run_bg(_work, _done)
