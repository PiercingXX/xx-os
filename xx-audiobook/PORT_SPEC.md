# xx-audiobook — Linux port spec (spec + scaffold lane)

Source: Android client `ref/xx-audiobook/xx-audiobook-app` (Kotlin/Compose/Media3,
`com.piercingxx.audiobook`), backend `ref/xx-audiobook/xx-audiobook-server`
(FastAPI, stays as-is on Linux). Product plan: `ref/xx-audiobook/MASTER-TODO.md`
decisions D1–D11, §A. Server contract: `contracts/openapi.json` (vendored from the server)
(88 paths; client uses the non-`/api/admin/*` subset plus `GET /api/status` for
admins; §2.1). App id: `io.piercingxx.XXAudiobook`.

Stack, like the finished siblings (xx-vitals, xx-calendar): Python 3.12+,
GTK4/libadwaita via PyGObject, `common/xx_ui` for the look (all presets live),
stdlib + GI only — no new dependencies. Phone canvas 360x720 (min 320x640),
text-first UI per `common/DESIGN.md`. `XXWM_RULES.md` runtime traps apply
(tuple D-Bus replies, no `user_data` on async calls, `GLib.VariantType` for
`reply_type`, no nested `a{sv}` Variants, XDG dirs mode 0700 before first open,
private bus for tests, XDG isolation in `conftest.py`, `Gdk.Display.get_default()`,
no `get_display()` on the app, lists via `ListView` + `NoSelection` +
single-click + long-press, entries focusable only after tap, no startup focus,
`Adw.AlertDialog.choose(parent, …)` first arg, int64 GObject props for ms ids,
Broadway smoke test on a seeded DB, service bus name ≠ app id, service is the
single writer and owns its signals, session activation file for idle-exit
services, 360 px fit with ellipsize/wrap + `snap.py` WARNING treated as failure,
symbolic icons only).

## 0. What this lane delivers

`PORT_SPEC.md` (this file) plus a runnable scaffold: `meson.build`, `data/`
(launcher, `.desktop`, metainfo, icon, D-Bus activation), `src/xx_audiobook/`
(`main.py` placeholder page on xx_ui), `tests/` (headless pytest + XDG
isolation), `ruff.toml`, `README.md`. Later lanes build M1–M5 (§6).

## 1. Feature inventory (Android → Linux)

Base for Kotlin cites:
`ref/xx-audiobook/xx-audiobook-app/app/src/main/java/com/piercingxx/audiobook/`.

### 1.1 Tabs and screens

The app gates on auth (`ui/App.kt` `App()`): `Checking` → spinner;
`SignedOut` → `SignInScreen`; signed-in + grant denied (and not preview) →
`NoAccessScreen` (Retry = re-check grant, Sign out); else `AppNavHost`.
Five bottom tabs (`ui/navigation/AppNavHost.kt` `Tab`, labels are the design.md
screen names): Library, Playing (route `now_playing`), Downloads, Zones, Setup.
Reselect pops to the tab root; every push stays inside its tab graph
(`Routes.inTab`) so the tab stays lit; a mini-player shows on all tabs except
the player; cold start restores playback (`playback.restore()`, §1.5).

| Screen (Android file) | Android behaviour | Linux mapping |
|---|---|---|
| Sign in (`ui/login/SignInScreen.kt`, `SignInViewModel.kt`) | Estate address + username + password (show/hide), busy "Signing in…", `POST {origin}/auth/login`; "I signed in to xx-apps — try again" re-probes hub+provider; debug-only API-base override; preview session renders without requests | Same fields and verbs; override becomes env `XX_AUDIOBOOK_API_BASE` (never stored, never backed up); retry re-probes hub then stored token |
| No access (`ui/login/NoAccessScreen.kt`) | 403 `not_granted`: Retry + Sign out, never auto-sign-out | Same; Retry re-`GET /api/me`, Sign out wipes token only |
| Library (`ui/library/LibraryScreen.kt`, `LibraryViewModel.kt`) | Search field, sort + filter chips (+ Downloaded toggle), grid/list toggle, collapsible shelves; Continue listening (Room, offline-first) then server `GET /api/me/home` shelves; Home hidden offline | Same structure with xx_ui `search_bar` (deferred focus), `chip_bar`, `bottom_tabs`; shelves = `continue` (SQLite) + home shelves filtered to non-empty `continue_series`, `listen_again`, `discover` in order (`data/repo/HomeRepository.kt`) |
| Book (`ui/detail/BookScreen.kt`, `BookViewModel.kt`) | Order: actions, ratings + reviews, description (`\n` → paragraphs), chapters (first 5 + "All N"), bookmarks; damaged (`not_playable`) book: Play/Download disabled + one muted line; conflict dialog "Where were you?" (other device / this phone) | Same order and rules; conflict prompt when local and server progress differ by > 60 s and neither side finished (`ProgressMerge.PROMPT_THRESHOLD_S = 60.0` in `data/repo/ProgressRepository.kt`) |
| Now Playing (`ui/player/NowPlayingScreen.kt`, `NowPlayingViewModel.kt`, `SeekBar.kt`) | Transport + book-level seek bar (played part bright, live follow) + 5-cell sheet grid → Chapters / Speed / Sleep / Bookmark / PlayOn (cast zones) sheets; bookmark dialog + delete confirm; sleep follows playhead × speed, 10 s fade, pause-aware (`playback/SleepTimer.kt`) | Same sheets as `Adw.Dialog` pages; GStreamer playbin behind a book timeline (§2.6); speed via scaletempo; MPRIS exposes the same transport (§2.7) |
| Downloads (`ui/downloads/DownloadsScreen.kt`, `DownloadsViewModel.kt`) | One row per book: downloading → Cancel; done → Delete (confirm); failed → Retry + Delete | Same rows; constraints from §2.9 |
| Zones (`ui/zones/ZonesScreen.kt`, `ZonesViewModel.kt`) | Zone cards with transport (play/pause/stop, ±30 s), volume slider + Quieter/Louder, "Bring back to phone", location chips, cast-target rows, refresh | Same; Home Assistant only (§2.10) |
| Setup (`ui/setup/SetupScreen.kt`, `SetupViewModel.kt`) | Session block (origin, source, preview/API-base lines), Listening-stats button, server status rows (●/○ + Refresh, admin `GET /api/status`), skip-back/forward + default-speed + rewind-on-resume chips (`SKIP_CHOICES` 10/15/30), Wi-Fi-only checkbox, storage used, theme line ("follows launcher/xx-apps"), Share logs, Sign out (confirm), version lines | Same; theme line becomes "follows the shell theme" (live, §2.13); skip/speed/rewind sync to `PUT /api/me/settings` (§2.5); storage used walks `books/` + `covers/` |
| Stats (`ui/stats/StatsScreen.kt`) | Year-bounded `GET /api/me/stats?year=`, last-30-days zero-filled chart ending today, listening-history pages + load-more + delete (failed delete keeps the row + reason); switching books closes the old session with real listened seconds | Same; chart drawn with plain GTK drawing on theme tokens (no new deps) |

