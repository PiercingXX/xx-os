# xx-files — Linux Phone Port Spec

Port of the PiercingXX Android app `xx-files` ("File manager" on Google
Play, Kotlin/View, `ref/xx-files/`, brief `todo.md` + contracts
`xx-files.md`) to Linux phones running phoc (XX-WM or Phosh).
Stack: Python 3.12+, GTK4 (+ libadwaita via the `Adw.Application` shell
only, as xx-auto does), meson install, stdlib + GI only.
App id `io.piercingxx.XXFiles`.

Visual target: the Android tree list (text-first rows: kind glyph +
name + size/count/date second line, `tree`-style guide lines in tree
mode, breadcrumb path bar, overflow menu) restyled in the PiercingXX
brand via the shared `common/xx_ui` design system; every theme preset
works live.

## 1. Android → Linux mapping

### F1. Browse engine: `File.listFiles()` → `os.scandir` on a worker

- The listing root on Android is the primary volume
  (`/storage/emulated/0`, labelled **Internal**). On Linux there is no
  single external-storage root: the roots are **Home** plus
  Documents, Downloads, Pictures, Music, and removable media from
  `Gio.VolumeMonitor`. A volumes screen lists them; Home is the start
  root (ports `Volumes`/`VolumeLabels`: primary → fixed label,
  removable → OS label; `isVolumeRoot`; `containing` longest-root
  match; `mounted` never hides an inserted stick).
- `Listing.sort` (directories first, each group case-insensitive) is
  ported verbatim, extended with a sort control (name / size /
  modified; each still directories-first).
- `Hidden.shouldShow` (dotfiles hidden unless the persisted toggle is
  on; trash dir never a normal row) is ported verbatim.
- `DirectoryContents.classify` (null children = blocked, empty =
  empty, exception = useful message) is ported with `BLOCKED_COPY`
  reworded for Linux ("Could not open this folder.").
- `Breadcrumbs.split/segmentPath/navigateTo` (root label + child
  segments; tap sets cwd) is ported verbatim, with the root label
  being the current root's label (F3 rule: never a hardcoded
  "Internal" on every root).
- `BackNav.dest` (volume list → leave; volume root → volume list;
  deeper → parent) is ported verbatim against the Linux roots.
- `Tree.flatten` + `Tree.guide` (expand-in-place, depth cap,
  box-drawing prefix) is ported verbatim as the tree view mode.
- `Row`/`RowIcon`/`Format` (type glyph, name, second line: files
  `12.4 KB · 2026-09-04`, folders `12 items` / `Empty · 1 hidden`) is
  ported verbatim; Linux rows use symbolic icon-theme images instead
  of the Android stroke drawables.
- `FileKind.fromName/of` (extension → IMAGE/VIDEO/AUDIO/DOCUMENT/
  TEXT/ARCHIVE/CODE/PACKAGE/OTHER, images+video have thumbnails) is
  ported verbatim. No in-app thumbnail rendering in v1: rows show the
  kind glyph (Android rendered image/video thumbnails with prefetch;
  Linux defers that — see §3).
- `ShowHiddenStore` (persisted `show_hidden` boolean) is ported onto
  the JSON settings file (`show_hidden`, plus `sort` and `view`).
- Every directory read and every file operation runs on a worker
  thread and posts back with `GLib.idle_add`; the main loop never
  blocks (the Android app did IO on the UI thread in places — Linux
  must not).

### F2. Row actions: intents → Gio + xx-share

- Tap a file → `Gio.AppInfo.launch_default_for_uri` (ports
  `ACTION_VIEW` via FileProvider; folders enter on tap, never
  "view").
- Long-press a row → context actions per `MenuActions.forType`
  (files: Open, Share, Copy, Cut, Rename, Delete, Properties;
  folders: same minus Share) plus multi-select mode with the same
  verbs in the selection bar.
- `Clip.canPaste/uniqueName` (paste enabled only with a staged item
  and a writable cwd; conflicts resolve `foo (2).txt`, never silent
  overwrite) ported verbatim.
- `Rename.validate` (reject empty and `/`) ported verbatim; rename
  onto a taken name confirms overwrite or aborts (P1 rule) instead of
  auto-uniquifying; paste/save-here/restore still uniquify via
  `nextAvailableName`/`resolveTarget`/`wouldOverwrite`.
