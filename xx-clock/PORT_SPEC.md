# xx-clock — Linux Phone Port Spec

Target: postmarketOS / Mobian / FuriOS phones running phoc (XX-WM or Phosh).
360x720 logical px (works at 320 px wide and 600x1333 FLX1), touch-first.
Stack: Python 3.12+, GTK4 + libadwaita via PyGObject, meson install,
stdlib + GI libraries only (GStreamer for sound, feedbackd for vibration).

Reference: `ref/xx-clock/` (Android Kotlin app, read-only). The public Play
edition ("Alarms and timers"): offline, no `INTERNET`, no Play Services,
no analytics. App id `io.piercingxx.XXClock`, window title `xx-clock`.

## 0. What the Android app is

Three tabs under one activity (`ui/MainActivity.kt` + `menu/bottom_nav.xml`):

- **Clock** (`ui/ClockFragment.kt`): big digital time (`TextClock`), date
  line, next-alarm card (`AlarmRepository.nextArmed()` → label + `Fmt.time`
  + `Fmt.until`), digital/analog toggle persisted in `xx_clock_ui` prefs.
- **Alarms** (`ui/AlarmsFragment.kt`, `ui/AlarmsAdapter.kt`,
  `ui/AlarmEditDialogFragment.kt`): list sorted by hour/minute; rows show
  time, `Alarm.summary(daysMask)`, label, vibrate mark, enable switch; tap
  edits, long-press/swipe deletes; FAB creates `Alarm.newAlarm(next
  round half hour)`; editor has time picker, 7 day chips Mon..Sun, label,
  vibrate switch; snackbar "Alarm set for X" via `scheduledFire`.
- **Timers** (`ui/TimersFragment.kt`): preset chips 1/3/5/10 min + custom
  minutes; cards show `TimerMath.display(remaining)`, label, pause/resume,
  reset, +1 min, delete; 500 ms UI tick; expiry handled by the coordinator.
- **Setup** (`ui/SetupActivity.kt`): permission checklist (notifications,
  exact alarms, DND access, full-screen intent, battery exemption) + theme
  picker + OSS licenses + the Nope-Mode warning card.
- **Ring path**: `AlarmManager.setAlarmClock()` → `AlarmEventReceiver` →
  `AlarmCoordinator` → `RingService` (foreground, screen wake lock,
  `KlaxonPlayer` on `STREAM_ALARM`, vibration) + `AlarmAlertActivity`
  full-screen alert (always dark). Timers use `setExactAndAllowWhileIdle`.
  Auto-silence at 10 min, snooze default 10 min, volume ramp 60 s.
- **Home widget** (`widget/DigitalWidgetProvider.kt`): clock, date,
  next alarm. **Direct boot**: device-protected storage + locked-boot
  receiver so alarms re-arm before first unlock.

## 1. Feature inventory (Android) → Linux implementation

### 1.1 Screens

| Android | Behavior | Linux implementation |
|---|---|---|
| `MainActivity` bottom nav (clock/alarms/timers) | 3 tabs, remembers last tab, `EXTRA_TAB` deep link | `Adw.ApplicationWindow` + `xx_ui.widgets.bottom_tabs` (Clock/Alarms/Timers), `Gtk.Stack`, last tab in app config. Module `ui/shell.py` |
| `ClockFragment` | big time, date, next-alarm card, digital/analog toggle | `ui/clock_page.py`: `xx-display-face` hero time, date line, next-alarm card (hidden when none), analog `Gtk.DrawingArea` toggle. Same prefs key `clock_style` |
| `AlarmsFragment` + adapter + edit dialog | sorted list, switch rows, edit dialog, FAB, snackbar | `ui/alarms_page.py` + `ui/alarm_dialog.py`: same rows/switches (inverted `xx_ui` switch, never Adwaita blue), day chips Mon..Sun, label, vibrate, per-alarm sound, snackbar via `Adw.ToastOverlay` |
| `TimersFragment` | preset chips, cards, 500 ms tick | `ui/timers_page.py`: chips 1/3/5/10 + custom minutes, same card buttons, `GLib.timeout_add(500)` tick while visible |
| `SetupActivity` checklist | 5 permission cards + theme + licenses | `ui/setup_page.py` (header button): **reliability page**, not permissions — daemon state, autostart, suspend limitation notice (§4), snooze length, gradual volume, sound test, licenses |
| `AlarmAlertActivity` (always dark) | full-screen ring card, Dismiss / Snooze / Stop / +1 min | `ui/ring_card.py`: `OVERLAY` layer-shell window like `call_ui` (§5), always ink. Daemon also posts urgency-critical notification with the same actions so the lock screen shows it |
| Home widget | clock/date/next-alarm on launcher | OUT — xx-wm has no widget host. The Clock tab's next-alarm card is the equivalent surface |