### 1.2 Pushed views (all `Routes.<kind>` inside the current tab)

Series / Author / Narrator (`ui/browse/BrowseScreen.kt`, `BrowseViewModel.kt`):
server detail in sequence order with live progress, offline/name fallback from
cache, playing chapter marked and kept in view. Collections list + collection
detail in list order with live rows (`ui/collections/CollectionsScreen.kt`,
`CollectionsViewModel.kt`: `GET /api/collections`, `GET
/api/collections/{id}`, playlists under `/api/me/playlists*`).

### 1.3 Sheets, dialogs, transient UI

Chapter / Speed / Sleep / Bookmark / PlayOn bottom sheets (Now Playing);
bookmark editor + delete confirm; download-delete confirm; review editor with
`StarPicker` radio group, own review shown as "You" + Edit, delete asks first,
"All reviews N" after the 5 newest-first (`ui/detail/ReviewsBlock.kt`,
`ReviewViewModel.kt`); sign-out confirm; debug mill row (debug builds on
emulators only, `ui/debug/DebugMillRow.kt`, `DebugRoutes.kt`) — NOT ported as a
screen; its coverage becomes the Linux screenshot matrix (§5, "What is NOT
ported"). Notification permission is asked on first Play and playback proceeds
regardless (`ui/common/NotificationPermission.kt`) — no Linux equivalent
permission exists; play directly, download completion uses a shell notification.

### 1.4 Settings (synced subset + local-only)

Synced via `GET`/`PUT /api/me/settings` (`UserSettings`: `skip_back_s`,
`skip_forward_s`, `default_speed`, `auto_rewind_s`, `sleep_default_min`,
`sleep_end_of_chapter`, `theme_preset`, `library_view`, `updated_at` +
`applied`): skip-back/forward, default speed (clamped 0.5–3.0,
`SPEED_STEPS` in `playback/PlayerCommands.kt`), auto-rewind seconds (0–30,
applied in `beforePlay`, only resumes paused ≥ 10 s per `BrowseTree.kt`
`AutoRewind`). `SettingsSync.kt` sends only locally edited keys with a stamp,
applies the server row when `applied == false`, and pulls only when the server
`updated_at` is newer (mutex-guarded). `theme_preset`/`library_view`/`sleep_*`
are read from the server row but owned elsewhere on Linux (shell theme, local
sleep default). Local-only: Wi-Fi-only downloads (default true,
`AppPrefs.kt`), last book + position (cold-start mini-player),
`owner_user_id` (a different user id wipes local state first,
`SessionManager.kt` `adopt()`), debug base override (env only).

### 1.5 Background behaviour

Foreground media service (`playback/PlaybackService.kt`: Media3 session,
speech audio attributes, audio focus, noisy receiver, network wakelock,
notification id 1001 tapping into Now Playing, authed resolving data source,
per-book speed, end-of-book `finish` + flush, stream-401 renew-and-retry once
at the same position, Bluetooth/resumption via `last_item_id`); 15 s progress
tick with 60 s screen-off/power-save backoff and immediate writes on
Pause/Seek/Chapter/Stop (`playback/SyncScheduler.kt` `INTERVAL_MS = 15_000`,
`BACKOFF_INTERVAL_MS = 60_000`, `MAX_TICK_GAP_MS = 5_000`); unique download
work (`download-{id}`, `MAX_ATTEMPTS = 8`) and progress-flush work
(`progress-flush`, 30 s backoff) with `CONNECTED` / `UNMETERED` constraints
(`download/DownloadWorker.kt`, `DownloadScheduler.kt`,
`download/ProgressFlushWorker.kt`); startup catch-up (flush + pending sync +
`pullAll` + settings sync + home refresh + resume signed-out downloads,
`AppGraph.kt` `catchUp()`); sign-out halts playback with a final write unless
wiping (`beforeSignOut(wipe)`). Linux mapping: §2.7 (player service) and §2.9.

### 1.6 Stores

Room `audiobook.db` v1, 8 tables, no media/covers (`data/db/Entities.kt`,
`Daos.kt`, `AudiobookDatabase.kt` v1 `exportSchema`, schema `1.json`):
`items` (summary + `authors`/`narrators` "-joined", `series_name`,
`series_sequence`, `duration_s`, cover urls, `added_at`/`last_played_at`,
rating, `missing`, `fetched_at_ms`; indexes on title/authors/added),
`item_detail` (`item_id` + full-detail JSON + stamp, the offline reader),
`progress` (`item_id`, `position_s`, `duration_s`, `is_finished`,
`updated_at_ms`, `speed`), `pending_sync` (same + nullable finish flag:
null = position, true = finish, false = unfinish; `ack()` deletes only rows
`≤` stamp, `bumpAttempts()`), `downloads` (per-book state
Queued/Running/Done/Failed/Cancelled + bytes + error + stamps),
`download_files` (PK `item_id,file_id`: index, size, sha256, mime, local path,
bytes done, verified), `bookmarks` (id, item, position, note, stamps,
`pending` null/create/edit/delete; `replaceSynced()` keeps pending rows),
`history` (auto id: play/pause/seek/finish/unfinish + position + speed + ms;
`recent(500)`). Pure-SQL library builder (`data/db/LibrarySql.kt`: bound args
only, title/author/narrator/series `LIKE … ESCAPE`, `IN` lists,
InProgress/Finished/NotStarted, sorts Title/Author/Added/LastPlayed
(`COALESCE(progress.updated_at_ms,0)`)/Rating). DataStore `app_prefs`
(`data/prefs/AppPrefs.kt`: `skip_seconds` 30, split skip-back, `default_speed`
1.0, `auto_rewind_seconds` 2, `download_wifi_only` true, `last_item_id`,
`last_position_s`, `settings_updated_at`, `settings_dirty`, `owner_user_id`;
suite-backed-up) and `audiobook_debug.xml` (`DebugPrefs.kt`, override only,
never backed up). Encrypted prefs `audiobook_secure`
(`auth/CredentialStore.kt`: origin, token, user id, expiry, username, display
name, role; corrupt/restored → delete + recreate; excluded from backup;
password never stored). Coil/ExoPlayer caches (wiped on user switch).
Linux mapping: §2.2–§2.4.

### 1.7 Integrations

xx-apps session content provider
(`hub/SessionProviderClient.kt`
`content://com.piercingxx.apps.session/current`: token, origin, user id,
expiry; blank/expired/`SecurityException` → fall through); hub discovery
(`hub/HubDiscovery.kt`: `GET http://127.0.0.1:17331/hub/discovery`, 250 ms,
never throws/retries/requires; HMAC-SHA256 envelope keyed by `sha256(session)`,
claims session/listen/exp/origin/hub_id; `trustedOrigin()` only https on an
already-known host); estate login/refresh (`auth/EstateAuthClient.kt`: plain
client without the auth interceptor, `POST {origin}/auth/login`
`{username,password,client:"xx-audiobook",device_label}` and `POST
{origin}/auth/refresh` bearer `{}` → `Grant` or Rejected/RateLimited/
Unreachable); Home Assistant cast (D8: `media_player.play_media` + signed
URLs, no Google Cast); suite backup BK-1 (`backup/AudiobookBackupProvider.kt`
`APP_NAME = xx-audiobook`, `SCHEMA = 1`, prefs minus exclusions, WAL-
checkpointed DB copy minus `downloads`/`download_files`, `export/history.json`
via `HistoryJson.kt`, never `files/`, `schema > 1` refused pre-swap,
`requireSuiteCaller`); theme sync (`ui/theme/ThemeSyncReceiver.kt`
`xx.launcher.THEME_CHANGED`, preset name beats stray background,
custom/unknown keeps ARGB, `ThemeLive` repaint without restart); log dump
(`log/LogDumpProvider.kt` read-only cursor, 200 KB tail cap). The audiobook
server's own `/api/auth/*` cookie login is studio-only and is NOT used by the
client (verified: no caller in `app/src/main`); the client logs in at the
estate origin and Bearers every `/api/*` call.

## 2. Linux implementation + decisions

### 2.1 HTTP client: urllib + threads — DECIDED

`urllib` (stdlib) on worker threads, results posted with `GLib.idle_add`.
`HttpAudiobookApi.kt` is already hand-rolled OkHttp + kotlinx.serialization
(no Retrofit); the Linux port mirrors that shape 1:1: base `{origin}/audiobook/`
(trailing `/` enforced), ids stripped of `/` (`seg()`), list decoding accepts
a bare array or `{results|…}` (`decodeList()`), server-supplied track urls are
resolved keeping the `/audiobook/` prefix (`resolveUrl()`), timeouts 10 s
connect / 30 s read with 120 s for the manifest, no redirects, unknown-field
rejection surfaces as 422 (never silently dropped). A GI-packaged lib
(libsoup) was rejected: its session API drifts across distros, it cannot run
headless under the fake-server fixture without a main loop, and every sibling
(xx-vitals `sync/http.py`) already proves urllib + threads against this exact
test strategy. One module owns all I/O (`api/http.py`); no network call ever
runs on the GTK thread (XXWM_RULES).

### 2.2 SQLite schema mirroring Room

`sqlite3` (stdlib), one file `~/.local/share/xx-audiobook/audiobook.db`,
same 8 tables and columns as §1.6 with the same indexes; `PRAGMA
journal_mode=WAL`, `user_version = 1`. `item_detail` keeps the full-detail
JSON blob so the offline reader path (`BookLoader.kt` offline fallback:
complete download + cached detail ⇒ play with `sessionId = null`) works
unchanged. `pending_sync.ack()` keeps the `≤ stamp` rule; `bookmarks`
replace keeps pending rows; history caps at 500 recent. Migration rule: future
schema bumps add `user_version` + explicit `ALTER TABLE` steps; the backup
refuses newer schemas pre-swap, as on Android.

### 2.3 Prefs in `~/.config/xx-audiobook/`

`config.json` holds the `app_prefs` keys (§1.6) with identical defaults
(skip 30 s, speed 1.0 clamped 0.5–3.0, rewind 2 s clamped 0–30, wifi-only
true). Debug base override is env `XX_AUDIOBOOK_API_BASE` only (verbatim URL
when set; the Android `allowOverride` debug-build gate has no meaning on
Linux, and env keeps it out of backups by construction). First run creates
all four XDG leaves (data/config/cache/state, mode 0700) before opening
anything (xx-vitals first-launch crash trap).

### 2.4 Credentials: libsecret via GI — password never stored

Token bundle (origin, token, user id, expiry, username, display name, role)
in the default collection, schema attributes `application = APP_ID`, `host =
originKey` (the xx-vitals `LibsecretBackend` shape:
`Secret.password_{store,lookup,clear}_sync`). Only the token persists; the
password exists only in the login request body, exactly as
`Session.StoredCredentials` (never password). Corrupt/restored entries are
deleted and recreated. All libsecret calls run on worker threads. Tests use an
in-memory backend (mirrors `InMemoryCredentialStore`).

### 2.5 Auth order on Linux: hub probe → stored token → Sign in

`AuthCoordinator.kt` order minus the provider: there is NO Linux equivalent
of the xx-apps content provider, and nothing replaces it — no sibling D-Bus
interface may be guessed (XXWM_RULES: model fakes on introspection XML only;
none exists for a session provider, so the port does not invent one). Order:
(1) `GET http://127.0.0.1:17331/hub/discovery`, 250 ms total timeout, never
throws/retries/blocks sign-in; accept only a valid HMAC envelope whose origin
is https on an already-known host (`trustedOrigin()`); (2) libsecret stored
token (unexpired); (3) Sign in (`POST {origin}/auth/login`, then
`persist()`). `renew()` re-probes first, then `POST {origin}/auth/refresh`
with the stale bearer. Origin rules (`hub/EstateOrigin.kt`) are kept verbatim:
normalize, `apiBase = {origin}/audiobook/`, `originKey = scheme://host:port`,
`problem()` rejects paths/queries and forbidden ports
(8450–8459 + 8448–MINT 8457), `UNREACHABLE_MESSAGE` ("Can't reach…Tailscale?")
shown for refused/timeout/timeout-like failures. A different user id on adopt
wipes local state first (`wipeLocalState()`: cancel workers, release player,
clear tables + prefs + books/covers/image cache).

### 2.6 401 reason handling + 403 `not_granted`, exactly as Android

`UnauthorizedReason.kt` / `AuthInterceptor.kt` semantics, unchanged: the
Bearer [REDACTED] sent only when the request origin key equals the session origin key
(`StreamAuth.kt` extends this to media: `file://` and foreign hosts get no
header); 2xx marks access good; 403 + `reason == not_granted` marks access
denied only → `NoAccessScreen`, never sign-out, never wipe (D10); 401
`expired` → `renew()` + exactly one retry, else sign out without wipe; 401 in
{`disabled`, `revoked`, `account_disabled`, `session_revoked`} → sign out AND
wipe (xx-apps DisableWatcher set); `malformed` / `not_a_person` / absent →
return to the caller, never wipe. `parse()` reads top-level `reason` or
nested `detail.reason`, trimmed, case-insensitive. 409 `not_playable` maps to
the one-line "damaged book" state; review-403 `not_started` maps to the
must-start-first gate (`messageFor()`).

### 2.7 Playback: GStreamer playbin + book timeline; bearer only for the estate host

One `playbin` element with a `scaletempo` audio filter for speed. The book
timeline (`playback/TrackMath.kt` `BookTimeline`: `locate` ms→track+offset,
`globalOf`, `chapterAt`, `chapterStart/EndAt`, `mediaId = "{item}/{file}"`;
`normalizeOffsets()`: trust server `start_offset_s` only when within 1 s of
the running sum, else lay tracks end-to-end) maps book position to
track + offset; track transitions seek the playbin to the file URI
(preferring a verified local `file://` when `DownloadRepository.localFile()`
passes, else the absolute resolved stream URL). Auth headers apply only to
estate-host media (origin-key match), never to `file://`. Transport rules
(`playback/PlayerCommands.kt`) kept: skip ±N across tracks (choices 10/15/30,
clamped 5–120 s), prev/next = chapter with 3 s restart threshold
(`RESTART_THRESHOLD_MS = 3_000`), speed steps
0.5–3.0, remembered per-book speed falling back to the default, auto-rewind on
resume-after-≥10 s-pause. Sleep timer (`SleepTimer.kt`): presets
5/10/15/30/45/60/90 min + custom ms + end-of-chapter (end captured once,
speed-scaled), `FADE_MS = 10_000` to pause then restore volume, countdown
pauses with playback. Sleep/skip/speed prefs from §1.4.

### 2.8 MPRIS2 + background: separate player service — DECIDED

Bus `org.mpris.MediaPlayer2.xx_audiobook` (`/org/mpris/MediaPlayer2`,
`org.mpris.MediaPlayer2` + `org.mpris.MediaPlayer2.Player`), `DesktopEntry
= io.piercingxx.XXAudiobook`. The player is a SEPARATE process/D-Bus service
(`io.piercingxx.XXAudiobook.Player`, activation file shipped so idle-exit
never breaks clients), not in-app. Reasons: (a) the task requires background
playback while the window is closed — an in-app playbin stops with the GTK
process, while Android keeps a foreground `PlaybackService`; a daemon is the
only faithful mapping; (b) the shell shade, lock screen, Bluetooth buttons
and xx-auto all drive MPRIS, which must outlive the window; (c) XXWM_RULES
forbids the daemon owning the app id (secondary-instance exit trap) and
requires the data owner to be the single writer that emits the signals —
so the player service owns position/speed/session, emits MPRIS
`PropertiesChanged` + `Seeked`, and the GUI reads state but writes playback
only through the service's methods (same pattern as xx-calendar's
service-is-single-writer rule). The GUI still shows the mini-player via the
service's state. Progress writes go through the existing sync path (§2.8);
the service never touches the token beyond the in-memory bearer handed at
play time (libsecret stays in the GUI + sync worker).

