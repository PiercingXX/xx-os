#!/usr/bin/env python3
"""Fail when blocking calls are reachable from GTK main-loop code.

Flags ``subprocess.run`` / ``check_output`` / D-Bus ``call_sync`` /
``Popen(...).wait()`` anywhere in ``launcher/src`` except inside the
allow-listed worker helpers below. Fire-and-forget ``Popen`` (never waited)
is allowed everywhere: only ``.wait()`` on a Popen handle blocks.

Each allow-list entry carries a one-line justification. Fix a violation by
moving the call to a worker thread (``threading.Thread`` + ``GLib.idle_add``
back, or the module's ``_run_bg``/``_spawn_*_worker`` helper), or add an
entry with a justification. Stale entries (matching no call site) also
fail, so the list cannot silently grow.

Limitations: enclosing scope is the nearest ``def`` (nested ``_work`` /
``_worker`` helpers are listed by their own name), and ``.wait()`` is
matched on ``proc`` or a name assigned from a Popen call in the same file
(``threading.Event.wait`` is never flagged).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / 'launcher' / 'src'

# (file, enclosing function) -> why blocking here is safe.
ALLOW = {
    ('als_brightness.py', '_brightnessctl_set'):
        'brightness-slider fallback when brightnessctl is missing; single bounded local exec.',
    ('als_brightness.py', 'login1_set_brightness'):
        'same fallback chain; single SetBrightness property write.',
    ('app_permissions.py', 'lookup_permissions'):
        'permission-store read; callers are settings-page workers, never GTK.',
    ('app_permissions.py', 'set_permission'):
        'permission-store write; callers are settings-page workers, never GTK.',
    ('app_permissions.py', 'delete_permission'):
        'permission-store write; callers are settings-page workers, never GTK.',
    ('app_standby.py', '_get_prop'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', 'query_mpris_playing'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', 'query_calls_owner_pid'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', 'query_battery'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', '_default_pw_dump'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', '_ensure_dbus_units_sync'):
        'one bounded sync unit resolve so a frozen app thaws before raise.',
    ('app_standby.py', 'shutdown_thaw'):
        'shutdown path; blocking while the session exits is safe.',
    ('call_ui.py', '_pactl_card_names'):
        'call-audio worker thread.',
    ('call_ui.py', '_run_pactl_hunts'):
        'call-audio worker thread.',
    ('call_ui.py', '_work'):
        'call-audio mute worker threads.',
    ('capture.py', '_run_grim'):
        'screenshot/scroll workers (_screenshot_work, _scrolling_work).',
    ('capture.py', 'probe_h264'):
        'recorder _start_work thread; one bounded local probe per record.',
    ('capture.py', '_reap'):
        '_spawn_worker reap thread after SIGINT, 10 s cap then kill.',
    ('contacts.py', 'load_sms_history'):
        'SMS history worker (sms _fetch thread).',
    ('dialer.py', '_run_mmcli'):
        'telephony worker threads (place-call / emergency paths).',
    ('dialer.py', 'get_modem_emergency_numbers'):
        'documented worker-only; lock-screen modem thread.',
    ('display_manager.py', '_detect_output'):
        'first-blank output probe; result cached for later wlopm calls.',
    ('display_manager.py', '_wlopm'):
        'blank/wake power path; single bounded compositor call.',
    ('display_manager.py', '_brightnessctl'):
        'blank/wake power path; single bounded sysfs read.',
    ('display_manager.py', '_get_volume_pct'):
        'volume-key HUD read on the evdev reader thread; single bounded pactl call.',
    ('home_launcher.py', '_waydroid_session_running'):
        'waydroid-launch worker thread (moved off the home tap path).',
    ('lock_screen.py', '_fp_dbus_enrolled'):
        'fingerprint probe worker (_fp_probe_sync).',
    ('lock_screen.py', '_fp_list_enrolled'):
        'fingerprint probe worker (_fp_probe_sync).',
    ('lock_screen.py', '_fp_thread'):
        'fingerprint verify worker thread.',
    ('modem_monitor.py', 'bus_name_has_owner'):
        'call/SMS signal-callback GetNameOwner probe (500 ms cap), no scan.',
    ('modem_monitor.py', 'resolve_modem_path'):
        'telephony workers (dial/sms threads).',
    ('modem_monitor.py', 'voice_create_and_start'):
        'telephony worker (dialer _place_call thread).',
    ('modem_monitor.py', 'messaging_create_and_send'):
        'telephony worker (sms send thread).',
    ('modem_monitor.py', 'messaging_list'):
        'SMS history worker (contacts load path).',
    ('notification_shade.py', '_external_daemon_owns_notifications'):
        'external-daemon probe worker, posts back via _post_to_main.',
    ('power_menu.py', 'power_action_sync'):
        'power_action_async worker thread; sync export kept for tests.',
    ('privacy_monitor.py', '_default_pw_dump_once'):
        'privacy monitor thread.',
    ('privacy_monitor.py', '_run'):
        'privacy monitor thread (pw-dump child lifecycle).',
    ('privacy_monitor.py', '_watch'):
        'privacy monitor thread (child reap, 1 s cap).',
    ('tailscale.py', '_run'):
        'tile query worker (_query_tile_states) and toggle worker (_set_tailscale).',
    ('quick_actions.py', '_nm_get'):
        'tile query worker (_query_tile_states).',
    ('quick_actions.py', '_nm_set'):
        'user-initiated single NM property write (pinned sync snap-back).',
    ('quick_actions.py', '_nm_has_wifi_device'):
        'hardware-gate probe worker.',
    ('quick_actions.py', '_bluez_get'):
        'tile query worker (_query_tile_states).',
    ('quick_actions.py', '_bluez_set'):
        'user-initiated single BlueZ property write (pinned sync snap-back).',
    ('quick_actions.py', '_get_bt_device_name'):
        'tile detail worker (_query_tile_details).',
    ('quick_actions.py', '_active_hotspot_path'):
        'hotspot gate probe worker.',
    ('quick_actions.py', '_toggle_hotspot'):
        'user-initiated hotspot toggle; single bounded NM call.',
    ('quick_actions.py', 'active_clients'):
        'location/hotspot availability probe worker.',
    ('quick_actions.py', 'available'):
        'location/hotspot availability probe worker.',
    ('quick_actions.py', '_register'):
        'location toggle path; single bounded agent registration.',
    ('quick_actions.py', '_unregister'):
        'location toggle path; single bounded agent release.',
    ('quick_actions.py', '_get_volume_pct'):
        'slider expand batch worker (_read_slider_values).',
    ('settings_page.py', '_gsm_connection_name'):
        'APN apply worker (_apply_apn _work).',
    ('settings_page.py', '_run_xx_apply'):
        'XX-app apply worker (_run_bg); timeout-bounded, never the main loop.',
    ('settings_storage.py', '_run'):
        'sole subprocess chokepoint; invoked via _run_bg workers only.',
    ('settings_device.py', '_run'):
        'sole subprocess chokepoint; invoked via _run_bg workers only.',
    ('settings_device.py', 'dbus_call_sync'):
        'sole D-Bus sync chokepoint; invoked via _run_bg workers only.',
    ('settings_logs.py', '_run'):
        'sole subprocess chokepoint; invoked via _run_bg workers only.',
    ('settings_security.py', '_run'):
        'sole subprocess chokepoint; invoked via _run_bg workers only.',
    ('filebackup.py', '_run_capture'):
        'sole capturing chokepoint; dry-run preview worker only.',
    ('filebackup.py', 'run_transfer'):
        'rsync transfer worker thread (Popen + wait off the GTK loop).',
    ('sms.py', '_run_mmcli'):
        'SMS worker threads (send/history), documented worker-only.',
    ('sound.py', '_loop_worker'):
        'ringtone loop worker thread.',
    # All nmcli/pactl helpers above route through this one chokepoint, which
    # settings surfaces only ever invoke via _run_bg (spot-checked); direct
    # main-thread callers of the helpers would evade this site-based check.
    ('system_settings.py', '_run'):
        'sole subprocess chokepoint; invoked via _run_bg workers only.',
    ('system_settings.py', 'battery_status'):
        'settings _run_bg worker.',
    ('system_settings.py', 'list_bt_devices'):
        'settings _run_bg worker.',
    ('system_settings.py', '_managed_objects'):
        'settings _run_bg worker.',
    ('system_settings.py', '_get_prop'):
        'settings _run_bg worker.',
    ('system_settings.py', '_set_prop'):
        'settings _run_bg worker (user-initiated pick).',
    ('system_settings.py', '_register_agent'):
        'settings pairing worker.',
    ('system_settings.py', '_unregister_agent'):
        'settings pairing worker.',
    ('system_settings.py', '_start_bt_discovery_sync'):
        'start_bt_discovery worker thread.',
    ('system_settings.py', 'pair_bt_device'):
        'settings _run_bg worker (user-initiated pair).',
    ('system_settings.py', 'connect_bt_device'):
        'settings _run_bg worker (user-initiated connect).',
    ('system_settings.py', 'disconnect_bt_device'):
        'settings _run_bg worker (user-initiated disconnect).',
    ('system_settings.py', 'forget_bt_device'):
        'settings _run_bg worker (user-initiated forget).',
    ('system_settings.py', 'systemd_unit_state'):
        'settings _run_bg worker (unprivileged state read).',
    ('system_status.py', 'wifi_ssid'):
        'status_line_async worker and tile SSID detail source, --rescan no.',
    ('system_status.py', 'modem_paths'):
        'status_line_async worker.',
    ('system_status.py', 'mobile_signal'):
        'status_line_async worker.',
    ('update_checker.py', 'count_updates'):
        'update-check worker thread.',
    ('update_checker.py', '_headless_worker'):
        'headless owner-script worker thread; lines stream via GLib.idle_add.',
    ('weather.py', '_geoclue_fix_sync'):
        'weather auto-fix worker thread (weather-locate); bounded GeoClue calls.',
    ('wayland_bindings.py', '_run_scanner'):
        'xx-wm-wayland-bindings worker; cached per pywayland version.',
    ('output_capture.py', '_run'):
        'grim worker thread spawned by capture(); never runs on the GTK loop.',
    ('xxdb_daemon.py', '_default_shell_runner'):
        'xxdb service process, no GTK main loop; runner injectable.',
    ('xxdb_daemon.py', '_default_screenshot'):
        'xxdb service process, no GTK main loop; runner injectable.',
    ('xxdb_daemon.py', '_default_sudo_check'):
        'xxdb service process, no GTK main loop; runner injectable.',
    ('xxdb_daemon.py', '_journalctl_snapshot'):
        'xxdb service process, no GTK main loop.',
    ('xxdb_daemon.py', '_cmd_install'):
        'xxdb service process, no GTK main loop.',
    ('xx_apps_update.py', '_default_run'):
        'xx-apps-update bindir CLI, no GTK main loop; runner injectable.',
}

_POPENISH = {'Popen', '_spawn', '_popen', 'popen_factory'}


def _popen_names(tree: ast.AST) -> set[str]:
    """Names that may hold a Popen handle: assigned from a Popen-ish call."""
    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def _call_target(self, node: ast.AST) -> str | None:
            if not isinstance(node, ast.Call):
                return None
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _POPENISH:
                return func.attr
            if isinstance(func, ast.Name) and func.id in _POPENISH:
                return func.id
            return None

        def _record(self, target: ast.AST) -> None:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add('@' + target.attr)

        def visit_Assign(self, node: ast.Assign) -> None:
            if self._call_target(node.value) is not None:
                for target in node.targets:
                    self._record(target)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None and self._call_target(node.value) is not None:
                self._record(node.target)
            self.generic_visit(node)

    Visitor().visit(tree)
    return names


def _iter_sites(path: Path) -> list[tuple[int, str, str, str]]:
    """(line, kind, enclosing function, source line) for each banned call."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    popen_names = _popen_names(tree)
    sites: list[tuple[int, str, str, str]] = []
    lines = path.read_text(encoding='utf-8').splitlines()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            kind: str | None = None
            if isinstance(func, ast.Attribute):
                if func.attr in ('call_sync', 'check_output'):
                    kind = func.attr
                elif func.attr == 'run' and isinstance(func.value, ast.Name) \
                        and func.value.id == 'subprocess':
                    kind = 'subprocess.run'
                elif func.attr == 'wait':
                    recv = func.value
                    if isinstance(recv, ast.Name) and (
                            recv.id == 'proc' or recv.id in popen_names):
                        kind = 'Popen.wait'
                    elif isinstance(recv, ast.Attribute) and (
                            '@' + recv.attr in popen_names):
                        kind = 'Popen.wait'
            scope = self.stack[-1] if self.stack else '<module>'
            if kind is not None:
                text = lines[node.lineno - 1].strip() if node.lineno else ''
                sites.append((node.lineno or 0, kind, scope, text))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


def main() -> int:
    failures: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in sorted(SRC.glob('*.py')):
        for lineno, kind, scope, text in _iter_sites(path):
            key = (path.name, scope)
            if key in ALLOW:
                used.add(key)
                continue
            failures.append(
                f'{path.name}:{lineno}: {kind} in {scope}() '
                f'reachable from main-loop code: {text}')
    stale = sorted(set(ALLOW) - used)
    if stale:
        failures.append(
            'stale allow-list entries (match no call site): '
            + ', '.join(f'{name}::{scope}' for name, scope in stale))
    if failures:
        print('main-loop blocking-call violations:')
        for failure in failures:
            print(f'  {failure}')
        print(f'\n{len(failures)} violation(s). Move the call to a worker '
              'thread or allow-list it with a one-line justification.')
        return 1
    print(f'main-loop block check OK '
          f'({len(used)}/{len(ALLOW)} allow-list entries exercised).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
