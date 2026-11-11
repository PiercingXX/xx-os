# D-Bus registry

Every bus name in XX-Linux-Phone, who owns it, and who calls it. Rules that apply to all of them:

- A background service never owns the same name as a GTK app's `application_id` (the GUI would think it's a second instance and exit).
- Services that own data are the **single writer**; GUIs may read their own SQLite files directly, but every write goes through the service so it can emit `Changed`.
- Idle-exiting services ship a session activation file (`share/dbus-1/services/<Name>.service`); clients call methods directly with auto-start and never gate on `NameHasOwner`.
- Notifications whose actions must work after the poster exits carry the `x-xx-wm-action-target` hint (see [xx-wm/docs/notifications.md](../xx-wm/docs/notifications.md)); the target only acts on notification ids it posted.

## Services (session bus unless noted)

| Bus name | Object path | Interface | Owner (process) | Activation | Main callers |
|---|---|---|---|---|---|
| `io.piercingxx.Contacts` | `/io/piercingxx/Contacts` | `io.piercingxx.Contacts1` | `xx-contacts --service` | activatable, idle exit | shell, dialer, TxxT, email, xx-wm Medical ID |
| `io.piercingxx.XXDialer.Policy` | `/io/piercingxx/XXDialer/Policy` | `io.piercingxx.XXDialer.Policy1` | `xx-dialer --service` | activatable, idle exit | shell (`CheckNumber` before ringing, `NoteCall` after) |
| `io.piercingxx.TxxT.Daemon` | `/io/piercingxx/TxxT/Daemon` | `io.piercingxx.TxxT1` | `txxt --daemon` (owns SMS/MMS) | activatable + autostart | TxxT UI (`Send`, `SendMms`, `MarkRead`, `MessagesChanged`); shell defers SMS while it's owned |
| `io.piercingxx.Calendar` | `/io/piercingxx/Calendar` | `io.piercingxx.Calendar1` | `xx-calendar --service` | activatable, idle exit | calendar UI, reminders, shell shade |
| `io.piercingxx.XXEmail.Daemon` | (actions only) | | `xx-email --daemon-tick` (systemd timer) | timer | notification actions |
| `io.piercingxx.XXShare1` | `/io/piercingxx/XXShare` | `io.piercingxx.XXShare1` | `xx-share --service` | systemd user unit | xx-share UI |
| `io.piercingxx.Captions1` | `/io/piercingxx/Captions1` | `io.piercingxx.Captions1` | `python -m xx_captions --serve` | activatable | caption overlay, shell Captions tile |
| `io.piercingxx.XXWM.Helper1` (**system bus**) | `/io/piercingxx/XXWM/Helper1` | `io.piercingxx.XXWM.Helper1` | root helper (polkit) | system activation | shell (charge limit, per-app network, freeze, firewall, services, lock PIN, RTC wake for alarms); methods below |
| `org.freedesktop.ScreenSaver` | `/org/freedesktop/ScreenSaver` and `/ScreenSaver` | `org.freedesktop.ScreenSaver` | xx-wm shell (`screensaver_service.py`), only when nobody else owns it | with the shell | phoc (forwards Wayland idle inhibitors), Firefox, mpv, Flatpak/X11 apps (`Inhibit`, `UnInhibit`, `GetActive`, `GetActiveTime`, `SimulateUserActivity`, `ActiveChanged`) |
| `io.piercingxx.XXRadio.Player` | `/io/piercingxx/XXRadio/Player` | `io.piercingxx.XXRadio.Player1` | `xx-radio --player-service` | activatable, idle exit | xx-radio window (`Play`, `Enqueue`, `Snapshot`, `Changed`) |
| `org.mpris.MediaPlayer2.xx_radio` | `/org/mpris/MediaPlayer2` | `org.mpris.MediaPlayer2` + `.Player` + `io.piercingxx.XXAuto.Actions1` | `xx-radio --player-service` | with the Player name | shade, lock screen, media keys, xx-auto (Like/Dislike) |