M2 UI contract (`io.piercingxx.XXAudiobook.Player1` at
`/io/piercingxx/XXAudiobook/Player`; every call blocks, so the GUI runs
them on a worker thread): `Open(item)` loads paused at the resume point
(streaming `POST /play`, else the cached detail + verified local files
with no session); `Play/Pause/PlayPause/Stop`; `SeekTo(us)`,
`SeekRelative(us)`, `SkipBack/SkipForward` (prefs intervals),
`NextChapter/PreviousChapter` (3 s restart rule); `SetRate` (0.5-3.0,
remembered per book); `Sleep(ms)` / `SleepEndOfChapter` / `CancelSleep`;
`SetSession(api_base, bearer)` / `ClearSession` (memory only);
`GetState() -> a{sv}` (`item_id, title, author, cover_url, status,
position_us, duration_us, rate, volume, chapter_index/count,
track_index/count, has_book, can_go_next/previous, sleep_remaining_ms,
sleep_end_of_chapter`); `CheckConflict(item)` / `ResolveConflict(item,
use_server)` for the > 60 s prompt. Signals: `StateChanged(a{sv})` on
real changes, `BookmarkRequested(item, position_us)` for the bookmark
custom action. Cold start restores the mini-player paused from the
last-played pointer + cached detail (`restore`), then plays through
`Open` + `Play`. MPRIS `OpenUri` takes `book:{item_id}`; the
`io.piercingxx.XXAuto.Actions1` rows are skip-back, bookmark,
skip-forward in that order.

