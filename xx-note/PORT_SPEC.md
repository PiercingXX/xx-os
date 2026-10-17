# xx-note — Linux Phone Port Spec

Port of the PiercingXX Android app `xx-note` ("Markdown notes",
`ref/xx-note/`, design.md + `design/xx-note-screens.html`) to Linux phones
running phoc (XX-WM or Phosh). Stack: Python 3.12+, GTK4 (+ libadwaita via
the `Adw.Application` shell only, as xx-calendar/xx-auto do), meson install,
stdlib + GI only. App id `io.piercingxx.XXNote`, package
`xx-note/src/xx_note/`.

Visual target: the Android card grid (Pinned/Others split), fast editor with
inline Markdown styling, checklists, labels, archive, trash, search, settings
and sync screens — re-expressed in the shared `common/xx_ui` design system
(see `common/DESIGN.md`). Every theme preset works live. Phone canvas 360 px
wide (works down to 320 px, large text; also fits 600x1333 FLX1 and 360x800).

## 1. Android feature list → Linux mapping

### F1. Vault: one note = one `.md` file with YAML frontmatter (§8, R2)
- Android: local mirror in `filesDir/vault`, Room as cache+outbox, identity
  is a ULID in `id:`, filename `<ulid>-<slug>.md`, `title/pinned/archived/
  color/labels/type/reminder/conflictOf/conflictAt/trashedAt` owned keys,
  unknown keys preserved verbatim, malformed block degrades to body (R5).
- Linux: same bytes. Notes folder default `~/Documents/Notes` (changeable in
  Settings); trash at `<notes>/.xxnote/trash/`, index at
  `<notes>/.xxnote/index.json` (base snapshots, etags, outbox) instead of
  Room. `store.py` + `frontmatter.py` implement the file representation;
  Obsidian interop (labels as tags) preserved.

### F2. Grid: masonry card grid, Pinned/Others, capture in one tap (R1)
- Android: `GridScreen` + `NoteCard` (title, 6-line preview, labels, tone),
  capture box starts typing immediately, long-press multi-select, pull to
  refresh triggers sync.
- Linux: notes list with Pinned/Other sections, capture entry at top
  (does not steal focus at startup — no OSK pop), tap opens editor,
  long-press multi-select (pin/archive/label/trash), refresh button runs a
  WebDAV pass on a worker thread. Cards show tone dot from `color:`.

### F3. Editor: live Markdown styling + read view, autosave, undo (R4)
- Android: `EditorScreen` Compose `BasicTextField` with inline spans
  (headings, emphasis, links, code, lists, tappable checkboxes),
  `FormatToolbar` (bold/italic/strike/highlight/code/link/H1-3/bullet/
  number/task/quote/code-block/rule/indent/clear), `EditHistory` undo,
  `ChecklistToggle` tap handling, `TaskWrap` enter-continuation,
  `LineEdit` indent rules, debounced save with checklist sort-to-bottom.
- Linux: `Gtk.TextView` with `Gtk.TextTag`s for headings/bold/italic/
  strike/code/links/lists, checkbox lines toggled by tap (click gesture
  on `- [ ]`/`- [x]` runs) plus toolbar task button, read view toggle,
  `MarkdownFormat` commands ported 1:1, `EditHistory` undo/redo stacks,
  debounced autosave (750 ms) on worker, `type: checklist` bodies sorted
  checked-to-bottom on save only (never on pull/sync, D19).

### F4. Checklists: sort-to-bottom + item-wise merge (D18, §7.1)
- Android: `ChecklistSorter` (stable partition per task block on save),
  `ChecklistMerge` (per-item 3-way boolean merge, can never conflict on
  ticks; same-item divergent text forks), `TaskList` split/render law.
- Linux: `tasklist.py` + `checklist.py` port all three pure. Same save-only
  discipline.

### F5. Labels: flat tag set + label screens
- Android: `LabelsScreen`, `LabelGridScreen`, `LabelOps` (add/remove/rename),
  `LabelAggregation` counts, chip overflow rules, `LabelRouteEncoding`.
- Linux: Labels tab with counts, per-label note list, add/remove/rename
  via frontmatter rewrite; chips ellipsize, never widen the window.

### F6. Archive (first-class, distinct from delete) + Trash (7-day expiry)
- Android: `ArchiveScreen` filter, trash rows with days-remaining chip
  (`TrashMath`, ceiling semantics, final 24 h reads "today"), restore/
  delete, expiry is the only unlink (D9), edit outranks delete (D10).
- Linux: Archive tab, Trash tab with same chip math (`trash.py`), restore
  moves file back, expiry worker unlinks past-7-day files only.

### F7. Search: offline FTS over title+body (R4)
- Android: Room FTS4 `note_fts` + `SearchQuery.sanitize` (inert MATCH,
  prefix `*` inside quotes, CJK barewords, MAX_TERMS 64) + debounced field.
- Linux: Python substring index over the file scan (no sqlite dependency
  per "no new dependencies" — siblings use JSON/SQLite? xx-vitals uses
  sqlite; but substring keeps zero-dep and is honest at notes scale) with
  the same `search_query.sanitize` tokenization for term splitting,
  prefix matching and CJK runs. Search never touches the network.

### F8. Sync engine: pure policy + three-way merge + fork (R5/R6, §6–§7)
- Android: `SyncPolicy.decide` (12-row truth table → Pull/Push/Merge/Fork/
  Trash/Resurrect/Nothing/Replan), `MergeEngine` (prose diff3, checklist
  item-wise, frontmatter key-wise), `ConflictNamer`
  (`<slug>_<device>_<MMM-dd-HHmm-yyyy>_EditConflict_<n>.md`), base
  snapshots, `If-Match`/`If-None-Match` lost-update protection, ETag
  strength judgment + SHA-256 fallback (§4.2), WorkManager scheduling.
