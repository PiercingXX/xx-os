# TxxT Linux Port — Specification

Target: postmarketOS / Mobian / FuriOS / PureOS phones running phoc
(XX-WM or Phosh). 360x720 logical px (min 320x200), touch-first, on-screen
keyboard. Stack: Python 3.12+, GTK4 + libadwaita via PyGObject
(`Adw.Application`, `Adw.NavigationView`), meson install, stdlib + GI only.

Android source: `ref/Txxt/` (`com.piercingxx.txxt`, minSdk 24, Views +
viewBinding, Room + Gson, pure-Kotlin `core/` with zero `android.*`
imports). Shell conventions: `ref/xx-wm/` (`design.md`, `docs/config.md`,
`launcher/src/modem_monitor.py`, `contacts.py`, `notif_daemon.py`,
`launcher/src/config.py` `THEME_PRESETS` / `FONT_FAMILIES`).

App id: `io.piercingxx.TxxT`. Session-bus name: `io.piercingxx.TxxT`
(owned while the app runs; the shell defers its own SMS surface to it —
§4). No pip dependencies: everything below is stdlib (`sqlite3`, `json`,
`xml.etree`, `html.parser`-free regex link scan) + GI
(`Gtk`, `Adw`, `Gio`, `GLib`, `EBook`/`EDataServer`/`ECal`, `Secret`,
`GWeather`, `Geoclue` via GI only where used).

## 1. Feature inventory (Android → Linux)

Screens. All UI is Views + viewBinding, text-first lines, never bubbles
(`docs/PRIVACY.md` §2), AMOLED black, Space Mono chrome + JetBrains Mono
body (`res/font/`, `docs/DESIGN.md`).