### 2.9 Progress sync: 15 s cadence, offline queue, LWW, > 60 s prompt

`SyncScheduler` cadence verbatim: local `record()` + `setLastPlayed()` (+
history on pause/seek/stop) every tick while playing; server write every 15 s
(60 s backoff when screen off / power save via login1/GNOME idle inhibit
state, not Android APIs), `listened_s` monotonic with 5 s max tick gap,
per-book `reset()`; immediate write on Pause/Seek/Chapter/Stop.
`ProgressSync`: prefer `POST /sessions/{id}/sync`, else `close` on Stop;
gone sessions (404 / 409 `session_closed`) reopen via `POST /play` (no seek,
resume ignored, `listened = 0`) with old→new id remap (null = dropped → PUT
path); `progress_applied = false` merges the server row. Offline: everything
lands in `pending_sync`; `flush()` maps finish→`finish()`,
unfinish→`unfinish()` (+ `PUT` when position > 0), else `PUT progress` with
client `updated_at`; `Unreachable`/`NotSignedIn` stop the pass, 4xx (except
401/408/429) drops the row, else attempts bump; `progress-flush` equivalent is
a systemd user timer + retry, not WorkManager. Conflict: `compareOnOpen`
prompts only when both sides unfinished and |local − server| > 60 s; resolving
writes the winner (newest); finished-mismatch is pure last-write-wins. The
Linux backoff source (screen/power) replaces `PowerManager`; thresholds stay
identical so server behaviour is unchanged.

