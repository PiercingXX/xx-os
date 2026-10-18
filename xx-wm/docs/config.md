# XX-WM — Config Reference

Every shell preference lives in `~/.config/xx-wm/`. The config file
**is the public API**: edit it in a terminal and the running shell hot-reloads
it (debounced; an invalid file is ignored and the last good config stays
live). The in-shell Settings page covers system things (network, sound
devices, battery, updates, backup) plus one Launcher section mirroring the
Android launcher's settings — home, widgets, weather, clock, drawer,
gestures, appearance — so a tablet without a keyboard can still change them.
Everything else is edited in the file directly.

Files:

- `config.json` — everything below except gestures
- `gestures.json` — gesture → action bindings

Missing keys fall back to their defaults; unknown keys are ignored. No
migration steps are ever required.

## config.json

### Appearance

| Key | Type | Default | Effect |
|---|---|---|---|
| `theme` | string | `"amoled"` | Preset key: `amoled`, `graphite`, `forest`, `ocean`, `paper`, `mist`, `aura` (Linux bonus), `burgundy`, or `custom`. |
| `custom_background` | string | — | `#RRGGBB` used when `theme` is `custom`; shades and text color are derived. Burgundy `#2A1018` is the blessed extra. |
| `prefer_dark` | bool | `true` | Light/dark surface mode (used when `appearance_mode` is `theme` or as the `system` fallback). |
| `appearance_mode` | string | `"theme"` | `theme` (follow the preset), `light` (Paper), `dark` (your dark preset), or `system` (Paper vs your dark preset from `org.gnome.desktop.interface color-scheme`). |
| `font` | string | `"jetbrains-mono-nerd"` | `system-light`, `monospace` (generic), `space-mono`, `jetbrains-mono`, `jetbrains-mono-nerd`, or `custom`. |
| `custom_font_family` | string | — | Family used when `font` is `custom`. Settings → Launcher → Import font… installs a `.ttf`/`.otf`, or drop a font in `~/.local/share/fonts` and set both keys. Font files are not part of backups. |
| `text_size_scale` | float | `1.0` | Launcher-wide type scale, clamped 0.5–2.0. Scales home slots, the clock and date, drawer and folder rows. |
| `home_scale` | float | `0.8` | Home screen only (app labels, folders, item spacing, clock, date), multiplied with `text_size_scale`. Clamped 0.5–1.5. |
| `home_alignment` | string | `"center"` | `left` / `center` / `right` for home rows, widget lines, drawer rows, folder and member rows. |

### Home screen

| Key | Type | Default | Effect |
|---|---|---|---|
| `home_slots` | list | `[]` | Up to 8 slots: `{type: 'app'\|'folder', label, app_id, cmd, folder}`. `app_id` is a `.desktop` id; `cmd` an argv list for non-desktop entries; `folder` a list of `{label, app_id or cmd}` members. Seeded on first boot when empty. |
| `home_to_recents` | bool | `false` | Pressing Home while already on home with nothing on top opens the app switcher instead of staying. |
| `home_slot_count` | int\|null | `null` | Android "Home Slot Count" (0–8): home shows the first N slots; empty ones render as `· · ·` and a tap picks an app; slots past N stay stored. `null` (never set) shows every slot. |
| `date_time_mode` | string | `"date_time"` | Android "Date/Time Mode": `"off"` hides the clock and date widgets, `"date_only"` hides the clock, `"date_time"` shows both. |
| `status_bar_visible` | bool | `false` | Android "Show Status Bar": the top status bar (status_bar.py) shows its clock and status glyphs. Off, the strip stays empty but still keeps apps below the camera hole. |
| `swipe_left_enabled` / `swipe_right_enabled` | bool | `true` | Android "Enable Swipe Left/Right". Off: that side swipe on home does nothing. |
| `swipe_down_action` | string\|null | `null` → `"notifications"` | Android "Swipe Down Action" on home: `"notifications"` (the shade), `"web_search"` (drawer search with the `!` web prefix), `"disabled"`. Seeded once from `gestures.json` `swipe_down_top` (`none` → `disabled`). |
| `default_layout_applied` | bool | `false` | Set once seeding has run; never re-seeds. |
| `app_labels` | dict | `{}` | `{app_id: label}` renames; shown everywhere, searched alongside original names. |
| `pinned` | list | `[]` | App ids surfaced first in the drawer browse list, in this order. |
| `hidden_apps` | list | `[]` | App ids hidden from the drawer browse list (still searchable). |
| `launch_counts` | dict | `{}` | Usage counters (recorded automatically; excluded from backups). Kept in memory and flushed to flash at most once per minute plus once on shutdown. |

