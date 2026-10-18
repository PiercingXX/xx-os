# XX-Contacts Linux Port — Spec

Linux port of the PiercingXX Android contacts app (`ref/xx-contacts`,
package `com.piercingxx.xxcontacts`, GrapheneOS-targeted) to postmarketOS /
Mobian / FuriOS / PureOS phones running phoc (XX-WM or Phosh), 360x720
logical px (min 320x200), touch-first with on-screen keyboard.

Stack: Python 3.12+, GTK4 + libadwaita via PyGObject (`Adw.Application`,
`Adw.NavigationView`), meson install, stdlib + GI only. This lane delivers
this spec plus a runnable scaffold (`src/xx_contacts/`, `meson.build`,
`tests/`); later lanes build features from this spec.

Core principle (unchanged from Android): a UI over the system address book.
Not a second contact store. On Linux the system address book is Evolution
Data Server (EDS), so GNOME Calls, Chatty, the XX-WM shell caller ID, and
xx-dialer/TxxT all see the same contacts.

## 1. Feature inventory of the Android app

Every screen, setting, background behavior, store, permission, integration,
with the Kotlin files behind each.

### 1.1 Screens

| Screen | Behavior | Files |
|---|---|---|
| List | Aggregate rows, search (name / normalized digits / email), has-phone filter, photos toggle, long-press row menu (Open/Call/Message/Share/Group/Delete), new-contact button, Me button, picker mode | `MainActivity.kt`, `data/ContactsQuery.kt`, `data/ContactLookup.kt`, `data/ContactVisibility.kt`, `data/HasPhoneToggle.kt`, `data/ShowPhotosToggle.kt`, `data/RowActions.kt`, `ui/ContactListAdapter.kt`, `ui/HairlineDecoration.kt`, `ui/ImeLift.kt`, `res/layout/activity_main.xml`, `res/layout/item_contact.xml` |
| Detail | Full fields, star / custom ringtone / send-to-voicemail toggles, group row + picker, Business toggle, call history, Call/Message/Email/Maps actions, Edit, Share vCard, Delete, incoming-vCard import confirm | `ContactDetailActivity.kt`, `data/ContactLoad.kt`, `OutboundIntents.kt`, `GroupPicker.kt`, `data/ContactCallHistory.kt`, `data/ContactGroups.kt`, `data/FamilyGroups.kt`, `data/BusinessTier.kt`, `data/GroupChoices.kt`, `res/layout/activity_contact_detail.xml` |
| Editor | New + edit, First/Last name, multiple phones/emails with type spinners, org/title/address/birthday/nickname/note, photo picker, account picker, cancel, save with validation, INSERT/EDIT/INSERT_OR_EDIT/SHOW_OR_CREATE prefill, finish-on-save result | `ContactEditActivity.kt`, `data/ContactsWrite.kt`, `data/ContactLoad.kt`, `EditorNames.kt`, `data/AccountPicker.kt`, `PickContract.kt` (mode), `SaveCompleted.kt`, `ui/TypeSpinnerAdapter.kt`, `res/layout/activity_contact_edit.xml`, `res/layout/item_contact_edit_row.xml` |
| Me card | Own profile (ContactsContract.Profile), edit-to-profile write, share/export/import vCard | `MeCardActivity.kt`, `data/VCard.kt`, `data/VCardImport.kt`, `data/VCardSaf.kt`, `res/layout/activity_me_card.xml` |
| Settings | Has-phone toggle, photos toggle, Me entry, SIM import, Logs entry | `SettingsActivity.kt`, `data/SimImport.kt`, `log/LogsUi.kt`, `res/layout/activity_settings.xml` |

### 1.2 Settings and toggles (persisted)

| Setting | Default | File |
|---|---|---|
| Has-phone list filter | off, remembered | `data/HasPhoneToggle.kt` |
| Show photos | off, remembered | `data/ShowPhotosToggle.kt` |
| Theme name + background | AMOLED Night until sync | `theme/ThemeStore.kt` |
| Group membership | dialer store / prefs fallback | `data/ContactGroups.kt`, `data/FamilyGroups.kt` |