### 1.2 Pure logic (ported with tests)

| Android | Linux module | Notes |
|---|---|---|
| `model/Alarm.kt` mask/summary | `alarm_logic.py` | bit0=Mon..bit6=Sun, `Once/Weekdays/Weekends/Every day/Mon, Wed`; Monday-first ordering kept |
| `model/TimerItem.kt` states | `timer_math.py` + store | IDLE/RUNNING/PAUSED/FINISHED, deadline-based so timers survive death |
| `time/TimerMath.kt` | `timer_math.py` | `remaining_ms`, `is_expired`, `display` (`MM:SS` / `H:MM:SS`, rounds up) |
| `time/NextOccurrence.kt` | `next_occurrence.py` | zoneinfo-based, DST-gap safe, 8-day scan, strictly at/after now |
| `util/Fmt.kt` | `fmt.py` | `until` (`2 d 7 h 53 m`); `format_time` is locale 24h/12h like `DateFormat.getTimeFormat` |
| `repo/AlarmRepository.nextArmed` core | `next_armed.py` | enabled OR snooze-pending counts; latest of snooze/scheduled fire; soonest wins |
| `repo/TimerRepository.startOrReuse` | `coordinator.start_or_reuse_timer` | identical idle reuse (same duration + label) |
| `audio/RingCandidates.kt` | `ring_candidates.py` | chosen → default alarm → notification default; blanks dropped, deduped |
| `scheduler/ExactScheduler.requestCode` | `request_codes.py` | same kind<<29 \| hi-xor-lo fold; keys daemon GLib sources |
| `data/ClockStore` monotonic ids | `ids.py` + `store.py` | one shared alarm/timer namespace, floor = max(stored ids, wall clock) |
| `alarm/AlarmCoordinator.kt` | `coordinator.py` | same seams; one-shot disables at fire, repeating pre-schedules, newest-wins, missed → notification |
| `Prefs.kt` constants | `prefs.py` | snooze 10 min, auto-silence 10 min, timer boot grace 15 min, volume ramp 60 s |
| `theme/ThemePreset.kt` contrast rule | `family_theme.py` | Android broadcast names → xx_ui preset keys; luminance `0.299r+0.587g+0.114b > 182` rule kept for custom grounds |
| `log/AppLog.kt` ring log | `applog.py` | local file + bounded ring only; **no dump provider, no upload** |

### 1.3 Out of scope and why

- **Home-server / home-server login / log upload**: the task drops them. In ref
  this is `suite/backup/` (`SuiteBackupProvider` → xx-apps → the home server)
  and `log/LogDumpProvider.kt`. The Linux port keeps alarms/timers
  on-device (SQLite); the DB file itself is the backup surface. No network
  code exists in the port.
- **Stopwatch with laps / world clocks**: ref/xx-clock has neither (grep
  over `app/src/main/java` finds no stopwatch/world-clock code; the three
  tabs are clock/alarms/timers). Not invented here, per the estate rule
  "Do not invent features". The Clock tab answers "what time is it".
- **Widget**: no widget host in xx-wm (see §1.1).
- **`AlarmClock` intents** (`SET_ALARM`, `SET_TIMER`, …): Android-only IPC.
  The Linux equivalent is the daemon D-Bus API (§3); there is no system
  intent bus to register with.
- **Permission checklist** (notifications / exact alarms / DND /
  full-screen / battery): no such gates on Linux. Replaced by the
  reliability page (§4).
- **Direct boot**: no credential-encryption split on Linux phones; the
  systemd user unit starts the daemon at login, before the GUI.
- **Nope-Mode / Doze / exact-alarm permission**: Android power-management
  concepts. The Linux analogues are suspend (device stays up, §4) and the
  degraded-scheduling marker, which has no Linux trigger and is not ported.

## 2. Data

SQLite via stdlib `sqlite3` at `$XDG_DATA_HOME/xx-clock/clock.db`
(dirs created mode 0700 on first run). Tables `alarms`, `timers`,
`runtime` (per-id `scheduled_fire`, `snoozed_until`, `ringing`), `meta`
(`next_id`). Row decode is per-row tolerant like `ClockStore.decodeList`.
User settings (snooze minutes, gradual volume, clock style, last tab) live
in `$XDG_CONFIG_HOME/xx-clock/settings.json`.

## 3. D-Bus API (the shell contract)

Daemon owns `io.piercingxx.XXClock.Daemon`, object
`/io/piercingxx/XXClock`, interface `io.piercingxx.XXClock.Daemon`.
The GUI app id `io.piercingxx.XXClock` stays with the GTK app (a daemon
sharing it would make the GUI exit as a secondary instance).