### Widgets

| Key | Type | Default | Effect |
|---|---|---|---|
| `widgets` | dict | all enabled | Per-widget `{enabled, order, tap}` for `time`, `date`, `weather`, `battery`. `tap` ∈ `"default"`, `"none"`, `"refresh"`, or `{"app": "<app_id>"}`. Defaults: order time→date→weather→battery; weather tap `default` (opens a weather app, refreshing in the background). The clock also keeps double-tap-to-lock. |
| `weather_lat` / `weather_lon` | float\|null | `null` | Coordinates for the weather widget (Open-Meteo, no API key). Explicit floats win over `weather_location`, which wins over the automatic fix. Unset → the weather line is hidden (never a `--°` placeholder). |
| `weather_location` | string | `""` | Location for the weather widget as `lat,lon` (e.g. `"48.85,2.35"`), used when `weather_lat`/`weather_lon` are unset. Anything else is ignored. |
| `weather_temp_unit` | string | `"fahrenheit"` | `"fahrenheit"` or `"celsius"`: Open-Meteo unit and widget suffix (`72°F` / `22°C`). The 15-minute cache is keyed by unit. |
| `weather_auto_location` | bool | `true` | Coarse GeoClue fix when no manual coordinates are set (cached, refreshed at most every 30 minutes). Manual coordinates always win; with no fix the line stays hidden. |
| `clock_24h` | bool\|null | `true` | Home and lock-screen clock: `true` for 24-hour, `false` for 12-hour with no AM/PM, `null` to follow `org.gnome.desktop.interface clock-format`. |

### Drawer & search

| Key | Type | Default | Effect |
|---|---|---|---|
| `search_auto_launch` | bool | `false` | Launch immediately when a search narrows to one result (never for `!` queries). A leading space suppresses it, as does a matching Launcher Settings row. |
| `auto_show_keyboard` | bool | `false` | Android "Auto-show Keyboard": opening the drawer focuses search and raises the keyboard. |
| `drawer_sort` | string | `"default"` | Android "Sort Mode": `"default"` (A–Z, pinned first in their manual order), `"alphabetical"` (A–Z, pinned first), `"alphabetical_no_priority"` (plain A–Z), `"install_date"` (newest first, pinned on top), `"apk_size"` (largest installed package/Flatpak first, pinned on top; sizes are looked up off the main loop). Legacy `"az"`/`"install"` load as `alphabetical_no_priority`/`install_date`. Cycled by the drawer toggle button. |
| `preload_gesture_apps` | bool | `false` | Warm swipe-bound apps into RAM at session start so gestures open a resident process instead of cold-starting (the camera through the Android HAL can be slow). Opt-in — preloading is counter to the minimalism directive; only takes effect in a real session (`XX_WM_SESSION`). |

### Sounds & notifications

| Key | Type | Default | Effect |
|---|---|---|---|
| `sound_ringtone` | bool | `true` | Loop the ringtone on incoming calls. |
| `sound_notifications` | bool | `true` | Play `notify.ogg` on notifications (DnD, mutes, and `suppress-sound` still win). |
| `muted_apps` | dict | `{}` | `{app_id: until_epoch}` from "Disable for…" — notifications are discarded until the deadline. Expires silently; excluded from backups. |
| `lock_screen_notifications` | string | `"summary"` | `summary` (app + summary rows), `count`, or `off`. Hidden while DnD is active. |

### Screen capture

