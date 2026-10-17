# xx-calculator — Linux Phone Port Spec

Target: postmarketOS / Mobian / FuriOS phones running phoc (XX-WM or Phosh).
360x720 logical px (min 320 px wide), touch-first. Stack: Python 3.12+,
GTK4 + libadwaita via PyGObject, meson install, stdlib + GI only.

Reference: `ref/xx-calculator/` (Android Kotlin app, read-only; Play
edition behavior per `docs/SPEC.md`). Shell conventions: `common/xx_ui`
(`common/DESIGN.md`), sibling apps xx-auto / xx-vitals.

## 0. What the Android app is

A cleanroom Pixel-Calculator clone: BigDecimal expression engine with an
input grammar (not a text box), live preview, 4x5 touch keypad, expandable
science panel (INV, DEG/RAD, trig, ln/log, √ π ^ ! e ( )), tap/long-press
backspace, 100-entry persisted history (swipe-down sheet, tap inserts
result), launcher theme sync, on-device logs, OSS licenses screen.

## 1. Feature inventory (Android) → Linux implementation

### 1.1 Engine (pure logic — ported WITH JVM tests translated to pytest)

| Android (file) | Behavior | Linux implementation |
|---|---|---|
| `engine/Calculator.kt` | `evaluate(expr)` / `evaluate(expr, mode)`; final round to 15 sig digits; error mapping | `engine.py: Calculator.evaluate` — same signatures/semantics over `decimal.Decimal` |
| `engine/Parser.kt` | Lexer (display glyphs `− × ÷ π √`, E-form lexing, trailing-dot rule) + recursive descent; additive → multiplicative → unary → power (right-assoc) → postfix → primary; D1 shopkeeper percent rewrite | `engine.py` — direct port; no `eval` of user input anywhere |
| `engine/Evaluator.kt` | Exact `+ − ×`, 50-digit `÷`, integer `^`, `!` (≤169, else Overflow), Newton `√`; trig/ln/log via doubles with 12-sig snap floor + quadrantal snap; tan pole rejection on BigDecimal remainders; non-integral `^` at 15-sig | `engine.py` — same contexts over `Decimal` (`ROUND_HALF_EVEN`); float detours guarded (`OverflowError` → DOMAIN) |
| `engine/Format.kt` | `plain` (15 sig, E-form at ≥1e13 / <1e-9, uppercase E, no `+`, zero is `0`) + `grouped` (`,` thousands, E-form untouched) | `engine.py: format_plain / format_grouped` |
| `engine/ExpressionBuilder.kt` | Grammar state machine: operator replace, one dot, leading-zero collapse, `%` after value only, labeled/combined paren rules (§5), constants/functions ignored in value position, `raw`/`seed`/`backspace`/`clear`, `canEvaluate` | `builder.py: ExpressionBuilder` — same method set and glyphs |
| `engine/AngleMode.kt` | DEG (default) / RAD | `engine.py: AngleMode` |
| `ui/Formatting.kt` | `formatNumber` = grouped | Re-export of `format_grouped` |
| `ui/HistoryStore.kt` | Newest-first JSON history, cap 100, corrupt payload → empty, over a KV seam | `history.py: HistoryStore` over the same KV seam; production KV is a JSON file under XDG data (mode 0700 dirs), not SharedPreferences |

### 1.2 UI (Android → Linux)

