# xx-auto — Linux Phone Port Spec

Port of the PiercingXX Android app `xx-auto` (Kotlin/Compose,
`ref/xx-auto/`, design locks AU1–AU14) to Linux phones running phoc
(XX-WM or Phosh). Stack: Python 3.12+, GTK4 (+ libadwaita via the
`Adw.Application` shell only, as xx-calendar does), meson install,
stdlib + GI only. App id `io.piercingxx.XXAuto`.

Visual target: `ref/xx-auto` drive screen (Ink ground, 72 px targets,
24 px body / 40 px title) in portrait 360x720 and landscape 720x360.
Theme: shared `common/xx_ui`; every preset works live; `always_ink`
(default on) forces the amoled preset for this app only.

## 1. Android → Linux mapping

### M1. Media: Media3 → MPRIS2 (AU4–AU6)

- Transport over the **session bus**: every `org.mpris.MediaPlayer2.*`
  name is a candidate player. Read `Metadata` (`xesam:title`,
  `xesam:artist`, `mpris:artUrl`, `mpris:length`), `PlaybackStatus`,
  `Position` + `Rate`, `CanGoNext/Previous`, `Volume`; call
  `Play/Pause/PlayPause/Next/Previous/Seek/SetPosition`.
- Active player = the one with `PlaybackStatus == "Playing"`; else the
  one most recently seen with a track; else none → `Nothing playing`
  (ports `SessionPick.active`).
- Quick-pick sheet lists the MPRIS player names (radio stations =
  players on Linux; there is no browsable library tree over MPRIS).
  Tapping a row `Raise()`s that player and `Play()`s it when it is
  not playing.
- Suite preference: names whose MPRIS `DesktopEntry` is
  `io.piercingxx.*` sort first, in most-recent order; all other
  players follow. That covers the radio app `io.piercingxx.XXRadio`
  (MPRIS `org.mpris.MediaPlayer2.xx_radio`) and the audiobook app
  `io.piercingxx.XXAudiobook` (MPRIS
  `org.mpris.MediaPlayer2.xx_audiobook`), so the Radio/Audiobook
  tiles invert correctly. Another app built on the shared music core
  registers the same way and is treated the same; no
  player-specific code anywhere in this path (AU4 holds).
- Installed detection: each tile checks its desktop id
  (`io.piercingxx.XXRadio.desktop`,
  `io.piercingxx.XXAudiobook.desktop`) via `Gio.DesktopAppInfo`
  (`media_apps.installed`, resolved on the poll worker). Missing →
  the tile reads `not installed` and stays insensitive.
- Tile taps: when the tile's player is on the bus the tap opens the
  quick-pick sheet; when it is not (nothing playing for that tile)
  the tap launches the tile's app instead (Radio → xx-radio,
  Audiobook → xx-audiobook). The lookup is by tile, never by app
  id, so another music-core app takes the sheet path as well.
- Custom buttons (thumbs): MPRIS has no standard. Optional extension
  `io.piercingxx.XXAuto.Actions1` at `/org/mpris/MediaPlayer2`:
  `ListActions() → a(sssb)` rows of `(id, label, icon_name, enabled)`
  plus `ActivateAction(in s id)`. Rendered in session order when a
  player offers the interface; cards stay correct without it
  (transport only). Each button draws its `icon_name` from the icon
  theme (else its label) on the same 72 px keys as the transport.
  Adwaita has no thumbs, so xx-auto ships
  `xx-auto-thumb-up-symbolic` and `xx-auto-thumb-down-symbolic`:
  xx-radio names them for Like/Dislike, and the audiobook app's
  skip/bookmark rows carry no icon names, so they render as labels.

### M2. Navigation tile (AU7)

Resolution order: `Gio.DesktopAppInfo.new("io.piercingxx.XXMaps.desktop")`
→ default handler for `x-scheme-handler/geo` → `org.gnome.Maps`
→ anything else handling `x-scheme-handler/geo`. None installed →
tile reads `Maps — not installed` and opens the app store listing
(`io.piercingxx.Apps` when present). Never launches anything but a
maps app (ports `MapsLaunch.resolve`: drive action → launcher →
not-installed).

### M3. Calls: favourites + handoff (AU8)

Starred contacts come from xx-contacts over its public D-Bus API
(`io.piercingxx.Contacts` / `/io/piercingxx/Contacts` /
`io.piercingxx.Contacts1`: `List(in t since) → (t, as, as)`, `Get(in s
uid) → (s vcard)`). Filter vCards with `X-STARRED:1` (store
`VALID_TAGS` includes `starred`), show display name + one number, at
most 8 rows. Primary number prefers a `CELL`/mobile `TEL` (ports
`FavouritePick.primaryNumber`). Tap hands a `tel:` URI to
`Gio.AppInfo.launch_default_for_uri` (xx-dialer). Never an in-call UI.
The Calls surface stays off until the user enables it.

