# XX-WM — Design Spec

This document is the UI contract for XX-WM: every surface, theme, and gesture the shell provides. Anything the shell draws must conform to it.

## Design language — PiercingXX

Text-first. No icon grids, no app icons on home. Low visual noise, local-only customization, monochrome surfaces, large type, everything reachable by search or gesture.

## Home screen

- Up to **8 home slots**; each slot holds an app, a pinned shortcut, or a **folder**.
- Default layout (seeded on first boot, never overwrites user config):
  - **Notes** → daily note (`piercing-note` via piercing-dots, else first notes app)
  - **Audio** (folder) → Audiobook, Music
  - **Comms** (folder) → Phone, Text, Email, Chat, SoftPhone. Phone and Text bind to the device's real telephony apps when installed (GNOME Calls / Chatty — the working stack on FuriOS and postmarketOS); the shell's built-in dialer is only the fallback, so the two always correlate with whatever stack actually places calls and texts on that device.
  - **Calendar**
  - **Tools** (folder) → browser (labeled with its own name), Calculator, Camera, Photos
  - Members that don't resolve to an installed app are skipped; empty folders are not created; slots compact (no gaps).
  - **Every seeded name is a real rename**: seeding writes each label into `app_labels`, so drawer, search, and folders show the same names as home.
  - Seeding also hides stock clutter (shell-redundant apps like GNOME Settings, Software, Calls, phosh-mobile-settings) — hidden apps stay reachable via search.
- **Screen-fraction anchoring**: the widget block is centered on the **1/4 line** of the screen; the slot list is centered on the **2/3 line**.
- Widgets above the slots, in default order: **time, date, weather, battery** — individually toggleable, manually orderable, each with a configurable tap action (open default app / refresh weather / open chosen app). Weather is **on** by default. Date renders `Mon, Jul 20`.
- Alignment configurable (left/center/right); default **centered**.
- Long-press on home → **edit mode** (slot editing), not a wallpaper picker: per-slot ✕ / ↑ / ↓ / rename, "Add app" (drawer pick mode) and "New folder" while under 8 slots, plus Settings and Done entries. Widgets stay visible.

## App drawer

- **Bottom sheet, ~85% of screen height** — the top ~15% stays free so the drawer reads as a sheet, not a full-screen takeover. **Search lives at the bottom** where the thumb is. The keyboard opens only when the search field is tapped — never automatically.
- **Search results anchor at the bottom**, directly above the search field (and above the on-screen keyboard when open), within thumb reach: a short result set sits at the bottom of the list; a set that overflows the view reads from the top. The drawer rises above the keyboard, never behind it.
- Drawer rows use a **smaller type size** than home slots.
- Search can **auto-launch the single result**; `!query` falls back to web search.
- Sort: a single toggle cycling **A–Z ↔ install date**, plus a default sort setting (pinned-first, plain A–Z, or install date) in Settings → Launcher. No size or usage sorts.
- A–Z character jump strip on the right edge.
- Row order: folders first, then pinned apps (in pin order), then the rest per the active sort, then a synthetic **Launcher Settings** entry always last.
- **Folder rows expand inline**: tapping a folder drops its members open directly under the row as an indented drop-down; tapping it again (or a swipe right) collapses it. On expand, the folder row plus its members scroll to sit vertically centered. The expansion survives list refreshes and collapses automatically if the folder disappears.
- **Search-only items**: hidden apps, apps occupying home slots, and folder members are absent from the browse list but all surface in search results.
- Other launchers/shells never appear in the drawer at all, not even in search.
- Per-app rename labels (`app_labels`); the renamed label shows everywhere and search matches both the original and renamed name.
- Long-press a row → one plain action list: App info, Rename, Add to folder, Hide/Show, **Disable for…**, Pin/Unpin, Move up/down. (No uninstall — package ops are distro-specific.)
- Long-press an **expanded folder member** → the shared folder-member menu (see Folders) — identical on home and in the drawer.

### Disable for… (notification muting)

