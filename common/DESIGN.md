# XX design rules (one page)

Phone canvas: 360 px logical width. Gutter 20 px. Spacing scale 4/8/12/16/20/24/32.

## Color

Flat, monochrome per theme. Theme tokens only, never hard-coded colors —
except danger (fill for End call / destructive confirm, text `#ffffff`;
red text for Missed/Rejected, rejected input): `#ff6b6b` on dark themes,
`#a61b1b` on light ones (paper, mist; darker still on a light custom
color), so danger text reads at 4.5:1 or better on the background. Use
the `danger` token (`Theme.danger`), never a literal.

| Token | Use |
|---|---|
| `background` | whole window |
| `surface` | cards, keypad keys, inputs, popovers |
| `surface_alt` | avatar discs, pressed states |
| `border` | 1 px hairlines and outlines |
| `foreground` | primary text, filled/selected controls |
| `muted` | secondary text, captions, inactive tabs |
| `accent` | sparingly: links, active indicator when the theme defines one |

Selection is INVERTED: fill `foreground`, text `background` (ALL chip, FAB,
SPEAKER toggle, call button).

## Type

One family: the theme font (default JetBrainsMono Nerd Font). Regular 400
for nearly everything, medium 500 for row primary text at most. No bold
titles. Caps labels are uppercased in code.

| Role | Size | Notes |
|---|---|---|
| display (page title) | 30 px regular | left-aligned at gutter |
| display-large (detail/in-call name) | 32 px regular | |
| numeric-hero (dial number, timer) | 36 px light | tabular (monospace font) |
| title (row primary, field value) | 16 px | |
| body | 15 px | |
| secondary (row subtitle) | 14 px, `muted` | |
| caption | 11 px caps, `muted` | counts, field labels, section labels, day separators |
| chip label | 12 px caps | tab label 11 px caps |

## Shape

Rows min height 64 px; 1 px `border` hairline between rows (gutter to
gutter), none after the last. Radii: chips/segmented 3 px, outline
buttons and inputs 12 px, cards/dialogs 16 px, FAB/call button 20 px,
avatars circle, outgoing bubbles 4 px.

## Components

`PageHeader` (display title + caption + chromeless text actions, no
HeaderBar) and a compact 56 px back-chevron variant whose caps title
wraps rather than ellipsizes; `ListRow` (40 px avatar or glyph, title +
secondary, muted meta, pressed state); `Avatar` (32/40/64/88); `ChipBar`
(32 px chips, inverted selected, scrollable); `Segmented`; `BottomTabs`
(4 equal tabs, 2 px top indicator on active, top hairline, `background`
fill); `SearchBar` (flat glyph + muted placeholder at list bottoms;
filled `surface` box only where a box is needed); outline/primary/
danger/text/caps buttons (44 px) and icon buttons (48 × 48 px targets),
disabled at 40% opacity; `ButtonPair` (two buttons side by side, stacked
full width when a label would not fit); 56 px inverted `FAB`
bottom-right above tabs; `Field` (caption over title value, muted
placeholder); `SectionLabel`; borderless `Card`; `SettingRow` with
hairlines (title and subtitle wrap, never ellipsize); inverted `Switch`
(never Adwaita blue) with a 48 px tap area around the drawn pill;
one-sentence `EmptyState`; 72×64 `surface` keypad keys; `surface`
dialogs/popovers/menus (48 px menu rows); entries with a 1 px `border`
hairline, `foreground` while focused, danger outline and text while
rejected (`error`); spin buttons at the title size; outgoing inverted
bubbles, incoming plain text, 10 px muted timestamps, caption day
separators between hairlines.

`GlyphTile` (quick settings, 4-up at 360 px, 80 px tall, radius 14): a
20 px symbolic glyph over a caps label and a state line. An 8-char
label tracks tighter; one too wide for its tile (320 px, large text)
scales down to fit, never cut. Off is the foreground ramp (3.5% fill,
10% hairline); on inverts; `working` dashes the border at 40% while a
toggle runs off-thread; `attention` adds a 6 px foreground dot and lifts
the state line to text weight.
`.xx-display-face` sets the brand display face (Space Mono) on clocks
while the UI font is a house mono.

## Never

Libadwaita blue, HeaderBar chrome, button gradients/shadows, default
GtkSwitch, bold headers, system-sans text, 8 px gutters, mixed
alignments, gray slabs behind tabs.