### 1.3 Background behaviors

| Behavior | Files |
|---|---|
| Theme-sync broadcast receiver (`xx.launcher.THEME_CHANGED`, signature permission) | `theme/ThemeSyncReceiver.kt`, `theme/ThemeStore.kt`, `theme/ThemeApplier.kt`, `theme/ThemeGround.kt`, `theme/ThemePalette.kt`, `theme/HairlineDrawable.kt` |
| Provider change observer (list reload on ContactsContract change) | `MainActivity.kt` (ContentObserver) |
| Suite backup provider (prefs + `export/contacts.vcf` vCard 4.0 with photos + `export/groups.json`, merge-by-lookup-key restore) | `backup/ContactsBackupProvider.kt`, `backup/ContactsBackupRepository.kt`, `backup/VCardCodec.kt`, `suite/backup/SuiteBackupProvider.kt`, `suite/backup/Snapshot.kt`, `suite/backup/TarStream.kt` |
| Log dump provider + rotating file log + ring | `log/LogDumpProvider.kt`, `log/AppLog.kt`, `log/LogsUi.kt` |
| Join/split aggregates via AggregationExceptions | `data/AggregationJoinSplit.kt` |

### 1.4 Data stores, permissions, integrations

| Item | Detail | Files |
|---|---|---|
| ContactsContract (system of record; RawContacts null/null local inserts; StructuredName always; LOOKUP_KEY identity) | read/write | `data/ContactsQuery.kt`, `data/ContactsWrite.kt`, `data/ContactLookup.kt`, `data/ContactLoad.kt`, `design/intent-and-provider-contract.md` |
| Writable aggregate columns STARRED / CUSTOM_RINGTONE / SEND_TO_VOICEMAIL | provider columns | `ContactDetailActivity.kt` (`starredPairs`, `voicemailPairs`, `ringtonePairs`) |
| Dialer tier store (`content://com.piercingxx.xxdialer.tier/biz`, `/groups`, `/history`, `/window`): Business flag, user groups + Blocked, call history window | cross-app provider, fail-open | `data/BusinessTier.kt`, `data/FamilyGroups.kt`, `data/ContactCallHistory.kt` |
| Runtime permissions READ_CONTACTS + WRITE_CONTACTS + GET_ACCOUNTS, request-once, honest empty-list deny path, Contact Scopes guard | no INTERNET, allowBackup=false | `data/RuntimePermissions.kt`, `data/PermissionAsk.kt`, `data/ContactScopesGuard.kt`, `ContactsApp.kt`, `AndroidManifest.xml` |
| Intent contract: PICK / GET_CONTENT (contact, phone_v2, email_v2, postal, vcard), INSERT / EDIT / INSERT_OR_EDIT / SHOW_OR_CREATE (tel:, mailto:) / VIEW / QUICK_CONTACT / SEND+VIEW vcard, APP_CONTACTS category, FileProvider share | outbound: ACTION_DIAL tel:, SENDTO smsto:/mailto:, VIEW geo: | `PickContract.kt`, `SaveCompleted.kt`, `OutboundIntents.kt`, `data/VCardSaf.kt`, `AndroidManifest.xml`, `res/xml/file_paths.xml` |
| vCard 3.0 share/SAF (FN/N/TEL/EMAIL/ORG/ADR/BDAY/NICKNAME/NOTE, folded-line tolerant, 4.0 tolerant parse) | per-contact share, export, import | `data/VCard.kt`, `data/VCardImport.kt`, `data/VCardSaf.kt` |
| SIM import (com.android.sim RawContacts copied to local null/null) | with toasts | `data/SimImport.kt` |
| Editor name rules (First + Last only, compose/split) | pure | `EditorNames.kt` |
| Text glyphs (star, close, caret), monochrome drawables, JetBrains Mono | theme | `ui/GroupGlyphs.kt`, `res/drawable/*`, `res/values/*` |

## 2. Linux implementation per feature