### 2.10 Downloads: Range resume, sha256, Wi-Fi-only via NetworkManager

Per-file `GET /api/items/{id}/manifest` → stream each file with
`Range: bytes={have}-` against `.part` files (206 append, 200 restart-from-0,
416 → verify-what-we-have, 401/403 retryable, 404 failed); size must match and
`sha256(.part) == manifest.sha256` (blank sha ⇒ size-only), else delete and
fail without keeping unverified bytes; progress persisted every 1 MB
(`SAVE_EVERY`); on success cache the detail JSON + full cover for offline
(`cacheForOffline()`); layout `~/.local/share/xx-audiobook/books/{safeId}/
{idx:03d}-{safeFileId}` + `~/.local/share/xx-audiobook/covers/{id}.jpg`
(`download/BookFiles.kt`); 8 attempts max, unique per-book scheduling,
signed-out parks with "Sign in to resume." Network state
(`data/repo/NetworkMonitor.kt` → NM `StateChanged`/`Metered`): Offline
(no connectivity / captive-unvalidated non-VPN), Online (unmetered), Metered;
VPN (Tailscale) is not metering by itself; default wifi-only ⇒ download only
when Online. Storage-low refuses new downloads. The queue UI (§1.1) and
`isComplete()` offline-play gate are unchanged.

M4 UI state API (`src/xx_audiobook/download.py`, no UI here): the
Downloads screen uses `DownloadsState.list()` (book rows newest-first),
`status(item_id)` (`state`, `bytes_done/total`, `error`, `is_complete`),
`enqueue(item_id, title)` / `cancel(item_id)` / `retry(item_id)` /
`delete(item_id)`, and `block_reason()` ("Waiting for Wi-Fi." /
"No network connection." / None); the Setup screen uses
`wifi_only()` / `set_wifi_only(bool)` (the `download_wifi_only` pref,
default true), `storage_used_bytes()` / `storage_state()`
(`used_bytes`, `free_bytes`, `low`), and `submit_enqueue` /
`submit_probe_network` from the GTK thread (results via
`GLib.idle_add`). The player service resolves every track through
`DownloadRepository.local_file()` first (verified `file://`, no
auth header) and opens fully-downloaded books offline from the
cached detail with `sessionId = null`.

### 2.11 Zones (Home Assistant)

Unchanged verbs through the service: `GET /api/locations` →
`GET /api/cast/targets?location_id=` → `POST /api/cast/play`
(`entity_id`, `item_id`, chapter/position) → `POST /api/cast/transport`
(play/pause/stop/volume/seek) → "Bring back to phone" only after a confirmed
`stop` (`ZoneRepository.kt` `bringBackToPhone()`). Signed `/stream/*` URLs go
to the speaker, never the bearer. HA errors surface verbatim (503
`stream_not_configured`, 409 `ha_not_configured`, 502s).