- `Mime.fromExtension` (extension → MIME, fallback
  `application/octet-stream`) ported verbatim; used for the
  Properties screen and for `Gio.AppInfo` fallback display only.
- `SaveHere.fileName/destination/extensionForMime` ("Save here" into
  cwd with a collision-free name) ported verbatim against the Linux
  cwd; the Android content-resolver byte copy becomes a plain file
  copy. Out of scope in v1: receiving `ACTION_SEND` from other apps
  (there is no Android share-sheet on Linux; see §3).
- Share → `xx-share --send <files>` (its `Send` desktop action) when
  `io.piercingxx.XXShare.desktop` is installed, else an honest
  "xx-share is not installed" message. xx-share owns no stable D-Bus
  send API (its bus `io.piercingxx.XXShare1` is the daemon control
  channel), so the desktop-action launch is the contract.
- `Transfer.copy/move` (copy recursive no-overwrite; move prefers
  rename, falls back to copy+delete) ported onto a cancellable
  worker with byte progress and cancel for big copies
  (`jobs.py`); errors surface via `IoError.message` (useful bit or
  stable fallback, never a crash).
- New folder, Properties (absolute path, size, modified, MIME,
  item count for folders) as dialogs.

### F3. Trash: per-volume `.xx-trash` → freedesktop Trash

- The lane task overrides the Android design here: Linux uses the
  freedesktop Trash (`Gio.File.trash`, `trash:///`), not an
  app-managed `.xx-trash` folder. Delete trashes (never unlinks);
  the Trash view lists `trash:///` with Restore (original path, or a
  `foo (2).ext` suffix when taken — the `Trash.restoreDestination`
  rule, kept) and Delete forever, plus Empty.
- The `.trashinfo` original-path/deleted-date parse is a small pure
  module (`trash_info.py`) with pytest coverage; expiry is the
  desktop's job, so the Android 30-day purge is dropped (see §3).

### F4. Gate, theme, icon

- The Android all-files-access gate (`GateUi`, `MANAGE_EXTERNAL_STORAGE`)
  has no Linux counterpart: home-directory access needs no permission,
  so the app always shows the listing. `GateUi.viewFor` is not ported.
- Theme sync (`ThemeSyncReceiver`/`ThemeStore`/`ThemeApplier`) maps to
  the shared `common/xx_ui` theme: every preset works live, no
  per-app theme code.
- Adaptive launcher icon → hicolor
  `data/icons/io.piercingxx.XXFiles.svg` (stand-in folder mark until
  the owner's xx-files mark lands).
- Registers as the default handler for `inode/directory`
  (`MimeType=inode/directory;` in the desktop file).

## 2. Out of scope and why

- **Home-server / home-server login / log upload** (`AppLog`,
  `LogDumpProvider`, `LogsUi`, suite `TarStream`/`Snapshot` backup
  provider): the lane task explicitly drops anything talking to the
  owner's home server. No network code at all (`verifyNoInternet`
  equivalent: the guards test bans network imports).
- **30-day `.xx-trash` store + purge**: replaced by the freedesktop
  Trash per the lane task; keeping a second private trash would hide
  deletes from the desktop's Trash and confuse restore.
- **Share-in (`ACTION_SEND` receiver)**: no Android intent bus on
  Linux; receiving files from other apps is a future portal/xdp
  item, not v1.
- **In-app image/video thumbnails**: Android prefetched bitmaps;
  Linux v1 shows kind glyphs. Rationale: thumbnail pipelines
  (gdk-pixbuf scale jobs + cache) are the main main-loop-jank risk
  and are not needed for an honest tree.
- **Storage-access permission flows / Storage Scopes**: Linux needs
  neither; showing a gate would be a lie.
- ** NAS / photos library roots**: xx-drive and the photos app own
  those; this app is local folders only (same estate split as
  Android: "The NAS lives in XX-Drive").

## 3. Screens (snap_all states)

volumes, files (list), tree, grid, search, multiselect, properties,
new_folder, rename, delete_confirm, trash, trash_empty_state,
empty_folder, blocked, progress, sort_menu — each in amoled + paper
at 360x720, plus graphite/aura spot checks and a 320-px-wide fit
check of the main listing.