System address book = EDS (`EBook.BookClient` / `EBookContacts.EContact`
over `EBook.SourceRegistry`, GI namespaces `EBook 3.0` /
`EBookContacts 1.2`). Identity = EContact UID (persistent string, same role
as LOOKUP_KEY). All provider I/O runs off the GTK main loop (section 5).

### 2.1 List, search, row menu

EDS `BookClient.get_contacts` with an `EBookQuery` (name/phone/email
contains) replaces `ContactsQuery`; UID-keyed rows replace
`ContactLookup`. The ContentObserver becomes an EDS `BookView` with added /
changed / removed signals. Search ranking (number, then name-prefix, then
substring) and the tail-digit matcher are pure ports already in the
scaffold (`contacts.py`). Has-phone filter and photos toggle become app
prefs (`app_config.py`); blocked contacts stay search-only
(`visibility.py`, port of `ContactVisibility`). The row menu
(Open/Call/Message/Share/Group/Delete) becomes a text `Adw` action list;
`ImeLift` is unneeded (squeekboard lifts natively) but the search field
must sit above the keyboard like the drawer does. Hairlines and monochrome
rows come from the shell theme CSS (`shell_theme.py`); text glyphs
(Star, close, caret) carry over as text, not icons.

### 2.2 Detail

Field load maps EContact properties (names, TEL, EMAIL, ORG, ADR, BDAY,
NICKNAME, NOTE, PHOTO) onto the same sections (`ContactLoad` port). Star,
Business, Blocked and custom groups live in the shared sidecar
(section 4), not in EDS, and render in the same group row (`GroupPicker`
port as a bottom sheet). Call history stays read-only and fail-open, but
its source changes: the xx-dialer tier provider does not exist on Linux,
so history comes from the shell/Dialer lane's call log over D-Bus when
available, else the section is hidden. Outbound actions become:
tel: via the default call handler (GNOME Calls, `org.gnome.Calls`) with
`xdg-open` fallback; smsto: via Chatty (`sm.puri.Chatty`) with fallback;
mailto: default mailer; address via the maps handler (`geo:`). Delete
confirms before removing the EContact.

### 2.3 Editor and Me card

One EContact create/update form: First + Last (`names.py`, port of
`EditorNames`), typed phone/email rows (`Adw.ComboRow`, port of
`TypeSpinnerAdapter`), org/title/address/birthday/nickname/note, photo via
`Gtk.FileDialog` written as EDS PHOTO bytes. Local saves target the
default EDS source. The Me card is a designated EContact UID kept in app
config (EDS has no self-profile concept like `ContactsContract.Profile`);
share/export/import vCard behave exactly as on Android.

### 2.4 vCard, export, SIM

The vCard 3.0 core is ported pure (`vcard.py`: FN/N/TEL/EMAIL/ORG/TITLE/
ADR/BDAY/NICKNAME/NOTE, unfolding, 4.0-tolerant parse). SAF becomes
`Gtk.FileDialog` (portal-backed on the phone): import with the same
confirm dialog as `detail_vcard_import_confirm`, export, and share via
`xdg-open`/portal instead of FileProvider. SIM import is NOT portable:
ModemManager exposes no SIM address-book API (and the stack uses MM, not
oFone phonebook), so there is nothing to copy from; the closest equivalent
is importing a `.vcf` exported from the old phone, which the spec keeps as
the migration path (section 4).

### 2.5 Star / ringtone / voicemail columns

- STARRED: no portable EDS flag exists, so starred lives in the sidecar
  and drives the DnD bridge (section 3).
- CUSTOM_RINGTONE: no EDS field; the closest equivalent is a
  ringtone-URI-per-UID sidecar entry the shell call UI consults at ring
  time (polish lane). The sound picker becomes a `Gtk.FileDialog` over the
  XDG sound dirs with `Gst` preview.
- SEND_TO_VOICEMAIL: no EDS field and no per-contact voicemail routing on
  Linux; the closest equivalent is the Blocked tag, which the call UI uses
  to auto-decline.

### 2.6 Intents, picker, sharing-out