Power + Volume-down takes a screenshot (`grim`) to
`~/Pictures/Screenshots/Screenshot_YYYY-MM-DD_HH-MM-SS.png` with a
flash, a "Screenshot saved" notification (Share / Delete /
Capture more for a scrolling stitch), and the "Record" quick-settings
tile (expanded tier, hidden while `wf-recorder` is absent) records to
`~/Videos/Screenrecordings/`. Recording stops on screen blank and at
≤5% battery.

| Key | Type | Default | Effect |
|---|---|---|---|
| `record_audio` | bool | `false` | Pass `--audio` to `wf-recorder` so screen recordings capture microphone audio. |

### Do Not Disturb

| Key | Type | Default | Effect |
|---|---|---|---|
| `dnd_enabled` | bool | `false` | Manual toggle. Notifications collect silently; only exceptions ring. |
| `dnd_schedules` | list | `[]` | `{days: [0-6 Monday-first], start: "HH:MM", end: "HH:MM"}`; overnight ranges wrap. |
| `dnd_starred_numbers` | list | `[]` | Numbers that always ring through (star contacts by long-pressing a dialer suggestion). Repeat callers (twice within 15 min) also ring. |

### Focus Mode

| Key | Type | Default | Effect |
|---|---|---|---|
| `focus_enabled` | bool | `false` | Manual toggle; paused apps dim, won't launch, and their notifications are held until focus ends. |
| `focus_apps` | list | `[]` | App ids to pause (or use the drawer long-press "Focus: pause this app"). |
| `focus_schedules` | list | `[]` | Same shape as `dnd_schedules`. |
| `focus_break_until` | float | `0.0` | Break deadline (set by "Take a break" 5/10/15 min); transient, excluded from backups. |

### Nope-Mode

| Key | Type | Default | Effect |
|---|---|---|---|
| `nope_enabled` | bool | `false` | Master switch. Off means nothing is blocked, whatever the schedule says. |
| `nope_apps` | list | `[]` | Desktop ids that go silent and won't open while Nope is on. |
| `nope_schedules` | list | `[]` | Same shape as `dnd_schedules`. |
| `nope_override` | string | `"none"` | `"force"` (manual on) or `"break"` (bounded leave-early); `"none"` lets the schedule decide. |
| `nope_override_until` | float | `0.0` | Override deadline epoch; `0.0` is an indefinite ForceOn. Transient, excluded from backups. |
| `nope_break_log` | list | `[]` | Recent break start epochs enforcing the 30 min interval and 3-per-window budget. |
| `nope_min_interval` | int | `30` | Minimum minutes between breaks. Editable only while Nope is off. |
| `nope_budget` | int | `3` | Breaks allowed per schedule window. Editable only while Nope is off. |

While on, open windows of chosen apps are closed and their notifications are
dropped (not held like Focus). Calls still ring through DnD's starred and
repeat-caller exceptions.

### Screen time

| Key | Type | Default | Effect |
|---|---|---|---|
| `usage_stats_enabled` | bool | `true` | Record foreground seconds per app, unlocks, and notification counts (Settings → Screen time reads them back). While `false`, nothing is recorded and existing files are left alone. |
| `app_time_limits` | dict | `{}` | Per-app daily screen-time limits in minutes (`{app_id: minutes}`); reaching one shows a banner and greys the app in the drawer until midnight. |
| `app_limit_ignored` | dict | `{}` | `{app_id: "YYYY-MM-DD"}` the limit was ignored for ("Ignore for today"); only today's rows matter. |

Counts accumulate only while the screen is on and unlocked. Storage is
`~/.local/share/xx-wm/usage/YYYY-MM-DD.json` (atomic writes, mode 0600),
flushed at most every 5 minutes and on blank/lock — never per event.
Day files older than 35 days are pruned; day boundaries are local
midnight, and a session spanning midnight splits across both days.
Query API for the future UI lives in `launcher/src/usage_stats.py`
(`daily_totals`, `week`, `unlocks`, `notifications`).

### Emergency & Medical ID

