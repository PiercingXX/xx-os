#!/usr/bin/env python3

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gio

from first_boot import FirstBootWizard
from ipc import IPCServer, parse_page_command, suppress_during_tour
from notif_daemon import NotificationDaemon
from shell_log import get_logger, setup_logging
from window import ShellWindow

_log = get_logger('main')


def drop_layer_shell_preload(environ=os.environ) -> None:
    """Keep the gtk4-layer-shell LD_PRELOAD out of every app the shell
    launches. It is loaded into this process already; children that are
    not GTK4 (Firefox, GTK3 apps) abort when they inherit it."""
    kept = [lib for lib in environ.get('LD_PRELOAD', '').split(':')
            if lib and 'libgtk4-layer-shell' not in lib]
    if kept:
        environ['LD_PRELOAD'] = ':'.join(kept)
    else:
        environ.pop('LD_PRELOAD', None)


drop_layer_shell_preload()


def drop_portal_opt_out(environ=os.environ) -> None:
    """Undo the shell-only portal opt-out set by the xx-wm wrapper.

    The shell themes itself, so it starts with ADW_DISABLE_PORTAL=1 and
    GDK_DEBUG=no-portals: a portal still bound to a previous session's
    display made GTK startup wait out its Settings timeout (90 s of black
    screen on the FLX1). Apps the shell launches must get the portal
    (it carries the dark color-scheme), so both are dropped once GTK and
    libadwaita have initialised."""
    environ.pop('ADW_DISABLE_PORTAL', None)
    flags = [flag for flag in environ.get('GDK_DEBUG', '').split(',')
             if flag and flag != 'no-portals']
    if flags:
        environ['GDK_DEBUG'] = ','.join(flags)
    else:
        environ.pop('GDK_DEBUG', None)