Android intents become a session-bus API: the app owns
`io.piercingxx.XXContacts` (automatic via `Adw.Application`) and exposes
`/io/piercingxx/Contacts` with a `PickContact(kind)` method returning the
chosen UID plus phone/email, replacing PICK / GET_CONTENT / INSERT_OR_EDIT
/ SHOW_OR_CREATE / QUICK_CONTACT. `finishActivityOnSaveCompleted` becomes a
`close-on-save` call flag. `text/vcard` files open in the app through the
`.desktop` `MimeType` entry with the Android import-confirm dialog.
Outbound tel:/smsto:/mailto:/geo: URIs are owned by the dialer/SMS/mail/
maps handlers, never this app.

### 2.7 Theme, logging, backup, guards

`THEME_SYNC` broadcasts do not exist on Linux; instead the app reads
`~/.config/xx-wm/config.json` (same 8 presets + fonts, `shell_theme.py`)
and watches it with a `Gio.FileMonitor` for instant apply, falling back to
libadwaita dark/light when absent. The rotating file log and ring port to
`~/.local/share/xx-contacts/logs/` with the same caps (256 KB, 300 lines,
no message bodies/numbers); the cross-app log provider becomes a local
copy/share action. The suite backup keeps its exact format —
`contacts.vcf` (vCard 4.0, UID line, base64 PHOTO) plus `groups.json`,
merge-by-UID restore — so Android exports import cleanly. The
`verifyNoInternet`/`verifyNoRoom` build gates become a CI grep over `src`
(no `socket`/`requests`/`sqlite3` imports) plus the existing ruff gate.
Contact Scopes partial grants have no Linux equivalent and are dropped;
EDS sources are either present or not. Join/split via AggregationExceptions
is NOT portable (EDS exposes no link/unlink API); the equivalent is a
manual merge (copy fields into one EContact, delete the duplicate) offered
from the detail screen, never silent client-merge by name.

## 3. Integration with XX-WM

App id `io.piercingxx.XXContacts` (`.desktop`, metainfo, `Adw.Application`).
The shell's default layout seeds a Comms folder (Phone, Text, Email, Chat,
SoftPhone) bound to the real telephony apps; the contacts entry point is a
Contacts member of that folder resolved by this `.desktop` id
(`default_layout.py` gains it as a candidate, skipped when not installed,
like every other seeded member). The Calendar home slot is unaffected,
except birthdays may feed EDS calendar events as an opt-in polish item.

- Caller ID lookup: the shell's `contacts.py` `ContactBook` currently reads
  `~/.local/share/xx-wm/contacts.json` plus GNOME Contacts VCF paths and
  matches by number tail. The spec migrates it to EDS as the primary source:
  query the default address book asynchronously, keep the tail-7 matcher
  semantics (`lookup_number` port in the scaffold), and retain the JSON/VCF
  files only as an offline fallback until EDS is populated on-device. The
  migration must keep `lookup_number(number) -> name | None` synchronous for
  existing callers by caching the last EDS snapshot in memory.
- SMS ownership: none. The app never touches ModemManager Messaging; Chatty
  (`sm.puri.Chatty`, probed like `modem_monitor.py` probes
  `CALLS_BUS_NAME`/`CHATTY_BUS_NAME` with `bus_name_has_owner`, 500 ms)
  keeps the SMS role and the shell's `sms.py` fallback stays as-is.
- Notifications: the app is a plain `org.freedesktop.Notifications` client
  (import-done, save-done); the shell daemon applies `muted_apps`, DnD, and
  Focus rules to it like any app. It never owns the Notifications name.
- Shell defers to the app: wherever the shell has a contact surface of its
  own (dialer suggestions, "Save contact" from Recents/SMS), it probes
  `io.piercingxx.XXContacts` with the same 500 ms owner probe and, when
  owned, routes through the app's D-Bus picker/editor instead of its inline
  fallback. No dual writes: EDS is the only store, so there is nothing to
  reconcile.