The lock screen always shows an **Emergency** text button (bottom, in every
state — resting, keypad revealed, lockout). It opens an emergency surface
without unlocking: a dial pad that only places calls to emergency numbers,
one row per configured emergency contact (tap to call — explicitly allowed
from the lock screen), and Medical info. Emergency numbers are the built-in
list `112`, `911`, `999`, `000`, `08`, `110`, `118`, `119`, `100`, `101`,
`102`, `108` plus the modem/SIM's own list when ModemManager exposes one;
anything else typed on that pad is refused. Emergency calling itself cannot
be disabled.

| Key | Type | Default | Effect |
|---|---|---|---|
| `medical_id` | dict | `{}` | Medical ID shown read-only from Emergency → Medical info: `{name, date_of_birth, blood_type, allergies, medications, conditions, organ_donor (bool), notes, emergency_contacts: [{name, number, relation}]}`. Every field is optional; bad types are ignored, never crash. User data, not a secret: included in backup export/restore. |
| `lock_screen_medical_id` | bool | `true` | Show Medical info on the lock screen. `false` hides it; emergency calling stays available. |

### Smart Lock

Android parity: while the phone sits in a trusted situation, swipe-up
unlocks without the PIN. Both trust agents are opt-in and both lists are
empty by default (Smart Lock off). On-body detection is **not**
implemented — there is no reliable on-body sensor on this hardware.

Trust agents:

- A trusted Bluetooth device is currently connected (BlueZ
  `org.bluez.Device1` `Connected` for an address in
  `smart_lock_bt_devices`).
