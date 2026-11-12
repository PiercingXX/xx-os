#!/usr/bin/env python3

import os
import sys
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gio

from first_boot import FirstBootWizard
from ipc import (
    IPCServer, parse_page_command, queue_pending_command, suppress_during_tour,
)
from notif_daemon import NotificationDaemon
from shell_log import get_logger, setup_logging
from window import ShellWindow

_log = get_logger('main')

# Ceiling on holding a suspend for the lock screen's first paint: a blanked
# output may never paint, and logind gives up after InhibitDelayMaxSec (5 s).
_SLEEP_DELAY_SAFETY_MS = 1000

# How old a PIN entry that resumed setup may be for the shell built at
# the end of that wizard to skip its own first lock (take_setup_unlock).
SETUP_UNLOCK_GRACE_S = 300


def gesture_silenced_while_blanked(command: str, blanked: bool) -> bool:
    """Gesture verbs (lisgd and the bottom strip) are dropped while the
    display is blanked: touch stays live until the auto-lock fires, so a
    pocket swipe would otherwise go home or open recents unseen."""
    return blanked and command.startswith('gesture.')


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


class WizardTelephony:
    """Calls while the first-boot wizard is up.

    The modem monitor normally lives on the shell window, which only
    exists once setup is done, so a call during the wizard rang nowhere.
    This stand-in host runs the same CallWiring paths (incoming surface,
    screening, ringer, answer, hang-up, end) and counts as locked while
    the resumed-wizard lock is up, so the call surface maps above it.
    SMS go to TxxT or Chatty when either owns messages (started if
    installed but not running) and otherwise stay on the modem for the
    shell: there is no notification shade yet to post them in.
    close() hands over to the shell window's own monitor."""

    def __init__(self, app) -> None:
        from call_wiring import CallWiring
        from config import ShellConfig
        from dnd import DndState
        from modem_monitor import ModemMonitor

        self._app = app
        self.config = ShellConfig()
        self.dnd_state = DndState(self.config)
        self._call_ui = None
        self._call_bar = None
        self._display_mgr = None
        self._dialer = None
        self._calls_owner_warned = False
        self._chatty_owner_warned = False
        self._txtt_owner_warned = False
        self._sms_notif_seq = 0
        self._wiring = CallWiring(self)
        self._modem_monitor = ModemMonitor(
            on_incoming=self._wiring._on_call_incoming,
            on_answered=self._wiring._on_call_answered,
            on_ended=self._wiring._on_call_ended,
            on_calls_changed=self._wiring._on_calls_changed,
            on_sms=self._on_sms,
        )

    def get_application(self):
        return self._app

    def is_locked(self) -> bool:
        lock = getattr(self._app, '_wizard_lock', None)
        if lock is None:
            return False
        try:
            return bool(lock.get_visible())
        except Exception:
            return True

    def _ensure_call_surfaces(self) -> tuple:
        return self._wiring._ensure_call_surfaces()

    def _sync_call_inhibit(self, calls: list) -> None:
        self._wiring._sync_call_inhibit(calls)

    def _expand_call_ui(self) -> None:
        self._wiring._expand_call_ui()

    def _call_live(self) -> bool:
        return self._wiring._call_live()

    def _on_sms(self, _number: str, _text: str) -> bool:
        import modem_monitor
        for name in (modem_monitor.TXXT_BUS_NAME,
                     modem_monitor.CHATTY_BUS_NAME):
            if modem_monitor.bus_name_has_owner(name):
                break
            if modem_monitor.bus_name_activatable(name):
                modem_monitor.start_bus_name(name)
                break
        return False

    def close(self) -> None:
        monitor, self._modem_monitor = self._modem_monitor, None
        if monitor is not None:
            monitor.close()
        ui, self._call_ui = self._call_ui, None
        bar, self._call_bar = self._call_bar, None
        for surface, stop in ((ui, 'end_call'), (bar, 'hide_bar')):
            if surface is None:
                continue
            for method in (stop, 'destroy'):
                try:
                    getattr(surface, method)()
                except Exception:
                    pass