- DnD starred bridge: starred contacts drive the shell's DnD exceptions
  (config `dnd_starred_numbers`, plus the repeat-caller rule which stays
  shell-side). The bridge collects starred numbers from the sidecar,
  normalizes to digits, and merges them into that shell config key
  (pure helpers in the scaffold: `dnd_bridge.py`). The shell owns
  `config.json`; the app edits only this key (read-modify-write) and tracks
  the set it applied so unstarring removes numbers again. Last-writer-wins
  on the key is acceptable because the app only adds/removes numbers
  derived from the starred set.
- Settings scope stays split as today: system items in the shell Settings
  page, shell prefs in `~/.config/xx-wm/`, app prefs in
  `~/.config/xx-contacts/` — never in the shell config except the
  `dnd_starred_numbers` bridge key.

## 4. Data model and storage

EDS is the system of record for every contact field it can carry
(names, phones, emails, org/title, addresses, birthday, nickname, note,
photo, CATEGORIES for custom groups). Everything EDS cannot carry lives in
one shared sidecar so xx-dialer/TxxT and the shell read the same tags:

| Path | Content |
|---|---|
| EDS (default source; `~/.local/share/evolution/addressbook/`) | Contacts, custom groups mirrored to CATEGORIES |
| `~/.local/share/piercingxx/contact-tags.json` | `{econtact_uid: {star, business, blocked, groups[], ringtone}}`, shared with xx-dialer/TxxT lanes |
| `~/.config/xx-contacts/config.json` | Prefs (has-phone-only, show-photos, me-contact UID, applied starred set) |
| `~/.local/share/xx-contacts/exports/` | vCard exports |
| `~/.local/share/xx-contacts/logs/contacts.log` | Rotating log (256 KB cap, 300-line ring) |

Import path from Android: (a) any standard `.vcf` 3.0/4.0 file via the
file picker, merged by FN + phone match to avoid duplicates; (b) the
Android suite-backup `export/contacts.vcf` (vCard 4.0, UID = lookup key):
UIDs are remapped to fresh EContact UIDs after a FN + phone match pass, so
restore merges instead of duplicating; (c) the backup `export/groups.json`
(group name lists): names fold into the sidecar and EDS CATEGORIES, with
Star/Business/Blocked mapping to the sidecar flags. (SMS backup XML is out
of scope here; it belongs to the dialer lane.)

## 5. Architecture

Packages (`src/xx_contacts/`): `main` (Adw.Application + NavigationView),
`list`, `detail`, `editor`, `mecard`, `settings_ui`, `eds` (BookClient
wrapper, BookView subscription, SourceRegistry picker), `membership`
(sidecar read/write + change signal), `vcard`, `names`, `groups`,
`visibility`, `contacts` (model + pure match/search), `dnd_bridge`,
`shell_theme`, `app_config`, `applog`, `outbound` (tel/smsto/mailto/geo
dispatch), `picker_service` (D-Bus `PickContact`). Pure logic stays in
import-safe modules so pytest runs headless; GTK/EDS objects live only in
the UI and backend layers.

Threading: never block the GTK main loop. EDS calls go through async
Gio calls or a worker thread posting back with `GLib.idle_add`; D-Bus
probes use the async `Gio.DBusConnection.call` form (tuple replies,
two-arg callbacks per the XXWM traps); the shell-config `Gio.FileMonitor`
and EDS `BookView` signals drive live updates instead of polling. No
`subprocess`, no `time.sleep`, no `call_sync` with long timeouts on the
main thread.

D-Bus and launch: well-known name `io.piercingxx.XXContacts` (held by the
running app), object `/io/piercingxx/Contacts`, interface
`io.piercingxx.Contacts` (`PickContact(kind) -> (uid, phone, email)`).
`.desktop` id `io.piercingxx.XXContacts` with `MimeType=text/vcard`.
No autostart daemon in v1: the DnD bridge syncs while the app runs (plus a
config-file monitor); a `--sync-starred` one-shot covers the closed-app
case, and an opt-in `systemd --user` unit is a polish-lane decision, not a
requirement.

## 6. Milestone plan (build lanes, each <= ~1500 lines of change)

