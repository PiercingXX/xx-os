# Android parity

The Linux client follows the Android one
(`xx-android/xx-audiobook/xx-audiobook-app`, Kotlin + Compose) screen by
screen. Where the two differed, Android won unless the brand guide said
otherwise. This page lists what changed and what can't match, and why.

Proof: `tools/snap_all.sh OUT` renders every screen in amoled, paper,
graphite and aura, plus 320 px and text-scale-2 fit sheets, and fails on
any `snap: WARNING`.

## Look (all screens)

Sources: `ui/theme/Type.kt`, `Brand.kt`, `Theme.kt`, `Color.kt`.

| Android | Linux now | Before |
|---|---|---|
| Square corners on everything (`BrandShapes`, 0 dp) | 0 px radius on buttons, chips, covers, sheets, entries, panels | 2-3 px radii |
| `HairlineProgress`: 3 dp, Signal fill, 25% well | 3 px, foreground fill, 25% foreground well | 2 px, border-colour well |
| `HairlineSlider`: 1 dp well, 3 dp played bar, 14 dp square thumb | Same, drawn in Cairo (Scrubber, zone volume) | 2 px bar, slim playhead, chapter ticks |
| `FilterChip` + `brandChipColors`: hairline, title-case label, inverted when selected | Same (`common.chip`) | Uppercase, letter-spaced, muted labels |
| `SectionTitle`: titleSmall, muted, title case | 14 px, weight 500, muted, title case | 11 px caps, letter-spaced |
| `ScreenTitle`: headlineSmall, Space Mono | 24 px Space Mono | 30 px |
| Row titles titleMedium (16), body 13, labels 12/10 | Same scale (`.xx-ab-t16` … `.xx-ab-l10`) | 16/14/12 mixed |
| `CoverArt`: tile colour, hairline, book glyph | Same; the glyph ships as `ui/icons/xx-ab-book-symbolic.svg` | Initials over a short rule |
| 16 dp gutter | 16 px | 20 px (suite default) |
| Bottom bar: icon + label, 1 dp line on the active tab's top edge, labels drop at font scale 1.5 | Same; tab icons ship in `ui/icons/` | Text only, uppercase |
| `TopBar` on pushed screens: Back, the screen's own title (16), hairline | Same; the Book bar carries the book's title | A small caps kind ("BOOK") |

**Can't match: bold.** Compose sets Space Mono Bold for titles and
JetBrains Mono Bold for row titles. The brand guide says "Weight is
light. Bold is a scalpel, not a highlighter", and the suite rules ban
bold headers, so Linux uses 400 for display titles and 500 for row and
section titles.

**Can't match: the theme picker.** Android picks a preset in Setup and
syncs it from XX-Launcher. On Linux the shell (XX-WM) owns the theme;
Setup names the current one and says it follows the shell.

**Can't match: Material icons.** Adwaita has no book, gauge, cast or
box-speaker glyphs, so the app ships its own filled symbolic SVGs (book,
library, play circle, download, speaker, settings, speed, cast). They
recolour with every preset. Everything else uses Adwaita symbolics.

## Sign in (new: sign-in first)

Sources: `ui/App.kt`, `ui/login/SignInScreen.kt`, `SignInViewModel.kt`.

- The window opens on a neutral Checking frame (the ground and one
  spinner) while the context resolves auth on a worker: hub probe, then
  the stored token. There is no flash of Library.
- When both fail, Sign in covers the whole window: "Audiobook · estate
  account", one muted line, estate address, username, password (show /
  hide), Sign in, and "I signed in to xx-apps — try again". The tabs exist
  only after a sign-in succeeds. A sign-out or a revoked session returns
  here; its reason shows in the error line.
- The password stays in its entry until the login request is built, then
  the entry is cleared. It is never stored or logged.