M5 UI state API (`src/xx_audiobook/zones.py`, no UI here): the Zones
screen uses `ZonesState.snapshot()` (`locations`, `targets`, `output`,
`selected`, `message`, `busy`, `volume`, `zone_playing`) plus
`refresh()` / `select(location_id)` / `play_on(target, item_id,
position_s)` / `transport(action, value)` / `seek_by(seconds)` /
`seek_step(forward)` / `set_volume(v)` (clamped 0–1) / `quieter()` /
`louder()` / `bring_back_to_phone()` (returns the zone's `item_id` so
the caller can resume locally, only after the confirmed stop), and
`submit_refresh` / `submit_select` / `submit_transport` /
`submit_bring_back` from the GTK thread (results via `GLib.idle_add`).
`ZoneRepository` underneath mirrors `ZoneRepository.kt` exactly
(`Phone` vs `ZoneOutput`, output becomes `Phone` only on a good
`stop`).

### 2.12 Reviews, bookmarks, stats, collections, series, authors, narrators

Reviews: cached per-item in memory, offline keeps last; `PUT` 403
`not_started` ⇒ must-start gate; delete-own (204) + admin delete; posting
refreshes the cached Room detail star line. Bookmarks: Room-first
`local-<uuid>` rows with `pending` create/edit/delete; `syncPending()` then
`replaceSynced()`; HTTP 404 drops the row, other errors stay pending.
Stats: `GET /api/me/stats?year=` + `GET /api/me/sessions` pages; year bounds
enforced client-side; failed history delete keeps the row + reason.
Collections/playlists: list order kept, rows live (progress merges in);
writes are person-level (`POST`/`PATCH`/`PUT`/`DELETE` per openapi list).
Series/author/narrator detail: sequence order, live progress, offline/name
fallback from cache; playing chapter marked and kept in view. Search:
`GET /api/search?q&limit=8` grouped caps; facets via
`GET /api/library/facets`. All DTOs decode epoch-seconds wire exactly
(`Dto.kt`); unknown JSON fields are rejected by the server (422), so the
client sends only documented keys.

M5 UI state APIs (no UI here; all sync with `submit_*` twins for the
GTK thread, results via `GLib.idle_add`):
- Stats (`src/xx_audiobook/stats.py`): `StatsState.view()` (`year`,
  `stats`, `loading`, `error`, `first_year`, `current_year`,
  `can_go_back`/`can_go_forward` over [`MIN_YEAR`..current]) plus
  `select(year)` / `previous_year()` / `next_year()` / `retry()`;
  `history()` (`sessions`, `total`, `page`, `loading`, `error`,
  `can_load_more`) plus `load_first_page()` / `load_more()` (page 1
  replaces, later pages append deduped by `session_id`) /
  `delete_session(id)` (reloads the year on success; a failed delete
  keeps the row and reports the reason). Pure helpers:
  `clamp_year()`, `last_days()` (30-day zero-filled series ending
  today, oldest first), `monthly_totals()` (12 figures),
  `format_listened()` ("12 h 5 min" / "40 min" / "0 min").
- Reviews (`src/xx_audiobook/reviews.py`): `ReviewRepository`
  (per-item in-memory cache, offline keeps last; `put()` →
  `WriteOk` / `WriteNotStarted` (403 `not_started`) /
  `WriteFailed`; `delete_mine()`; admin `admin_delete()`; writes
  call `on_changed(item_id)` so the detail star line refreshes) and
  `ReviewsState` for the block (`mine` / newest-first `others` /
  `gated` / `show_finished_prompt` / `can_load_more`;
  `start_editing()` shows the gate explanation instead of the form
  while ineligible; `mark_finished_and_edit()` /
  `mark_finished_and_save()` finish-then-open; `save()` is
  optimistic with rollback; `request/delete_mine()` and
  `request/confirm_delete()` ask first).
- Bookmarks (`src/xx_audiobook/bookmarks.py`): `BookmarkRepository`
  (Room-first `local-<uuid>` rows with `pending`
  create/edit/delete; `add()` / `edit()` (an unsent create stays a
  create) / `delete()` (an unsent create is forgotten locally);
  `sync_pending()` replays the queue and returns the remainder; 404
  drops the row, other errors stay pending; `refresh()` replays
  first, then `replaceSynced()` keeping pending rows) and the thin
  `BookmarksState` wrapper.
- Settings sync: M1's `SettingsSync` already covers every synced
  pref — the PUT body holds exactly the four phone-owned keys
  (`skip_back_s`, `skip_forward_s`, `default_speed`,
  `auto_rewind_s`) plus the `updated_at` stamp, matching
  `UserSettingsPutDto`; `sleep_*` / `theme_preset` /
  `library_view` arrive read-only and stay owned elsewhere (§1.4).
  Pinned by `test_settings_push_sends_only_owned_keys`; no change
  needed in this lane.

### 2.13 Theme from the shell config

No in-app picker (as on Android: "follows launcher/xx-apps"). On Linux the
source is `$XDG_CONFIG_HOME/xx-wm/config.json` through `xx_ui` (`apply(app)`
once at startup, live re-style without restart). Server `theme_preset`
arrives read-only in the settings row and is informational only. Preset
mapping: Android ships 7 presets (amoled default, graphite, forest, ocean,
burgundy, paper, mist; `ui/theme/ThemePresets.kt`); xx_ui ships those 7 plus
`aura` — all 7 Android names resolve 1:1 (same keys), `aura` is a Linux-only
extra, custom/unknown ARGB keeps its color with luminance-derived foreground.
Type follows the theme font (SpaceMono/JetBrainsMono lineage); the
≤ 2 dp radii rule and Signal-white progress idiom map onto xx_ui tokens
(`foreground` fill / `background` text for played parts).

### 2.14 Logs

