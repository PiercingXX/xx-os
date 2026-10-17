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
| `io.piercingxx.XXWM.Helper1` (**system bus**) | `/io/piercingxx/XXWM/Helper1` | `io.piercingxx.XXWM.Helper1` | root helper (polkit) | system activation | shell (charge limit, per-app network, freeze) |
| `io.piercingxx.XXRadio.Player` | `/io/piercingxx/XXRadio/Player` | `io.piercingxx.XXRadio.Player1` | `xx-radio --player-service` | activatable, idle exit | xx-radio window (`Play`, `Enqueue`, `Snapshot`, `Changed`) |
| `org.mpris.MediaPlayer2.xx_radio` | `/org/mpris/MediaPlayer2` | `org.mpris.MediaPlayer2` + `.Player` + `io.piercingxx.XXAuto.Actions1` | `xx-radio --player-service` | with the Player name | shade, lock screen, media keys, xx-auto (Like/Dislike) |

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