- `GetState() → s`: JSON `{alarms, timers, ringing, next}` for any client.
- `SaveAlarm(s alarm_json) → x`, `DeleteAlarm(x) → b`,
  `ToggleAlarm(x, b) → b`, `SnoozeRinging(x, i minutes) → b`,
  `DismissRinging(x) → b`.
- `StartTimer(x duration_ms, s label) → x`, `PauseTimer(x) → b`,
  `ResumeTimer(x) → b`, `ResetTimer(x) → b`, `AddMinuteTimer(x) → b`,
  `DeleteTimer(x) → b`, `StopTimerRinging(x) → b`.
- Signals: `StateChanged`, `RingingChanged(x id, b ringing, s kind, s label)`.
- Activation: `share/dbus-1/services/io.piercingxx.XXClock.Daemon.service`.
  The daemon never idle-exits while armed (a clock must outlive its window).
- Writes go through the daemon; it is the single writer and the only
  `RingingChanged` emitter. Direct store access is the offline/test fallback.

## 4. Wake-from-suspend (investigated, honest)

- **What blank does**: xx-wm's `display_manager.py` blanks the backlight /
  output on idle or power-key press and tracks blank state internally; the
  lock screen follows the display. **Nothing in the blank path suspends the
  device** — suspend is only the explicit power-menu row, which calls
  logind (`power_menu.py` queries `CanSuspend` over logind's Manager).
  So with the screen off, the user session (and the daemon's GLib clock)
  keeps running: alarms and timers fire.
- **RTC wake unprivileged**: not available. `systemd` user timers with
  `WakeSystem=` need privileges the session does not have, and logind
  offers no unprivileged wakealarm. Writing `/sys/class/rtc/rtc0/wakealarm`
  needs root. So a device that is **suspended or powered off cannot be
  woken by this app**.
- **Implemented**: `xx-clock-daemon.service` user unit
  (`WantedBy=default.target`, restart on failure) + D-Bus activation; the
  daemon reconciles on start (force re-register, the Android `force` rule:
  persisted bookkeeping can look current while the timers are gone).
  Missed alarms become "Missed alarm" notifications; timers expired within
  the 15-minute boot grace still ring, older ones return to idle.
- **Limitation stated in-app**: the Setup → Reliability page says alarms
  do not fire while the phone is suspended or off, and offers the systemd
  unit state + enable hint.

## 5. Ringing UI over the lock screen

xx-wm's `call_ui.CallWindow` shows over the lock as a `Gtk4LayerShell`
`OVERLAY` surface anchored on all edges with an exclusive zone of -1
(`call_ui.py`, same compositor: phoc layer-shell v3, keyboard NONE).
`ui/ring_card.py` does exactly the same for the alarm/timer ring card, in
the always-ink style of `AlarmAlertActivity` (3 a.m. is not the moment for
a Paper screen). In parallel the daemon posts an urgency-critical
notification (`Notify` with `urgency: 2` byte, actions Snooze/Dismiss or
Stop/+1 min) — the lock screen already renders notifications
(`lock_screen.py` `.lock-notif` styles), so a locked phone still shows the
ring with actions. Sound: GStreamer playbin at alarm volume with the 60 s
ramp; vibration: feedbackd `TriggerFeedback` (`alarm-clock-elapsed`),
async with a silent fallback when feedbackd is absent. **No xx-wm changes
were needed**; the documented `RingingChanged` signal (§3) is the hook if
the shell ever wants its own surface.

## 6. Look

`common/xx_ui` design system, every preset live via `xx_ui.apply` +
`on_change` (same pattern as xx-auto, including the always-ink ring card).
Text-first, monochrome per theme, inverted selection. Phone fit: 360x720,
320 px min, 600x1333 FLX1, large/dynamic text — every body scrolls, labels
ellipsize or wrap, `snap.py` WARNING is a failure.

## 7. Ports of record

JVM tests translated: `AlarmMaskTest`, `TimerMathTest`,
`NextOccurrenceTest`, `AlarmAlertsTest` (wake/alert policy parts),
`AlarmClockApiTest` (pure request validation: hour/weekday mapping,
1 s..24 h timer range, dismiss modes, snooze clamp — intents OUT, the
rules live on as D-Bus input validation), `AlarmToggleThenSaveTest`
(save-after-toggle-off stays disabled), `ClockSystemIntentsTest`
(`--tab=` routing only — manifest assertions OUT), `NextArmedTest`,
`SchedulerRequestCodeTest`, `ExactSchedulerTest`
(schedule/reconcile policy; exact-permission and manifest tests OUT —
no AlarmManager, no manifest), `UniqueIdGenerationTest`,
`RingCandidatesTest`, `AlarmCoordinatorTest`, `ThemePresetTest` (pure
contrast/resolve parts; receiver/applier tests OUT — Linux theming is
xx_ui). `ClockBackupProviderTest` is OUT — the backup provider is
dropped (§1.3).
