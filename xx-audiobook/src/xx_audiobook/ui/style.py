"""App-level CSS on top of xx_ui: the Android client's look in GTK.

The Compose app (ui/theme/Type.kt, Brand.kt) sets the rules mirrored
here: square corners everywhere, 1 px hairlines, flat fills, emphasis by
inversion (a foreground block with background text), Space Mono for
screen and book titles, JetBrains Mono for the rest on the Material type
scale (16/14 titles, 15/13/11 body, 13/12/10 labels), a 16 px gutter,
3 px progress lines and a bottom bar of icon + label with a 1 px line on
the active tab. The brand guide keeps weights light: where Compose sets
Bold, this sets 400 (display) or 500 (row titles) instead.

Colors come from xx_ui's named colors (theme_fg/bg, card_bg = surface,
borders, insensitive_fg = muted), so every preset repaints live; sizes
scale with the shell's text size and re-render on every theme change.
"""

from __future__ import annotations

from string import Template

CSS = Template("""
/* -- shape: square, flat, hairlines ------------------------------------- */
.xx-ab button { font-weight: 400; }
.xx-ab button.xx-btn-primary, .xx-ab button.xx-btn-outline,
.xx-ab button.xx-btn-danger, .xx-ab button.xx-btn-text {
  border-radius: 0; min-height: 48px; padding: 0 12px;
  font-size: ${l13}px; letter-spacing: 0.01em; }
.xx-ab button.xx-btn-text { padding: 0 8px; }
.xx-ab button.xx-btn-icon {
  min-width: 48px; min-height: 48px; padding: 0; border-radius: 0; }
.xx-ab button:disabled { opacity: 0.38; filter: none; }
.xx-ab button.xx-btn-outline { border-color: @borders; }
.xx-ab .xx-page-body { padding-left: 16px; padding-right: 16px; }
.xx-ab .xx-chip { border-radius: 0; }
.xx-ab .xx-row, .xx-ab .xx-setting { min-height: 56px; }

/* -- type scale (Type.kt) ------------------------------------------------ */
.xx-ab-screen { font-size: ${screen}px; font-weight: 400;
  color: @theme_fg_color; }
.xx-ab-heading { font-size: ${heading}px; font-weight: 400;
  color: @theme_fg_color; }
.xx-ab-np-title { font-size: ${heading}px; font-weight: 400;
  color: @theme_fg_color; }
.xx-ab-year { font-size: ${t20}px; font-weight: 400;
  color: @theme_fg_color; }
.xx-ab-t16 { font-size: ${t16}px; font-weight: 500;
  color: @theme_fg_color; }
.xx-ab-t14 { font-size: ${t14}px; font-weight: 500;
  color: @theme_fg_color; }
.xx-ab-b15 { font-size: ${b15}px; color: @theme_fg_color; }
.xx-ab-b13 { font-size: ${b13}px; color: @theme_fg_color; }
.xx-ab-b11 { font-size: ${b11}px; color: @theme_fg_color; }
.xx-ab-l13 { font-size: ${l13}px; color: @theme_fg_color; }
.xx-ab-l12 { font-size: ${l12}px; color: @theme_fg_color; }
.xx-ab-l10 { font-size: ${l10}px; letter-spacing: 0.05em;
  color: @theme_fg_color; }
.xx-ab-muted, .xx-ab .xx-ab-muted { color: @insensitive_fg_color; }
.xx-ab .xx-title { font-size: ${t16}px; font-weight: 500; }
.xx-ab .xx-secondary { font-size: ${b13}px; }
.xx-ab .xx-body { font-size: ${b13}px; }
.xx-ab-label { font-size: ${l10}px; letter-spacing: 0.08em;
  color: @insensitive_fg_color; }
.xx-ab-meta { font-size: ${l12}px; color: @insensitive_fg_color; }
.xx-ab-strong { color: @theme_fg_color; }
.xx-ab-num { font-feature-settings: "tnum"; }
.xx-ab-link { text-decoration-line: underline; }
.xx-ab-section { font-size: ${t14}px; font-weight: 500;
  color: @insensitive_fg_color; margin-top: 16px; margin-bottom: 4px; }
.xx-ab-section.first { margin-top: 4px; }
.xx-ab-caps { font-size: ${l12}px; letter-spacing: 0.06em;
  color: @insensitive_fg_color; margin-top: 16px; margin-bottom: 4px; }
.xx-ab-error { font-size: ${b13}px; color: @error_color; }

/* -- chrome: screen title, top bar, bottom bar --------------------------- */
.xx-ab-top { min-height: 56px; padding: 4px 4px 4px 16px; }
.xx-ab-bar { min-height: 56px; padding: 0 16px 0 0;
  background-color: @theme_bg_color; border-bottom: 1px solid @borders; }
.xx-ab-bar .xx-ab-t16 { margin-left: 4px; }
.xx-ab-tabs { background-color: @theme_bg_color;
  border-top: 1px solid @borders; }
button.xx-ab-tab { min-height: ${tab_h}px; padding: 10px 0 8px 0;
  border-radius: 0; border-top: 1px solid transparent;
  margin-top: -1px; color: @insensitive_fg_color; }
button.xx-ab-tab label { font-size: ${l10}px; letter-spacing: 0;
  color: @insensitive_fg_color; }
button.xx-ab-tab image { color: @insensitive_fg_color; }
button.xx-ab-tab.active { border-top-color: @theme_fg_color; }
button.xx-ab-tab.active label, button.xx-ab-tab.active image {
  color: @theme_fg_color; }
.xx-ab-mini { background-color: @theme_bg_color;
  border-top: 1px solid @borders; }
button.xx-ab-mini-open { padding: 0; border-radius: 0; }

/* -- covers and marks ----------------------------------------------------- */
.xx-ab-cover { background-color: @insensitive_bg_color;
  border: 1px solid @borders; border-radius: 0; }
.xx-ab-cover picture { border-radius: 0; }
.xx-ab-cover image { color: @insensitive_fg_color; }
.xx-ab-tag { border: 1px solid @borders; border-radius: 0;
  padding: 1px 4px; font-size: ${l10}px;
  color: @insensitive_fg_color; }
.xx-ab-tag image { color: @insensitive_fg_color; }
.xx-ab-badge { border: 1px solid @borders; border-radius: 0;
  padding: 2px 6px; font-size: ${l10}px; letter-spacing: 0.05em;
  color: @insensitive_fg_color; }
.xx-ab-glyph { color: @insensitive_fg_color; }
.xx-ab-glyph.on { color: @theme_fg_color; }
.xx-ab-dot { font-size: ${l12}px; color: @insensitive_fg_color; }
.xx-ab-dot.on { color: @theme_fg_color; }

progressbar.xx-ab-line trough, progressbar.xx-ab-line progress {
  min-height: 3px; min-width: 0; border-radius: 0; border: none; }
progressbar.xx-ab-line { min-width: 0; }
progressbar.xx-ab-line trough {
  background-color: alpha(@theme_fg_color, 0.25); }
progressbar.xx-ab-line progress { background-color: @theme_fg_color; }

/* -- rows ----------------------------------------------------------------- */
button.xx-ab-row { padding: 8px 0; border-radius: 0; min-height: 56px;
  border-bottom: 1px solid @borders; }
button.xx-ab-row.last { border-bottom: none; }
button.xx-ab-row:active, button.xx-ab-tile:active, button.xx-ab-flat:active,
button.xx-ab-chapter:active, button.xx-ab-sheetbtn:active,
button.xx-ab-shelf-head:active {
  background-color: alpha(@theme_fg_color, 0.06); }
button.xx-ab-tile { padding: 0; border-radius: 0; }
button.xx-ab-flat { padding: 0; border-radius: 0; min-height: 48px; }
.xx-ab-shelf-head { min-height: 48px; padding: 0; border-radius: 0; }
.xx-ab-shelf-head image { color: @insensitive_fg_color; }
.xx-ab-hair { border-bottom: 1px solid @borders; }

/* -- chips (FilterChip with brandChipColors) ------------------------------ */
.xx-ab button.xx-ab-chip { min-height: 32px; padding: 0 12px;
  border: 1px solid @borders; border-radius: 0; letter-spacing: 0;
  font-size: ${l13}px; color: @theme_fg_color;
  background-color: transparent; }
.xx-ab button.xx-ab-chip.selected { background-color: @theme_fg_color;
  color: @theme_bg_color; border-color: @theme_fg_color; }
.xx-ab button.xx-ab-chip.selected label { color: @theme_bg_color; }
.xx-ab-chiprow { margin-top: 2px; margin-bottom: 2px; }

/* -- Now Playing ---------------------------------------------------------- */
button.xx-ab-play { min-width: 72px; min-height: 72px; padding: 0;
  border-radius: 0; }
button.xx-ab-play:active, button.xx-ab-key:active {
  background-color: alpha(@theme_fg_color, 0.08); }
button.xx-ab-key { min-width: 48px; min-height: 48px; padding: 0;
  border-radius: 0; }
button.xx-ab-sheetbtn { min-height: 56px; padding: 4px 0;
  border-radius: 0; }
button.xx-ab-sheetbtn label { font-size: ${l10}px; }

.xx-ab-chapter { min-height: 48px; padding: 4px 0 4px 8px;
  border-radius: 0; border-left: 2px solid transparent; }
.xx-ab-chapter.now { border-left-color: @theme_fg_color; }

/* -- sheets and dialogs --------------------------------------------------- */
.xx-ab-sheet { padding: 8px 16px 24px 16px; }
dialog.xx-ab-dialog sheet, dialog.xx-ab-dialog floating-sheet {
  background-color: @card_bg_color; border-radius: 0; }

/* -- forms ---------------------------------------------------------------- */
.xx-ab .xx-entry { border-radius: 0; border: 1px solid @borders;
  background-color: transparent; padding: 12px 14px;
  font-size: ${b15}px; }
.xx-ab .xx-entry:focus { border-color: @theme_fg_color; }
.xx-ab .xx-searchbar { padding: 0 12px; margin: 0 16px;
  border: 1px solid @borders; min-height: 52px; }
.xx-ab .xx-searchbar image { color: @insensitive_fg_color; }
.xx-ab .xx-searchbar entry { font-size: ${b15}px; }
.xx-ab-field { font-size: ${l12}px; color: @insensitive_fg_color;
  margin-top: 4px; margin-bottom: 4px; }
.xx-ab check { border-radius: 0; }
scrolledwindow.xx-ab-text { border: 1px solid @borders;
  border-radius: 0; background-color: transparent; }
scrolledwindow.xx-ab-text:focus-within { border-color: @theme_fg_color; }
scrolledwindow.xx-ab-text textview, scrolledwindow.xx-ab-text text {
  background-color: transparent; color: @theme_fg_color;
  font-size: ${b15}px; }

/* -- panels (Reviews, Zones, Stats) -------------------------------------- */
.xx-ab-panel { border: 1px solid @borders; border-radius: 0;
  padding: 12px; }
.xx-ab-panel.live { border-color: @theme_fg_color; }
button.xx-ab-star { min-width: 48px; min-height: 48px; padding: 0;
  border-radius: 0; }
button.xx-ab-star image { color: @insensitive_fg_color; }
button.xx-ab-star.on image { color: @theme_fg_color; }
.xx-ab-stars { font-size: ${l13}px; color: @theme_fg_color; }
.xx-ab-review { padding: 6px 0; border-top: 1px solid @borders; }
.xx-ab-session { padding: 6px 0; border-bottom: 1px solid @borders; }
.xx-ab-chart { color: @theme_fg_color; }
.xx-ab-tile-box { border: 1px solid @borders; padding: 10px; }
.xx-ab-noaccess, .xx-ab-signin { padding: 24px; }
.xx-ab-checking { background-color: @theme_bg_color; }
""")