- Before: a signed-out user landed on Library ("Not signed in. Sign in
  from Setup.") and the form lived in the Setup tab.
- Differs: Android's field labels float inside an `OutlinedTextField`;
  GTK entries have no floating label, so each caption sits above its
  hairline entry. The debug API-base override is Android-only.

## No access

Source: `ui/login/NoAccessScreen.kt`. Headline, the not_granted
sentence, "Signed in as …", Retry (filled) and Sign out (outlined). The
glyph and caps kicker are gone. Behaviour unchanged: never signs out,
wipes or retries by itself.

## Library

Source: `ui/library/LibraryScreen.kt`.

- Title row: ScreenTitle, Collections and list/grid icons.
- Search is a hairline box with the glyph and "Search".
- "Sort" and "Show" rows label their chip strips in title case; the
  chips keep whole labels and scroll sideways.
- Shelf headers are title-case section titles with the expand glyph.
  Continue listening tiles are 120 px, the other shelves 96 px.
- `LibraryRow`: 56 px cover, title (16, two lines), author (13, muted),
  then `★ 4.6 (12)` / Finished / Missing / the Offline tag, then the
  3 px progress line. `BookTile`: cover, progress only while in
  progress, a 13 px title, 10 px author, rating and Offline.
- "More books" is a text button (Android pages on reaching the end).

## Book

Source: `ui/detail/BookScreen.kt`.

- TopBar with the book's title. 120 px cover beside the Space Mono title,
  subtitle, underlined author links, "Read by" and narrator links, series
  links, duration, Missing; the progress line under the header.
- Play / Resume (filled, full width), then Download and Mark finished /
  Mark unfinished side by side (stacked at large text).
- Ratings & reviews, About (13 px body, not muted), Chapters (N) with
  each chapter's start and length ("32 min"), All N chapters,
  Bookmarks.
- Dropped to match: the rating / year line and the percent / time-left
  line under the header (the reviews block and Now Playing carry them).
- Kept on purpose: Download reads "Downloading 42%", "Queued" or
  "Downloaded" and opens Downloads, where cancel and delete live
  (Android cancels and removes in place). Behaviour stays as it was.

## Ratings & reviews

Source: `ui/detail/ReviewsBlock.kt`. "Ratings & reviews" heading, the
summary as `★ 4.4 (7)` at the headline size, Your review in a hairline
square panel (stars, Write a review / Edit your review + Delete), the
gate text with Start listening / Mark finished, "Finished. Rate it?" in a
foreground-bordered panel, and each review as name, `★★★★☆`, date and
text under a hairline.

## Now Playing and the mini-player

Sources: `ui/player/NowPlayingScreen.kt`, `SeekBar.kt`.

- Cover centred (220 px), then left-aligned NOW PLAYING, the Space Mono
  title and author (both open the Book), the chapter, the SeekBar with
  the position and `-remaining` under it, then "x left in book · y left
  in chapter at 1.25×".
- Transport: previous chapter, skip glyph, a 72 px play / pause with a
  56 px glyph (no ring), skip glyph, next chapter.
- Five chromeless sheet buttons, icon over label: Chapters, the speed,
  Sleep (or the time left), Bookmark, Play on…. Before: bordered cells,
  inverted when active.
- Sheets: Speed is "Remembered for this book." plus the steps as chips
  (the big readout and +/- steppers are gone). Sleep shows "Stops in …"
  with +5 min / Off, the minute chips and End of chapter, and a Custom
  minutes field with Set (new, from Android).
- Mini-player: hairline, 3 px progress, 44 px cover, title (14) and
  time (10), skip back and play / pause.
- Not done: Android's two-pane layout when wider than tall. The page
  stays one column (it scrolls) in landscape.

## Downloads

Source: `ui/downloads/DownloadsScreen.kt`. "Storage used" heads the
page; Active, On this phone, Failed or cancelled as section titles
(no counts); each row a 44 px cover, title, one status line and progress
while running, with Cancel / Retry / Delete icons. The Wi-Fi banner and
the storage footer are gone; a queued row's line says why it waits.

## Zones and Play on…

Source: `ui/zones/ZonesScreen.kt`. Title + refresh; "Playing on …" with
the phone or speaker glyph; the active zone in a hairline square panel
(name, location · state, position, plain transport icons, "Volume 35%",
the slider, Bring back to phone); Locations as chips; Players as rows
with the cast glyph. Linux drops Quieter / Louder (the slider is the
one volume control; arrow keys step it), the line under Bring back to
phone and the "Play there?" ask: a tap on a speaker casts. Kept from
Linux: the "Now" tag on the active player.

## Setup

Source: `ui/setup/SetupScreen.kt`. Account (name, admin tag, origin,
how it signed in), Listening stats (outlined, full width), Server status
(admins), Playback with a label over each chip row ("10 s", "1.00×"),
Downloads as one "Download on Wi-Fi only" row with a square check box
plus the storage line, Theme, Support (Share logs, Sign out, both
outlined), About ("XX audiobook" with the XX underlined, version,
license, Open-source licenses). The avatar and list-row chevrons are
gone. Sign in moved out of Setup (see above).

## Series, Author, Narrator, Collections, Collection

Sources: `ui/browse/BrowseScreen.kt`, `ui/collections/CollectionsScreen.kt`.
A TopBar with the name, one muted kind line ("Series · 3 books"),
uppercase list labels (SERIES, BOOKS, COLLECTIONS, YOUR PLAYLISTS),
underlined series links and LibraryRows. Collection rows show the first
book's cover, name, "N books" and the description on one line. The big
display title block is gone.

## Listening stats

Source: `ui/stats/StatsScreen.kt`. TopBar; the year switcher
("‹ 2025 · 2026 · 2027 ›"); hairline tiles (Listened, Finished, Days,
then Streak, Longest); the last 30 days as thin columns; By month as
twelve columns with initials; Top books ("1." + title + time over a
3 px bar) and Top authors; Recent sessions with "More sessions (x of
y)". Before: a hero figure, a two-by-two grid and horizontal month bars.

## Naming

The desktop Name, the metainfo name, the window title, the MPRIS
Identity and the About / licenses name are all `xx-audiobook`, lowercase
per the brand guide.