| Android | Behavior | Linux implementation (`main.py`) |
|---|---|---|
| Display region: expression line (auto-shrink, scroll) + preview/result slot; swipe-down opens history | Live preview on every edit (Success only); `=` commits bright result + seeds builder; errors in result slot, expression stays editable | Same; expression label right-aligned with length-stepped font sizes; swipe-down via `Gtk.GestureSwipe` (guarded) + history button |
| 4x5 keypad `AC () % ÷ / 7 8 9 × / … / 0 . ⌫ =`; `=` the single accented key | Every key routes through `ExpressionBuilder`; `⌫` tap deletes, long-press clears | Same grid of borderless round keys sized from the width (80% of a column, pills when the window is too short); long-press via `Gtk.GestureLongPress`; `=` inverted (foreground fill); digits on `surface`, rest tinted per theme tokens |
| Science expander + pill row `INV DEG/RAD sin cos tan ln log √ π ^ ! e ( )`; landscape starts expanded | INV flips trig/ln/log labels; DEG/RAD toggles unit; inserts ignored in value position | Same row of chips; INV inverts; starts expanded when the window is wider than tall (covers landscape) or `XX_CALC_SCIENCE=1` |
| Post-equals (D8): operator continues, digit/dot/paren/const/function starts fresh | `freshIfPostEquals` on exactly those keys; history tap appends at operator position else seeds | Same split, same tail-char set |
| History sheet: newest first, tap inserts result, overflow → Clear history (confirm), empty text `No history` | `HistorySheet` bottom sheet | History page with the same rules (Space Mono title, hairlines); Clear history only in the overflow menu, no confirm popup (owner rule: direct actions, no popups); an empty history shows nothing |
| Overflow menu: Clear history, Logs, Licenses | Logs viewer (on-device ring buffer), OSS licenses | Clear history + Copy result + Open-source licenses; Logs screen dropped (see §3 — nothing to upload; errors surface inline) |
| Rotation preserves expression/history | `onSaveInstanceState` | In-memory state survives (single window); history persists across launches via the JSON file |
| Copy/paste | Android clipboard via long-press/selection | `Copy result` menu item + Ctrl+C; Ctrl+V pastes sanitized display-form text; hardware keyboard drives every key |

### 1.3 Theme

Android repaints from the XX-Launcher `THEME_CHANGED` broadcast. On Linux
there is no broadcast: the app renders through the shared `common/xx_ui`
design system (`xx_ui.apply(window)` at startup, `xx_ui.on_change` live
repaint), so every preset (amoled, graphite, forest, ocean, paper, mist,
aura, burgundy, custom) works live. No per-preset palette code: key fills
use theme tokens (`surface`/`surface_alt`), `=` inverts, errors use the
shared danger token. History heading uses the brand display face
(`xx-display-face`); everything else the theme font.

## 2. Linux integration

- App id `io.piercingxx.XXCalculator`, package `xx_calculator`, launcher
  `xx-calculator`, desktop `Name=xx-calculator` / `GenericName=Calculator`,
  metainfo, hicolor SVG icon, `DBusActivatable=false` (like xx-auto: no
  session service needed for a stateless calculator).
- `scripts/check-all.sh` PROJECTS gains `xx-calculator`;
  `xx-wm/scripts/install.sh` XX_APPS gains `xx-calculator` (no new distro
  packages: stdlib + GTK4/libadwaita already in the base deps);
  top-level README app list gains a row; the xx-wm default-layout seeder
  tries `io.piercingxx.XXCalculator` first for the Calculator slot.

## 3. Out of scope and why

- **Home-server traffic, home-server login, log upload**: the task drops them.
  The port is fully offline (a static guard test bans network imports).
  The Logs viewer goes with log upload: there is no log pipeline left to
  view; diagnostics stay as inline error strings.
- **Launcher theme-sync broadcast/receiver** (`ThemeSyncReceiver`,
  `ThemeSyncApplier`, `ThemePreset.paletteFor`, `ThemePaint`): an Android
  IPC mechanism with no Linux counterpart. `xx_ui` already owns live
  suite theming; duplicating the ground-derived key mixing would fork the
  design system.
- **Backup provider** (`SuiteBackupProvider`, `CalculatorBackupProvider`):
  the shell's backup story is file-based; the history JSON file is
  covered by it with no app code.
- **Haptics/ripples**: no GTK4 phone equivalent; keys use normal press states.
- **Exact Material key shapes** (72dp circles, v9.0 edge-to-edge grid):
  adapted to the PiercingXX flat style per `common/DESIGN.md`, which
  governs all sibling apps.