_BASE = {"screen": 24, "heading": 22, "t20": 20, "t16": 16, "t14": 14,
         "b15": 15, "b13": 13, "b11": 11, "l13": 13, "l12": 12, "l10": 10,
         "tab_h": 64}

_provider = None
_icons = False
scale = 1.0
ground = "#000000"


def large_text() -> bool:
    """Text size 1.5x and up: pages stack what sits side by side, and
    the bottom bar drops its labels (LABEL_MAX_FONT_SCALE on Android)."""
    return scale >= 1.5


def render(scale: float = 1.0) -> str:
    values = {k: str(round(v * scale)) for k, v in _BASE.items()}
    # The bar is 64 px with labels; icon-only at large text, 56 px.
    values["tab_h"] = "56" if scale >= 1.5 else "64"
    return CSS.substitute(values)


def install_icons(display) -> None:
    """The app's own symbolic glyphs (book, tabs, cast) in ui/icons."""
    global _icons
    if _icons or display is None:
        return
    from pathlib import Path

    from gi.repository import Gtk

    Gtk.IconTheme.get_for_display(display).add_search_path(
        str(Path(__file__).parent / "icons"))
    _icons = True


def install() -> None:
    """Install once, then follow every live theme/text-size change."""
    global _provider, scale
    import xx_ui
    from gi.repository import Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return
    install_icons(display)
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        xx_ui.on_change(_restyle)
    _restyle(xx_ui.load())


def _restyle(theme) -> None:
    global scale, ground
    scale = theme.scale
    ground = getattr(theme, "background", ground) or ground
    _provider.load_from_data(render(theme.scale).encode("utf-8"))