| Lane | Content | Acceptance tests |
|---|---|---|
| L1 Data layer | `eds` wrapper + `membership` sidecar + pure ports (scaffold grows up) | pytest headless: CRUD against a throwaway EDS test source or fake client, sidecar round-trip, match/search/vCard/groups suites green |
| L2 List + search | NavigationView list, search ranking, filters, row menu, live BookView updates, 360x720 layout | pytest on query builders; manual phone check: type-to-search, toggle filters, rotate-equivalent resize without overflow |
| L3 Detail | Sections, star/Business/Blocked/groups, outbound actions, read-only history-or-hidden, delete confirm | pytest on update payloads; phone check: star survives restart, blocked hides from list but search finds it |
| L4 Editor + Me card | Create/update form, validation, photo, account (source) picker, Me designate/share | pytest on validation + operation builders; phone check: new contact visible in GNOME Calls, Me share yields parseable vCard |
| L5 Import/export/share | File picker flows, vCard confirm dialog, Android suite-backup import (UID remap, groups fold-in) | pytest on merge logic with fixture `contacts.vcf` + `groups.json`; phone check: round-trip export re-imports clean |
| L6 Shell wiring | D-Bus picker service, vcard MIME handling, shell `contacts.py` EDS migration, DnD bridge sync + one-shot | pytest on bridge merge; phone check: shell shows caller name from EDS, starred number rings through DnD |
| L7 Polish | Per-contact ringtone sidecar + call-UI consult, birthday-to-calendar opt-in, backup export action, distro packaging, touch/a11y pass | Full gate green on device; ruff + pytest + no-network grep; 320x640 overflow audit |

## Review notes (orchestrator, 2026-10-15) — these override the text above