| # | Android surface | Kotlin files | Linux implementation |
|---|---|---|---|
| F1 | Conversation list: pin (cap 5), mute forever / mute-until (1h/8h/tonight/Monday), archive, swipe-to-delete (confirmed), call, copy number, block, star, list search | `ui/ThreadActivity.kt` (thread), `ui/ConversationListAdapter.kt`, `ui/ConversationListLoader.kt`, `ui/ConversationListPresenter.kt`, `ui/ConversationSearchFilter.kt`, `ui/ConversationSwipeHelper.kt`, `ui/ConversationVisibility.kt`, `ui/MainActivity.kt` (`MainActivity.kt` at `txxt/` root), `data/ConversationDao.kt`, `data/ConversationEntity.kt`, `data/ConversationMute.kt`, `data/ConversationPin.kt`, `core/ConversationList.kt`, `core/ConversationFlags.kt`, `core/ConversationPin.kt`, `core/MuteUntil.kt` | `Adw.NavigationView` root page: `Gtk.ListView` rows (name, preview, timestamp, all ellipsize, no horizontal overflow at 320 px). Swipe via `Gtk.GestureSwipe` or row long-press action list (matches shell drawer menus). Call row → `org.gnome.Calls` D-Bus (`Dial`); copy → `Gdk.Clipboard`. Pin cap 5, mute-until wall-clock — same pure-Python logic ported from `core/` (unit-testable). Search: list filter + in-thread search (`ThreadSearchFilter.kt` → `Gtk.SearchBar` + match navigator). |
| F2 | Conversation thread: text-first lines, timestamps, compose bar, ⊕ photo picker → MMS, ➜ send, staged-photo text line + ✕, tappable links (offsets only, never fetched), tap-row reads aloud (TTS), no voice-message affordance | `ui/ThreadActivity.kt`, `ui/ThreadAdapter.kt`, `ui/ThreadMessageLoader.kt`, `ui/ThreadMessagePresenter.kt`, `ui/MessageLinks.kt`, `ui/MessageLinkSpan.kt`, `ui/MessageReadAloud.kt`, `ui/PhotoAttachment.kt`, `ui/PhotoStaging.kt`, `ui/ComposeActivity.kt` (sms:/smsto:/mms:/mmsto: entry), `ui/SendGate.kt`, `ui/DictationInsert.kt` (pure seam, unwired — no mic), `service/SendPipeline.kt`, `service/SendPolicy.kt`, `ui/EmojiNerdCompose.kt`, `ui/EmojiNerdFont.kt`, `ui/EmojiTypeface.kt`, `ui/GroupGlyphs.kt` | Thread page on the navigation stack. Compose: `Gtk.TextView` (squeekboard OSK; Enter sends only on hardware keyboard, like shell dialogs). Photo attach: XDG portal `org.freedesktop.portal.desktop.OpenURI`/FileChooser (`Gtk.FileDialog`), stage into app-private cache (same no-grant-retention idea as `PhotoStaging`), scrub EXIF/XMP/IPTC with a pure-Python stripper (JPEG APPn / PNG iTXt/zTXt removal — port of `core/ImageMetadataScrubber.kt`, `PngMetadataScrubber.kt`, `ImageContainer.kt`, fail-closed), send via mmsd-tng (F8). Links: regex detect, `Gtk.TextTag` underline, `Gtk.UriLauncher` on tap — never fetched (same rule). TTS: Speech Dispatcher over D-Bus (`org.freedesktop.speech-dispatcher`) or `spd-say` backend — `flite`/`espeak-ng` is packaged on all four target distros; NOT in-process audio. Emoji: system font fallback only, monochrome (no bundled Nerd-font emoji compose; `EmojiNerd*` becomes "render with system fallback, never colour emoji"). Dictation: NOT ported (no mic — PRIVACY.md §5; `RECORD_AUDIO` deliberately absent from the manifest). |
| F3 | NEW-conversation recipient picker (contact-name search + typed number) | `ui/NewConversationActivity.kt`, `ui/RecipientAdapter.kt`, `ui/RecipientPicker.kt`, `contacts/ContactDirectory.kt`, `contacts/ContactNameResolver.kt`, `contacts/PhoneLookupIdentity.kt`, `contacts/ContactLaunch.kt` | New-conversation page: `Gtk.SearchEntry` over EDS address book (`EBook.BookClient` via `EDataSourceRegistry`, async, contacts appear as rows) + free number entry. Name resolution for threads: EDS `BookClient.get_contacts` phone-match; denial/failure → bare number, never blank (same never-silent rule as `PermissionGate`). Port `core/PhoneNumbers.kt` normalization for matching. |
| F4 | Settings: theme presets, font toggle, backup/restore, blocking, lock-screen privacy, notification prefs, sound picker | `ui/SettingsActivity.kt`, `ui/SettingsStore.kt`, `ui/SettingsBackup.kt`, `ui/SettingsBlocking.kt`, `ui/SettingsBlockingStore.kt`, `ui/SettingsStarred.kt`, `ui/FontMode.kt` | `Adw.PreferencesWindow` (fits 360 px, stock libadwaita — styled only by theme CSS). Sound: XDG sound theme / `GSettings org.gnome.desktop.sound` name, not a ringtone picker (no `RingtoneManager` on Linux; channel-generation hack in `NotificationPrefs.kt` has no equivalent — delete it). |
| F5 | Blocking & starred editor; inbound filter (unknown-sender hold default-off, keyword/phrase filters, starred call-through, block reasons) | `ui/BlockingActivity.kt`, `ui/BlockingRules.kt`, `block/BlockOverrideStore.kt`, `block/BlockReason.kt`, `block/InboundFilter.kt`, `block/LiveInboundFilter.kt`, `block/MessageDisposition.kt`, `core/BlockingFilter.kt`, `core/Blocklist.kt`, `core/BlockDecision.kt` | Pure-Python port of `block/` + `core/Blocking*.kt` (decision is fully offline logic — unit tests carry over). UI page in Settings. Storage: SQLite `blocklist` table (§5). Starred = EDS contact "starred/favorite" flag read + local starred set; starred bypasses every suppression (same rule). |
| F6 | Quarantine review: deliver / block / delete per sender; store not drop | `ui/QuarantineActivity.kt`, `data/QuarantineStore.kt` | Quarantine page (badge count on root). Same `is_quarantined` flag model (§5). |
| F7 | SMS send/receive pipeline; default-SMS role; delivery/read reports never requested; auto-reply SMS for dropped voice-MMS (default off, per-contact override) | `service/SmsReceiver.kt`, `service/SmsDeliverReceiver.kt`, `service/SendPipeline.kt`, `service/SendPolicy.kt`, `service/ReceivePolicy.kt`, `service/RespondViaMessageService.kt` (call-screen canned reply), `service/ArrivalNotify.kt`, `service/PermissionGate.kt`, `service/DefaultHandlerMonitor.kt`, `service/RebootReconcile.kt`, `service/TelephonyInboxImport.kt`, `data/InboundStore.kt`, `data/OutboundStore.kt`, `data/Mappers.kt`, `TxxtApp.kt` | ModemManager `org.freedesktop.ModemManager1.Modem.Messaging` over the **system** bus: `Create` + `Sms.Send` for outbound; `Added` signal + `List`/`GetAll` for inbound; `Delete` after SQLite import (MM deletes nothing itself — §5). No "default SMS role" on Linux: ownership = session-bus name `io.piercingxx.TxxT` (§4). Reports: never set validity/report flags (same as `SendPolicy`). Auto-reply: send via same `Create` path, default off. Reboot reconcile → startup reconcile: on launch, `List` modem store and merge anything missing (port of `RebootReconcile.kt` + `TelephonyInboxImport.kt`, 5-min match window, never duplicate). Call-screen reply: NOT portable (no platform in-call UI hook) — closest equivalent is the shell CallUI quick-reply button, owned by the shell lane. `PermissionGate` → startup D-Bus availability checks (MM present? mmsd-tng present?) with honest degraded banners, never silent. |
| F8 | MMS photo send/receive: scrubbed on send, fetched on arrival, `[Photo]` until tapped; audio MMS dropped; download retry; foreground service for slow MMSC GET | `service/MmsReceiver.kt`, `service/MmsDeliverReceiver.kt`, `service/MmsRetrieve.kt`, `service/MmsRetrieveService.kt`, `service/MmsDownloadRetry.kt`, `service/MmsImageFit.kt`, `service/MmsUriGrants.kt`, `service/PhotoMedia.kt`, `service/PhotoRepairPass.kt`, `service/MmsDownloadedReceiver.kt` + `MmsSentReceiver` (manifest), `ui/PhotoAttachment.kt`, `core/MmsPduHeader.kt`, `core/MmsRetrievedContent.kt`, `core/MmsSendReq.kt`, `core/PhotoRepair.kt` | mmsd-tng (`org.ofono.mms`, shipped by postmarketOS) over the **system** bus for both directions; MM Messaging handles SMS only. `[Photo]` placeholder until tapped (tap → download via mmsd-tng, store under XDG data, show). Audio-part MMS dropped at the inbox boundary; optional auto-reply SMS (same as F7). Retry with backoff in the background service (§6). No foreground-service concept on Linux — the `txxt-daemon` user service (§6) holds the process instead. `MmsImageFit` → scale-to-screen with `GdkPixbuf` on load (never full-res into a 360 px row). APN: read shell `apn*` keys? No — mmsd-tng owns its own APN config; do not duplicate it. |
| F9 | Group MMS, scheduled send, SMS reactions, SMS encryption, burn-after-read, biometric lock, voice messages, RCS, link previews, bubbles/chat-heads, delivery/read/typing indicators | FEATURES.md Q5–Q9 (all resolved EXCLUDE), `docs/PRIVACY.md` §§1,2,5,8.2,9 | NOT ported — excluded upstream, stays excluded. Group MMS stays out (mmsd-tng supports it; still out — product decision, not a platform gap). |
| F10 | Notifications: sender-only default, lock-screen sender/content/nothing, per-contact alert style (silent/vibrate/sound), starred bypass, quiet-hours/`BusinessSchedule`, no bubbles, quick reply persists to thread | `service/NotificationService.kt`, `service/NotificationPolicy.kt`, `service/NotificationPosture.kt`, `service/NotificationPrefs.kt`, `service/ArrivalNotify.kt`, `service/BusinessSchedule.kt`, `ui/ConversationMute.kt`, `data/ConversationMute.kt` | Client of `org.freedesktop.Notifications.Notify` (session bus; the shell daemon or any compliant daemon renders). Sender-only body by default → empty/placeholder body; lock-screen posture maps to notification hints + shell `lock_screen_notifications` (summary/count/off — shell already enforces). Sound: `sound-file` hint only when alert style is sound; vibrate: `feedbackd` (`org.sigxcpu.Feedback`) — vibrate-only needs `libfeedback`, allow via GI `Feedback` if present, else silent (documented degradation). Quick reply: `Respond` action in the notification → daemon writes to SQLite + sends. Quiet hours/`BusinessSchedule` → port pure logic; shell DnD/starred (`dnd_starred_numbers`, repeat-callers) stays shell-side and starred bypass is mirrored in-app. |
| F11 | Theme: 7 presets, auto-sync with xx-launcher broadcast (manual wins), persisted store | `theme/ThemePreset.kt`, `theme/ThemeStore.kt`, `theme/ThemeController.kt`, `theme/ThemeApplier.kt`, `theme/ThemeSyncReceiver.kt`, `theme/SuiteThemeClient.kt` | No broadcasts on Linux. Theme = read `~/.config/xx-wm/config.json` (`theme`, `font`, `text_size_scale`) via `GFileMonitor` (hot-reload like the shell) + local manual override (manual wins, same rule). Preset keys map 1:1 to shell `THEME_PRESETS` (`amoled`, `graphite`, `forest`, `ocean`, `burgundy`, `paper`, `mist`; Android `amoled-night` key → `amoled`; shell bonus `aura` accepted). Fallback: libadwaita dark/light when no shell config exists (Phosh sessions). Font: shell `font` key → GTK CSS `font-family` (`FONT_FAMILIES`), `text_size_scale` → CSS scale. Scaffold ships this (`src/txxt/theme.py`). |
| F12 | Backup: conversation+message JSON export (SAF create-document), idempotent `RestoreService` import (REPLACE-upsert, never destroys live ids, never duplicates); settings+blocklist dump; suite backup provider (sms.json + mms/ for xx-apps); platform-store safety-net import | `data/BackupJson.kt`, `data/ConversationExporter.kt`, `data/RestoreService.kt`, `core/BackupData.kt`, `core/BackupSerializer.kt` (version + range gates, strict parse), `backup/SmsBackupCodec.kt`, `backup/SmsBackupModels.kt`, `backup/SmsExportBudget.kt`, `backup/PlatformSmsImport.kt`, `backup/PlatformStoreRestore.kt`, `backup/PendingPlatformRestore.kt`, `backup/TxxtBackupProvider.kt`, `backup/TxxtBackupProvider.kt` door, `ui/SettingsBackup.kt`, `com.piercingxx.suite.backup/*` (`SuiteBackupProvider.kt`, `Snapshot.kt`, `TarStream.kt`) | Export: `Gtk.FileDialog` save → canonical JSON (same `BackupData` shape, version gate kept so Android↔Linux backups interchange). Import: file-open → same gates → REPLACE-upsert into SQLite (port `RestoreService` matching: conversation key + body + 5-min window). Settings+blocklist JSON included in the same file (Android keeps them separate — unify; document in import). MMS photos: export files alongside (`mms/<id>/` like `TxxtBackupProvider`'s `export/mms/`), JSON records the filenames; count-and-toast honestly when photos are absent (same rule as T1). Suite backup: NOT portable (no xx-apps/signature-permission IPC on Linux) — closest equivalent is the plain JSON export the user copies; document it. Platform safety-net import → modem-store reconcile (F7). SMS Backup & Restore XML (.xml `sms-backup-restore` schema) → supported IMPORT format (§5). |
| F13 | Dialer business-tier export (signature permission), dialer groups | `service/DialerBusinessTier.kt`, `service/DialerGroups.kt` | NOT portable (Android signature-permission IPC between suite apps). No equivalent: the Linux dialer is GNOME Calls / shell fallback; TxxT reads starred numbers from EDS instead. Drop. |
| F14 | App log + log-dump provider for suite debugging | `log/AppLog.kt`, `log/LogDumpProvider.kt` | Rotating log file under XDG state (`$XDG_STATE_HOME/txxt/txxt.log`); no provider IPC. `journalctl --user -u txxt-daemon` covers the daemon. |
| F15 | Role-switch warning (leaving default-SMS drops the archive), first-run copy | `todo.md` T3, `service/DefaultHandlerMonitor.kt` | No platform role on Linux → reframe: "uninstall wipes `~/.local/share/txxt/`" warning in Settings + export nudge. First-run copy explains SMS ownership (§4). |

Permissions (Android) → Linux equivalents: `RECEIVE/SEND/READ/WRITE_SMS`,
`RECEIVE_MMS`, `RECEIVE_WAP_PUSH` → polkit/system-bus access to
ModemManager + mmsd-tng (user in the right groups on pmOS; document).
`READ_CONTACTS` → EDS (portal-sanctioned, no prompt; denial path = bare
numbers stays). `POST_NOTIFICATIONS` → none needed (notification client).
`RECEIVE_BOOT_COMPLETED` → systemd user unit `txxt-daemon.service`
(autostart). `FOREGROUND_SERVICE*` → the daemon unit (no FGS concept).
`INTERNET` stays absent (kept: no sockets except system-bus D-Bus, EDS,
mmsd-tng, speech-dispatcher). Package-visibility `<queries>` → N/A.
`THEME_SYNC`/`TIER_SYNC`/`BACKUP` signature permissions → dropped (F11–F13).

## 2. What is NOT portable (with reasons)

1. Default-SMS-handler role + `SMS_DELIVER`/`WAP_PUSH_DELIVER` ordering
   (`SmsDeliverReceiver.kt`, `MmsDeliverReceiver.kt`). Linux has no
   exclusive SMS role; any session process can talk to MM. Replaced by the
   bus-name ownership convention (§4).
2. `RespondViaMessageService` (call-screen canned reply). No platform
   in-call hook on Linux; shell CallUI owns any quick-reply there.
3. Notification channels (`txxt_messages_v4…`, `NotificationPrefs`
   generation hack). `org.freedesktop.Notifications` has no channels;
   sound choice is a sound-file hint. Delete the generation logic.
4. SAF create/open-document (`SettingsBackup.kt` flows). Replaced by
   `Gtk.FileDialog` + XDG portals (same user gesture, sandboxed path).
5. `RingtoneManager` sound picker. Replaced by sound-theme name setting.
6. Suite IPC: `TxxtBackupProvider` (signature `BACKUP`), `LogDumpProvider`
   (`READ_SUITE_LOGS`), `DialerBusinessTier`/`DialerGroups` (`TIER_SYNC`),
   `SuiteThemeClient` broadcast, `ThemeSyncReceiver`. No cross-app
   signature IPC on a Linux phone; each becomes a local equivalent (F11, F12,
   F14) or is dropped (F13).
7. `BootReceiver`/`RebootReconcile` as a broadcast. Becomes daemon startup
   reconcile (F7) — same logic, different trigger.
8. `RECORD_AUDIO` absence stays absent: no dictation mic, no voice notes
   (PRIVACY.md §5). Speech-to-text would need a mic permission the product
   refuses; TTS (read-aloud) is output-only and fine.

## 3. Data model + storage paths (XDG)

SQLite at `$XDG_DATA_HOME/txxt/txxt.db` (`~/.local/share/txxt/txxt.db`),
Room schema v5 mapped 1:1 (cites: `app/schemas/…/TxxTDatabase/5.json`,
`data/ConversationEntity.kt`, `data/MessageEntity.kt`):

- `conversations(id, participant_addresses [\u0001-joined], is_starred,
  is_pinned, is_archived, is_muted, is_quarantined, muted_until_millis,
  sort_order)` — flags mirror `core/ConversationFlags.kt`.
- `messages(id, conversation_id, direction, transport [sms/mms],
  body, timestamp_millis, sender_address, is_read, sent,
  content_location, media_path)` — MMS `media_path` points under
  `$XDG_DATA_HOME/txxt/mms/<message-id>/` (matches provider `export/mms/`).
- `blocklist(kind [sender|keyword], value)` + `starred(address)` —
  ports `SettingsBlockingStore.kt` / `BlockingRules.kt` maps.
- `settings(key, value)` JSON — ports `SettingsStore.kt` + `ThemeStore.kt`
  (`txxt_theme`, auto-sync flag).
- `meta(key, value)` — `backup_version`, `last_reconcile_millis`.

Config (user-editable, shell-style): `$XDG_CONFIG_HOME/txxt/config.json`
(settings UI writes it; unknown keys ignored, invalid file keeps last
good — same contract as `docs/config.md`).
State: `$XDG_STATE_HOME/txxt/txxt.log` (rotating). Cache:
`$XDG_CACHE_HOME/txxt/` (staged photos pre-send — same lifetime rule as
`PhotoStaging`: deleted after send or on launch; MMS download scratch).

Modem store discipline (MM deletes nothing by itself): inbound SMS is
`List`ed → stored in SQLite → `Messaging.Delete(path)` so the modem never
fills; a `pending_delete` flag per message makes the delete idempotent
across crashes (reconcile re-deletes paths already imported). Outbound
stays in SQLite as the archive; modem copies are deleted after `Send`
confirms. Quarantined rows stay in SQLite with `is_quarantined` (never
deleted implicitly).

Import paths: (a) TxxT Android canonical backup JSON (version-gated,
REPLACE-upsert, photo files resolved from sibling `mms/` dir); (b) SMS
Backup & Restore XML (`smses/sms[@address,date,type,body,read]`,
`mms[parts/part]`) → same upsert path, photos from `mypictures/`-style
siblings when present; (c) vCard 3.0/4.0 → starred/blocklist contact
bootstrap via EDS match (numbers only — names stay in EDS).

## 4. Integration with XX-WM

- Home slots: Comms folder binds "Text" to `.desktop` id
  `io.piercingxx.TxxT` when installed (same rule as Phone/Text binding to
  GNOME Calls/Chatty in `design.md`); Calendar slot is untouched (TxxT has
  no calendar). Seeded label "Text".
- Caller-ID lookup: TxxT reads EDS directly (F3); it does not replace the
  shell `ContactBook` (`contacts.py` VCF/JSON). If a later lane wants one
  shared lookup, it goes through EDS, not shell imports.
- SMS ownership: the app owns session-bus `io.piercingxx.TxxT` while
  running. The shell's `_on_sms_received` (`window.py:2370`) currently
  defers only to `sm.puri.Chatty` (`modem_monitor.py:36`,
  `bus_name_has_owner`, `window.py:2371-2378`). Spec change for the shell
  lane: defer when **either** name is owned —
  `any(bus_name_has_owner(n) for n in (CHATTY_BUS_NAME,
  'io.piercingxx.TxxT'))` — so no double notifications and no shell SMS
  fallback surface while TxxT runs. TxxT posts its own notifications via
  `org.freedesktop.Notifications` (with quick-reply action); when TxxT is
  absent/not running, the shell fallback behaves exactly as today.
- The shell's built-in dialer/SMS fallbacks (`dialer.py`, `sms.py`) stay
  as fallbacks; they are used only when neither Chatty nor TxxT owns its
  name. No D-Bus API change beyond the deferral list.
- DnD/starred: shell `dnd_starred_numbers` + repeat-callers stay
  shell-side for calls; TxxT mirrors starred-bypass for message
  notifications (F10). Mute (`muted_apps`) applies to `io.piercingxx.TxxT`
  notifications like any app.
- Theme: read-only consumer of `~/.config/xx-wm/config.json` (F11); TxxT
  never writes shell config.

## 5. Architecture

Packages (`src/txxt/`): `main.py` (Adw.Application, NavigationView,
D-Bus claim of `io.piercingxx.TxxT`), `conversations.py` (list page +
presenter, ports `ConversationListPresenter.kt`), `thread_page.py`
(ports `ThreadActivity.kt` + presenters), `compose.py` (send gate +
pipeline trigger, ports `SendGate.kt` + `SendPipeline.kt`),
`new_conversation.py` (ports `RecipientPicker.kt`), `settings_page.py`
(+ `blocking_page.py`, `quarantine_page.py`), `store.py` (SQLite layer,
ports `*Dao`/`*Entity`/`InboundStore`/`OutboundStore`/
`QuarantineStore`/`RestoreService`), `mm_client.py` (system-bus MM
Messaging + mmsd-tng client, worker-thread only), `daemon.py`
(`txxt-daemon`: inbox reconcile, retry queue, notifications, quick-reply
handler), `notify.py` (notification client: posture/redaction per
`NotificationPolicy.kt`), `blocking.py` (pure filter, ports
`block/*` + `core/Blocking*.kt`), `backup.py` (pure codec + import,
ports `core/BackupSerializer.kt` + `SmsBackupCodec.kt`),
`theme.py` (pure preset map + shell-config reader), `settings.py`
(pure store), `phone_numbers.py` (pure normalization, ports
`core/PhoneNumbers.kt`), `media.py` (scrub + fit, ports
`ImageMetadataScrubber.kt` + `MmsImageFit.kt`), `log.py` (rotating
state-dir log, ports `AppLog.kt`).

Threading: GTK main loop never blocks (XXWM_RULES.md). All MM/mmsd-tng/
EDS/SQLite-heavy I/O runs in a `ThreadPoolExecutor` (1 writer + N
readers) or async Gio calls; results return via `GLib.idle_add`. Sync
`call_sync` with timeouts lives only on worker threads (same rule as
`modem_monitor.resolve_modem_path`). D-Bus signal subscriptions
(`Messaging.Added`, mmsd-tng events) use `signal_subscribe` with the
exact 7-arg handler shape per XXWM_RULES.md.

D-Bus: owns `io.piercingxx.TxxT` (session) for the ownership convention;
notification replies arrive as `org.freedesktop.Notifications` actions
(no separate name needed); MM/mmsd-tng are clients on the system bus.

`.desktop`/activation/autostart: `io.piercingxx.TxxT.desktop`
(`DBusActivatable=true`), metainfo `io.piercingxx.TxxT.metainfo.xml`;
`txxt-daemon.service` (systemd `--user`, `WantedBy=default.target`,
`After=ModemManager.service`) runs the background reconcile/retry/notify
path headless; the GUI talks to the same SQLite file (WAL mode, single
writer thread per process, `busy_timeout=5000`).

## 6. Milestone plan (ordered build lanes, each ≤ ~1500 lines + tests)

- L1 Core data layer: `store.py` (schema §3 incl. flags/mute-until/
  quarantine), `phone_numbers.py`, `blocking.py` ports. Tests: Room-parity
  unit tests (pin cap, mute-until, starred bypass, quarantine hide).
- L2 Backup/import: `backup.py` (version+range gates, REPLACE-upsert),
  SMS Backup & Restore XML import, photo-sibling handling. Tests:
  round-trip, double-import-no-dup, Android-JSON interchange fixtures.
- L3 Modem client + daemon: `mm_client.py` (MM Messaging + mmsd-tng),
  `daemon.py` (reconcile, delete-after-import, retry queue,
  notifications via `notify.py` posture). Tests: fake system-bus
  (tuple-unpack replies per XXWM_RULES.md), reconcile idempotence,
  pending_delete crash recovery.
- L4 Conversation list + thread UI: pages, search (list + in-thread),
  swipe/long-press actions, compose + send gate, `[Photo]` placeholders.
  The UI sends via `io.piercingxx.TxxT1.Send` and marks read via
  `MarkRead` (never directly via ModemManager, never by writing the
  store behind the daemon), and subscribes to `MessagesChanged` instead
  of polling. Tests: headless presenter tests + GTK smoke (offscreen)
  for 320 px width, ellipsize, no-overflow.
- L5 MMS photos end-to-end: `media.py` scrub/fit, attach pipeline,
  tap-to-fetch, audio-drop + optional auto-reply. Tests: EXIF-strip
  fixtures (fail-closed on unknown formats), fit math.
- L6 Settings/blocking/quarantine pages + EDS wiring + theme shell-sync
  (`theme.py` reader, GFileMonitor hot-reload, manual-wins). Tests:
  shell-config mapping (all 8 presets + custom + missing-file fallback),
  EDS-denied → bare numbers.
- L7 Polish: first-run copy, role-warning reframed (F15), log rotation,
  `.desktop`/metainfo/daemon unit, meson install, shell deferral patch
  (window.py one-liner + shell test). Gate: full pytest + ruff, on-device
  SIM proof (send/receive/MMS/delete-after-import) before DONE.

Sources: every Kotlin cite above is under
`ref/Txxt/app/src/main/{kotlin,java}/com/piercingxx/txxt/`; shell cites
are `ref/xx-wm/launcher/src/{modem_monitor,contacts,notif_daemon,window,config}.py`,
`ref/xx-wm/{design.md,docs/config.md}`.

## Review notes (orchestrator, 2026-10-15) — these override the text above

1. mmsd-tng runs per user on the **session** bus (`org.ofono.mms`, started by the user's systemd as "Multimedia Messaging Service Daemon" on pmOS v26.06), not the system bus. ModemManager is on the system bus.
2. `Adw.PreferencesWindow` is deprecated (libadwaita ≥ 1.6): use `Adw.PreferencesDialog` (and `Adw.Dialog` generally).
3. "Call" from a thread opens a `tel:` URI with `Gtk.UriLauncher` so it reaches whichever dialer is installed (xx-dialer port, GNOME Calls, the shell fallback). Never hardcode `org.gnome.Calls`.
4. postmarketOS v26.06 Phosh images run systemd; still keep the daemon startable without systemd (an XDG autostart `.desktop` with `X-GNOME-Autostart-Phase` is not portable — ship both the systemd user unit and an autostart entry guarded by `systemctl --user is-enabled` / a lock so only one instance runs).
5. Contacts (owner directive 2026-10-15): names, starred and blocked come from xx-contacts' D-Bus API `io.piercingxx.Contacts1` (LookupNumber, Search for the recipient picker, SetTags, `Changed` signal), not from EDS directly. Fall back to EDS read-only lookups, then bare numbers, when xx-contacts isn't installed.
6. D-Bus split (orchestrator bug 2026-10-16): the daemon owned the GTK app id `io.piercingxx.TxxT`, so the GUI registered as a secondary instance and exited (`Failed to register: ... No such interface "org.gtk.Actions"`). The daemon now owns bus name `io.piercingxx.TxxT.Daemon` at object path `/io/piercingxx/TxxT/Daemon` (interface `io.piercingxx.TxxT1` unchanged; D-Bus activation file `data/io.piercingxx.TxxT.Daemon.service`, installed to `dbus-1/services`). The GUI keeps `application_id` = `io.piercingxx.TxxT` (desktop id, metainfo `<dbus>` entry, and the name the shell defers to for SMS ownership — all unchanged). Shell lane: consult the daemon at the new name/path; keep deferring to `io.piercingxx.TxxT` for SMS ownership.