### M4. Auto-launch on car Bluetooth (AU2)

Off by default. When on: one saved paired-device address; BlueZ
`org.bluez.Device1` on the **system** bus is the source of truth.
`xx-auto --watch` (systemd user unit `xx-auto-watch.service`,
installed but enabled/started only while the toggle is on) opens
xx-auto on `Connected == true` for the saved address and closes it on
disconnect **only if it opened it** (tracks the child PID; ports
`CarConnectDecision.decide`: open / open-via-notification collapses to
open on Linux since no overlay gate exists / close / ignore, with
case-insensitive address compare). A manual launch never self-closes.
There is no notification fallback path on Linux: the watcher either
runs (toggle on) or does not exist.

### M5. Screen options (AU9)

- `keep_screen_on` (on): `Gtk.Application.inhibit(window, IDLE)` while
  the drive window is visible, uninhibit on hide.
- `follow_rotation` (on): the compositor owns rotation — phoc
  resizes the toplevel and the layout reflows (portrait column vs
  landscape card-left/tiles-right). Off requests a 720x360 landscape
  window instead of locking the sensor (there is no sensor API to
  lock); the toggle and its `Off = landscape` caption stay honest.
- `always_ink` (on): forces the amoled preset for this app only; off
  follows the suite theme from `~/.config/xx-wm/config.json`.

### M6. Settings, backup, log, licenses

- Settings live in `~/.config/xx-auto/settings.json` (mode 0700 dir,
  0600 file, atomic tmp+rename). Keys and defaults mirror the Android
  table: `auto_launch` off, `auto_launch_device` null,
  `surface_now_playing` / `surface_quick_pick` / `surface_nav` on,
  `surface_calls` off, `keep_screen_on` / `follow_rotation` /
  `always_ink` on. Unknown keys ignored; corrupt file → defaults.
- xx-wm backup: the shell backup covers shell config only — no
  sibling app feeds it — so xx-auto is **not** added there either.
  `settings.json` itself is the backup unit (`backup.py`
  export/parse round-trips it).
- Log: `AppLog` ring buffer (300 lines) with file rotation, same
  shape as the Android one; messages are redacted (tokens, emails,
  and phone-number runs) and contact numbers are never logged.
- Licenses/About screen lists the app (GPL-3.0) plus the suite note
  `Free and ad-free. Collects no personal data. Nothing here leaves
  your device.` No network anywhere: no `http`, `urllib`, `requests`,
  or socket code in `src/` (pinned by test).

### M7. Out of scope on Linux (per brief)

`gms` flavor / Android Auto projection (AU12), external
`Presentation` display (AU13), suite backup provider. No new
dependencies, no daemons besides `--watch`, no D-Bus service name
(the GUI keeps `io.piercingxx.XXAuto` to itself).

## 2. Pure-logic ports (JVM → pytest, same branches)

| Android | Linux module | Rule |
|---|---|---|
| `SessionPick.active` | `session_pick.active` | playing > most-recent item > none |
| `Elapsed.format/row` | `elapsed.format_ms/row` | `m:ss` under an hour, `h:mm:ss` above, clamp |
| `FavouritePick.primaryNumber` | `favourite_pick.primary_number` | mobile first, else first, else none |
| `CarConnectDecision.decide` | `car_decision.decide` | open/close/ignore, case-insensitive BT addr |
| `SettingsMapper` + `Settings` | `settings_store` | prefs map ⇄ dataclass, design defaults |
| `MapsLaunch.resolve` | `maps_resolve.resolve` | drive → launcher → not-installed |
| `DriveTiles.visible` | `drive_tiles.visible` | quick-pick/nav/calls toggles gate tiles |
| `AppLog` ring | `applog` | cap, redact, dump |

## 3. Threading / bus rules

- No blocking call on the GTK thread: MPRIS polling and the
  xx-contacts `List`/`Get` fan-out run on a worker thread and post
  back with `GLib.idle_add`. D-Bus `reply_type` is always a real
  `GLib.VariantType`, never a string; signal args follow the
  7-arg `signal_subscribe` shape; replies unpack as tuples.
- Tests never touch the real buses: a `private_bus` fixture starts
  its own `dbus-daemon`; fake MPRIS players and a fake xx-contacts
  service register on it with the exact bus names/paths/interfaces.
