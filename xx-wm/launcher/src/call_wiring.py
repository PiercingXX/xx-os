from __future__ import annotations
from shell_log import log_exc


#: Screening reasons that mean "unknown caller, allowed to ring" (the
#: dialer's ``Tone.UNKNOWN`` rings plus the observe-week unknowns); the
#: ringer plays ``unknown.ogg`` for these, ``ringtone.ogg`` otherwise.
#: Strings mirror the dialer verdict reasons on the CheckNumber reply.
UNKNOWN_RING_REASONS = frozenset({
    "unknown caller",
    "you called them Tue",
    "repeat caller",
    "rang · expecting a call",
    "Unknown, outside 09–17",
    "silenced by pattern",
})


#: MMCallStateReason codes of an ordinary end (unknown, started, new,
#: accepted, terminated): no end notice for these.
_NORMAL_END_REASONS = frozenset({0, 1, 2, 3, 4})
_REASON_REFUSED_OR_BUSY = 5
#: An end notice older than this belongs to an earlier call.
_END_NOTICE_MAX_AGE_S = 10.0


def end_reason_notice(info, now: float | None = None) -> str | None:
    """Short text to show when a call ended abnormally ('Busy', 'Network
    error', 'Audio setup failed', ...), None for a normal hang-up. 'Busy'
    (REFUSED_OR_BUSY) only reads true for a call we placed: on an
    incoming call it means the caller gave up or we declined."""
    if info is None:
        return None
    try:
        reason = int(getattr(info, 'state_reason', 0) or 0)
    except (TypeError, ValueError):
        return None
    if reason in _NORMAL_END_REASONS:
        return None
    if reason == _REASON_REFUSED_OR_BUSY:
        try:
            if int(getattr(info, 'direction', 0)) != 2:
                return None
        except (TypeError, ValueError):
            return None
    ended_at = getattr(info, 'ended_at', None)
    if ended_at is not None:
        import time as _time
        current = _time.monotonic() if now is None else now
        if current - float(ended_at) > _END_NOTICE_MAX_AGE_S:
            return None
    text = getattr(info, 'end_reason', '') or ''
    if not text:
        try:
            from modem_monitor import end_reason_text
            text = end_reason_text(reason)
        except Exception:
            return None
    return text


def ringer_tone_for(verdict: str, reason: str) -> str:
    """Sound file for a ringing verdict: the unknown-caller tone when
    the dialer's screening verdict says the caller is unknown but
    allowed to ring, else the default ringtone."""
    if (verdict or "").strip().lower() == "ring" and \
            (reason or "").strip() in UNKNOWN_RING_REASONS:
        return "unknown.ogg"
    return "ringtone.ogg"


def ringer_active(win) -> bool:
    """True while an incoming call rings (feedbackd or the sound loop):
    notification sounds stay quiet over it."""
    ui = getattr(win, '_call_ui', None)
    if bool(getattr(getattr(ui, '_ringer', None), 'ringing', False)):
        return True
    try:
        import sound
        return bool(sound.ringing())
    except Exception:
        return False


