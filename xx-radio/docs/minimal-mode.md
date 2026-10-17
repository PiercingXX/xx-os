# Minimal Mode

xx-radio's Minimal Mode, after xx-launcher and TxxT: words instead of
icons, tiles and cards. It is off by default and lives in
**Settings › Playback › Minimal mode**. Flipping it saves the setting
(`minimal_mode` in the window's `config.json`) and rebuilds every
screen at once; no restart.

The vocabulary is `src/xx_music/ui/minimal.py`; each screen asks
`minimal.active()` while it builds and listens with `minimal.listen()`
to rebuild when the setting flips.

## The vocabulary

| Piece | What it is |
|---|---|
| `[ label ]` | `bracket_button`: Minimal Mode's only kind of button. |
| word | `word_button`: a bare word that acts, where brackets would crowd (tabs, top rows, filters, "more…"). |
| Tones | State is carried by weight and opacity of the theme's own foreground, never by fills or a second color: **Strong** = foreground at 100 % and bold (on), **Plain** = 90 %, **Quiet** = 50 % (off), unavailable = 25 %. |
| `Title — Artist` | `title_artist`: a one-line shelf row. Suffixes carry state, as in the launcher drawer: `▸` opens a page, `↓` is on this phone, `…` is still resolving. |
| list row | `list_row`: title (Plain) over a Quiet second line, no art and no icons; the playing row reads `▸ title` in Strong. Long-press opens the menu. |
| shelf | `shelf`: five rows, the rest behind `more…` (and back with `less`). |
| hairline | a 1 px line in the 10 % foreground; the seek bar is the same line with the played part at 90 % and a 3 px dot. |
| sizes | text follows the text size; button words stop at 1.3x so a row of brackets still fits 320 px. Rows of buttons wrap instead of widening the window. |

All theme presets work: the tones are rendered from the live theme's
`foreground` (`alpha(fg, 0.9)` and so on), so paper's dark ink and
amoled's white both follow the same ramp. Nothing is fixed white.

## Behaviours

| # | Behaviour | Status |
|---|---|---|
| 1 | Setting in Settings › Playback, off by default, saved in `config.json`, applied live (every screen rebuilds) and read before the first paint at start | done |
| 2 | No album art anywhere while it is on: screens build no art tile, and the art loader refuses every request (not even the disk cache is read), so no art leaves the network: a bandwidth saver | done |
| 3 | The player service shows no art to the system media controls (MPRIS `artUrl`) while it is on | done |
| 4 | Tabs are words: the open one in caps and Strong, the rest lower case and Quiet, no indicator | done |
| 5 | Home: chips become a scrolling row of words; every shelf (quick picks, Listen again, the engine's shelves, On this phone, charts, moods) is `Title — Artist` rows, five before `more…` | done |
| 6 | Empty states: one Quiet sentence and a bracketed action | done |
| 7 | Search: the field without glyphs (`clear` is a word), filters and recent searches as words, suggestions as plain lines, results as list rows without art or icons, `see all` / `clear` as words | done |
| 8 | Album, Playlist, Artist: no hero art; the title in the display face; names as plain words (no underline); `[ play ] [ shuffle ] [ download ]`, `[ play ] [ radio ]`; song lists without art, `▸ title` on the playing one; Albums, Singles and Fans also like as `Title — Artist` shelves | done |
| 9 | Mood, Moods & genres, Charts: shelves of rows, moods as words, chart songs numbered rows | done |
| 10 | Sub-page headers: `back`, the caption, the trailing action as a word (`more`, `copy`) | done |
| 11 | Now Playing: no cover; top row `close` … `sleep` `lyrics` `queue` as words, Strong while on (`sleep 29:59` / `sleep end` while armed) | done |
| 12 | Now Playing: the title is the stage, Space Mono at 26 px, bold, wrapping; artists as plain words under it (no underline), the album Quiet | done |
| 13 | Now Playing: swipe the title left/right to skip, up for the queue; long-press for the song's menu | done |
| 14 | Now Playing: the hairline seek (tap or drag to scrub, arrow keys step) with Quiet times | done |
| 15 | Now Playing: `[ like ] [ down ]`, then `[ prev ] [ pause ] [ next ]` (pause Strong, larger), then `[ shuffle ] [ repeat ]` / `[ repeat 1 ]`, Strong while on; `from <origin>` as a Quiet line | done |
| 16 | Now Playing problems: the sentence and a bracketed action | done |
| 17 | Lyrics and Up next: the compact head without art; queue rows without art | done |
| 18 | Mini player: no art, no fill; a 1 px progress hairline on top; one line `Title — Artist` and `[ pause ]` / `[ play ]` in Strong | done |
| 19 | Library: section chips as words; the storage meter as one line of figures over a hairline; rows without art; downloads with `[ retry ]` / `[ cancel ]` and a hairline while downloading | done |
| 20 | Settings: switches as `[ on ]` (Strong) / `[ off ]` (Quiet); rows that open end in `▸`; the storage meter as figures and a hairline; Account `[ sign in ]` / `[ sign out ]`; Playback engine states as words (`ok`, `fix`), no dots or blocks | done |
| 21 | Sheets (sleep, storage limit, menus, confirm, welcome): `close` as a word; choices as brackets, the chosen one Strong; menus without art or icons, entries as words | done |
| 22 | Banners: a plain line, no fill or glyph (loud ones Strong), the action in brackets; toasts a plain line over a hairline | done |
| 23 | First-run and sign-in screens: their buttons become brackets | done |

## Not changed

- The Open-source licenses page comes from the shared `xx_ui` library
  and keeps its own look.
- Space Mono is the brand's display face; where it is not installed the
  title falls back to the house mono, at the same size and weight.

## Proof

`tools/snap_all.sh OUTDIR` renders every screen twice, regular and as
`mm-LABEL` in Minimal Mode, in amoled, paper, graphite and aura, plus
320 px and text size 2 fit checks (`sheet-minimal-THEME.png`,
`sheet-minimal-fit.png`), and the live switch from Settings both ways
(`mm-toggle-on`, `mm-toggle-off`). `tests/test_minimal_mode.py` checks
the tones, the row text, the setting's round trip, the art gate, and
runs the real window to prove the live switch rebuilds every screen
with no art request while it is on.
