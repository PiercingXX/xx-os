#!/usr/bin/env python3
"""Fail when blocking calls are reachable from GTK main-loop code.

Flags, anywhere in ``launcher/src`` except inside the allow-listed worker
helpers below: ``subprocess.run`` / ``call`` / ``check_call`` /
``check_output``, a name bound to one of those (``runner = run or
subprocess.run``, ``def f(run=subprocess.run)``), ``Popen(...).wait()`` and
``.communicate()``, D-Bus ``call_sync`` and ``Gio.bus_get_sync``,
``time.sleep``, ``urlopen`` and socket ``recv``. Fire-and-forget ``Popen``
(never waited) is allowed everywhere.

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
        'backlight worker (run_backlight_io: ALS steps, slider fallback).',
    ('als_brightness.py', 'login1_set_brightness'):
        'backlight worker (same chain, and display_manager._brightnessctl).',
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
    ('app_standby.py', 'query_mpris_players'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', '_name_owner_pid'):
        'standby exemption-refresh worker thread (via query_mpris_players).',
    ('app_standby.py', 'query_calls_owner_pid'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', 'query_battery'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', '_default_pw_dump'):
        'standby exemption-refresh worker thread.',
    ('app_standby.py', '_ensure_dbus_units_sync'):
        'ON the main loop in _freeze: one ListUnits capped at 500 ms, only '
        'for an app with no pids, scopes or known units yet.',
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
    ('dialer.py', '_run_mmcli'):
        'telephony worker threads (place-call / emergency paths).',
    ('dialer.py', 'get_modem_emergency_numbers'):
        'documented worker-only; lock-screen modem thread.',
    ('dialer.py', 'ensure_radio_on'):
        'place_emergency_call worker (lock-screen emg-call / dialer thread).',
    ('dialer.py', 'modem_sim_missing'):
        'place_emergency_call worker, after a failed emergency dial.',
    ('display_manager.py', '_detect_output'):
        'backlight worker (via _wlopm); result cached for later wlopm calls.',
    ('display_manager.py', '_wlopm'):
        'backlight worker (_blank_io/_wake_io via _run_io).',
    ('display_manager.py', '_brightnessctl'):
        'backlight worker (_blank_io/_wake_io/_restore_backlight via _run_io).',
    ('display_manager.py', '_set_volume'):
        'volume worker (_change_volume job via _run_volume_io).',
    ('display_manager.py', '_get_volume_pct'):
        'volume worker (_change_volume job via _run_volume_io).',
    ('home_launcher.py', '_waydroid_session_running'):
        'waydroid-launch worker thread (moved off the home tap path).',
    ('lock_screen.py', '_fp_dbus_enrolled'):
        'fingerprint probe worker (_fp_probe_sync).',
    ('lock_screen.py', '_fp_list_enrolled'):
        'fingerprint probe worker (_fp_probe_sync).',
    ('lock_screen.py', '_fp_thread'):
        'fingerprint verify worker thread.',
    ('modem_monitor.py', 'resolve_modem_path'):
        'telephony workers (dial/sms threads).',
    ('modem_monitor.py', 'voice_create_and_start'):
        'telephony worker (dialer _place_call thread).',
    ('modem_monitor.py', '_delete_created_call'):
        'voice_create_and_start cleanup after a failed Start; same worker.',
    ('notification_shade.py', '_external_daemon_owns_notifications'):
        'external-daemon probe worker, posts back via _post_to_main.',
    ('power_menu.py', 'power_action_sync'):
        'power_action_async worker thread; sync export kept for tests.',
    ('privacy_monitor.py', '_default_pw_dump_once'):
        'privacy monitor thread.',
    ('privacy_monitor.py', '_run'):
        'privacy monitor thread (pw-dump child lifecycle).',
    ('privacy_monitor.py', '_end_child'):
        'privacy monitor thread (child reap, 1 s cap).',
    ('tailscale.py', '_run'):
        'tile query worker (_query_tile_states) and toggle worker (_set_tailscale).',
    ('quick_actions.py', '_nm_get'):
        'tile query worker (_query_tile_states).',
    ('quick_actions.py', '_nm_set'):
        'tile toggle worker (_toggle_on_worker).',
    ('quick_actions.py', '_nm_has_wifi_device'):
        'hardware-gate probe worker.',
    ('quick_actions.py', '_bluez_get'):
        'tile query worker (_query_tile_states).',
    ('quick_actions.py', '_bluez_set'):
        'tile toggle worker (_toggle_on_worker).',
    ('quick_actions.py', '_get_bt_device_name'):
        'tile detail worker (_query_tile_details).',
    ('quick_actions.py', '_active_hotspot_path'):
        'hotspot gate probe worker.',
    ('quick_actions.py', '_toggle_hotspot'):
        'tile toggle worker (_toggle_on_worker).',
    ('quick_actions.py', 'active_clients'):
        'location/hotspot availability probe worker.',
    ('quick_actions.py', 'available'):
        'location/hotspot availability probe worker.',
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
    ('sound.py', '_loop_worker'):
        'ringtone loop worker thread.',
    # All nmcli/pactl helpers above route through this one chokepoint, which
    # settings surfaces only ever invoke via _run_bg (spot-checked); direct
    # main-thread callers of the helpers would evade this site-based check.
    ('system_settings.py', '_run'):
        'sole subprocess chokepoint; invoked via _run_bg workers only.',
    ('system_settings.py', '_nm_call'):
        'APN apply worker (settings_page._apply_apn _work) only.',
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
    # xxdb runs inside the shell: start() on the controller's _run_bg
    # worker, every command on its connection thread (_serve ->
    # _run_session -> _cmd_*), pty output on a _pump_pty thread.
    ('xxdb_daemon.py', '_default_shell_runner'):
        'xxdb connection thread (_cmd_shell); runner injectable.',
    ('xxdb_daemon.py', '_default_screenshot'):
        'xxdb connection thread (_cmd_screenshot); provider injectable.',
    ('xxdb_daemon.py', '_default_sudo_check'):
        'xxdb connection thread (_cmd_install); check injectable.',
    ('xxdb_daemon.py', '_journalctl_snapshot'):
        'xxdb connection thread (_cmd_logcat).',
    ('xxdb_daemon.py', '_cmd_install'):
        'xxdb connection thread.',
    ('xxdb_daemon.py', 'ensure_cert'):
        'xxdb start(), on the controller enable worker (_run_bg).',
    ('xxdb_daemon.py', 'reap'):
        'xxdb _pump_pty thread: 50 ms polls for at most the 2 s grace.',
    ('xxdb_daemon.py', '_run_session'):
        'xxdb connection thread (_serve); the TLS socket read loop.',
    ('xxdb.py', 'interface_entries'):
        'xxdb start(), on the controller enable worker (_run_bg).',
    ('xxdb_input.py', 'nap'):
        'xxdb connection thread (_cmd_input -> inject): swipe step pacing.',
    ('xx_apps_update.py', '_default_run'):
        'xx-apps-update bindir CLI, never imported by the shell; '
        'runner injectable.',
    ('xx_apps_update.py', '_run_git'):
        'xx-apps-update bindir CLI, never imported by the shell; '
        'runner injectable.',
    # Gio.bus_get_sync returns the process-wide connection. The session
    # one is held by Gtk.Application from registration on; the system one
    # is opened at startup by main._setup_logind and held for the shell's
    # life by its sleep-delay inhibitor. Later calls return the cached
    # connection without I/O.
    ('main.py', '_setup_logind'):
        'do_startup, before any window: the one system bus connect.',
    ('app_standby.py', '_default_session_bus'):
        'session bus singleton (cached; see above).',
    ('app_standby.py', '_default_system_bus'):
        'system bus singleton (cached; see above).',
    ('call_screen.py', 'consult_check_number'):
        'session bus singleton (cached); the CheckNumber call is async.',
    ('call_screen.py', 'note_call'):
        'session bus singleton (cached); the NoteCall call is async.',
    ('call_ui.py', '_bus'):
        'session/system bus singletons (cached; see above).',
    ('call_ui.py', '_default_dtmf'):
        'system bus singleton (cached); the DTMF call is async.',
    ('captions.py', 'connect'):
        'quick-actions worker (_connect_captions_async); session bus.',
    ('capture.py', 'share_file'):
        'session bus singleton (cached); the portal call is async.',
    ('dialer.py', '_system_bus'):
        'telephony workers (dial / emergency paths); system bus.',
    ('display_manager.py', '_get_bus'):
        'system bus singleton (cached), memoized on the manager.',
    ('first_boot.py', 'set_timezone_async'):
        'system bus singleton (cached); the timedated call is async.',
    ('media_controls.py', '_init_bus'):
        'session bus singleton (cached); MPRIS calls are async.',
    ('modem_monitor.py', '_sync_system_bus'):
        'telephony workers (resolve_modem_path, voice_create_and_start).',
    ('modem_monitor.py', '_init_bus'):
        'system bus singleton (cached); ModemManager calls are async.',
    ('notif_daemon.py', '_start'):
        'session bus singleton (cached), idle after construction.',
    ('notification_shade.py', '_session_bus'):
        'session bus singleton (cached; see above).',
    ('osk.py', '_link'):
        'session bus singleton (cached); the keyboard calls are async.',
    ('power_menu.py', 'query_can_suspend_async'):
        'system bus singleton (cached); CanSuspend is async.',
    ('quick_actions.py', '_dbus_system'):
        'tile query/toggle workers (_nm_*/_bluez_* helpers); system bus.',
    ('settings_device.py', '_modem_props_sync'):
        'settings _run_bg worker (device summary).',
    ('settings_device.py', 'battery_health_sync'):
        'settings _run_bg worker (settings_page refresh _work).',
    ('settings_display.py', '_profile_bus'):
        'power-profile probe worker (probe_power_state via _run_bg).',
    ('settings_display.py', 'read_power_full'):
        'power-profile probe worker (probe_power_state via _run_bg).',
    ('settings_display.py', 'set_power_profile'):
        'apply_profile_async _work on _run_bg.',
    ('settings_permissions.py', 'query_store_maps'):
        'settings run_bg worker (query_all).',
    ('settings_permissions.py', 'apply_store_perm'):
        'settings run_bg worker (toggle and reset).',
    ('smart_lock.py', '_query_objects'):
        'system bus singleton (cached); GetManagedObjects is async.',
    ('smart_lock.py', 'start'):
        'system bus singleton (cached); every query after it is async.',
    ('system_settings.py', 'apply_apn'):
        'APN apply worker (settings_page._apply_apn _work).',
    ('system_settings.py', '_bluez_bus'):
        'settings _run_bg workers (bluetooth list/pair/power).',
    ('system_settings.py', '_systemd_bus'):
        'settings _run_bg worker (systemd_unit_state).',
    # Other blocking shapes.
    ('capture.py', '_scrolling_work'):
        'scrolling-screenshot worker (_spawn_worker): settle between swipes.',
    ('config.py', 'install_font_file'):
        'font import worker (settings_launcher._install_font via _run_bg).',
    ('dialer.py', 'place_emergency_call'):
        'lock-screen emg-call thread: radio-up retry pause.',
    ('ipc.py', '_read_conn'):
        'non-blocking socket (setblocking(False)): recv never waits.',
    ('settings_updates.py', 'run_xx_apps_check'):
        'settings update-check workers (_run_bg _work).',
    ('tailscale.py', 'up'):
        'tile toggle worker (quick_actions._set_tailscale).',
    ('weather.py', '_http_fetch'):
        'weather refresh worker thread (_refresh_worker).',
    ('xxwm_backend.py', 'request'):
        'request_async and the state watcher threads only.',
}


_POPENISH = {'Popen', '_spawn', '_popen', 'popen_factory'}
_RUNNERS = {'run', 'call', 'check_call', 'check_output'}
_RECV = {'recv', 'recv_into', 'recvfrom', 'recvmsg'}


def _is_subprocess_runner(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr in _RUNNERS
            and isinstance(node.value, ast.Name)
            and node.value.id == 'subprocess')


def _runner_aliases(tree: ast.AST) -> set[str]:
    """Names bound to a subprocess runner: ``runner = run or
    subprocess.run`` or a parameter defaulting to ``subprocess.run``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                _is_subprocess_runner(sub) for sub in ast.walk(node.value)):
            names.update(t.id for t in node.targets
                         if isinstance(t, ast.Name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            positional = args.posonlyargs + args.args
            pairs = list(zip(positional[len(positional)
                                        - len(args.defaults):],
                             args.defaults, strict=True))
            pairs += [(a, d) for a, d in zip(args.kwonlyargs,
                                              args.kw_defaults, strict=True)
                      if d is not None]
            names.update(a.arg for a, d in pairs
                         if _is_subprocess_runner(d))
    return names


def _from_imports(tree: ast.AST, module: str, wanted: set[str]) -> set[str]:
    """Local names of ``from <module> import <wanted>``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(a.asname or a.name for a in node.names
                         if a.name in wanted)
    return names


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
    aliases = _runner_aliases(tree)
    sleeps = _from_imports(tree, 'time', {'sleep'})
    urlopens = (_from_imports(tree, 'urllib.request', {'urlopen'})
                | {'urlopen'})
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
                if func.attr in ('call_sync', 'check_output',
                                 'bus_get_sync', 'communicate', 'urlopen'):
                    kind = func.attr
                elif _is_subprocess_runner(func):
                    kind = 'subprocess.' + func.attr
                elif func.attr == 'sleep' and isinstance(
                        func.value, ast.Name) and func.value.id == 'time':
                    kind = 'time.sleep'
                elif func.attr in _RECV:
                    kind = 'socket.' + func.attr
                elif func.attr == 'wait':
                    recv = func.value
                    if isinstance(recv, ast.Name) and (
                            recv.id == 'proc' or recv.id in popen_names):
                        kind = 'Popen.wait'
                    elif isinstance(recv, ast.Attribute) and (
                            '@' + recv.attr in popen_names):
                        kind = 'Popen.wait'
            elif isinstance(func, ast.Name):
                if func.id in aliases:
                    kind = f'subprocess runner {func.id}'
                elif func.id in sleeps:
                    kind = 'time.sleep'
                elif func.id in urlopens:
                    kind = 'urlopen'

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