1. SMS is owned by the TxxT port (`io.piercingxx.TxxT`) on XX-WM devices (Chatty only when TxxT is absent).
2. Shared contact tags — single source of truth, settled across the suite: `~/.local/share/piercingxx/contact-tags.json` (this app's `membership` module defines and owns the schema; writes are atomic replace + a monotonically increasing `revision` field; readers watch it with Gio.FileMonitor). xx-dialer and TxxT read AND write star/business/blocked through the same schema (ship the module as a small shared file they vendor, or document the schema precisely in `docs/contact-tags.md`); xx-dialer's SQLite tier/blocked tables are a warm cache rebuilt from this file, never a second source of truth.
3. Starred also goes to EDS where possible (folks/GNOME Contacts read `X-EVOLUTION-…`/category conventions — verify which one GNOME Calls honors on the device and use it) so non-PiercingXX apps see favourites too; the sidecar remains authoritative for business/blocked.

## Revision 2 — owner directive (2026-10-15): xx-contacts OWNS the contacts

The owner's decision: xx-contacts is the contacts store itself, not a UI over a system address book (the Android app mirrored ContactsContract; the Linux app must not mirror EDS the same way). This revision overrides every "EDS is the system of record" statement above, and review note 2 (the sidecar) is folded into the database below.

1. **Store (source of truth):** `~/.local/share/xx-contacts/contacts.db` — SQLite, WAL, `busy_timeout`, schema-versioned migrations, mode 0600, atomic writes. Tables: `contacts` (uid TEXT PK — RFC 4122 uuid, display_name, given, family, middle, prefix, suffix, nickname, org, title, birthday, anniversary, note, photo_path, starred, business, blocked, ringtone, created, updated, revision), `phones` (contact_uid, number_raw, number_e164, number_digits, type, label, is_primary), `emails`, `addresses` (structured ADR fields), `urls`, `ims`, `relations`, `dates`, `groups` + `group_members`, `extra_vcard` (lossless storage of any vCard property the model doesn't map, so import → export round-trips byte-equivalent in content), `tombstones` (uid, deleted_at, revision — for sync), and a global monotonically increasing `revision` in `meta`. Photos stored as files under `~/.local/share/xx-contacts/photos/<uid>.jpg` (re-encoded, EXIF stripped).
2. **vCard 4.0 is the interchange format** (3.0 read too): full import/export, lossless via `extra_vcard`. Android suite backup `export/contacts.vcf` + `groups.json` import as already specified (UID remap, FN+phone merge).
3. **D-Bus service — the API every other app uses** (session bus name `io.piercingxx.Contacts`, object `/io/piercingxx/Contacts`, interface `io.piercingxx.Contacts1`; D-Bus activatable headless via `xx-contacts --service`, exits after 60 s idle; activation file `share/dbus-1/services/io.piercingxx.Contacts.service` ships so the first idle exit does not silently break clients — no `SystemdService=` line, no systemd unit):
   - `LookupNumber(s number) -> (s uid, s display_name, as tags)` — tags ⊂ {starred, business, blocked}; must answer in < 5 ms from an in-memory index (digits-suffix match, same semantics as the scaffold's tail matcher); used by the shell's caller ID, xx-dialer's ring policy and TxxT.
   - `Search(s query, u limit) -> a(sss)` (uid, name, primary number), `Get(s uid) -> s vcard`, `List(t since_revision) -> (t revision, as changed_uids, as deleted_uids)` for incremental consumers, `Create(s vcard) -> s uid`, `Update(s uid, s vcard)`, `Delete(s uid)`, `SetTags(s uid, as tags)`, `UpdateTags(s uid, as add, as remove) -> t revision` (atomic single-transaction add/remove for the star/business/blocked switches; unknown tags rejected like `SetTags`), `Pick(s kind) -> (s uid, s value)` (the headless service launches a `--pick` GUI that replies over `PickReply`; the caller contract is unchanged).
   - Signal `Changed(t revision, as uids)` — emitted only by the service, on its own connection.
   - The service is the single writer: the GUI reads SQLite directly but every GUI write (create, update/edit including photo, delete, tag switches) goes through these D-Bus methods (auto-activating), so `Changed` reaches the sender-filtered subscribers in the dialer, TxxT, email and shell. The Me designation lives in app config, not the database, so it needs no service method; vCard/file imports (L5) must likewise use `Create`.
   - Photo bytes ride inside the vCard `PHOTO` line (data-URI, which the codec already carries losslessly into `extra`); the service materializes the payload into `photos/<uid>.jpg` (normalized, EXIF-stripped, mode 0600) on `Create`/`Update` and deletes the file it orphans — no separate `SetPhoto` method, one call per save, one revision bump. Photo removal is a `PHOTO`-less `Update`.
   - The idle exit honors `XX_CONTACTS_IDLE_SECONDS` (tests shorten the wait).
   - Writes over D-Bus are allowed only for the same user (session bus is per user) — no further auth.
4. **Interop publishing (one-way, optional):** when Evolution Data Server is present, maintain a read-only EDS address book source named "XX Contacts" mirrored FROM the database (create/update/delete on every change, debounced) so GNOME Calls, Chatty and other apps show caller names. Setting `publish_to_eds` (default true when EDS exists). Never read back from it except the explicit one-time "Import from GNOME Contacts/EDS" action.
5. **Sync (later milestone, opt-in):** CardDAV client against the owner's Synology (Synology Contacts speaks CardDAV): two-way with ETags + the tombstones table, conflict policy newest-wins with a conflict log. Credentials in libsecret.
6. **Consumers:** the shell's `contacts.py` switches to `LookupNumber` over D-Bus with a local cached fallback (the shell must keep caller ID working when xx-contacts isn't installed — keep the current JSON/VCF path as the fallback). xx-dialer and TxxT use `LookupNumber`/`SetTags`/`Changed` instead of the sidecar file; DnD starred bridge reads `starred` from the database.
7. **Revised milestones:** L1 = database + models + vCard 4.0/3.0 codec (lossless) + phone normalization/index + migrations + the D-Bus service skeleton (LookupNumber/Search/Get/List/Create/Update/Delete/SetTags/UpdateTags/Changed) with tests; L2 = list/search UI; L3 = detail; L4 = editor + Me card + photos; L5 = import/export (vCard files, Android backup, one-time EDS import) + EDS publishing; L6 = shell/dialer/TxxT wiring (D-Bus consumers, picker); L7 = CardDAV sync; L8 = polish.