class XXWMApplication(Adw.Application):
    def __init__(self, replay_welcome: bool = False) -> None:
        super().__init__(
            application_id='io.piercingxx.XXWM',
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._replay_welcome = replay_welcome
        self._tour_visible = False
        self._shell: ShellWindow | None = None
        self._wizard = None
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
        self._screensaver = None
        self._auto_suspend = None
        self._wizard_lock = None
        self._wizard_telephony: WizardTelephony | None = None
        self._pin_gate_pending = False
        self._pin_gate_done = False
        self._pin_proven_at: float | None = None
        self._setup_finishing = False
        self._banner = None
        from focus_mode import HeldNotifications
        self._held_notifications = HeldNotifications()
        GLib.timeout_add_seconds(30, self._tick_focus_release)

    def do_activate(self) -> None:
        drop_portal_opt_out()
        # Re-activation (a second launch, D-Bus activation) must bring the
        # existing UI forward, never build another one. active_window alone
        # was not enough: layer-shell windows never become the active
        # window, so each activation stacked a new first-boot wizard or
        # shell (the Librem showed ~16 wizards that "wouldn't close").
        existing = (self._shell or self._wizard
                    or getattr(self, '_wizard_lock', None)
                    or self.props.active_window)
        if existing is not None:
            _log.info('activate: UI already up, presenting it')
            existing.present()
            return
        if getattr(self, '_pin_gate_pending', False):
            _log.info('activate: waiting for the PIN helper')
            return

        # The configured font applies launcher-wide, wizard included
        from config import ShellConfig
        from font_theme import apply_global_font
        config = ShellConfig()
        apply_global_font(config.font_family)

        self._ipc = IPCServer(self._on_ipc_command)
        # The delay inhibitor rides along: suspend waits until the lock
        # screen is drawn (see _on_sleep).
        self._sleep_delay = _setup_logind(self._on_sleep)
        self._notif_daemon = NotificationDaemon(
            on_notify=self._on_notification,
            on_close=self._on_notification_closed,
        )

        first_boot = FirstBootWizard.is_needed()
        if first_boot:
            # A marker the gate restores from the helper is saved: the
            # file it creates must still read as setup not finished.
            config.data.setdefault('setup_complete', False)
        self._gate_startup(config, first_boot)

    def _gate_startup(self, config, first_boot: bool) -> None:
        """Nothing is shown until it is known whether a PIN guards this
        session (helper_client.startup_pin_gate): a config.json that was
        deleted, reset or moved aside as corrupt has no marker, but the
        helper still holds the PIN, so the wizard and the shell both come
        up behind the lock. The decision lands in config (marker restored
        or assumed for this process), where _start_first_boot and the
        shell window read it. phoc's shield (-S) covers the wait; under
        ext-session-lock there is nothing on screen to cover."""
        import helper_client
        self._pin_gate_pending = True
        self.hold()

        def _decided(locked: bool) -> None:
            if not self._pin_gate_pending:
                return
            _log.info('startup PIN gate: %s',
                      'locked' if locked else 'no PIN')
            self._pin_gate_pending = False
            self._pin_gate_done = True
            try:
                if first_boot:
                    # Calls work during setup too: the shell window's
                    # modem monitor does not exist until the wizard is
                    # done. Started once the lock decision is made, so
                    # the call surface knows whether it sits above it.
                    self._start_wizard_telephony()
                    self._start_first_boot()
                else:
                    self._show_shell()
            finally:
                self.release()

        try:
            helper_client.startup_pin_gate(config, _decided,
                                           on_late=self._on_late_helper_pin)
        except Exception as exc:
            if not self._pin_gate_pending:
                raise  # the wizard or shell itself failed, not the gate
            _log.error('PIN gate failed: %s', exc)
            if helper_client.helper_installed():
                config.assume_pin_in_helper()
            _decided(True)

    def _on_late_helper_pin(self) -> None:
        """The helper reported a PIN after the gate let the session up
        unlocked (it looked uninstalled): lock what is on screen now."""
        if self._shell is not None:
            self._shell._show_lock_screen()
        elif self._wizard is not None and self._wizard_lock is None:
            self._lock_before_wizard()

    def _start_first_boot(self) -> None:
        """First-boot wizard. A wizard cut short after its PIN step (crash,
        reboot, deploy respawn), or one rerun because config.json was
        lost while the helper still holds the PIN (_gate_startup), comes
        back behind the lock screen: the PIN is entered before setup
        continues, phoc's shield (-S) lowers onto the lock rather than the
        wizard, and the wizard then skips its PIN step, so it can never
        overwrite the PIN."""
        from config import ShellConfig
        try:
            pin_set = ShellConfig().pin_hash is not None
        except Exception:
            pin_set = False
        if pin_set and self._lock_before_wizard():
            return
        self._present_wizard()

    def _present_wizard(self) -> None:
        self._wizard = FirstBootWizard(on_complete=self._wizard_done)
        self._wizard.set_application(self)
        self._wizard.present()

    def _lock_before_wizard(self) -> bool:
        """Show a standalone lock ahead of the resumed wizard; True when
        it is up (the wizard follows on unlock). The phoc report waits for
        the lock surface to map, exactly like window._lock_at_startup."""
        from config import ShellConfig
        from lock_screen import LockScreen, _shell_state
        reporter = _shell_state()
        if reporter is not None:
            reporter.require_lock(True)
        try:
            lock = LockScreen(on_unlock=self._wizard_unlocked,
                              config=ShellConfig())
            lock.set_application(self)
            lock.present()
            lock.on_shown()
        except Exception as exc:
            # The shield stays up (lock required, never mapped), so phoc
            # keeps the wizard covered, and it skips its PIN step: the
            # PIN cannot be replaced from there.
            _log.error('first-boot lock failed: %s', exc)
            return False
        finally:
            if reporter is not None:
                reporter.note_shell_ready()
        self._wizard_lock = lock
        _log.info('first boot resumed with a PIN set: locked first')
        return True

    def _wizard_unlocked(self) -> None:
        """LockScreen on_unlock (before it hides itself): carry on with
        setup, then drop the one-off lock window."""
        lock = getattr(self, '_wizard_lock', None)
        self._wizard_lock = None
        if self._wizard is None and self._shell is None:
            self._pin_proven_at = time.monotonic()
            self._present_wizard()
        if lock is not None:
            GLib.idle_add(lambda: (lock.destroy(), False)[1])

    def _start_wizard_telephony(self) -> None:
        if getattr(self, '_wizard_telephony', None) is not None:
            return
        try:
            self._wizard_telephony = WizardTelephony(self)
        except Exception as exc:
            _log.error('telephony during setup unavailable: %s', exc)

    def _stop_wizard_telephony(self) -> None:
        telephony = getattr(self, '_wizard_telephony', None)
        self._wizard_telephony = None
        if telephony is not None:
            telephony.close()

    def take_setup_unlock(self) -> bool:
        """One-shot, for the shell window's first lock: True when the PIN
        was entered in this process to resume setup, the wizard ran
        straight after it, and that unlock is under
        SETUP_UNLOCK_GRACE_S old. The shell built at the end of that
        wizard then skips its own early lock (river) instead of asking the
        PIN twice; anything else (a restart, a slow or abandoned setup)
        locks as usual."""
        proven = getattr(self, '_pin_proven_at', None)
        self._pin_proven_at = None
        if proven is None or not getattr(self, '_setup_finishing', False):
            return False
        return time.monotonic() - proven < SETUP_UNLOCK_GRACE_S

    def _wizard_done(self) -> None:
        self._wizard = None
        # The shell window builds its own modem monitor, which adopts any
        # call still up (Voice.ListCalls); two monitors would ring twice.
        self._stop_wizard_telephony()
        self._setup_finishing = True
        try:
            self._show_shell()
        finally:
            self._setup_finishing = False
            self._pin_proven_at = None

    def _show_shell(self) -> None:
        if self._shell is not None:
            self._shell.present()
            return
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
            on_saver_changed=self._on_saver_power_changed,
        )
        self._shell.standby = self._standby
        self._standby.attach_daemon(self._notif_daemon)
        self._standby.start()
        install_saver_check_gates(self._shell, self._standby)
        self._reapply_power_and_night()

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
        self._power_menu = PowerMenu(config=self._shell.config,
                                     on_lock=self._shell._show_lock_screen,
                                     call_live=self._shell._call_live)
        self._power_menu.set_application(self)
        self._power_menu.set_lock_host(
            lambda: getattr(self._shell, '_lock_screen', None))
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
            # A frozen player is thawed before the key reaches it.
            on_media_key=self._standby.wrap_media_key(
                self._mpris.handle_media_key),
        )
        self._shell._display_mgr = self._display_mgr
        self._start_idle_services()
        # Screen capture wiring: screenshot/record notifications reach the
        # shade through the live shell; blank state feeds the recorder's
        # auto-stop so recordings never run against a dark screen.
        import capture
        capture.configure(shell_provider=lambda: self._shell)
        capture.set_blanked(False)
        self._wire_usage_lock()

        self._start_status_bar()
        self._start_bottom_gesture()
        self._start_desk()

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

    def _start_desk(self) -> None:
        """Docked surfaces under xxwm: top bar per monitor, launcher on
        Super, keybind sheet (desk_session.py). Nothing on phoc."""
        shell = self._shell
        if shell is None:
            return
        try:
            from desk_session import start_desk_session
            desk = start_desk_session(shell)
        except Exception as exc:
            _log.warning('docked surfaces unavailable: %s', exc)
            return
        if desk is None:
            return
        shell._desk = desk
        snapshot = getattr(shell, '_status_snapshot', None)
        if snapshot:
            desk.set_status(snapshot)
        shell._refresh_clock()

    def _start_bottom_gesture(self) -> None:
        """Bottom-edge gesture strip (bottom_gesture.py): flick home,
        swipe-and-pause recents, sideways previous/next app. Built in the
        same synchronous pass as the shell window, so the window's startup
        idle that restarts lisgd already sees it and drops lisgd's bottom
        bindings. No layer shell: no strip, lisgd keeps the bottom edge."""
        shell = self._shell
        if shell is None:
            return
        try:
            from bottom_gesture import create_bottom_gesture
            strip = create_bottom_gesture(on_action=self._on_bottom_gesture)
        except Exception as exc:
            _log.warning('bottom gesture strip unavailable: %s', exc)
            return
        if strip is None:
            return
        strip.set_application(self)
        shell._bottom_gesture = strip
        # Mapped by the visibility rule, never under the lock screen.
        shell._sync_status_bar()

    def _on_bottom_gesture(self, outcome: str) -> None:
        """Strip outcome -> the same IPC verb lisgd would have fired
        (gestures.json swipe_up_short / swipe_up_long), through the IPC
        path so lock and tour silencing apply; sideways switches apps."""
        shell = self._shell
        if shell is None:
            return
        from bottom_gesture import HOME, NEXT, PREVIOUS, SWITCHER, verb_for
        if outcome in (PREVIOUS, NEXT):
            if not shell.is_locked() and not self._display_blanked():
                switched = shell.switch_adjacent_app(outcome)
                _log.info('bottom gesture: %s -> %s', outcome,
                          'switched' if switched else 'no neighbour')
            return
        switcher = getattr(shell, '_switcher', None)
        try:
            in_recents = switcher is not None and switcher.get_visible()
        except Exception:
            in_recents = False
        if in_recents and outcome == SWITCHER:
            # Android: swiping up inside recents goes home; the switcher
            # verb would toggle recents shut instead.
            outcome = HOME
        verb = verb_for(outcome, shell.gesture_config)
        if verb:
            _log.info('bottom gesture: %s -> %s', outcome, verb)
            self._on_ipc_command(verb)

    def _start_idle_services(self) -> None:
        """Idle inhibit (org.freedesktop.ScreenSaver + the Caffeine tile)
        and blank-driven automatic suspend. Each piece fails soft: the
        shell runs without them, exactly as before they existed."""
        from idle_inhibit import REASON_SCREENSAVER, get_hub
        hub = get_hub()
        hub.attach(self._display_mgr)
        try:
            from screensaver_service import ScreenSaverService
            self._screensaver = ScreenSaverService(
                on_inhibit_changed=lambda held: hub.set(
                    REASON_SCREENSAVER, held),
                is_blanked=self._display_blanked,
                on_user_activity=hub.wake,
            )
            self._screensaver.start()
        except Exception as exc:
            _log.warning('screensaver service unavailable: %s', exc)
        try:
            from auto_suspend import AutoSuspendController, HelperRtcWake
            shell = self._shell
            rtc = HelperRtcWake()
            self._auto_suspend = AutoSuspendController(
                config=shell.config,
                is_blanked=self._display_blanked,
                call_live=shell._call_live,
                inhibit_reasons=self._idle_reasons,
                media_playing=self._media_playing,
                charging=self._on_charger,
                debug_clients=self._xxdb_clients,
                rtc_wake=rtc.arm,
                rtc_clear=rtc.clear,
            )
        except Exception as exc:
            _log.warning('automatic suspend unavailable: %s', exc)

    def _idle_reasons(self) -> set:
        """Every held idle inhibitor: the hub's (screensaver, caffeine)
        plus the DisplayManager's own (an active call)."""
        from idle_inhibit import get_hub
        reasons = set(get_hub().active_reasons())
        display_mgr = getattr(self, '_display_mgr', None)
        reasons |= set(getattr(display_mgr, 'idle_inhibitors', ()) or ())
        return reasons

    def _media_playing(self) -> bool:
        mpris = getattr(self, '_mpris', None)
        if mpris is None:
            return False
        return any(getattr(p, 'status', '') == 'Playing'
                   for p in mpris.players())

    def _on_charger(self) -> bool:
        """Charging per the standby controller's cached UPower read (its
        refresh worker keeps it current; never a bus call here)."""
        battery = getattr(getattr(self, '_standby', None), '_battery', None)
        try:
            return bool(battery[1])
        except (TypeError, IndexError):
            return False

    def _xxdb_clients(self) -> int:
        return int(getattr(getattr(self, '_xxdb', None), 'active_clients',
                           0) or 0)

    def _display_blanked(self) -> bool:
        """The DisplayManager's own blank state; False before it exists."""
        display_mgr = getattr(self, '_display_mgr', None)
        try:
            return bool(getattr(display_mgr, 'blanked', False))
        except Exception:
            return False

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
        _log.info('notification %d from %s', notif_id, app_name)
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

        from dnd import is_alarm_notification
        is_alarm = is_alarm_notification(hints)

        # Nope: notifications from blocked apps are dropped outright —
        # never held, never shown (Android fallback cancels them).
        nope = getattr(self._shell, 'nope_state', None) if self._shell else None
        try:
            nope_drop = bool(nope is not None and nope.silences(
                desktop_entry, app_name))
        except Exception:
            nope_drop = False
        if nope_drop:
            _log.debug('notification %d dropped by Nope: %s', notif_id,
                       desktop_entry or app_name)
            return

        # Focus: notifications from paused apps are held outright — no sound,
        # not in the shade — and released in one batch when focus ends
        focus = getattr(self._shell, 'focus_state', None)
        if (focus is not None and not is_alarm
                and self._focus_holds(focus, desktop_entry, app_name)):
            self._held_notifications.hold(
                notif_id, app_name, summary, body, desktop_entry,
                actions if actions is not None else [],
                hints if isinstance(hints, dict) else {})
            _log.debug('notification %d held by Focus: %s', notif_id,
                       desktop_entry or app_name)
            return
        # A replacement that is not held supersedes any held copy.
        self._held_notifications.withdraw(notif_id)

        # DnD: notifications still collect in the shade, but silently —
        # no sound. Alarms are always exempt.
        dnd = getattr(self._shell, 'dnd_state', None)
        dnd_silenced = dnd is not None and dnd.is_active() and not is_alarm

        if (config is not None and config.sound_notifications
                and not dnd_silenced and not hints.get('suppress-sound')):
            from call_wiring import ringer_active
            if not ringer_active(self._shell):
                import sound
                sound.notify('notify.ogg')

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
        self._refresh_lock_notifications()

        self._maybe_banner(
            notif_id, app_name, summary, body, desktop_entry,
            hints if isinstance(hints, dict) else {}, actions,
            dnd_active=dnd is not None and dnd.is_active(),
            is_alarm=is_alarm)

    def _focus_holds(self, focus, desktop_entry: str, app_name: str) -> bool:
        def name_of(app_id: str) -> str:
            from app_names import friendly_app_name
            return friendly_app_name(self._shell, app_id)
        try:
            return bool(focus.is_paused_sender(
                desktop_entry, app_name, name_of))
        except Exception:
            return False

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
                nope_state=getattr(self._shell, 'nope_state', None),
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
        self._refresh_lock_notifications()
        return True

    def _refresh_lock_notifications(self) -> None:
        """The lock-screen list follows the shade while locked."""
        if self._shell is None or not self._banner_locked():
            return
        lock = getattr(self._shell, '_lock_screen', None)
        refresh = getattr(lock, 'refresh_notifications', None)
        if not callable(refresh):
            return
        try:
            refresh()
        except Exception as exc:
            _log.warning('lock screen notification refresh failed: %s', exc)

    def _on_notification_closed(self, notif_id: int) -> None:
        # Withdrawn while Focus held it: never deliver it later as a ghost.
        self._held_notifications.withdraw(notif_id)
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
            self._refresh_lock_notifications()

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
            queue_pending_command(pending, command)
            return
        if (self._shell.is_locked()
                and self._ipc_silenced_while_locked(command)):
            _log.info('IPC command %s ignored while locked', command)
            return
        if gesture_silenced_while_blanked(command, self._display_blanked()):
            # Touch stays live on a dark-but-unlocked screen (auto-lock
            # pending): pocket swipes must not go home, open recents, etc.
            _log.info('IPC command %s ignored while blanked', command)
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
        self._idle_blank_hooks(blanked)
        if not blanked:
            try:
                from settings_display import refresh_night_scheduler
                refresh_night_scheduler('wake')
            except Exception as exc:
                _log.warning('night schedule wake failed: %s', exc)
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

    def _idle_blank_hooks(self, blanked: bool) -> None:
        """Screen off ends Caffeine, ScreenSaver.ActiveChanged follows the
        display, and automatic suspend arms (blank) or cancels (wake)."""
        if blanked:
            try:
                from idle_inhibit import get_hub
                get_hub().clear_caffeine('screen off')
            except Exception as exc:
                _log.warning('caffeine blank hook failed: %s', exc)
        for name, method in (('_screensaver', 'set_active'),
                             ('_auto_suspend', 'note_blank')):
            target = getattr(self, name, None)
            if target is None:
                continue
            try:
                getattr(target, method)(blanked)
            except Exception as exc:
                _log.warning('%s blank hook failed: %s', name, exc)

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
        ctrl.lock_probe = self._shell.is_locked
        shell = self._shell

        def _on_xxdb_state_changed() -> None:
            GLib.idle_add(shell._refresh_xxdb_section)
            try:
                import settings_firewall
                settings_firewall.reapply(shell, _on_xxdb_firewall_done)
            except Exception:
                pass

        def _on_xxdb_firewall_done(ok: bool, detail) -> None:
            if not ok:
                try:
                    shell._show_status(
                        'Could not update firewall: %s' % detail)
                except Exception:
                    pass

        ctrl.on_state_changed = _on_xxdb_state_changed
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
        """logind PrepareForSleep. Going down: lock, and let the delay
        inhibitor go only once the lock surface has drawn, so resume never
        flashes the unlocked session. Coming back: re-take the inhibitor
        for the next suspend."""
        delay = getattr(self, '_sleep_delay', None)
        if sleeping and self._shell:
            _log.info('system suspending — locking screen')
            GLib.idle_add(self._lock_for_sleep)
        elif sleeping:
            # No shell yet (first-boot wizard): nothing to lock.
            if delay is not None:
                delay.release()
        else:
            if delay is not None:
                delay.acquire()
            auto = getattr(self, '_auto_suspend', None)
            if auto is not None:
                try:
                    auto.note_resumed()
                except Exception as exc:
                    _log.warning('auto-suspend resume hook failed: %s', exc)
            try:
                from settings_display import refresh_night_scheduler
                refresh_night_scheduler('resume')
            except Exception as exc:
                _log.warning('night schedule resume failed: %s', exc)

    def _lock_for_sleep(self) -> bool:
        """Show the lock, then release the sleep delay after its first
        paint (or a short safety timeout: a blanked output may never paint,
        and logind only waits InhibitDelayMaxSec anyway)."""
        shell = self._shell
        already_locked = False
        try:
            already_locked = bool(shell.is_locked())
        except Exception:
            pass
        try:
            shell._show_lock_screen()
        except Exception as exc:
            _log.warning('lock before suspend failed: %s', exc)
            already_locked = True   # nothing will paint; do not hold sleep
        if already_locked:
            self._release_sleep_delay()
            return GLib.SOURCE_REMOVE
        lock = getattr(shell, '_lock_screen', None)
        clock = None
        try:
            clock = lock.get_frame_clock() if lock is not None else None
        except Exception:
            clock = None
        if clock is not None:
            handler = {}

            def _after_paint(_clock) -> None:
                try:
                    clock.disconnect(handler['id'])
                except Exception:
                    pass
                self._release_sleep_delay()

            try:
                handler['id'] = clock.connect('after-paint', _after_paint)
                lock.queue_draw()
            except Exception:
                clock = None
        if clock is None:
            GLib.idle_add(self._release_sleep_delay)
        GLib.timeout_add(_SLEEP_DELAY_SAFETY_MS, self._release_sleep_delay)
        return GLib.SOURCE_REMOVE

    def _release_sleep_delay(self) -> bool:
        """Let a pending suspend proceed. Idempotent: the paint hook and
        the safety timeout both land here."""
        delay = getattr(self, '_sleep_delay', None)
        if delay is not None:
            delay.release()
        return GLib.SOURCE_REMOVE

    def _on_saver_power_changed(self, saver_on: bool) -> None:
        """Battery saver selects the power-saver profile (and back)."""
        shell = self._shell
        config = getattr(shell, 'config', None)
        if config is None:
            return
        try:
            from settings_display import reapply_power_profile
            reapply_power_profile(config, bool(saver_on))
        except Exception as exc:
            _log.warning('saver power re-apply failed: %s', exc)

    def _reapply_power_and_night(self) -> None:
        """Session start: enforce the remembered power profile and the
        night-light schedule (both degrade silently without backends)."""
        shell = self._shell
        if shell is None:
            return
        try:
            from settings_display import (ensure_night_scheduler,
                                          reapply_power_profile)
            saver = bool(getattr(self._standby, 'saver_effective', False))
            reapply_power_profile(shell.config, saver)
            ensure_night_scheduler(shell)
        except Exception as exc:
            _log.warning('session power re-apply failed: %s', exc)

    def do_shutdown(self) -> None:
        screensaver = getattr(self, '_screensaver', None)
        if screensaver is not None:
            try:
                screensaver.stop()
            except Exception:
                pass
        standby = getattr(self, '_standby', None)
        if standby is not None:
            try:
                standby.shutdown_thaw()
            except Exception:
                pass
        shell = getattr(self, '_shell', None)
        desk = getattr(shell, '_desk', None)
        if desk is not None:
            try:
                desk.shutdown()
            except Exception:
                pass
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


def _setup_logind(on_sleep_callback: object) -> object | None:
    """Subscribe to PrepareForSleep and take a logind 'delay' sleep
    inhibitor, returned so _on_sleep can release it once the lock screen
    is drawn. None without a system bus (fails soft)."""
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
        return None
    try:
        from display_manager import LogindInhibitor
        delay = LogindInhibitor(
            'sleep', 'Lock screen before suspend', 'delay', bus=bus)
        delay.acquire()
        return delay
    except Exception as e:
        _log.warning('logind sleep delay unavailable: %s', e)
        return None


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