## `io.piercingxx.XXWM.Helper1` methods (shell → root helper, system bus)

Full table, polkit actions and confinement: [xx-wm/docs/helper.md](../xx-wm/docs/helper.md). Every call is async from `helper_client.py`.

- Polkit-checked (`allow_active`, a few `auth_admin_keep`): `SetChargeLimit(u)`, `SetAppNetwork(s, b)`, `FreezeCgroup(s, b)`, `CleanSystem(as) → as`, `ReadLog(s) → s`, `RefreshPackageIndex()`, `UpgradeSystem() → s`, `RemovePackage(s)`, `ManageService(s, s)`, `SetRfkill(s, b)`, `SetPowerProfile(s)`, `SetInboundPreset(s, b)`, `SetInboundAllowed(as)`, `SetFirewallEnabled(b)`.
- RTC wake (`…wake-alarm`, `allow_active=yes`): `SetWakeAlarm(t epoch)` arms `/sys/class/rtc/rtc0/wakealarm` for an absolute epoch 5 s – 48 h ahead (one helper-owned wake; another program's pending wake is never overwritten); `ClearWakeAlarm()` clears it if it is still ours. Automatic suspend arms it a minute before the next xx-clock alarm/timer.
- Lock PIN (no polkit: the caller's bus uid selects its own record): `HasPin() → b`, `VerifyPin(s pin) → (b matched, u retry_after, b has_pin)`, `SetPin(s old, s new) → (b done, u retry_after)`, `ClearPin(s old) → (b done, u retry_after)`. scrypt hashes in `/var/lib/xx-wm-helper/pin/<uid>.json` (0600), lockout enforced root-side; PIN text is never logged.
- Unauthenticated probes: `GetCapabilities() → a{sv}`, `GetFirewallState() → a{sv}`.

## `io.piercingxx.XXRadio.Player1` methods (window ↔ player service)

Payloads are JSON strings of contract models. All names derive from the
app spec, so a second app on `xx_music` never collides.

- `Play(in s tracks_json, in i start, in s origin_json)`: replaces the queue; origin names the album/playlist/radio for "Playing from".
- `StartRadio(in s track_json)`: plays the song, then its radio.
- `Enqueue(in s tracks_json, in b next)`: Play next / Add to queue (dedup by id).
- `PlayAt(in i index)`, `Remove(in i index)`, `Move(in i from, in i to)`.
- `SetShuffle(in b on)`, `SetRepeat(in s none|all|one)`.
- `SetSleep(in i seconds)`: 0 off, −1 end of song, else seconds.
- `Rate(in s track_id, in s rating)`: through the engine; a dislike leaves the queue.
- `Download(in s items_json)`, `RemoveDownload(in s id)`: planned downloads (M6 stores them).
- `Snapshot()` → `(s snapshot_json)`: status, current song, position, sleep, problem.
- `Queue()` → `(u version, s tracks_json, i index)`.
- Signals: `Changed(s snapshot_json)` (≤ 4/s), `QueueChanged(u version)`, `DownloadsChanged(u version)`, `Problem(s kind, s text)`.

## `io.piercingxx.XXAuto.Actions1` on the MPRIS path (player service)

- `ListActions()` → `(a(sssb) rows)` of `(id, label, icon_name, enabled)` from the app spec's `player_actions` (Like/Dislike with xx-auto's thumb icons).
- `ActivateAction(in s id)`: runs the handler on a worker; a `PropertiesChanged` on the Player interface follows so xx-auto re-reads the rows.

## `io.piercingxx.Contacts1` methods (email lookup)

- `LookupEmail(in s email)` → `(s uid, s display_name)`: exact,
  case-insensitive address match, `("", "")` on a miss (never an
  error). Email clients resolve senders through this, never through
  `Search`, whose digit matching would misattribute addresses such as
  `john1@example.com` to `+1…` phone contacts.
- `Search(in s query, in u limit)` → `(a(sss) results)` of
  `(uid, display_name, primary)`: `primary` is the contact's primary
  email when it has one, else the primary phone. Number matching is
  skipped for `@` queries and queries with fewer than 3 digits.
- Publishing to GNOME Contacts mirrors only into the dedicated
  `XX Contacts` EDS source, never the default address book.

## GUI application ids (never owned by a service)

`io.piercingxx.XXWM` (shell), `io.piercingxx.XXContacts`, `io.piercingxx.XXDialer`, `io.piercingxx.TxxT`, `io.piercingxx.XXCalendar`, `io.piercingxx.XXEmail`, `io.piercingxx.XXVitals`, `io.piercingxx.XXShare`, `io.piercingxx.Captions` (overlay), `io.piercingxx.Find` (agent; built, not enabled), `io.piercingxx.XXRadio` (its player service owns `io.piercingxx.XXRadio.Player`, never this).

## External services the suite uses

ModemManager (system), mmsd-tng (session, `org.ofono.mms`), feedbackd (`org.sigxcpu.Feedback`), callaudiod (`org.mobian_project.CallAudio`), iio-sensor-proxy, GeoClue2, UPower, logind, BlueZ, NetworkManager, xdg-desktop-portal, PipeWire.

## ModemManager calls (system bus, `org.freedesktop.ModemManager1`)

All async (`bus.call` + callback, never `call_sync` on the GTK loop); every caller fails soft without a modem.

- **USSD / MMI** (`common/xx_phone/mmi.py`, sheet in `xx_phone/ussd_dialog.py`; used by the shell dialer and xx-dialer's keypad/people/recents). `mmi.classify()` sends `*#06#` to a local IMEI sheet (`GetManagedObjects` → `Modem.Modem3gpp.Imei`, else `Modem.EquipmentIdentifier`). Other USSD-shaped codes (`*`/`#`/`**`/`##`/`*#` … `#`, digits and `*` only — GNOME Calls' rule) go to `Modem.Modem3gpp.Ussd`: `Get State` → `Cancel` when ACTIVE(2)/USER_RESPONSE(3) → `Initiate(s) → s`; `Respond(s) → s` while State is USER_RESPONSE; `Cancel` on dismiss. Replies also arrive as raw `PropertiesChanged` (arg0 = the Ussd interface) for `State`/`NetworkRequest`/`NetworkNotification`, deduped against the method reply per user action. Supplementary-service MMI codes such as `**21*number#` (call forwarding) are **not** interpreted locally: like GNOME Calls they go to `Initiate` and the network handles them. Codes with `+` (e.g. `*21*+49…#`) are dialed as calls, as in GNOME Calls.
- **SIM PIN / PUK** (`xx-wm/launcher/src/modem_monitor.py` → `sim_pin.py`). The monitor keeps `Modem.State`/`UnlockRequired`/`UnlockRetries`/`Sim` from `GetManagedObjects`, `Modem.StateChanged(i,i,u)` and `PropertiesChanged` (arg0 = `…Modem`). Locked = State LOCKED(2) and UnlockRequired ≠ NONE(1) (phosh's rule). The prompt calls `Sim.SendPin(s)` or, for PUK-type locks, `Sim.SendPuk(s puk, s new_pin)` on the modem's `Sim` path. Digits are never logged. A modem reaching REGISTERED(8) re-runs `Voice.ListCalls` (catch-up for calls missed while registering). Interfaces added to a known modem path after unlock (Voice, Ussd) are merged, not replaced.
- **Calls**: `Voice.CreateCall`/`Call.Start`/`Accept`/`Hangup`/`Voice.DeleteCall`; `Call.StateChanged` reason → end notice ('Busy', 'Network error', 'Audio setup failed').