Mute an app's notifications for **1, 2, 4, or 8 hours**. While muted, the app's notifications are discarded by the notification daemon — no banner, no sound, not in the shade. The mute expires silently; no unmute action needed. Stored as `muted_apps: {app_id: until_epoch}`.

## Folders

Create, rename, delete, manage membership, manual reorder. **Folders expand as inline drop-downs** everywhere they appear:

- **On home**: tapping a folder slot drops its members open directly under that slot — same typography as home slots, wrap-width rows following the home alignment, no title, no close chrome. The slot list stays vertically centered and grows/shrinks around the expansion; **the widget block stays visible**. Tapping the folder again or launching a member collapses it; a home re-render (config change, app list refresh) collapses it too. Other gestures act normally — they don't force-dismiss the folder.
- **In the drawer**: members drop open indented under the folder row (see App drawer).
- **Long-press a member** (home or drawer) → one shared action menu: App info, Rename, **Disable for…** (apps only), Move up, Move down, **Remove from folder** (the Linux analog of Android's uninstall row — package ops are distro-specific).
- Expand/collapse uses a quick functional reveal (~120 ms); rows give pressed-state touch feedback. No nested folders.

## Dialogs & menus

Every dialog and action menu the shell draws (long-press menus, rename entries, confirm steps) follows the active theme's colors and the launcher font — no stock-toolkit styling. Text entries in dialogs commit on the keyboard's Enter/Done key, same as the confirm button.

## Gestures

| Gesture | Action |
|---|---|
| Swipe left on home | Launch the Swipe Left App (seeded: **Skippy**, resolved by app name — it installs as a PWA so its id varies; no app → Camera). Enable Swipe Left turns it off. |
| Swipe right on home | Launch the Swipe Right App (seeded: Camera; no app → Dialer). Enable Swipe Right turns it off. |
| Swipe down on home | Swipe Down Action: Expand notifications, Web search, or Disabled |
| Double-tap home | Lock the screen when Double Tap to Lock is on (off by default) |
| Double-tap | Lock screen (kept on Linux — Android dropped it only because it needed the accessibility service) |
| Short swipe up from bottom | Home (system-level, via lisgd). Works over any screen, including recents. |
| Long swipe up from bottom | App switcher / recents, full-screen (system-level, via lisgd). Swipe down, tap the dim, or back resumes the current app — home is the short swipe. |
| Swipe up on home (mid-display) | App drawer (in-shell). The bottom bezel belongs to lisgd. |
| Edge swipe left/right | Back (system-level, via lisgd) |

Swipe left/right accept any app via `launch:<app_id>` bindings, alongside the fixed actions.

System-level gestures (swipe up, edge swipes) belong to lisgd + IPC because they must work over any app. On-surface gestures (left/right/down/double-tap on home) are GTK gesture recognizers handled in-process by the launcher.

## Themes

Eight presets + custom solid colors. Backgrounds are **solid colors only** — never wallpaper images.

| Preset | Mode | Background |
|---|---|---|
| AMOLED (default) | dark | `#000000` |
| Graphite | dark | `#111827` |
| Forest | dark | `#10261B` |
| Ocean | dark | `#0F1C2E` |
| Paper | light | `#F3EEE2` |
| Mist | light | `#E6EDF5` |
| Aura | dark | `#0d0b14` |
| Burgundy | dark | `#2A1018` |

Light/dark/system mode switch. Text size scaling and per-surface alignment. **Theme changes apply instantly** — no preview/confirm step.

## Fonts

Bundled options: **JetBrains Mono Nerd (default)**, Space Mono, JetBrains Mono, System Light, generic Monospace, plus user-imported custom font. Font applies launcher-wide. The base type scale is deliberately small (home slots ~27pt, clock ~42pt, drawer rows ~13pt) — 20% below the original sizes.

## Backup / restore

Versioned JSON export covering: home slots, folders + membership, pins, app rename labels, widget config + tap actions, theme, hidden apps, gestures, prefs. Excluded: PIN hash, launch counts, active mutes, custom font files. Restore never writes on invalid payload. A phone reflash should restore the launcher in one file.

## System surfaces

XX-WM *is* the system UI, so the shell owns every surface beyond the launcher. These extend the same design language and already exist in `launcher/src/`:

lock screen (6-digit PIN, fingerprint when hardware supports), notification shade + daemon, quick settings tiles, app switcher, call UI + dialer + SMS + contacts, first-boot wizard, power menu, volume/brightness HUD (in-shell), virtual keyboard (squeekboard with the PiercingXX Colemak layouts), display/power management.

### Lock screen

- **Swipe up to unlock.** No PIN set → an upward swipe unlocks directly. PIN set → the swipe reveals the keypad (keypad is not shown until the swipe).
- 6-digit PIN with escalating lockout; fingerprint when hardware supports it.
- **Notifications on the lock screen**: a text list of app name + summary (no bodies, no actions), fed by the shell's notification daemon. Config `lock_screen_notifications`: `summary` (default) / `count` / `off`. Hidden while Do Not Disturb is active. Tapping one prompts unlock, then opens the shade.

### Notification shade & quick settings

- **Header**: date + time on the left; tapping it expands an inline text-first month calendar (no events — just the month). A **Settings** entry on the right opens the Settings page and collapses the shade.
- **Tiles**: WiFi, Bluetooth, Data, Airplane always visible; Torch, DnD, Focus, Auto-brightness, Location, Hotspot in the expanded tier. Hardware-gated tiles stay hidden until the device supports them.
- **Brightness and volume sliders** always visible under the tiles (not gated on expand).
- Notification list below: swipe to dismiss, clear all.

### Do Not Disturb

Modeled on the Pixel's DnD. One toggle silences notification sounds and banners; notifications still collect silently in the shade. Exceptions that always get through: alarms, **repeat callers** (same number calling twice within 15 minutes), and **starred contacts**. Optional schedules (days + start/end time). While active, the lock screen shows no notifications. State is config-backed so the notification daemon and call UI can consult it.

### Focus Mode

Modeled on the Pixel's Focus Mode. The user picks a list of distracting apps; while focus is active those apps render dimmed with a "paused" note on home and in the drawer, launching them is blocked, and their notifications are held silently and delivered when focus ends. **Take a break** pauses focus for 5/10/15 minutes, then auto-resumes. Optional schedule, sharing the DnD schedule machinery. Focus and DnD are independent toggles.

### Settings scope

The in-shell Settings page holds system sections — the things a config file can't own: WiFi networks, Bluetooth pairing, cellular/APN, sound devices, battery/power, system updates, backup/restore actions, about — plus one **Home & launcher** page that is the Android launcher's preference screen row for row (Home Screen, Widgets, App Drawer, Gestures — see `docs/launcher-settings-parity.md`). Menus and pickers open as in-window bottom sheets, like the Android launcher's ActionSheet; the system edge swipes live under System › Navigation gestures. It replaces both GNOME Settings and phosh-mobile-settings for everything that applies to this shell. Every *shell* preference lives in `~/.config/xx-wm/` and is edited there, through home edit mode, or through the Launcher section. The shell hot-reloads the config file, so editing it in a terminal is a first-class workflow.

Theme presets are also offered as a dedicated Appearance surface on the Settings page so they can be changed without a keyboard.

The page opens on a **category menu** (Android Settings style): Network & internet, Connected devices, Display & appearance, Home & launcher, Sound, Battery, Apps, Privacy & security, Storage & backup, System — each row with a live one-line summary, each opening a subpage with a back chevron (back returns to the menu, then to wherever Settings was opened from; `settings.open:<section>` deep links land on the subpage scrolled to that section). Every subpage uses one row grammar (`settings_ui.py`): caps caption, rounded card of rows with hairlines, title + muted status subtitle + one trailing control, at most one muted footnote; typed values open a sheet instead of sitting inline. Row titles 18 px, subtitles 14 px, captions 12 px (scaled by text size).

## Non-goals

Icon packs, wallpaper images, widgets from third-party apps, animations beyond functional reveals, desktop multi-window tiling.