class XXWMApplication(Adw.Application):
    def __init__(self, replay_welcome: bool = False) -> None:
        super().__init__(
            application_id='io.piercingxx.XXWM',
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._replay_welcome = replay_welcome
        self._tour_visible = False
        self._shell: ShellWindow | None = None
        # IPC verbs that arrive before the shell window is first presented
        # (e.g. the a11y-bus timeout stalls the first present ~25 s) wait
        # here and replay in order once the window is up, instead of being
        # silently dropped.
        self._pending_ipc: list[str] = []
        self._ipc: IPCServer | None = None
        self._notif_daemon: NotificationDaemon | None = None
        self._usage_tracker = None
        self._standby = None
        self._privacy_monitor = None
        self._xxdb = None
        self._banner = None
        from focus_mode import HeldNotifications
        self._held_notifications = HeldNotifications()
        GLib.timeout_add_seconds(30, self._tick_focus_release)

    def do_activate(self) -> None:
        drop_portal_opt_out()
        if self.props.active_window is not None:
            self.props.active_window.present()
            return

        # The configured font applies launcher-wide, wizard included
        from config import ShellConfig
        from font_theme import apply_global_font
        apply_global_font(ShellConfig().font_family)

        self._ipc = IPCServer(self._on_ipc_command)
        _setup_logind(self._on_sleep)
        self._notif_daemon = NotificationDaemon(
            on_notify=self._on_notification,
            on_close=self._on_notification_closed,
        )

        if FirstBootWizard.is_needed():
            wizard = FirstBootWizard(on_complete=self._show_shell)
            wizard.set_application(self)
            wizard.present()
        else:
            self._show_shell()

    def _show_shell(self) -> None:
        self._shell = ShellWindow(self)
        self._shell.present()
        _log.info('shell window presented')
        self._replay_pending_ipc()
        self._start_polkit_agent()
        self._start_xxdb()

        # App standby (Doze parity): track shell-launched apps in systemd
        # scopes, freeze the idle, thaw on return. The window launch paths
        # reach the controller as shell.standby; the tile list reaches the
        # same instance through the process-wide registry.
        from app_standby import configure_standby, install_saver_check_gates
        self._standby = configure_standby(
            config=self._shell.config,
            network_denied=lambda: list(
                self._shell.config.network_denied_apps or ()),
        )
        self._shell.standby = self._standby
        self._standby.attach_daemon(self._notif_daemon)
        self._standby.start()
        install_saver_check_gates(self._shell, self._standby)

        # Privacy indicators (mic/camera/location): one monitor feeds the
        # home status strip and the shade header. Location users come from
        # the shade panel's GeoClue agent state, so no second agent is
        # ever registered against the single-agent daemon.
        from privacy_monitor import PrivacyMonitor
        self._privacy_monitor = PrivacyMonitor(
            on_change=self._on_privacy_changed,
            location_provider=self._get_location_users,
        )
        self._privacy_monitor.start()

        # Warm swipe-bound apps in the real session only — under a host
        # shell (dev runs over Phosh) the preloads would grab the screen.
        # Also opt-in via config (default off): preloading is counter to the
        # minimalism directive on weak hardware.
        import os
        from config import should_preload_gesture_apps
        if should_preload_gesture_apps(
                self._shell.config, bool(os.environ.get('XX_WM_SESSION'))):
            GLib.timeout_add_seconds(8, self._preload_gesture_apps)

        # Display power management: power button + fingerprint wake the screen.
        # Must be started after shell window exists so on_wake can show lock screen.
        from display_manager import DisplayManager, _take_screenshot
        from media_controls import MprisWatcher
        from power_menu import PowerMenu
        from hud import Hud
        # Same live-config rule as window.py's own PowerMenu: without it this
        # instance keeps snapshot theming while hot reloads reach the rest.
        self._power_menu = PowerMenu(config=self._shell.config, on_lock=self._shell._show_lock_screen)
        self._power_menu.set_application(self)
        # Volume/brightness HUD overlay (plan T3/T4): flashes a level on the
        # layer-shell window after a hardware key press. Silent no-op when GTK
        # is unavailable, so wiring it in can never break a headless device.
        self._hud = Hud(config=self._shell.config)
        self._hud.set_application(self)
        # Screen-time statistics (data layer only, no UI yet): foreground
        # seconds per app plus unlock/notification counts. Any failure here
        # degrades to no stats; the shell itself is never affected.
        self._usage_tracker = _make_usage_tracker(self._shell.config)

        # MPRIS media controls: one shared watcher feeds the shade card
        # (via window._ensure_shade) and the headset/media keys below.
        self._mpris = MprisWatcher()
        self._display_mgr = DisplayManager(
            # Blank-driven auto-lock: the shell locks after
            # auto_lock_timeout seconds of darkness, never on wake.
            # One fan-out: shell auto-lock, standby freeze timer, screen time.
            on_blank_changed=self._on_display_blank,
            on_power_menu=self._power_menu.show_menu,
            on_screenshot=_take_screenshot,
            on_fingerprint=self._shell.try_fingerprint_unlock,
            hud=self._hud,
            # Live-config read so hot reloads apply on the next reset_idle.
            get_blank_timeout=lambda: self._shell.config.display_blank_timeout,
            on_power_press=self._shell.silence_ringer,
            on_media_key=self._mpris.handle_media_key,
        )
        self._shell._display_mgr = self._display_mgr
        # Screen capture wiring: screenshot/record notifications reach the
        # shade through the live shell; blank state feeds the recorder's
        # auto-stop so recordings never run against a dark screen.
        import capture
        capture.configure(shell_provider=lambda: self._shell)
        capture.set_blanked(False)
        self._wire_usage_lock()

        # Arrow overlay for back gesture feedback (lisgd fires gesture.back via IPC)
        from back_gesture import BackGestureLayer
        back = BackGestureLayer(config=self._shell.config)
        back.set_application(self)
        self._shell._back_layer = back

        self._start_status_bar()

        if self._replay_welcome:
            self._replay_welcome = False
            self._show_welcome_tour()

    def _start_status_bar(self) -> None:
        """Top status bar: reserves the camera area (exclusive zone) over
        home and apps; a pull down on it opens the shade, the same verb
        lisgd's top-edge swipe fires. Built only after first boot, so the
        wizard never shows it. No layer shell (nested dev run): no bar."""
        shell = self._shell
        if shell is None:
            return
        try:
            from status_bar import create_status_bar
            bar = create_status_bar(config=shell.config,
                                    on_open_shade=shell._show_shade)
        except Exception as exc:
            _log.warning('status bar unavailable: %s', exc)
            return
        if bar is None:
            return
        bar.set_application(self)
        shell._status_bar = bar
        # Seed from the shell's own sources; later ticks keep it current.
        snapshot = getattr(shell, '_status_snapshot', None)
        if snapshot:
            bar.set_status(snapshot)
        shell._refresh_clock()
        shell._sync_status_bar()

    def _replay_pending_ipc(self) -> None:
        """Run IPC verbs queued before the first present, in arrival order."""
        pending = getattr(self, '_pending_ipc', None) or []
        self._pending_ipc = []
        for command in pending:
            self._on_ipc_command(command)

    def _start_polkit_agent(self) -> None:
        """Register the in-shell polkit authentication agent for the
        session, so helper auth_admin_keep actions (and NetworkManager /
        Flatpak system prompts) can ask for the device password. A
        missing authority degrades to no agent; the shell itself is
        never affected."""
        self._polkit_agent = None
        try:
            import polkit_agent
            self._polkit_agent = polkit_agent.start_agent()
        except Exception as exc:
            _log.warning('polkit agent unavailable: %s', exc)
            self._polkit_agent = None

    def _wire_usage_lock(self) -> None:
        """Feed shell lock transitions to the usage tracker."""
        tracker = self._usage_tracker
        if tracker is None or self._shell is None:
            return
        try:
            orig_show = self._shell._show_lock_screen
            orig_dismiss = self._shell._dismiss_lock_screen
        except AttributeError as exc:
            _log.warning('usage stats lock hook unavailable: %s', exc)
            return

        def _show_lock_noted() -> None:
            orig_show()
            try:
                tracker.set_locked(True)
            except Exception as exc:
                _log.warning('usage stats lock hook failed: %s', exc)

        def _dismiss_lock_noted() -> None:
            orig_dismiss()
            try:
                tracker.set_locked(False)
            except Exception as exc:
                _log.warning('usage stats unlock hook failed: %s', exc)

        self._shell._show_lock_screen = _show_lock_noted  # type: ignore[method-assign]
        self._shell._dismiss_lock_screen = _dismiss_lock_noted  # type: ignore[method-assign]

    def _show_welcome_tour(self) -> None:
        self._tour_visible = True
        self._sync_status_bar()
        wizard = FirstBootWizard(on_complete=self._end_welcome_tour, tour_only=True)
        wizard.set_application(self)
        wizard.present()

    def _end_welcome_tour(self) -> None:
        self._tour_visible = False
        self._sync_status_bar()

    def _sync_status_bar(self) -> None:
        shell = getattr(self, '_shell', None)
        sync = getattr(shell, '_sync_status_bar', None)
        if callable(sync):
            sync()

    def _on_notification(
        self, notif_id: int, app_name: str, summary: str, body: str,
        desktop_entry: str, hints: dict, actions: list | None = None,
    ) -> None:
        _log.info('notification %d from %s: %s', notif_id, app_name, summary)
        if self._shell is None:
            return
        # Standby 60 s exemption: remember which app owns this notification
        # so an action invocation marks it as recently used.
        standby = getattr(self, '_standby', None)
        if standby is not None:
            try:
                standby.note_notification_shown(
                    notif_id, desktop_entry or app_name)
            except Exception:
                pass
        # "Disable for…" mute: notifications from a muted app are discarded
        # outright until the deadline passes
        config = getattr(self._shell, 'config', None)
        if config is not None and (config.is_app_muted(desktop_entry)
                                   or config.is_app_muted(app_name)):
            _log.debug('notification %d dropped: %s is muted', notif_id, desktop_entry or app_name)
            return

        category = str(hints.get('category', ''))
        urgency = hints.get('urgency', 1)
        is_alarm = category.startswith('alarm') or urgency == 2

        # Focus: notifications from paused apps are held outright — no sound,
        # not in the shade — and released in one batch when focus ends
        focus = getattr(self._shell, 'focus_state', None)
        if focus is not None and focus.is_paused_app(desktop_entry) and not is_alarm:
            self._held_notifications.hold(
                notif_id, app_name, summary, body, desktop_entry,
                actions if actions is not None else [],
                hints if isinstance(hints, dict) else {})
            _log.debug('notification %d held by Focus: %s', notif_id, desktop_entry)
            return

        # DnD: notifications still collect in the shade, but silently —
        # no sound. Alarms are always exempt.
        dnd = getattr(self._shell, 'dnd_state', None)
        dnd_silenced = dnd is not None and dnd.is_active() and not is_alarm

        if (config is not None and config.sound_notifications
                and not dnd_silenced and not hints.get('suppress-sound')):
            import sound
            sound.play('notify.ogg')

        # Screen-time count only (no content stored): notifications that
        # survived the mute/Focus discards. Muted and held notifications
        # never reach the user, so they are not counted.
        tracker = self._usage_tracker
        if tracker is not None:
            try:
                tracker.note_notification(desktop_entry or app_name)
            except Exception as exc:
                _log.warning('usage stats notification hook failed: %s', exc)

        # Create the shade on first notification so nothing is lost before
        # the user first opens it
        self._shell._ensure_shade().add_notification(
            notif_id, app_name, summary, body, desktop_entry,
            actions if actions is not None else [],
            hints if isinstance(hints, dict) else None)

        self._maybe_banner(
            notif_id, app_name, summary, body, desktop_entry,
            hints if isinstance(hints, dict) else {}, actions,
            dnd_active=dnd is not None and dnd.is_active(),
            is_alarm=is_alarm)

    def _banner_locked(self) -> bool:
        shell = self._shell
        is_locked = getattr(shell, 'is_locked', None)
        if callable(is_locked):
            try:
                return bool(is_locked())
            except Exception:
                pass
        lock = getattr(shell, '_lock_screen', None)
        try:
            if lock is not None and hasattr(lock, 'get_visible'):
                return bool(lock.get_visible())
        except Exception:
            pass
        return False

    def _ensure_banner(self):
        if self._banner is None:
            from banner import BannerWindow
            self._banner = BannerWindow(
                config=getattr(self._shell, 'config', None),
                notif_daemon=self._notif_daemon,
            )
            try:
                self._banner.set_application(self)
            except Exception:
                pass
            try:
                from window import resolve_theme
                self._banner.apply_theme(
                    resolve_theme(self._shell.config))
            except Exception:
                pass
        return self._banner

    def _shade_visible(self) -> bool:
        shell = self._shell
        shade = getattr(shell, '_shade', None)
        try:
            if shade is not None and hasattr(shade, 'get_visible'):
                return bool(shade.get_visible())
        except Exception:
            pass
        return False

    def _maybe_banner(self, notif_id, app_name, summary, body,
                      desktop_entry, hints, actions,
                      dnd_active: bool = False,
                      is_alarm: bool = False) -> None:
        from banner import should_show_banner
        raw_urgency = (hints or {}).get('urgency', 1)
        try:
            urgency = int(raw_urgency)
        except (TypeError, ValueError):
            urgency = 1
        if not should_show_banner(
                dnd_active, is_alarm, urgency, self._banner_locked(),
                hints, notif_id,
                shade_visible=self._shade_visible()):
            return
        try:
            self._ensure_banner().show_notification(
                notif_id, app_name, summary, body, desktop_entry,
                actions if actions is not None else [],
                hints if isinstance(hints, dict) else None)
        except Exception:
            pass

    def _tick_focus_release(self) -> bool:
        focus = getattr(self._shell, 'focus_state', None) if self._shell else None
        if focus is None or len(self._held_notifications) == 0:
            return True
        if focus.is_active():
            return True
        for held in self._held_notifications.release_all():
            self._shell._ensure_shade().add_notification(*held)
        return True

    def _on_notification_closed(self, notif_id: int) -> None:
        standby = getattr(self, '_standby', None)
        if standby is not None:
            try:
                standby.note_notification_closed(notif_id)
            except Exception:
                pass
        if self._shell is None:
            return
        shade = getattr(self._shell, '_shade', None)
        if shade is not None:
            # Daemon-driven removal: never echo back over D-Bus.
            shade.dismiss(notif_id, notify_daemon=False)

    # IPC verbs that must not open other surfaces while the lock screen
    # is up. `lock` stays allowed (idempotent re-lock); shade/switcher
    # hides still run so a stray visible surface can always be dismissed;
    # gesture.back does nothing under the lock.
    _LOCKED_SILENCED = frozenset({
        'shade.show', 'gesture.shade',
        'switcher.show', 'gesture.switcher',
        'gesture.home', 'gesture.keyboard', 'gesture.back',
        'welcome',
        'page.home', 'page.apps', 'page.settings',
    })

    def _ipc_silenced_while_locked(self, command: str) -> bool:
        """Page-driving verbs open surfaces, so they stay silenced under
        the lock like their gesture equivalents (settings.open covers the
        bare form and every settings.open:<section>)."""
        return (command in self._LOCKED_SILENCED
                or command == 'settings.open'
                or command.startswith('settings.open:'))

    def _on_ipc_command(self, command: str) -> None:
        _log.info('IPC command: %s', command)
        if self._shell is None:
            pending = getattr(self, '_pending_ipc', None)
            if pending is None:
                pending = self._pending_ipc = []
            pending.append(command)
            return
        if (self._shell.is_locked()
                and self._ipc_silenced_while_locked(command)):
            _log.info('IPC command %s ignored while locked', command)
            return
        if suppress_during_tour(command, getattr(self, '_tour_visible', False)):
            return
        if command == 'lock':
            GLib.idle_add(self._shell._show_lock_screen)
        elif command == 'shade.show':
            GLib.idle_add(lambda: self._shell._show_shade() if self._shell else None)
        elif command == 'shade.hide':
            shade = getattr(self._shell, '_shade', None)
            if shade:
                GLib.idle_add(shade.hide_shade)
        elif command == 'switcher.show':
            GLib.idle_add(lambda: self._shell._show_switcher() if self._shell else None)
        elif command == 'switcher.hide':
            switcher = getattr(self._shell, '_switcher', None)
            if switcher:
                GLib.idle_add(switcher.hide_switcher)
        elif command == 'gesture.back':
            GLib.idle_add(self._shell._handle_back)
            back = getattr(self._shell, '_back_layer', None)
            if back:
                GLib.idle_add(back.flash_back, True)
        elif command == 'gesture.home':
            GLib.idle_add(lambda: self._shell.go_home() if self._shell else None)
        elif command == 'gesture.shade':
            GLib.idle_add(lambda: self._shell._show_shade() if self._shell else None)
        elif command == 'gesture.keyboard':
            GLib.idle_add(lambda: self._shell._show_keyboard() if self._shell else None)
        elif command == 'gesture.switcher':
            GLib.idle_add(lambda: self._shell._show_switcher() if self._shell else None)
        elif command == 'welcome':
            GLib.idle_add(self._show_welcome_tour)
        else:
            # Page-driving verbs for rigs and scripts (no gesture timing):
            # page.home/apps/settings switch the stack, settings.open
            # reuses the settings deep-link.
            parsed = parse_page_command(command)
            if parsed is not None:
                kind, target = parsed
                if kind == 'page':
                    GLib.idle_add(lambda: self._show_ipc_page(target)
                                  if self._shell else None)
                else:
                    GLib.idle_add(lambda: self._open_ipc_settings(target)
                                  if self._shell else None)

    def _show_ipc_page(self, page: str) -> None:
        """page.home/apps/settings IPC verb: switch the stack page."""
        if self._shell is None:
            return
        if page == 'home':
            # Home also raises the shell above any app window, like the
            # gesture.home path; a bare stack switch would leave the app
            # on top.
            self._shell.go_home()
            return
        self._shell.stack.set_visible_child_name(page)
        # A page change that lands on the already-visible page emits no
        # notify signal, so recompute explicitly (a stale TOP layer
        # would bury the next externally launched app).
        self._shell._apply_layer_policy()

    def _open_ipc_settings(self, section: str | None) -> None:
        """settings.open[:section] IPC verb: the settings deep-link."""
        if self._shell is not None:
            self._shell.open_settings(section)

    def _preload_gesture_apps(self) -> bool:
        if self._shell:
            self._shell.preload_gesture_apps()
        return GLib.SOURCE_REMOVE

    def _get_location_users(self) -> set:
        """GeoClue clients holding a live fix, via the shade panel's
        agent state (worker thread only; never registers an agent)."""
        try:
            shade = getattr(self._shell, '_shade', None)
            panel = getattr(shade, 'quick_actions', None)
            location = getattr(panel, '_location', None)
            if location is None:
                return set()
            return set(location.active_clients() or ())
        except Exception as exc:
            _log.warning('privacy location query failed: %s', exc)
            return set()

    def _on_privacy_changed(self, snapshot: dict) -> None:
        """Monitor callback off the GTK thread: hop to the main loop."""
        try:
            GLib.idle_add(self._apply_privacy_snapshot, dict(snapshot))
        except Exception:
            pass

    def _apply_privacy_snapshot(self, snapshot: dict) -> bool:
        try:
            shell = self._shell
            if shell is not None:
                from privacy_monitor import indicators_enabled
                if not indicators_enabled(getattr(shell, 'config', None)):
                    snapshot = {}
                shell.set_privacy_snapshot(snapshot)
                shade = getattr(shell, '_shade', None)
                if shade is not None:
                    shade.set_privacy_snapshot(snapshot)
        except Exception as exc:
            _log.warning('privacy indicator update failed: %s', exc)
        return False

    def _on_display_blank(self, blanked: bool) -> None:
        """Fan-out for DisplayManager blank reports: the shell's auto-lock
        plus the standby controller's blank freeze timer."""
        try:
            import capture
            capture.set_blanked(blanked)
            if blanked:
                capture.get_recorder().stop(reason='screen blanked')
        except Exception as exc:
            _log.warning('capture blank hook failed: %s', exc)
        if self._shell is not None:
            self._shell.on_blank_changed(blanked)
        standby = getattr(self, '_standby', None)
        if standby is not None:
            try:
                standby.note_screen_blank(blanked)
            except Exception:
                pass
        monitor = getattr(self, '_privacy_monitor', None)
        if monitor is not None:
            try:
                monitor.set_screen_on(not blanked)
            except Exception:
                pass
        tracker = getattr(self, '_usage_tracker', None)
        if tracker is not None:
            try:
                tracker.on_blank_changed(blanked)
            except Exception as exc:
                _log.warning('usage stats blank hook failed: %s', exc)

    # -- wireless debugging (xxdb) --------------------------------------

    def _start_xxdb(self) -> None:
        """Create the xxdb controller: off on every restart unless the
        `xxdb_persistent` config key opts into remembering the switch."""
        if self._shell is None:
            return
        from xxdb_controller import XxdbController
        ctrl = XxdbController(
            self._shell.config,
            post=lambda fn: GLib.idle_add(fn),
        )
        ctrl.on_notify = self._on_xxdb_notify
        ctrl.on_unnotify = self._on_xxdb_unnotify
        shell = self._shell
        ctrl.on_state_changed = (
            lambda: GLib.idle_add(shell._refresh_xxdb_section))
        self._shell.xxdb_controller = ctrl
        self._xxdb = ctrl
        self._shell._refresh_xxdb_section()
        if ctrl.ensure_boot_state():
            ctrl.request_enable()
        GLib.timeout_add_seconds(60, self._tick_xxdb)

    def _tick_xxdb(self) -> bool:
        try:
            if self._xxdb is not None:
                self._xxdb.idle_check()
        except Exception:
            _log.exception('xxdb idle check failed')
        return True

    def _on_xxdb_notify(self, title: str, body: str) -> None:
        from xxdb_controller import NOTIF_ID
        if self._shell is None:
            return
        self._shell._ensure_shade().add_notification(
            NOTIF_ID, 'System', title, body, 'xx-wm',
            [('turn-off', 'Turn off', self._xxdb_turn_off)],
            {'resident': True})

    def _on_xxdb_unnotify(self) -> None:
        from xxdb_controller import NOTIF_ID
        if self._shell is None:
            return
        shade = getattr(self._shell, '_shade', None)
        if shade is not None:
            shade.dismiss(NOTIF_ID, notify_daemon=False)

    def _xxdb_turn_off(self) -> None:
        if self._xxdb is not None:
            self._xxdb.request_disable()

    def _on_sleep(self, sleeping: bool) -> None:
        if sleeping and self._shell:
            _log.info('system suspending — locking screen')
            GLib.idle_add(self._shell._show_lock_screen)

    def do_shutdown(self) -> None:
        standby = getattr(self, '_standby', None)
        if standby is not None:
            try:
                standby.shutdown_thaw()
            except Exception:
                pass
        shell = getattr(self, '_shell', None)
        config = getattr(shell, 'config', None)
        if config is not None:
            try:
                config.flush_launch_counts()
            except Exception:
                pass
        super().do_shutdown()


def _make_usage_tracker(config) -> object | None:
    """Build the screen-time tracker, or None when disabled/unavailable."""
    try:
        enabled = config.usage_stats_enabled
    except Exception:
        enabled = True
    if not enabled:
        return None
    try:
        from toplevel_manager import ToplevelManager
        from usage_stats import UsageTracker
        return UsageTracker(enabled=True, manager=ToplevelManager())
    except Exception as exc:
        _log.warning('usage stats unavailable: %s', exc)
        return None


def _setup_logind(on_sleep_callback: object) -> None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.signal_subscribe(
            'org.freedesktop.login1',
            'org.freedesktop.login1.Manager',
            'PrepareForSleep',
            '/org/freedesktop/login1',
            None,
            Gio.DBusSignalFlags.NONE,
            lambda _c, _s, _p, _i, _sig, params, _ud: on_sleep_callback(params.unpack()[0]),
            None,
        )
        _log.info('subscribed to logind PrepareForSleep')
    except GLib.Error as e:
        _log.warning('could not subscribe to logind: %s', e)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argv = list(argv if argv is not None else sys.argv)
    replay_welcome = '--welcome' in argv
    if replay_welcome:
        argv.remove('--welcome')
    app = XXWMApplication(replay_welcome=replay_welcome)
    return app.run(argv)


if __name__ == '__main__':
    raise SystemExit(main())
