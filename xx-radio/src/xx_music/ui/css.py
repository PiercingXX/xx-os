"""App CSS for the music screens, rendered from the live theme tokens.

Sits one priority step above xx_ui's stylesheet and re-renders on every
``xx_ui.on_change`` (preset, custom color, text size), so all presets
switch live. Colors are the theme's own: ``fg`` for text and inverted
emphasis, the ``fg`` opacity ramp (brand: 3.5 / 6 / 10 / 22 / 50 %) for
fills and hairlines, ``muted`` for secondary text. No gradients, no glow.
"""

from __future__ import annotations

from string import Template

CSS = Template("""
.xx-mu button, .xx-mu-sheet button { font-weight: 400; }
.xx-mu button.xx-btn-icon, .xx-mu-sheet button.xx-btn-icon {
  min-width: 48px; min-height: 48px; padding: 0; border-radius: 9999px; }
.xx-mu button.xx-btn-icon:active, .xx-mu-sheet button.xx-btn-icon:active {
  background-color: alpha(${fg}, 0.08); }
.xx-mu button.xx-btn-primary, .xx-mu button.xx-btn-outline,
.xx-mu button.xx-btn-danger, .xx-mu-sheet button.xx-btn-primary,
.xx-mu-sheet button.xx-btn-outline, .xx-mu-sheet button.xx-btn-danger {
  min-height: 48px; padding: 0 16px; border-radius: 12px; }
.xx-mu button.xx-btn-outline { border-color: alpha(${fg}, 0.22); }
.xx-mu button:disabled, .xx-mu-sheet button:disabled { opacity: 0.38; }
.xx-mu button.xx-btn-text { min-height: 48px; padding: 0 8px;
  font-size: ${body}px; }
.xx-mu .xx-tabs button.xx-tab { padding: 0; letter-spacing: 0.08em;
  font-size: ${tab}px; }
.xx-mu .xx-chip, .xx-mu-sheet .xx-chip { min-height: 36px; padding: 0 12px; }
.xx-mu .xx-chip:active { background-color: alpha(${fg}, 0.08); }
.xx-mu-chips { margin-top: 4px; margin-bottom: 4px; }
.xx-mu scrolledwindow.xx-mu-hscroll > scrollbar { opacity: 0; }

/* type */
.xx-mu-h1 { font-size: ${h1}px; font-weight: 300; color: ${fg}; }
.xx-mu-h2 { font-size: ${h2}px; font-weight: 300; color: ${fg}; }
.xx-mu-np-title { font-size: ${np}px; font-weight: 300; color: ${fg}; }
.xx-mu-label { font-size: ${label}px; letter-spacing: 0.14em;
  color: ${muted}; }
.xx-mu-tile-title { font-size: ${tile}px; color: ${fg}; }
.xx-mu-tile-sub { font-size: ${small}px; color: ${muted}; }
.xx-mu-meta { font-size: ${meta}px; color: ${muted}; }
.xx-mu-num { font-feature-settings: "tnum"; }
.xx-mu-strong { color: ${fg}; }
.xx-mu-faint { color: alpha(${fg}, 0.38); }
.xx-mu-error { color: ${danger}; }

/* art: monochrome tiles with initials over a short rule */
.xx-mu-art { background-color: alpha(${fg}, 0.045);
  border: 1px solid alpha(${fg}, 0.10); }
.xx-mu-art.r4 { border-radius: 4px; }
.xx-mu-art.r8 { border-radius: 8px; }
.xx-mu-art.r16 { border-radius: 16px; }
.xx-mu-art.round { border-radius: 9999px; }
.xx-mu-art picture { border-radius: inherit; }
.xx-mu-initials { font-weight: 300; letter-spacing: 0.06em;
  color: alpha(${fg}, 0.86); }
.xx-mu-initials.s { font-size: ${ph_s}px; }
.xx-mu-initials.m { font-size: ${ph_m}px; }
.xx-mu-initials.l { font-size: ${ph_l}px; }
.xx-mu-initials.xl { font-size: ${ph_xl}px; }
.xx-mu-rule { background-color: alpha(${fg}, 0.42); min-height: 2px; }

/* tags */
.xx-mu-tag { border: 1px solid alpha(${fg}, 0.22); border-radius: 3px;
  padding: 1px 5px; font-size: ${tag}px; letter-spacing: 0.12em;
  color: ${muted}; }
.xx-mu-tag image { color: ${muted}; }
.xx-mu-tag.solid { background-color: ${fg}; color: ${bg};
  border-color: ${fg}; }
.xx-mu-tag.solid image { color: ${bg}; }

/* rows and tiles */
button.xx-mu-row { padding: 8px 0; border-radius: 0;
  border-bottom: 1px solid alpha(${fg}, 0.10); }
button.xx-mu-row.last { border-bottom: none; }
button.xx-mu-row:active, button.xx-mu-tile:active,
button.xx-mu-plain:active { background-color: alpha(${fg}, 0.06); }
button.xx-mu-row.now .xx-title { color: ${fg}; }
button.xx-mu-tile, button.xx-mu-plain { padding: 0; border-radius: 4px; }
.xx-mu-rownum { font-size: ${meta}px; color: ${muted}; min-width: 24px; }
.xx-mu-now-bar { background-color: ${fg}; min-width: 2px;
  border-radius: 1px; }
.xx-mu-section { margin-top: 28px; margin-bottom: 8px; }
.xx-mu-section.first { margin-top: 12px; }
.xx-mu-section-head { min-height: 32px; }

button.xx-mu-mood { padding: 12px 14px; border-radius: 8px;
  background-color: alpha(${fg}, 0.045);
  border: 1px solid alpha(${fg}, 0.10); }
button.xx-mu-mood:active { background-color: alpha(${fg}, 0.10); }

/* lines: 2 px progress and the sweeping loading line */
progressbar.xx-mu-line trough, progressbar.xx-mu-line progress {
  min-height: 2px; min-width: 0; border-radius: 0; }
progressbar.xx-mu-line { min-width: 0; }
progressbar.xx-mu-line trough { background-color: alpha(${fg}, 0.12); }
progressbar.xx-mu-line progress { background-color: ${fg}; }

/* banners */
.xx-mu-banner { padding: 10px 12px 10px 20px; min-height: 48px;
  border-bottom: 1px solid alpha(${fg}, 0.10); }
.xx-mu-banner.loud { background-color: ${fg}; color: ${bg};
  border-bottom-color: ${fg}; }
.xx-mu-banner.loud label, .xx-mu-banner.loud image { color: ${bg}; }
.xx-mu-banner.calm { background-color: alpha(${fg}, 0.045); }
.xx-mu-banner .xx-mu-banner-text { font-size: ${meta2}px; }
.xx-mu-banner button.xx-mu-banner-act { min-height: 36px; padding: 0 12px;
  border-radius: 3px; border: 1px solid alpha(${fg}, 0.30);
  font-size: ${label}px; letter-spacing: 0.12em; color: ${fg}; }
.xx-mu-banner.loud button.xx-mu-banner-act { border-color: ${bg};
  background-color: ${bg}; color: ${fg}; }
.xx-mu-banner.loud button.xx-mu-banner-act label { color: ${fg}; }
.xx-mu-inline-note { font-size: ${meta2}px; color: ${muted};
  padding: 10px 0; }

/* mini player */
.xx-mu-mini { background-color: ${surface};
  border-top: 1px solid alpha(${fg}, 0.10); }
button.xx-mu-mini-open { padding: 0; border-radius: 0; }
.xx-mu-mini-title { font-size: ${mini_t}px; color: ${fg}; }
.xx-mu-mini-sub { font-size: ${mini_s}px; color: ${muted}; }

/* now playing */
.xx-mu-np { background-color: ${bg}; }
button.xx-mu-play { min-width: 64px; min-height: 64px; padding: 0;
  border-radius: 9999px; background-color: ${fg}; color: ${bg}; }
button.xx-mu-play image { color: ${bg}; }
button.xx-mu-play:active { background-color: alpha(${fg}, 0.82); }
button.xx-mu-key { min-width: 48px; min-height: 48px; padding: 0;
  border-radius: 9999px; color: ${fg}; }
button.xx-mu-key.dim image { color: alpha(${fg}, 0.42); }
button.xx-mu-key:active { background-color: alpha(${fg}, 0.08); }
button.xx-mu-act { min-height: 36px; padding: 0 12px; border-radius: 3px;
  border: 1px solid alpha(${fg}, 0.22); font-size: ${label}px;
  letter-spacing: 0.12em; color: ${muted}; }
button.xx-mu-act image { color: ${muted}; }
button.xx-mu-act.on { background-color: ${fg}; border-color: ${fg};
  color: ${bg}; }
button.xx-mu-act.on label, button.xx-mu-act.on image { color: ${bg}; }
button.xx-mu-link { padding: 0 2px; min-height: 24px; border-radius: 2px;
  color: ${fg}; }
button.xx-mu-link label { text-decoration-line: underline;
  text-decoration-color: alpha(${fg}, 0.30); }
button.xx-mu-thumb { min-width: 48px; min-height: 48px; padding: 0;
  border-radius: 9999px; border: 1px solid alpha(${fg}, 0.18); }
button.xx-mu-thumb.on { background-color: ${fg}; border-color: ${fg}; }
button.xx-mu-thumb.on image { color: ${bg}; }

/* lyrics */
button.xx-mu-lyric { padding: 6px 0; border-radius: 0; }
.xx-mu-lyric-text { font-size: ${body}px; color: alpha(${fg}, 0.42); }
.xx-mu-lyric-text.now { font-size: ${lyric_now}px; color: ${fg}; }
.xx-mu-lyric-text.past { color: alpha(${fg}, 0.62); }

/* sheets */
.xx-mu-sheet { padding: 8px 20px 24px 20px; }
/* The sheet card only: floating-sheet / bottom-sheet span the whole
   window (dimming included), so painting them blacked the app out. */
dialog.xx-mu-dialog sheet { background-color: ${surface}; }
dialog.xx-mu-dialog bottom-sheet > sheet { border-radius: 16px 16px 0 0; }
dialog.xx-mu-dialog floating-sheet > sheet { border-radius: 16px; }
dialog.xx-mu-dialog bottom-sheet > dimming,
dialog.xx-mu-dialog floating-sheet > dimming {
  background-color: rgba(0, 0, 0, 0.55); }
button.xx-mu-menu { min-height: 48px; padding: 0 4px; border-radius: 0; }
button.xx-mu-menu:active { background-color: alpha(${fg}, 0.08); }
button.xx-mu-menu image { color: ${muted}; }
.xx-mu-readout { font-size: ${readout}px; font-weight: 300; color: ${fg}; }

/* settings, steps, code */
.xx-mu-state-dot { min-width: 8px; min-height: 8px; border-radius: 1px;
  background-color: alpha(${fg}, 0.22); }
.xx-mu-state-dot.ok { background-color: ${fg}; }
.xx-mu-state-dot.bad { background-color: ${danger}; }
.xx-mu-state-dot.hollow { background-color: transparent;
  border: 1px solid alpha(${fg}, 0.42); }
.xx-mu-step { min-height: 44px; }
.xx-mu-stepnum { min-width: 28px; min-height: 28px; border-radius: 3px;
  border: 1px solid alpha(${fg}, 0.22); font-size: ${label}px;
  color: ${muted}; }
.xx-mu-stepnum.done { background-color: ${fg}; border-color: ${fg};
  color: ${bg}; }
.xx-mu-stepnum.now { border-color: ${fg}; color: ${fg}; }
.xx-mu-stepnum.bad { border-color: ${danger}; color: ${danger}; }
textview.xx-mu-paste, textview.xx-mu-paste text {
  background-color: ${surface}; color: ${fg}; }
.xx-mu-paste-box { background-color: ${surface}; border-radius: 12px;
  border: 1px solid transparent; padding: 10px 12px; }
.xx-mu-paste-box:focus-within { border-color: ${fg}; }
.xx-mu-code { font-size: ${code}px; font-weight: 300;
  letter-spacing: 0.12em; color: ${fg}; }
.xx-mu-log { font-size: ${small}px; color: alpha(${fg}, 0.78); }
.xx-mu-block { background-color: alpha(${fg}, 0.035);
  border: 1px solid alpha(${fg}, 0.10); border-radius: 12px;
  padding: 14px 16px; }
.xx-mu-meter trough { min-height: 4px; }
progressbar.xx-mu-meter trough, progressbar.xx-mu-meter progress {
  min-height: 4px; border-radius: 0; }
progressbar.xx-mu-meter trough { background-color: alpha(${fg}, 0.12); }
progressbar.xx-mu-meter progress { background-color: ${fg}; }
.xx-mu-firstrun { background-color: ${bg}; }

/* Minimal Mode: the theme foreground at four weights, never a fill */
label.xx-mm-strong, button.xx-mm-btn.xx-mm-strong label {
  color: ${fg}; font-weight: 700; }
label.xx-mm-plain, button.xx-mm-btn.xx-mm-plain label {
  color: alpha(${fg}, 0.9); font-weight: 400; }
label.xx-mm-quiet, button.xx-mm-btn.xx-mm-quiet label {
  color: alpha(${fg}, 0.5); font-weight: 400; }
label.xx-mm-off, button.xx-mm-btn.xx-mm-off label {
  color: alpha(${fg}, 0.25); font-weight: 400; }
.xx-mm-s12 { font-size: ${mm12}px; }
.xx-mm-s13 { font-size: ${mm13}px; }
.xx-mm-s14 { font-size: ${mm14}px; }
.xx-mm-s15 { font-size: ${mm15}px; }
.xx-mm-s17 { font-size: ${mm17}px; }
.xx-mm-b12 { font-size: ${mmb12}px; }
.xx-mm-b13 { font-size: ${mmb13}px; }
.xx-mm-b14 { font-size: ${mmb14}px; }
.xx-mm-b15 { font-size: ${mmb15}px; }
.xx-mm-b17 { font-size: ${mmb17}px; }
.xx-mm-caps { letter-spacing: 0.14em; }
button.xx-mm-btn, .xx-mu button.xx-mm-btn, .xx-mu-sheet button.xx-mm-btn {
  background: none; border: none; box-shadow: none; border-radius: 0;
  min-height: 40px; min-width: 0; padding: 0 4px; }
button.xx-mm-btn.xx-mm-word { padding: 0 8px; }
button.xx-mm-btn.xx-mm-flush { padding-left: 0; }
button.xx-mm-btn.xx-mm-word.xx-mm-tight { padding: 0 6px; }
button.xx-mm-btn.xx-mm-flush-end { padding-right: 0; }
button.xx-mm-btn:active label { opacity: 0.55; }
button.xx-mm-btn:focus-visible { outline: 1px solid alpha(${fg}, 0.5); }
button.xx-mm-row { background: none; border: none; box-shadow: none;
  border-radius: 0; padding: 8px 0; min-height: 0; }
button.xx-mm-row:active label { opacity: 0.55; }
flowbox.xx-mm-flow > flowboxchild { padding: 0; min-height: 0; }
.xx-mm-section { margin-top: 24px; margin-bottom: 4px; min-height: 32px; }
.xx-mm-section.first { margin-top: 8px; }
progressbar.xx-mm-hairline trough, progressbar.xx-mm-hairline progress {
  min-height: 1px; min-width: 0; border-radius: 0; }
progressbar.xx-mm-hairline { min-width: 0; }
progressbar.xx-mm-hairline trough { background-color: alpha(${fg}, 0.10); }
progressbar.xx-mm-hairline progress { background-color: alpha(${fg}, 0.9); }
.xx-mm-np-title { font-size: ${mm_np}px; font-weight: 700; color: alpha(${fg}, 0.9); }
.xx-mm-tabs { border-top: 1px solid alpha(${fg}, 0.10);
  background-color: ${bg}; padding: 2px 8px; }
button.xx-mm-btn.xx-mm-tab { padding: 0 2px; min-height: 44px; }
.xx-mm-mini { background-color: ${bg}; }
.xx-mm-banner { padding: 6px 12px 6px 20px; min-height: 44px;
  border-bottom: 1px solid alpha(${fg}, 0.10); background-color: ${bg}; }
.xx-mm-toast { padding: 12px 20px; background-color: ${bg};
  border-top: 1px solid alpha(${fg}, 0.10); }
.xx-mu-stepnum.xx-mm-stepnum { border: none; background: none;
  color: alpha(${fg}, 0.5); font-size: ${mm15}px; }
.xx-mu-stepnum.xx-mm-stepnum.done, .xx-mu-stepnum.xx-mm-stepnum.now,
.xx-mu-stepnum.xx-mm-stepnum.bad { color: ${fg}; font-weight: 700; }
.xx-mu-mark { color: ${fg}; }
""")

