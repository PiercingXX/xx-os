# Home & launcher settings: Android parity

Settings › Home & launcher is the Android xx-launcher preference screen
(`xx-launcher/app/src/main/res/xml/preferences.xml`, `SettingsActivity.kt`,
`data/SettingsRepository.kt`, `MainActivity.kt` gestures), row for row:
the same four groups in the same order, the same titles and summaries,
the same choices with the same labels, and the same defaults. The page
is `launcher/src/settings_launcher.py`; `tests/test_settings_sheets.py`
pins the contract.

Two presentation rules are shared by every Settings page, not changed
for parity:

- A list preference shows its current entry under the title (Android
  shows the entry only in its dialog). The sheet that opens lists the
  entries in Android's order and checks the current one.
- Every picker, chooser and prompt opens as an in-window bottom sheet
  (`launcher/src/action_sheet.py`), like the Android launcher's
  ActionSheet. A GtkPopover on the shell's layer-shell surface was
  closed by phoc whenever the shell changed keyboard mode, which is
  why Settings menus never opened.

Status: **same** = identical title, choices, default and behaviour.
**same (Linux)** = identical row; the behaviour uses the Linux
equivalent named in the notes.

## Home Screen

| Android (key · default) | Linux (key · default) | Status |
|---|---|---|
| Default Launcher (`default_launcher`) — asks for the HOME role | Default Launcher — "XX-WM is always the home screen", no action | same (Linux): XX-WM is the shell, so it is always home; there is no role to request |
| Home Slot Count (`slot_count` · 4) — None, 1–8 | Home Slot Count (`home_slot_count` · unset = every stored slot, 4 on the seeded layout) — None, 1–8 | same. Empty slots show `· · ·` at 40% and a tap picks an app; slots past the count stay stored; removing a slot shrinks the count, adding one past it grows it, as on Android |
| Text Alignment (`text_alignment` · center) — Left, Center, Right | Text Alignment (`home_alignment` · center) | same |
| Date/Time Mode (`date_time_mode` · date_time) — Off, Date only, Date and time | Date/Time Mode (`date_time_mode` · date_time) | same: Off hides the clock and date widgets, Date only hides the clock |
| Show Status Bar (`status_bar_visible` · off) | Show Status Bar (`status_bar_visible` · off) | same (Linux): a slim time · network · battery bar across the top of the launcher pages (Android shows its system status bar) |
| — | Clock Format (`clock_24h` · System) — System, 12 h, 24 h | Linux only: Android follows the system 12/24 h setting, which Linux phones do not have |

## Widgets

| Android (key · default) | Linux (key · default) | Status |
|---|---|---|
| Widgets — "Enable, reorder, and set tap actions": per widget a checkbox, ↑ ↓ and "Tap…" | Widgets — same summary; a sheet with per widget a switch, ↑ ↓ and its tap action | same. Widget names Clock, Date, Weather, Battery. Tap choices: Default action, Choose App… (Android's picker); Linux adds None, and Refresh for Weather |
| Temperature Unit (`weather_temp_unit` · fahrenheit) — Fahrenheit, Celsius | Temperature Unit (`weather_temp_unit` · fahrenheit) | same |
| — (Android asks for the location permission on tap) | Auto Location (`weather_auto_location` · on), Manual Location (`weather_lat`/`weather_lon`) | Linux only: GeoClue or typed coordinates stand in for the Android permission |

## App Drawer

| Android (key · default) | Linux (key · default) | Status |
|---|---|---|
| Auto-show Keyboard (`auto_show_keyboard` · off) | Auto-show Keyboard (`auto_show_keyboard` · off) | same: opening the drawer focuses search and raises the keyboard |
| Sort Mode (`sort_mode` · default) — Default (pinned first), Alphabetical, Alphabetical (no priority), Install date, APK size | Sort Mode (`drawer_sort` · default), same five | same, with Android's sort rules (`drawer_page.sort_browse_list`). APK size = installed size of the app's Flatpak or owning package (`app_size.py`, looked up off the main loop). Old `az`/`install` values migrate to `alphabetical_no_priority`/`install_date` |
| Hidden Apps — list; a tap shows the app ("Shown"); none → "No hidden apps" | Hidden Apps — same | same |
| — | Search Auto-launch (`search_auto_launch` · off) | Linux only (opens a lone search match) |

## Gestures

| Android (key · default) | Linux (key · default) | Status |
|---|---|---|
| Gestures — "What every swipe, tap, and long-press does" → help dialog | Gestures — same summary → help sheet | same; two help lines say the Linux way: long-press edits home, slots change in edit mode |
| Enable Swipe Left (`swipe_left_enabled` · on) | Enable Swipe Left (`swipe_left_enabled` · on) | same |
| Swipe Left App (`swipe_left_app` · unset → Camera; seeded Skippy) — app picker, Clear when set | Swipe Left App (gestures.json `swipe_left_home` · `none` → Camera; seeded Skippy) — Select App sheet, Clear when set | same |
| Enable Swipe Right (`swipe_right_enabled` · on) | Enable Swipe Right (`swipe_right_enabled` · on) | same |
| Swipe Right App (`swipe_right_app` · unset → Dialer; seeded Camera) | Swipe Right App (gestures.json `swipe_right_home` · `none` → Dialer; seeded Camera) | same |
| Swipe Down Action (`swipe_down_action` · notifications) — Expand notifications, Web search, Disabled | Swipe Down Action (`swipe_down_action` · notifications) | same (Linux): Web search opens drawer search with the `!` web prefix typed (Android opens the system web search). Seeded once from the old `swipe_down_top` slot |
| Double Tap to Lock (`double_tap_lock` · off) | Double Tap to Lock (gestures.json `double_tap_home` · `none`) | same. Older double-tap actions other than lock load as off |
| Home Button Opens Recents (`home_to_recents` · off) | Home Button Opens Recents (`home_to_recents` · off) | same: the home gesture while already on home opens recents |

Fixed Android gestures, no setting on either side: swipe up opens the
drawer (same); long-press the background opens Settings on Android,
while on Linux it opens home edit mode by default, because Linux edits
slots there (Android uses a per-slot long-press menu). That choice is
System › Navigation gestures › Long-press home.

## Android rows that live on other Linux pages

| Android category | Linux page |
|---|---|
| Theme: Theme Preset, Custom Background Color, Appearance Mode, Font Family, Import Font File, Text Size Scale | Display & appearance |
| Backup & Restore: Export Backup, Import Backup, Reset home layout | Storage & backup › Backup & restore (Reset home layout moved there from Home & launcher, with Android's summary) |
| About: Version, Logs | System › About |

## Linux-only gesture rows (moved, not removed)

These were on Home & launcher and are not in the Android launcher,
which leaves navigation to Android itself. They now sit under System ›
Navigation gestures so the launcher page matches Android:

- Long-press home (Edit home · Settings · Nothing)
- Swipe up (short), Swipe up (long), Swipe down (top), Edge swipe —
  the lisgd swipes that work over any app

Removed from Home & launcher because Android has a single row for them:
Double-tap home (any action) → Double Tap to Lock; Swipe left/right on
home (Choose app / Clear) → Enable Swipe Left/Right + Swipe Left/Right
App; Home opens recents → Home Button Opens Recents; per-widget rows
and Rearrange widgets → the Widgets sheet.