- The phone is connected to a trusted Wi-Fi network (the active
  NetworkManager connection's SSID is in `smart_lock_wifi_ssids`).

Even inside trust, the PIN is always required: after boot until the
first successful PIN unlock, when the last successful PIN unlock was
more than 4 hours ago, after 5 failed attempts / while any lockout
runs, and after tapping "Lock now" in the power menu (until the next
successful PIN unlock). Trust never unlocks the phone on its own — a
swipe is always required — and a trusted device that disconnects while
unlocked never locks the phone; the next lock simply requires the PIN
again. The lock screen shows a small muted line (`Kept unlocked by
<device / network>`) while trust waives the keypad, and the PIN-required
reason otherwise — but only while Smart Lock is configured.

| Key | Type | Default | Effect |
|---|---|---|---|
| `smart_lock_bt_devices` | list | `[]` | Trusted Bluetooth addresses (`AA:BB:CC:DD:EE:FF`; matched case-insensitively). Included in backups. |
| `smart_lock_wifi_ssids` | list | `[]` | Trusted Wi-Fi SSIDs (matched verbatim — SSIDs are case-sensitive). Included in backups. |
| `smart_lock_last_pin_time` | float | `0.0` | Wall-clock epoch of the last successful PIN unlock (the 4-hour clock). Written automatically; device-local transient — never exported in backups and never overwritten by a restore. |

### App standby & battery saver

Android Doze / app-standby parity: apps the shell launches are placed in
their own transient systemd user scope
(`app-xxwm-<app id>-<pid>.scope`) and frozen when unused — thawed
instantly on return (relaunch, switcher activation, notification tap, or
any other toplevel activation). Only shell-launched apps are ever frozen;
the shell, phoc, and everything else are never touched.

| Key | Type | Default | Effect |
|---|---|---|---|
| `app_standby_minutes` | int | `10` | Idle minutes (screen on) before an unused app freezes; `0` disables standby entirely. |
| `standby_exempt` | list | `[]` | App ids that are never frozen (compared without `.desktop`, case-insensitive). |
| `battery_saver` | bool | `false` | Manual saver toggle (also the "Saver" quick-settings tile). Halves `app_standby_minutes` (floor 1), freezes 30 s after blank instead of 2 min, and pauses automatic weather/update checks (manual refresh still works). |
| `battery_saver_auto` | bool | `true` | Turn the saver on automatically at low battery (see `battery_saver_auto_pct`); it stays on until the device is charging (or rises 5 points above the threshold). |
| `battery_saver_auto_pct` | int | `15` | Battery percent that trips the automatic saver (standby settings card: 10/15/20/25). Clamped 1..100. |
| `charge_limit` | int or null | `null` | Battery charge cap in percent (`null` = Off). Written through the root helper (50..100, clamped); the Settings → Charging control only appears when the battery driver exposes a charge-cap sysfs knob. |

Per-app CPU time for Settings → Battery use is read from each app's own
scope (`app-xxwm-<app id>-<pid>.scope` `cpu.stat` `usage_usec`) — the same
scopes standby already places — combined with foreground screen time into
an estimate labeled as such. There is no per-app power meter on Linux, so
shares are honest approximations, not measurements.

Always exempt regardless of timers: apps with an MPRIS player in Playing
state, apps holding a live PipeWire stream, voice-call apps (GNOME Calls,
xx-dialer, or whichever app currently owns `org.gnome.Calls`), the
on-screen keyboard (squeekboard/stevia), and apps whose notification
action was invoked in the last 60 s. On devices where systemd is not the
user manager (OpenRC) there are no scopes: pids are recorded and freezing
degrades to per-process SIGSTOP/SIGCONT.

### Privacy indicators & per-app permissions

Android/GrapheneOS parity for the three sensitive capabilities. The home
status strip gains a `● mic · cam · loc` tail (only the kinds currently in
use, in the theme accent color), and the same line sits at the right of
the notification-shade header — tapping it there reveals which apps hold
each kind. Text-first throughout: no dots-versus-icons, no colors outside
the active preset.

| Key | Type | Default | Effect |
|---|---|---|---|
| `network_denied_apps` | list | `[]` | App ids with no network access (compared without `.desktop`, case-insensitive, like `standby_exempt`). Enforced per launch by blocking the app's systemd scope cgroup through the root helper. |
| `privacy_indicators` | bool | `true` | Mic/camera/location indicators on home and in the shade. While `false`, snapshots are dropped before they reach the UI; the "recently used" log (`~/.local/share/xx-wm/privacy-usage.json`, last 24 h) keeps recording. |

Mic/camera detection reads the PipeWire graph (`pw-dump --monitor`, one
long-lived child off the main thread, restarted with backoff; polling
single-shot `pw-dump` every 3 s while the screen is on when the binary is
absent). A `Stream/Input/Audio` (mic) or `Stream/Input/Video` (camera)
node in state `running` counts as in-use, attributed by
`pipewire.access.portal.app_id` / `application.id` / `application.name`
(preferring the human name). Location users are the GeoClue2 clients
holding a live fix, read from the shell's existing GeoClue agent state —
no second agent is registered.

Network enforcement ordering per launch is: place the pid in its scope,
then `SetAppNetwork(scope_cgroup, False)` through the helper, then the
app runs. The residual race is honest: the pid exists before its scope
does, so packets sent between exec and the nft rule landing cannot be
stopped from the shell — closing it would need exec interception (a
helper fork), which does not exist yet. Scope names embed the pid, so the
block is re-applied on every launch and never persists stale paths; the
cgroup path is always verified against `/proc/<pid>/cgroup`, never
assumed.

Camera/microphone/location policy per app lives in the xdg-desktop-portal
PermissionStore (`org.freedesktop.impl.portal.PermissionStore`, tables
`devices` with ids `camera`, `microphone`, `speakers`, and `location`
with id `location`; values `yes`/`no`, deleting resets to ask). This
governs portal-mediated access only — Flatpak and portal-using apps.
Native apps reach `/dev/video*` and PipeWire directly, bypassing the
store entirely; for those the live indicators above are the control
surface, not the store.

### System

| Key | Type | Default | Effect |
|---|---|---|---|
| `auto_lock_timeout` | int | `0` | Seconds the display must stay blanked (idle blank or power-key blank) before the lock screen shows; `0` locks immediately on blank. Old values keep working — they now count darkness, not shell-window idleness. Never locks while a call is non-terminated. |
| `display_blank_timeout` | int | `60` | Idle seconds before the display blanks (backlight off); `0` never blanks. |
| `pin_hash` | string | — | SHA-256 of the PIN (minimum 6 digits). Never exported in backups. |
| `lock_screen_lockout` | dict | — | Private escalating-lockout resume point (`{fails, until}` wall-clock epoch), written automatically on wrong PINs and cleared on unlock. Survives shell restarts; never exported in backups and never overwritten by a restore. |
| `apn` / `apn_user` / `apn_pass` | string | — | Mobile-data APN pushed to NetworkManager (Settings page owns these). |
| `update_script` | string | PiercingXX menu path | Script run by "Update system". |
| `update_last_check` / `update_snooze_until` | float | `0.0` | Daily update-check bookkeeping. |
| `keyboard_layout` | string | `"us"` | Primary layout (`us` QWERTY default, `us+colemak` Colemak, `piercing-num` Piercing Num OSK-only). Session start maps `piercing-num` to `us` for the hardware xkb keymap; Settings → Keyboard applies live. xx-keyboard offers primary first, then QWERTY and Piercing Num. Included in backups. |
| `setup_complete` | bool | absent | Written by first boot: `false` from the Keyboard step (or the Status bar step, if it writes first), `true` at Finish. `false` reruns the wizard at next start (a reboot mid-setup must not skip the PIN). Absent counts as done, for configs made before the key existed. |
| `night_light` | bool | `false` | Night-light master switch state (Settings → Display, "Night" tile). |
| `night_light_temp` | int | `2200` | Night-light temperature in Kelvin, clamped 2200–6500. Included in backups. |
| `night_light_schedule` | bool | `false` | Legacy: `true` reads as `night_light_mode` `sunset`. Written alongside the mode for older readers. |
| `night_light_mode` | string | `"off"` | Night-light schedule: `"off"` (manual — the tile holds), `"custom"` (on between `night_light_from`/`night_light_to`, overnight ranges wrap), or `"sunset"` (sunset to sunrise from the weather coordinates, falling back to the custom times without a location). Included in backups. |
| `night_light_from` / `night_light_to` | string | `"22:00"` / `"06:00"` | Custom night-light window as `HH:MM`. Included in backups. |
| `power_profile` | string | `"balanced"` | Power profile: `"power-saver"`, `"balanced"` or `"performance"`. Applied through power-profiles-daemon while it runs with a real driver, else through the root helper's cpufreq governor map; re-applied at session start. Battery saver forces `power-saver`. Included in backups. |
| `status_bar_height` | int\|null | `null` | Height of the top status bar in logical px, clamped 20–96. It is first set by the first-boot Status bar step, between Theme and Timezone: a live strip at the top of the screen follows a slider that starts at the device default, Next saves the height and Use default writes `null`. Settings → Display, "Status bar height", changes it later. The bar reserves this much at the top edge so apps start below the camera hole. `null` uses the device default: 64 on the FuriPhone FLX1 (its punch-hole sits inside the bar), 28 elsewhere. Device-local: never exported in backups. |

### Wireless debugging (xxdb)

The phone-side daemon from the Settings page's Developer section. Off by
default; enabling it is always an explicit user action (the switch). Keys
and paired-host fingerprints live under `~/.local/share/xx-wm/xxdb/`
(files are 0600).

| Key | Type | Default | Effect |
|---|---|---|---|
| `xxdb_enabled` | bool | `false` | Whether wireless debugging is on. Written by the switch; reset to `false` on every shell restart/reboot unless `xxdb_persistent` is true. |
| `xxdb_persistent` | bool | `false` | When true, an enabled daemon stays enabled across shell restarts/reboots. Even then it still turns itself off after 60 minutes with no connected client. |

## gestures.json

Flat `{gesture: action}`. Valid actions: `home`, `app_switcher`,
`notification_shade`, `back`, `search` (drawer + keyboard focus),
`lock_screen`, `settings`, `camera`, `dialer`, `assistant`, `none`, or
`launch:<app_id>` for any installed app (an uninstalled target behaves as
`none`).

focused. Their bindings are generated at session start from this file.
Values may be an IPC verb (`gesture.home`, `gesture.shade`,
`gesture.back`, `gesture.keyboard`, `gesture.switcher`) **or** the
settings-UI action names `home`, `app_switcher`, `notification_shade`,
`back`, `search` (mapped to those verbs via `ACTION_TO_VERB`:
`home`→`gesture.home`, `app_switcher`→`gesture.switcher`,
`notification_shade`→`gesture.shade`, `back`→`gesture.back`,
`search`→`gesture.keyboard`). `launch:<app>` stays in-shell
and does not replace the lisgd verb. Two fallbacks: missing or corrupt
JSON loads schema defaults then `ACTION_TO_VERB` (short → `gesture.home`,
long → `gesture.switcher`); a **valid unmapped** value (`camera`, `none`,
`launch:…`) keeps that slot's legacy `DEFAULT_VERBS` entry (legacy: short →
`gesture.keyboard`, long → `gesture.home`); a truly invalid value is
ignored at load so the default action mapping applies instead. A file
that still has both bottom-up slots as `app_switcher` (the previous
default pair) is rewritten once so short becomes `home`. Garbage keys
are dropped at load, so they take the schema-default path, not
`DEFAULT_VERBS`.
Rebinding restarts lisgd immediately from Settings. If that restart
fails, it applies at the next session start.