`~/.local/state/xx-audiobook/xx-audiobook.log` + 256 KB rotated sibling +
`last-crash.txt` + 300-line in-memory ring (`log/AppLog.kt`); `redact()`
strips `Bearer`, `password/passwd/secret/token/otp/pin=`, emails,
`otpauth://` before any write/share; Setup "Share logs" shares the tailed
(200 KB cap) redacted dump. Crash handler writes `last-crash.txt` with
version/device/lifecycle first.

### 2.15 Backup through the xx-wm backup

Siblings do this by documentation + file posture, not by a provider API:
xx-vitals ships `docs/backup.md` naming every store and its posture, and the
xx-wm backup picks the files up. Same here — ship `docs/backup.md` stating:
INCLUDE `~/.config/xx-audiobook/config.json`,
`~/.local/share/xx-audiobook/audiobook.db`, `export/history.json` (history
table dump, LWW-merged on restore like `HistoryJson.kt`); EXCLUDE token
(libsecret, never on disk here), `books/`, `covers/`, logs, debug override
(env, not a file). The Android `SuiteBackupProvider`/xx-apps TAR plumbing has
no Linux equivalent and is not ported. Restore merges history LWW, restores
excluded prefs to defaults, deletes `books/` + `covers/` (re-download), and
refuses `schema > 1` exports pre-swap.

### 2.16 What is NOT portable, and why

- xx-apps session content provider (Android-only IPC; no Linux counterpart —
  §2.5, replaced by hub probe + stored token + sign-in).
- `EncryptedSharedPreferences` → libsecret (§2.4; same guarantees).
- Media3/ExoPlayer → GStreamer playbin (§2.7; no Media3 on Linux).
- Media3 media-session/Auto browse tree (`BrowseTree.kt` root→{continue,
  downloaded}, `book:{id}` playable) → MPRIS2 (§2.8; the car reads MPRIS,
  xx-auto drives it).
- WorkManager unique work + constraints → systemd user units + timers and the
  player-service scheduler (§2.7–§2.10).
- Coil auth image loading → plain `Gtk.Picture` over bytes fetched with the
  origin-keyed bearer (§2.6).
- `POST_NOTIFICATIONS` runtime permission → none exists; play proceeds, only
  download completion notifies.
- `xx.launcher.THEME_CHANGED` broadcast + `ThemeStore`/`ThemeSyncReceiver` →
  `xx_ui.apply` + config file watch (§2.13).
- Suite TAR backup provider → `docs/backup.md` posture (§2.15).
- Debug keystore / emulator props / `adb --es debug_*` → env vars, never in UI.
- Google Cast → already excluded upstream (D8, HA only).
- Android network/power APIs (`ConnectivityManager`, `PowerManager`) →
  NetworkManager + login1/idle state, same thresholds (§2.9–§2.10).
- `/api/admin/*` (34 paths): admin-only server surface, never called by the
  client (only `GET /api/status` is read, by admins, on Setup).

## 3. Integration with XX-WM

- Home slot / desktop id: `io.piercingxx.XXAudiobook` (`.desktop`
  `DBusActivatable=true`, `X-Purism-FormFactor=Workstation;Mobile;`,
  session activation file `share/dbus-1/services/io.piercingxx.XXAudiobook.service`
  so drawer activation works; app id also owns the GUI bus name only).
- MPRIS in the shade and lock screen: the player service's
  `org.mpris.MediaPlayer2.xx_audiobook` is the single instance the shell
  shows; metadata carries title/author/cover art url (`mpris:artUrl` as
  `file://` when cached), `CanPlay/CanPause/CanSeek/CanGoNext(chapter)/
  CanGoPrevious(chapter)` map the §2.7 transport; `Seeked` + polled position
  keep the shade lock-stepped.
- Notifications: download completion/failure (per-book, with Retry action)
  and sign-in-required (token dead while queued work waits). No marketing,
  no playback notification (MPRIS covers it). The first-Play permission
  prompt does not exist on Linux.
- xx-auto: drives MPRIS only (play/pause/next-chapter/previous-chapter/seek);
  no private interface.

## 4. Architecture

Modules (`src/xx_audiobook/`): `main.py` (Adw.Application + window),
`paths.py` (XDG dirs, 0700 leaves), `prefs.py` (config.json + defaults),
`auth.py` (EstateOrigin rules, hub probe, estate login/refresh,
session adopt/wipe, 401/403 handling), `store.py` (sqlite3 schema v1 + DAOs +
library SQL builder), `api.py` (DTO dataclasses + urllib client, timeouts,
`resolveUrl`, `decodeList`, error taxonomy `Http/Unreachable/Timeout/
NotSignedIn`), `repos.py` (M1: library/progress/settings mappers + LWW merge),
`zones.py` (M5: ZoneRepository + ZonesState),
`stats.py` (M5: year stats + sessions + StatsState),
`reviews.py` (M5: ReviewRepository + ReviewsState),
`bookmarks.py` (M5: BookmarkRepository + BookmarksState), `timeline.py`
(BookTimeline + locate/global/chapter math), `sleep.py` (sleep timer),
`player.py` (service client + local GStreamer control for M2+), `service/`
(M2: `player_service.py` D-Bus + MPRIS + playbin), `download.py` (M4),
`ui/` (M3+: pages on xx_ui), `applog.py` (ring + redact).
Threading: every blocking call (HTTP, libsecret, SQLite writes, downloads,
GStreamer bus watches that block) runs on worker threads; the GTK main loop
is never blocked — no `subprocess` waits, no `time.sleep`, no sync Gio with
long timeouts; results return via `GLib.idle_add`. D-Bus names: GUI
`io.piercingxx.XXAudiobook`; player `io.piercingxx.XXAudiobook.Player`;
MPRIS `org.mpris.MediaPlayer2.xx_audiobook`. File layout mirrors the
siblings: `meson.build`, `data/` (launcher `.in`, desktop `.in`, metainfo,
service `.in` files, icon), `src/xx_audiobook/`, `tests/`, `tools/`
(`demo_seed.py`, `snap_all.sh` + nav scripts from M3), `docs/backup.md`
(M4), `README.md`, `ruff.toml`.
Test strategy: headless pytest, no display, no network — `tests/conftest.py`
inserts `src` + `common` on `sys.path` and isolates `XDG_CONFIG_HOME` (and
`HOME`) per test; a `fake_server` fixture (`http.server` on 127.0.0.1,
ephemeral port, serving canned openapi-shaped JSON + Range media with sha256
manifests) backs API/download/sync tests; a contract test (mirroring Android
`data/ContractTest.kt` + `FakeAudiobookApi.kt`/`Fixtures.kt`) asserts every
client route exists exactly once in `openapi.json`, required DTO fields
decode, and stream/cover urls resolve under the API base — against the
vendored `contracts/openapi.json` (a copy of the server's; refresh it when
the server contract changes), so it never skips; error tests pin 401-reason actions, 403 handling, 409
`not_playable`, timeout taxonomy, and LWW/60 s-prompt merge rules; a Broadway
smoke test builds the real window on a seeded temp DB and pumps ~1 s without
exceptions (skips when `gtk4-broadwayd` is missing).