_BASE = {
    "h1": 28, "h2": 22, "np": 24, "label": 11, "tile": 13, "small": 11,
    "meta": 12, "meta2": 13, "ph_s": 14, "ph_m": 24, "ph_l": 40, "ph_xl": 56,
    "tag": 10, "mini_t": 15, "mini_s": 13, "body": 15, "lyric_now": 18,
    "readout": 40, "code": 30,
}

_provider = None
scale = 1.0


def large_text() -> bool:
    """Text size 1.5x and up: rows stack what sits side by side."""
    return scale >= 1.5


def render(theme) -> str:
    """The stylesheet for one ``xx_ui.Theme`` (pure)."""
    values = {key: str(round(px * theme.scale)) for key, px in _BASE.items()}
    # Four caps tabs share 360 px: they grow with the text size only so far.
    values["tab"] = str(round(11 * min(theme.scale, 1.3)))
    # Minimal Mode: text follows the text size; button words stop at
    # 1.3x so a row of brackets still fits 320 px.
    for px in (12, 13, 14, 15, 17):
        values[f"mm{px}"] = str(round(px * theme.scale))
        values[f"mmb{px}"] = str(round(px * min(theme.scale, 1.3)))
    values["mm_np"] = str(round(26 * min(theme.scale, 1.5)))
    values.update(fg=theme.foreground, bg=theme.background,
                  surface=theme.surface, muted=theme.muted,
                  danger="#ff6b6b")
    return CSS.substitute(values)


def install() -> None:
    """Install once, then follow every live theme or text-size change."""
    global _provider
    import xx_ui
    from gi.repository import Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return
    if _provider is None:
        from pathlib import Path

        Gtk.IconTheme.get_for_display(display).add_search_path(
            str(Path(__file__).with_name("icons")))
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        xx_ui.on_change(_restyle)
    _restyle(xx_ui.load())


def _restyle(theme) -> None:
    global scale
    scale = theme.scale
    _provider.load_from_data(render(theme).encode("utf-8"))
