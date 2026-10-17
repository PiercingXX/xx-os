from __future__ import annotations


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


def ringer_tone_for(verdict: str, reason: str) -> str:
    """Sound file for a ringing verdict: the unknown-caller tone when
    the dialer's screening verdict says the caller is unknown but
    allowed to ring, else the default ringtone."""
    if (verdict or "").strip().lower() == "ring" and \
            (reason or "").strip() in UNKNOWN_RING_REASONS:
        return "unknown.ogg"
    return "ringtone.ogg"


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
            # Same current-preset rule as _open_dialer.
            win._call_ui.apply_theme(resolve_theme(win.config))
        if win._call_bar is None:
            win._call_bar = CallBar(on_expand=win._expand_call_ui,
                                    config=win.config)
            win._call_bar.set_application(win.get_application())
            win._call_bar.apply_theme(resolve_theme(win.config))
        try:
            win._call_ui.bind_bar(win._call_bar)
        except Exception:
            pass
        return win._call_ui, win._call_bar

    def silence_ringer(self) -> None:
        """Power-key press while ringing only silences the ringer: the call
        stays live on the surface. Falls back to the sound loop when the
        surface (or its ringer) is not up yet."""
        win = self._win
        ui = getattr(win, '_call_ui', None)
        stopper = getattr(getattr(ui, '_ringer', None), 'stop', None)
        if callable(stopper):
            stopper()
            return
        try:
            import sound
            sound.stop()
        except Exception:
            pass

    def _on_call_incoming(self, caller: str, number: str) -> None:
        from modem_monitor import CALLS_BUS_NAME, bus_name_has_owner
        from shell_log import get_logger

        win = self._win
        if bus_name_has_owner(CALLS_BUS_NAME):
            if not win._calls_owner_warned:
                win._calls_owner_warned = True
                get_logger('window').info(
                    '%s owns the session bus; leaving calls to it', CALLS_BUS_NAME)
            return
        call_ui, _bar = win._ensure_call_surfaces()
        # DnD exception check runs before this call is recorded so the
        # 15-minute repeat window looks at prior calls; the ringer itself
        # starts only in the verdict callback (ring + DnD exception).
        win.dnd_state.note_call(number)
        raw = number if isinstance(number, str) else ''
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
        if win.is_locked():
            # An incoming call reaches the user through the lock: wake the
            # display and map the call UI above the lock (both OVERLAY;
            # the last mapped wins).
            display_mgr = getattr(win, '_display_mgr', None)
            if display_mgr is not None:
                display_mgr.wake()
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
                        pass
                try:
                    call_ui.end_call()
                except Exception:
                    pass
                try:
                    bar = getattr(win, '_call_bar', None)
                    if bar is not None:
                        bar.hide_bar()
                except Exception:
                    pass
                return
            if verdict == 'silence':
                try:
                    call_ui.stop_ringing()
                except Exception:
                    pass
                return
            try:
                rings_now = (not win.dnd_state.is_active()
                             or win.dnd_state.is_exception(raw))
            except Exception:
                rings_now = True
            if rings_now and win.config.sound_ringtone:
                # feedbackd first (system ringtone + silent profile),
                # sound loop only when it is absent — owned by the
                # surface, stopped on every terminal path.
                try:
                    call_ui.start_ringing(
                        ringer_tone_for(verdict, reason))
                except Exception:
                    pass

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
            pass
        try:
            tracked = getattr(win, '_screen_tracked', None)
            if isinstance(tracked, dict):
                for info in tracked.values():
                    if (isinstance(info, dict)
                            and info.get('number') == number
                            and info.get('answered')):
                        return True
        except Exception:
            pass
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
            pass
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
            pass
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
                pass
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
            pass

    def _sync_call_inhibit(self, calls: list) -> None:
        """Hold the display awake while any call is non-terminated; the
        blank-driven auto-lock consults the same snapshot and stands down."""
        win = self._win
        try:
            from modem_monitor import _STATE_TERMINATED
            live = any(call.state != _STATE_TERMINATED for call in calls)
        except Exception:
            live = False
        display_mgr = getattr(win, '_display_mgr', None)
        if display_mgr is not None:
            display_mgr.inhibit_idle('call', live)
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
                    pass
            except Exception:
                pass

    def _on_calls_changed(self, calls: list) -> None:
        win = self._win
        try:
            self._track_snapshot(calls)
        except Exception:
            pass
        win._sync_call_inhibit(calls)
        # The snapshot drives the whole surface: incoming, outgoing
        # dialing (full page with Hang up, not just the bar), active,
        # waiting, and the empty close. The legacy adapters stay as the
        # responsive path for their own transitions; render converges them.
        try:
            from modem_monitor import CALLS_BUS_NAME, bus_name_has_owner
            from shell_log import get_logger
            if bus_name_has_owner(CALLS_BUS_NAME):
                if not getattr(win, '_calls_owner_warned', False):
                    win._calls_owner_warned = True
                    get_logger('window').info(
                        '%s owns the session bus; leaving calls to it',
                        CALLS_BUS_NAME)
                return
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

    def _on_call_answered(self, caller: str, number: str) -> None:
        import sound

        win = self._win
        try:
            pending = getattr(win, '_screen_pending', None)
            if isinstance(pending, dict) and pending.get('number') == number:
                pending['answered'] = True
        except Exception:
            pass
        try:
            tracked = getattr(win, '_screen_tracked', None)
            if isinstance(tracked, dict):
                for info in tracked.values():
                    if info.get('number') == number:
                        info['answered'] = True
        except Exception:
            pass
        sound.stop()
        if win._call_ui is None:
            return
        try:
            win._call_ui.stop_ringing()
        except Exception:
            pass
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
            pass
        sound.stop()
        if win._call_ui is not None:
            try:
                win._call_ui.end_call()
            except Exception:
                pass
        if win._call_bar is not None:
            win._call_bar.hide_bar()

    def _on_sms_received(self, number: str, text: str) -> None:
        from modem_monitor import (
            CHATTY_BUS_NAME, TXXT_BUS_NAME, bus_name_has_owner)
        from shell_log import get_logger

        win = self._win
        # TxxT owns SMS on XX-WM devices (and posts its own notifications),
        # so it wins over Chatty when both are present.
        if bus_name_has_owner(TXXT_BUS_NAME):
            if not win._txtt_owner_warned:
                win._txtt_owner_warned = True
                get_logger('window').info(
                    '%s owns the session bus; leaving SMS to it', TXXT_BUS_NAME)
            return
        if bus_name_has_owner(CHATTY_BUS_NAME):
            if not win._chatty_owner_warned:
                win._chatty_owner_warned = True
                get_logger('window').info(
                    '%s owns the session bus; leaving SMS to it', CHATTY_BUS_NAME)
            return
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
        if win._call_live() and win._call_ui is not None:
            win._call_ui.present()