## 5. Contract test + fake server (Linux mirrors of ContractTest)

`tests/test_contract.py`: loads `openapi.json` (skip when absent, §4), then
(1) every path in the client's route table (§2.12 + §1: auth-estate paths
excluded — they belong to the estate fabric, not this contract) is present
exactly once with the used method; (2) required request/response fields for
`PlayOut`, `SyncIn`, `ProgressIn`, `UserSettings`, `BookmarkIn`, `ReviewIn`,
`CastPlayIn` match the DTO constructors; (3) `resolveUrl()` keeps the
`/audiobook/` prefix for `/audiobook/api/…`, `/api/…` and bare-relative
forms. `tests/fake_server.py`: threaded `http.server` fixture implementing
the used subset (`/audiobook/api/me`, `/items`, `/items/{id}`,
`/items/{id}/manifest`, `/items/{id}/files/{fid}` with real `Range`/`206`/
`416`/`ETag`/`If-Range`, `/items/{id}/play`, `/sessions/{id}/sync|close`,
`/me/progress*`, `/me/settings`, `/me/bookmarks*`, `/items/{id}/review*`,
`/collections*`, `/series*`, `/authors*`, `/narrators*`, `/search`,
`/library/facets`, `/locations`, `/cast/*`, `/me/stats`, `/me/sessions`,
`/me/continue`, `/me/home`) plus estate `/auth/login|refresh` and hub
`/hub/discovery` stubs, with injectable 401-reason / 403 / 409 / 422 / timeout
/ truncated-body faults. No test touches the real network (loopback fixture
only — "tests never use the network" means never beyond the fixture).

## 6. Milestone plan (each ≤ ~2000 changed lines, with acceptance + shots)

- M1 — data/API/auth/DB: `paths`, `prefs`, `auth` (origin/hub/login/401-403),
  `store` (8 tables + library SQL), `api` (DTOs + client + errors), `repos`
  (library/progress/settings + LWW), `applog`, contract test + fake server.
  Accept: contract test green against `openapi.json`; auth-order unit tests
  (provider-absent → hub → stored → login); 401/403 matrix; Room-parity test
  (every Android column present); offline progress queue + flush against fake
  server. Shots: none (headless).
- M2 — playback + MPRIS service: `timeline`, `sleep`, player service (playbin
  + scaletempo + book timeline + bearer-for-estate-only), MPRIS2 surface,
  session sync (15 s/60 s cadence, reopen, LWW, 60 s prompt plumbing),
  mini-player state restore. Accept: TrackMath parity tests (1 s normalize
  rule, chapter/track maps); sleep fade/pause/end-of-chapter tests; MPRIS
  property/Seeked tests under `dbus-run-session`; GUI starts while the
  service runs (bus-name trap test); real-service test. Shots: Now Playing +
  mini-player (seeded book, 4 themes).
- M3 — library/book/now-playing UI: Library (search/chips/grid-list/shelves),
  Book (order, damaged state, conflict dialog, chapters collapse, bookmarks
  UI), Now Playing sheets, collections + series/author/narrator pushed views,
  `snap_all.sh` + `demo_seed.py`. Accept: offline-first UI tests (whole cache
  pages without network); a11y labels + 48 dp targets at font-scale 2;
  360/320 px fit (`snap: WARNING` = failure). Shots: Library, Book, Now
  Playing, Series, Collections (seeded + empty HOME, 4 themes).
- M4 — downloads + offline: `download` (manifest/Range/sha256/NM-metered),
  systemd user units, offline play gate, `docs/backup.md`, Setup storage +
  wifi-only + logs share. Accept: resume-after-drop, checksum-mismatch
  (keeps nothing unverified), metered-blocked, storage-low, signed-out-park
  tests; backup doc covers every store. Shots: Downloads (queued/active/
  done/failed), Setup.
- M5 — zones/stats/reviews/setup polish: zones cards + transport +
  bring-back, stats + history, reviews gate + editor, settings sync, status
  rows, sign-out confirm, metainfo/next-release notes. Accept: cast
  play/transport/bring-back against fake HA endpoints; stats year-bounds +
  zero-fill chart tests; review `not_started` gate test; full gate green.
  Shots: Zones, Stats, Setup (final matrix), contact sheets per theme.

## 7. Orchestrator wiring (NOT done in this lane — do not edit those files here)

After the parallel xx-auto lane lands: add `xx-audiobook` to `PROJECTS` in
`scripts/check-all.sh`; add its install step to `xx-wm/scripts/install.sh`;
add its table row to the top-level `README.md` directory list.