The bottom edge is the shell's own gesture strip (`bottom_gesture.py`),
not lisgd, whenever the strip is up: an invisible 18 px OVERLAY strip that
also works over fullscreen apps. A quick flick up runs `swipe_up_short`;
swiping up past 12% of the screen height and holding still for 250 ms,
or releasing slowly past 25%, runs `swipe_up_long`; a sideways swipe
along the edge brings up the neighbouring app in recents (right: the
card to the left, left: the card to the right). Both slots resolve
exactly as above. While the strip is up the shell restarts lisgd without
its bottom-edge bindings, so nothing fires twice; without layer-shell
support there is no strip and lisgd keeps the bottom edge. A tap on the
strip is passed through to whatever is under it.

| Gesture | Default | Notes |
|---|---|---|
| `swipe_down_top` | `notification_shade` | lisgd → `gesture.shade`. Verb-rebindable. |
| `swipe_up_short` | `home` | Bottom strip → `gesture.home`. Verb-rebindable. A quick flick up from the bottom goes home from any screen (including recents). Missing/corrupt JSON uses this mapped default, not `gesture.keyboard`. |
| `swipe_up_long` | `app_switcher` | Bottom strip → `gesture.switcher`. Verb-rebindable. Swipe up and pause (or release slowly past a quarter of the screen) to open full-screen recents. Swipe down, tap the dim, or back resumes the current app. |
| `swipe_left_edge` | `back` | lisgd → `gesture.back` on both edges. Verb-rebindable. |
| `long_press_bottom` | `search` | |
| `double_tap_home` | `none` | Android "Double Tap to Lock": `lock_screen` or `none` (any older action loads as `none`). The clock keeps its own double-tap-to-lock. |
| `long_press_home` | `edit` | Long-press on the home background: `edit` opens slot edit mode, `settings` opens Settings, `none` does nothing. |
| `swipe_left_home` | `none` | Android "Swipe Left App". `none` = no app chosen: the swipe opens the camera. Seeded to `launch:<skippy>` when Skippy is installed. |
| `swipe_right_home` | `none` | Android "Swipe Right App". `none` = no app chosen: the swipe opens the dialer. Seeded to `camera` on first boot. |
| `squeeze` | `assistant` | Hardware-gated (Active Edge); needs device support. |
| `fingerprint_swipe` | `notification_shade` | Hardware-gated; needs device support. |
| `double_press_power` | `camera` | Hardware-gated; needs device support. |

## Not in these files

- Contacts: `~/.local/share/xx-wm/contacts.json` or GNOME Contacts VCF.
- Usage statistics: `~/.local/share/xx-wm/usage/YYYY-MM-DD.json` (see Screen time above).
- Weather cache: `~/.cache/xx-wm/weather.json` (safe to delete).
- Logs: `~/.local/share/xx-wm/shell.log`.
- Backups: versioned JSON via Settings → Backup; see `launcher/src/backup.py` for the exact scope.