class CallWiring:
    """Call/telephony glue for ShellWindow.

    Holds only a reference to the window; all state (call UI/bar,
    modem monitor, owner-warning flags) stays on the window so
    bare-instance headless tests keep working. Method names mirror the
    ShellWindow delegators in window.py. Every call routes through win so
    test stubs on the instance keep working.
    """

    def __init__(self, win) -> None:
        self._win = win

    def _ensure_call_surfaces(self) -> tuple:
        from call_ui import CallUI, CallBar
        from window import resolve_theme

        win = self._win
        if win._call_ui is None:
            # The surface owns its ringer/audio/proximity (call_ui.Ringer,
            # CallAudioRouter, ProximityMonitor), so no sound callbacks are
            # wired here — terminal paths stop themselves.
            win._call_ui = CallUI(config=win.config)
            win._call_ui.set_application(win.get_application())
            # Lazy construction renders the CURRENT preset.
            win._call_ui.apply_theme(resolve_theme(win.config))
            try:
                win._call_ui.set_lock_probe(win.is_locked)
                win._call_ui.set_lock_host(
                    lambda: getattr(win, '_lock_screen', None))
            except Exception:
                log_exc('_ensure_call_surfaces: _call_ui.set_lock_probe')
        if win._call_bar is None:
            win._call_bar = CallBar(on_expand=win._expand_call_ui,
                                    config=win.config)
            win._call_bar.set_application(win.get_application())
            win._call_bar.apply_theme(resolve_theme(win.config))
        try:
            win._call_ui.bind_bar(win._call_bar)
        except Exception:
            log_exc('call_wiring._ensure_call_surfaces: _call_ui.bind_bar')
        return win._call_ui, win._call_bar

    def silence_ringer(self) -> bool:
        """Power-key press while ringing only silences the ringer: the call
        stays live on the surface. Falls back to the sound loop when the
        surface (or its ringer) is not up yet. True when the press did
        silence a ringing call on a lit screen: the display manager then
        keeps the screen on instead of blanking the call away. A verdict
        still in flight will not start the ringer afterwards."""
        win = self._win
        was_ringing = ringer_active(win)
        pending = getattr(win, '_screen_pending', None)
        if isinstance(pending, dict):
            pending['silenced'] = True
        ui = getattr(win, '_call_ui', None)
        stopper = getattr(getattr(ui, '_ringer', None), 'stop', None)
        if callable(stopper):
            stopper()
        else:
            try:
                import sound
                sound.stop()
            except Exception:
                log_exc('call_wiring.silence_ringer: sound.stop')
        if not was_ringing:
            return False
        display_mgr = getattr(win, '_display_mgr', None)
        return not bool(getattr(display_mgr, 'blanked', False))

    def _calls_app_owns_calls(self) -> bool:
        """Whether GNOME Calls owns its bus name now, probed once for each
        new call (_app_owns_call). ``_calls_owner_warned`` mirrors the last
        answer: set (and logged once) while Calls owns the name, reset once
        it has left the bus, so the shell's surfaces and call end notices
        come back for the rest of the session instead of staying off for
        good."""
        from modem_monitor import CALLS_BUS_NAME, bus_name_has_owner
        from shell_log import get_logger

        win = self._win
        owned = bool(bus_name_has_owner(CALLS_BUS_NAME))
        was = bool(getattr(win, '_calls_owner_warned', False))
        if owned and not was:
            get_logger('window').info(
                '%s owns the session bus; leaving calls to it',
                CALLS_BUS_NAME)
        elif was and not owned:
            get_logger('window').info(
                '%s left the session bus; the shell handles calls again',
                CALLS_BUS_NAME)
        win._calls_owner_warned = owned
        return owned

    def _owners(self) -> dict:
        win = self._win
        owners = getattr(win, '_call_owners', None)
        if not isinstance(owners, dict):
            owners = {}
            win._call_owners = owners
        return owners

    def _app_owns_call(self, path) -> bool:
        """Whether GNOME Calls owns this call, decided once when the call
        is first seen and kept for its lifetime (a path-less call is
        probed each time)."""
        if not path:
            return self._calls_app_owns_calls()
        owners = self._owners()
        if path not in owners:
            owners[path] = self._calls_app_owns_calls()
        return bool(owners[path])

    def _decide_owners(self, calls: list) -> bool:
        """Settle ownership for every call in the snapshot (new paths
        once, gone paths forgotten). True when every live call belongs
        to GNOME Calls."""
        try:
            from modem_monitor import _STATE_TERMINATED
        except Exception:
            _STATE_TERMINATED = 7
        owners = self._owners()
        current = set()
        app_only = None
        for call in calls or []:
            path = getattr(call, 'path', None)
            if not path:
                continue
            current.add(path)
            if getattr(call, 'state', None) == _STATE_TERMINATED:
                continue
            owned = self._app_owns_call(path)
            app_only = owned if app_only is None else (app_only and owned)
        for path in [p for p in owners if p not in current]:
            del owners[path]
        return bool(app_only)

    def _incoming_call_path(self, number: str):
        """The ringing call this announcement is about: the monitor's
        RINGING_IN call with this number, else the active call path."""
        try:
            from modem_monitor import (
                _DIR_INCOMING, _STATE_RINGING_IN, _last_monitor)
            for call in (_last_monitor.calls() if _last_monitor else []):
                if (call.direction == _DIR_INCOMING
                        and call.state == _STATE_RINGING_IN
                        and (call.number or '') == number):
                    return call.path
        except Exception:
            log_exc('call_wiring._incoming_call_path: _last_monitor.calls')
        try:
            from modem_monitor import active_call_path
            return active_call_path()
        except Exception:
            return None

    def _wake_for_call(self) -> None:
        display_mgr = getattr(self._win, '_display_mgr', None)
        if display_mgr is not None:
            try:
                display_mgr.wake()
            except Exception:
                log_exc('call_wiring._wake_for_call: display_mgr.wake')

    def _on_call_incoming(self, caller: str, number: str) -> None:
        from shell_log import get_logger

        win = self._win
        raw = number if isinstance(number, str) else ''
        path = self._incoming_call_path(raw)
        if self._app_owns_call(path):
            # GNOME Calls rings and screens its own calls, but its window
            # sits under the lock: wake the screen, and while locked map
            # the shell surface (no ringer) so the call can be answered.
            self._wake_for_call()
            if win.is_locked():
                call_ui, _bar = win._ensure_call_surfaces()
                call_ui.show_incoming(caller or 'Incoming call', raw, path)
                call_ui.present()
            return
        call_ui, _bar = win._ensure_call_surfaces()
        # DnD exception check runs before this call is recorded so the
        # 15-minute repeat window looks at prior calls only (checked after
        # note_call, every call matched itself and rang through DnD); the
        # ringer itself starts only in the verdict callback.
        try:
            dnd_exception = bool(win.dnd_state.is_exception(raw))
        except Exception:
            dnd_exception = True
        # A waiting call promoted to ringing was already recorded when it
        # arrived WAITING (_note_waiting_calls): never count it twice.
        noted = getattr(win, '_dnd_waiting_noted', None)
        if not (raw and isinstance(noted, dict) and raw in noted.values()):
            win.dnd_state.note_call(number)
        import time as _time
        now_ms = int(_time.time() * 1000)
        try:
            win._screen_pending = {
                'number': raw, 'caller': caller, 'started_ms': now_ms,
                'verdict': None, 'reason': '', 'answered': False,
            }
        except Exception:
            pass
        # Show the incoming surface silently while the policy consult is
        # pending (at most 2 s); the verdict callback starts the ringer.
        # Caller is already the contact name when known (resolved by the
        # modem monitor); the literal fallback only shows for unknown numbers.
        call_ui.show_incoming(caller or 'Incoming call', raw)
        # A ringing phone is never dark, locked or not; through the lock
        # the call UI maps above it (both OVERLAY; the last mapped wins).
        self._wake_for_call()
        if win.is_locked():
            call_ui.present()
        from call_screen import consult_check_number

        def _on_verdict(verdict: str, reason: str) -> None:
            pending = getattr(win, '_screen_pending', None)
            if not isinstance(pending, dict) or pending.get('number') != raw:
                return
            pending['verdict'] = verdict
            pending['reason'] = reason
            # The consult is async (up to 2 s on cold activation); the user
            # may have answered, declined or hung up while it was in flight.
            # A late verdict is then only recorded, never acted on.
            if self._verdict_is_stale(raw):
                return
            if verdict == 'block':
                get_logger('window').info(
                    'blocked call from %s (%s)', raw or 'withheld', reason)
                pending['blocked'] = True
                try:
                    path = self._resolve_incoming_path(raw)
                except Exception:
                    path = None
                if path is not None:
                    try:
                        from modem_monitor import hangup_call
                        hangup_call(path, lambda _ok, _err: None)
                    except Exception:
                        log_exc('call_wiring._on_verdict: hangup_call')
                try:
                    call_ui.end_call()
                except Exception:
                    log_exc('call_wiring._on_verdict: call_ui.end_call')
                try:
                    bar = getattr(win, '_call_bar', None)
                    if bar is not None:
                        bar.hide_bar()
                except Exception:
                    log_exc('call_wiring._on_verdict: getattr')
                return
            if verdict == 'silence':
                try:
                    call_ui.stop_ringing()
                except Exception:
                    log_exc('call_wiring._on_verdict: call_ui.stop_ringing')
                return
            try:
                rings_now = (not win.dnd_state.is_active()
                             or dnd_exception)
            except Exception:
                rings_now = True
            if pending.get('silenced'):
                rings_now = False
            if rings_now and win.config.sound_ringtone:
                # feedbackd first (system ringtone + silent profile),
                # sound loop only when it is absent — owned by the
                # surface, stopped on every terminal path.
                try:
                    call_ui.start_ringing(
                        ringer_tone_for(verdict, reason))
                except Exception:
                    log_exc('call_wiring._on_verdict: call_ui.start_ringing')

        try:
            consult_check_number(raw, _on_verdict)
        except Exception:
            _on_verdict('ring', 'fail-open')

    def _verdict_is_stale(self, number: str) -> bool:
        """True once the incoming call the consult was requested for is gone:
        answered or declined by the user, already reported, or its
        ModemManager state left RINGING_IN. A stale verdict is recorded on
        the pending dict but must never ring, silence or hang up."""
        win = self._win
        try:
            pending = getattr(win, '_screen_pending', None)
            if isinstance(pending, dict) and (
                    pending.get('answered') or pending.get('rejected')
                    or pending.get('declined') or pending.get('reported')):
                return True
        except Exception:
            log_exc('call_wiring._verdict_is_stale: getattr')
        try:
            tracked = getattr(win, '_screen_tracked', None)
            if isinstance(tracked, dict):
                for info in tracked.values():
                    if (isinstance(info, dict)
                            and info.get('number') == number
                            and info.get('answered')):
                        return True
        except Exception:
            log_exc('call_wiring._verdict_is_stale: getattr')
        try:
            from modem_monitor import _STATE_RINGING_IN, _last_monitor
            monitor = _last_monitor
            calls = monitor.calls() if monitor is not None else []
            for call in calls or []:
                try:
                    if (getattr(call, 'number', '') or '') != number:
                        continue
                except Exception:
                    continue
                try:
                    return int(getattr(call, 'state', 0)) != int(
                        _STATE_RINGING_IN)
                except Exception:
                    return False
        except Exception:
            log_exc('call_wiring._verdict_is_stale: monitor.calls')
        return False

    def _resolve_incoming_path(self, number: str) -> str | None:
        try:
            from modem_monitor import active_call_path
            path = active_call_path()
        except Exception:
            path = None
        if path:
            return path
        try:
            from modem_monitor import _last_monitor
            monitor = _last_monitor
        except Exception:
            monitor = None
        if monitor is None:
            return None
        try:
            for call in monitor.calls():
                if call.number == number:
                    return call.path
        except Exception:
            log_exc('call_wiring._resolve_incoming_path: monitor.calls')
        return None

    def _report_call_end(self, number: str, direction: str, started_ms: int,
                         answered: bool) -> None:
        """Report one finished call to the dialer policy service.

        NoteCall wakes the service over D-Bus activation when it is not
        running, and the service owns every missed-call card — the
        shell never posts its own, so a call can never card twice.
        """
        win = self._win
        import time as _time
        try:
            done = getattr(win, '_screen_done', None)
            if not isinstance(done, set):
                done = set()
                win._screen_done = done
        except Exception:
            done = set()
        key = (direction, number, int(started_ms))
        if key in done:
            return
        done.add(key)
        if len(done) > 64:
            try:
                done.pop()
            except Exception:
                log_exc('call_wiring._report_call_end: done.pop')
        try:
            now_ms = int(_time.time() * 1000)
            duration_ms = max(0, now_ms - int(started_ms))
        except Exception:
            duration_ms = 0
        try:
            from call_screen import note_call
            note_call(number, direction, int(started_ms), duration_ms,
                      'answered' if answered else 'missed')
        except Exception:
            log_exc('call_wiring._report_call_end: note_call')

    def _sync_call_inhibit(self, calls: list) -> None:
        """Hold the display awake and block system sleep while any call is
        non-terminated; the blank-driven auto-lock consults the same
        snapshot and stands down."""
        win = self._win
        try:
            from modem_monitor import _STATE_TERMINATED
            live = any(call.state != _STATE_TERMINATED for call in calls)
        except Exception:
            live = False
        display_mgr = getattr(win, '_display_mgr', None)
        if display_mgr is not None:
            display_mgr.inhibit_idle('call', live)
            # And hold off system sleep (logind 'block' inhibitor) for the
            # whole call: suspend would drop the modem audio path.
            inhibit_sleep = getattr(display_mgr, 'inhibit_sleep', None)
            if callable(inhibit_sleep):
                try:
                    inhibit_sleep('call', live)
                except Exception:
                    log_exc('call_wiring._sync_call_inhibit: inhibit_sleep')
        # A blank during the call skipped the auto-lock; arm it now that the
        # last call is gone, or the phone stays unlocked in the pocket.
        was_live = getattr(win, '_call_was_live', False)
        win._call_was_live = live
        if (was_live and not live and getattr(win, '_blanked', False)
                and getattr(win, '_lock_src', None) is None
                and not win.is_locked()):
            win._schedule_blank_lock()

    def _track_snapshot(self, calls: list) -> None:
        win = self._win
        import time as _time
        try:
            from modem_monitor import _DIR_OUTGOING, _STATE_ACTIVE
        except Exception:
            _DIR_OUTGOING, _STATE_ACTIVE = 2, 4
        try:
            tracked = getattr(win, '_screen_tracked', None)
            if not isinstance(tracked, dict):
                tracked = {}
                win._screen_tracked = tracked
        except Exception:
            tracked = {}
        try:
            now_ms = int(_time.time() * 1000)
        except Exception:
            now_ms = 0
        try:
            current = {getattr(call, 'path', None): call for call in calls or []}
            current.pop(None, None)
        except Exception:
            return
        for path, call in current.items():
            info = tracked.get(path)
            try:
                number = getattr(call, 'number', '') or ''
                number = number if isinstance(number, str) else str(number)
            except Exception:
                number = ''
            try:
                direction = ('outgoing'
                             if int(getattr(call, 'direction', 0)) == _DIR_OUTGOING
                             else 'incoming')
            except Exception:
                direction = 'incoming'
            try:
                answered = int(getattr(call, 'state', 0)) == int(_STATE_ACTIVE)
            except Exception:
                answered = False
            if info is None:
                tracked[path] = {'number': number, 'direction': direction,
                                 'started_ms': now_ms, 'answered': answered}
            else:
                if not info.get('number') and number:
                    info['number'] = number
                if answered:
                    info['answered'] = True
        for path in [p for p in tracked if p not in current]:
            info = tracked.pop(path)
            try:
                self._report_call_end(info.get('number', ''),
                                      info.get('direction', 'incoming'),
                                      int(info.get('started_ms', now_ms)),
                                      bool(info.get('answered', False)))
                # The legacy on_ended adapter reports the same call below:
                # mark it reported so the log gets exactly one row.
                try:
                    pending = getattr(win, '_screen_pending', None)
                    if (isinstance(pending, dict)
                            and pending.get('number') == info.get('number')):
                        pending['reported'] = True
                except Exception:
                    log_exc('call_wiring._track_snapshot: getattr')
            except Exception:
                log_exc('call_wiring._track_snapshot: _report_call_end')

    def _on_calls_changed(self, calls: list) -> None:
        win = self._win
        try:
            self._track_snapshot(calls)
        except Exception:
            log_exc('call_wiring._on_calls_changed: _track_snapshot')
        win._sync_call_inhibit(calls)
        desk = getattr(win, '_desk', None)
        if desk is not None:
            try:
                from call_ui import select_page
                desk.set_call(select_page(calls))
            except Exception:
                log_exc('call_wiring._on_calls_changed: desk.set_call')
        # The snapshot drives the whole surface: incoming, outgoing
        # dialing (full page with Hang up, not just the bar), active,
        # waiting, and the empty close. The legacy adapters stay as the
        # responsive path for their own transitions; render converges them.
        try:
            if self._decide_owners(calls) and not self._cover_lock():
                return
            self._note_waiting_calls(calls)
            call_ui, _bar = win._ensure_call_surfaces()
        except Exception as err:
            from shell_log import get_logger
            get_logger('window').warning('call surface unavailable: %s', err)
            return
        try:
            call_ui.render_snapshot(
                calls, display_mgr=getattr(win, '_display_mgr', None))
        except Exception as err:
            from shell_log import get_logger
            get_logger('window').warning('call render failed: %s', err)
            return
        # Locked + outgoing dialing would otherwise sit under the lock:
        # dialing deliberately stays on the CallBar (TOP layer), which the
        # OVERLAY lock covers, so the caller would lose Hang up/speaker
        # until the far end answers. Map the full surface above the lock
        # (both OVERLAY; last mapped wins). A minimized surface stays down
        # and unlocked behavior is untouched.
        try:
            if (win.is_locked()
                    and not getattr(call_ui, '_minimized', False)):
                from call_ui import select_page
                if select_page(calls) == 'outgoing':
                    call_ui.present()
        except Exception as err:
            from shell_log import get_logger
            get_logger('window').warning(
                'locked outgoing present failed: %s', err)

    def _cover_lock(self) -> bool:
        """For a call GNOME Calls owns, the shell surface only stands in
        while locked (Calls' window is under the lock) and, once up,
        stays until the call ends."""
        win = self._win
        try:
            if win.is_locked():
                return True
        except Exception:
            log_exc('call_wiring._cover_lock: win.is_locked')
        ui = getattr(win, '_call_ui', None)
        return bool(getattr(ui, '_surface_live', False))

    def _note_waiting_calls(self, calls: list) -> None:
        """Record call-waiting arrivals for the DnD repeat-caller rule.

        A second incoming call arrives WAITING and never passes the
        ringing path (_on_call_incoming), so it was never noted: a caller
        who tried twice during another call did not count as a repeat.
        Each waiting call is noted once per path (again once a withheld
        number resolves); the ringing path skips a promoted one."""
        win = self._win
        try:
            from modem_monitor import _DIR_INCOMING, _STATE_WAITING
        except Exception:
            _DIR_INCOMING, _STATE_WAITING = 1, 6
        noted = getattr(win, '_dnd_waiting_noted', None)
        if not isinstance(noted, dict):
            noted = {}
            win._dnd_waiting_noted = noted
        current = set()
        for call in calls or []:
            path = getattr(call, 'path', None)
            if path is None:
                continue
            current.add(path)
            number = getattr(call, 'number', '') or ''
            if path in noted and (noted[path] or not number):
                continue
            try:
                waiting = (int(getattr(call, 'direction', 0)) == _DIR_INCOMING
                           and int(getattr(call, 'state', 0)) == _STATE_WAITING)
            except (TypeError, ValueError):
                waiting = False
            if not waiting:
                continue
            try:
                win.dnd_state.note_call(number)
            except Exception:
                log_exc('call_wiring._note_waiting_calls: dnd_state.note_call')
            noted[path] = number
        for path in [p for p in noted if p not in current]:
            del noted[path]

    def _show_end_reason(self) -> None:
        """Brief notice when the call that just ended did not end
        normally ('Busy', 'Network error', 'Audio setup failed'): on the
        dialer's status line when it is open, else the shell toast."""
        win = self._win
        if getattr(win, '_calls_owner_warned', False):
            return  # GNOME Calls owns calls (and shows its own reason)
        try:
            from modem_monitor import last_ended_call
            info = last_ended_call()
        except Exception:
            return
        text = end_reason_notice(info)
        if not text:
            return
        key = (getattr(info, 'path', None), getattr(info, 'ended_at', None))
        if getattr(win, '_end_reason_shown', None) == key:
            return
        win._end_reason_shown = key
        show = getattr(win, '_show_status', None)
        if callable(show):
            try:
                show(text)
            except Exception:
                log_exc('call_wiring._show_end_reason: show')

    def _on_call_answered(self, caller: str, number: str) -> None:
        import sound

        win = self._win
        try:
            pending = getattr(win, '_screen_pending', None)
            if isinstance(pending, dict) and pending.get('number') == number:
                pending['answered'] = True
        except Exception:
            log_exc('call_wiring._on_call_answered: getattr')
        try:
            tracked = getattr(win, '_screen_tracked', None)
            if isinstance(tracked, dict):
                for info in tracked.values():
                    if info.get('number') == number:
                        info['answered'] = True
        except Exception:
            log_exc('call_wiring._on_call_answered: getattr')
        sound.stop()
        if win._call_ui is None:
            return
        try:
            win._call_ui.stop_ringing()
        except Exception:
            log_exc('call_wiring._on_call_answered: _call_ui.stop_ringing')
        win._call_ui.show_active(caller or 'Active call', number)
        if win._call_bar is not None:
            win._call_bar.show_bar(caller or number)

    def _on_call_ended(self) -> None:
        import sound

        win = self._win
        try:
            pending = getattr(win, '_screen_pending', None)
            if isinstance(pending, dict) and not pending.get('reported'):
                self._report_call_end(pending.get('number', ''), 'incoming',
                                      int(pending.get('started_ms', 0)),
                                      bool(pending.get('answered', False)))
            win._screen_pending = None
        except Exception:
            log_exc('call_wiring._on_call_ended: getattr')
        sound.stop()
        if win._call_ui is not None:
            try:
                win._call_ui.end_call()
            except Exception:
                log_exc('call_wiring._on_call_ended: _call_ui.end_call')
        if win._call_bar is not None:
            win._call_bar.hide_bar()
        self._show_end_reason()

    def _on_sms_received(self, number: str, text: str) -> bool:
        """Post the Messages banner unless an SMS app owns messages.
        True when the shell posted it, which tells the modem monitor to
        delete the message from the modem store."""
        import modem_monitor
        from shell_log import get_logger

        win = self._win
        # TxxT owns SMS on XX-WM devices (and posts its own notifications),
        # so it wins over Chatty when both are present. An app that is
        # installed but not running yet owns the message too: it is
        # started and reads the modem store itself (posting here as well
        # would notify twice).
        for name, flag in ((modem_monitor.TXXT_BUS_NAME, '_txtt_owner_warned'),
                           (modem_monitor.CHATTY_BUS_NAME,
                            '_chatty_owner_warned')):
            running = modem_monitor.bus_name_has_owner(name)
            if not running and not modem_monitor.bus_name_activatable(name):
                continue
            if not running:
                modem_monitor.start_bus_name(name)
            if not getattr(win, flag, False):
                setattr(win, flag, True)
                get_logger('window').info(
                    '%s owns SMS; leaving messages to it', name)
            return False
        from contacts import shared_book, display_number
        try:
            summary = shared_book().lookup_number(number) or display_number(number)
        except Exception:
            summary = number
        # Route through the application's D-Bus notification path so the
        # mute/Focus/DnD and sound policy applies like any other banner.
        win._sms_notif_seq += 1
        notif_id = 1_900_000_000 + win._sms_notif_seq
        app = win.get_application()
        handler = getattr(app, '_on_notification', None)
        if handler is not None:
            handler(notif_id, 'Messages', summary, text, '', {})
        else:
            win._ensure_shade().add_notification(
                notif_id, 'Messages', summary, text)
        return True

    def _expand_call_ui(self) -> None:
        # CallBar's expand button: re-present the full call surface
        win = self._win
        if win._call_ui is not None:
            win._call_ui.present()

    def _call_live(self) -> bool:
        """True while any tracked call is non-terminated (ringing, dialing,
        active, or held) — the phone must never auto-lock under it."""
        win = self._win
        try:
            from modem_monitor import _STATE_TERMINATED
            return any(call.state != _STATE_TERMINATED
                       for call in win._modem_monitor.calls())
        except Exception:
            return False

    def _reassert_call_ui_above_lock(self) -> None:
        """A live call stays reachable: re-present the call UI above the
        lock (both are OVERLAY; the last mapped wins)."""
        win = self._win
        if not win._call_live():
            return
        try:
            calls = win._modem_monitor.calls()
            if (self._decide_owners(calls)
                    and not getattr(win._call_ui, '_surface_live', False)):
                # A GNOME Calls call the shell was not covering yet.
                call_ui, _bar = win._ensure_call_surfaces()
                call_ui.render_snapshot(
                    calls, display_mgr=getattr(win, '_display_mgr', None))
        except Exception:
            log_exc('_reassert_call_ui_above_lock: _modem_monitor.calls')
        if win._call_ui is not None:
            win._call_ui.present()