- Linux: `sync_policy.py` + `diff3.py` + `checklist.py` port the pure core
  1:1; `sync_engine.py` applies verdicts between the local folder and a
  generic WebDAV remote (Depth-1 PROPFIND for `getetag`/`getlastmodified`,
  GET/PUT/MOVE/MKCOL/DELETE, `If-Match` on PUT). Fork naming identical.
  Scheduling: sync on start, on debounced save, on refresh tap, and a
  systemd user timer (opt-in). Never claims synced on enqueue — per-note
  state set on response.

### F9. Transports: WebDAV kept, home-server transports dropped
- Android: two sync picks at setup — home-server/xx-drive (home-server door,
  FabricAuth, HubSession, DriveFilesClient, session hand-over) and WebDAV
  (DSM/Tailscale, Basic auth, `WebDavClient`, `PropfindParser`,
  `OneHostInterceptor`), plus SAF mirror export (xx-files one-way),
  share intents (`ACTION_SEND` mint a note), attachments
  (content-addressed `attachments/<sha256>.<ext>`).
- Linux per brief: DROP home-server login, home-server door, xx-drive client, hub
  hand-over, log upload, home-server sync. KEEP optional generic WebDAV
  (Nextcloud and others), off by default, Basic auth, credentials in
  libsecret only (`secrets.py`), never in config or logs. One-host guard
  kept (client refuses any host but the configured one; cleartext refused
  except loopback). Share-intent → open-text-file/`xdg-open` ingest where
  trivially mappable, else out of scope (§3). Attachments: referenced
  relative links keep rendering; capture/transcode UI is out of scope.

### F10. Settings/setup: local-first, Test connection, legible sync state
- Android: `SetupScreen` (Use locally / WebDAV / home server), `SettingsScreen`
  (`UiPrefs`: color mode Keep-pastels vs brand tones, theme sync),
  `SyncScreen` (connection, last success, queue, conflicts, Test button).
- Linux: first-run view offers Use locally / Set up WebDAV; Settings holds
  notes folder, color mode, sort order, WebDAV host/user/path + Test
  connection (real PROPFIND on a worker) + Sync now; syncrosis screen
  states last success/queue/conflicts plainly. No account screens.

### F11. Theme/colors (D12)
- Android: 12 Keep pastel names in `color:` round-trip; phone renders
  pastels by default with brand grey tones as a Settings choice; file
  unchanged either way.
- Linux: same `color:` names round-trip; tone dot maps name → xx_ui
  surface ramp; Keep-pastel vs tone choice in Settings.

## 2. Screens (Android → Linux)

| Android | Linux |
|---|---|
| Grid (Pinned/Others, capture, multi-select) | Notes tab: capture entry, pinned/other sections, long-press select |
| Editor (edit + toolbar + undo) | Editor page: TextView, format toolbar, undo/redo, read/edit toggle |
| Labels + label grid | Labels tab + per-label list |
| Archive | Archive tab (unarchive/restore) |
| Trash (days chip) | Trash tab (restore, empty-expired, days chip) |
| Search field | Search entry atop Notes (unfocusable until tapped, per suite rules) |
| Settings | Settings tab (folder, colors, sort, theme note) |
| Sync + Resolve sheets | Sync tab (status, Test, Sync now, conflicts list) |
| Setup | First-run page (Use locally / WebDAV) |
| Share ingest | Open `.md`/text via file dialog ingest (best-effort) |

## 3. Out of scope and why

- home-server/xx-drive sync, hub session hand-over, Fabric tokens:
  brief orders it dropped (public Play edition only).
- Log upload / log dump provider / AppLog screens: brief orders dropped;
  local error notice only, no tokens in logs.
- Google Drive / Dropbox: brief forbids; WebDAV only.
- Reminders/notifications, widgets/tiles, collaboration, drawings,
  rich text beyond Markdown, E2EE vault, realtime push: Android non-goals
  (§2), carried over unchanged.
- DSM/Tailscale-specific setup (certificates, MagicDNS, VPN): replaced by
  generic https WebDAV host config; Tailscale is just a network that
  resolves the host.
- Web page (`server/`, port 8462): server code is not a phone app; out of
  scope for this lane.
- Attachment capture/transcode (camera, HEIC, EXIF): phone-camera stack is
  its own lane; relative links still render.
- SAF mirror (xx-files one-way export): Linux equivalent is "the notes
  already are files"; a redundant exporter is not built.

## 4. Pure-logic port manifest (JVM → pytest)

`frontmatter` (parse/rewrite law), `slug`, `ulid` (generate/validate/
timestamp, monotonic same-ms), `diff3` (merge + markers + hunk contexts),
`sync_policy` (12-row table + T0 corners + push-rejection rounds),
`tasklist` (split/render law, indent cap), `checklist` (sorter stable
partition + item-wise tick merge, fork on divergent text),
`markdown_format` (toolbar commands, enter-continuation),
`etag` (strong/weak/sha256), `conflict_namer` (Synology-style fork names),
`search_query` (FTS-safe sanitize), `trash` (7-day ceiling math),
`labels` (normalize/aggregate/rename). Each has translated cases from the
matching `*Test.kt` in `ref/xx-note/core` and `app/src/test`.
