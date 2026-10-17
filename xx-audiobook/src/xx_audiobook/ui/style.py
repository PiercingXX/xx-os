"""App-level CSS on top of xx_ui (M3).

Colors come from xx_ui's named colors (theme_fg/bg, card_bg = surface,
borders, insensitive_fg = muted), so every preset repaints live; sizes
scale with the shell's text size and re-render on every theme change.
Radii stay at 2-3 px (the audiobook's square idiom).
"""

from __future__ import annotations

from string import Template

CSS = Template("""
.xx-ab button, .xx-ab-sheet button { font-weight: 400; }
.xx-ab button.xx-btn-primary, .xx-ab button.xx-btn-outline,
.xx-ab button.xx-btn-danger, .xx-ab-sheet button.xx-btn-primary,
.xx-ab-sheet button.xx-btn-outline, .xx-ab-sheet button.xx-btn-danger {
  border-radius: 3px; min-height: 48px; padding: 0 8px; }
.xx-ab-sheet .xx-chip { padding: 0 6px; }
.xx-ab button.xx-btn-caps, .xx-ab-sheet button.xx-btn-caps {
  min-height: 48px; padding: 0 14px; }
.xx-ab button.xx-btn-icon, .xx-ab-sheet button.xx-btn-icon {
  min-width: 48px; min-height: 48px; padding: 0; }
.xx-ab button:disabled { opacity: 0.38; filter: none; }
.xx-ab .xx-tabs button.xx-tab { padding: 0; letter-spacing: 0.06em;
  font-size: ${tab}px; }
.xx-ab-heading { font-size: ${heading}px; font-weight: 300;
  color: @theme_fg_color; }
.xx-ab-np-title { font-size: ${np_title}px; font-weight: 300;
  color: @theme_fg_color; }
.xx-ab-label { font-size: ${label}px; letter-spacing: 0.12em;
  color: @insensitive_fg_color; }
.xx-ab-meta { font-size: ${meta}px; color: @insensitive_fg_color; }
.xx-ab-strong { color: @theme_fg_color; }
.xx-ab-num { font-feature-settings: "tnum"; }
.xx-ab-link { text-decoration-line: underline;
  text-decoration-color: @borders; }

.xx-ab-cover { background-color: @card_bg_color; border: 1px solid @borders;
  border-radius: 2px; }
.xx-ab-cover picture { border-radius: 2px; }
.xx-ab-initials { font-weight: 300; letter-spacing: 0.06em;
  color: @theme_fg_color; }
.xx-ab-initials.s { font-size: ${ph_s}px; }
.xx-ab-initials.m { font-size: ${ph_m}px; }
.xx-ab-initials.l { font-size: ${ph_l}px; }
.xx-ab-rule { background-color: @insensitive_fg_color; min-height: 2px; }

progressbar.xx-ab-line trough, progressbar.xx-ab-line progress {
  min-height: 2px; min-width: 0; border-radius: 0; }
progressbar.xx-ab-line { min-width: 0; }
progressbar.xx-ab-line trough { background-color: @borders; }
progressbar.xx-ab-line progress { background-color: @theme_fg_color; }

.xx-ab-tag { border: 1px solid @borders; border-radius: 2px;
  padding: 0 4px; font-size: ${tag}px; letter-spacing: 0.12em;
  color: @insensitive_fg_color; }
.xx-ab-tag image { color: @insensitive_fg_color; }

button.xx-ab-row { padding: 10px 0; border-radius: 0;
  border-bottom: 1px solid @borders; }
button.xx-ab-row.last { border-bottom: none; }
button.xx-ab-row:active, button.xx-ab-tile:active,
button.xx-ab-cell:active { background-color: alpha(@theme_fg_color, 0.06); }
button.xx-ab-tile { padding: 0; border-radius: 2px; }
.xx-ab-shelf-head { min-height: 48px; padding: 0; border-radius: 0; }
.xx-ab-shelf-head image { color: @insensitive_fg_color; }

.xx-ab-chiprow .xx-chip { min-height: 32px; padding: 0 12px; }
.xx-ab-chiprow { margin-top: 2px; margin-bottom: 2px; }

button.xx-ab-play { min-width: 72px; min-height: 72px; padding: 0;
  border-radius: 9999px; border: 2px solid @theme_fg_color; }
button.xx-ab-play:active { background-color: alpha(@theme_fg_color, 0.08); }
button.xx-ab-key { min-width: 48px; min-height: 56px; padding: 0;
  border-radius: 9999px; }
button.xx-ab-key:active { background-color: alpha(@theme_fg_color, 0.08); }
button.xx-ab-cell { min-height: 56px; padding: 4px 0; border-radius: 3px;
  border: 1px solid @borders; }
button.xx-ab-cell.on { background-color: @theme_fg_color;
  color: @theme_bg_color; border-color: @theme_fg_color; }
button.xx-ab-cell.on label, button.xx-ab-cell.on image {
  color: @theme_bg_color; }
.xx-ab-cell-label { font-size: ${tag}px; letter-spacing: 0.12em; }

.xx-ab-mini { background-color: @theme_bg_color;
  border-top: 1px solid @borders; }
button.xx-ab-mini-open { padding: 0; border-radius: 0; }

.xx-ab-chapter { min-height: 48px; padding: 6px 0 6px 12px; border-radius: 0;
  border-left: 2px solid transparent; }
.xx-ab-chapter.now { border-left-color: @theme_fg_color; }
.xx-ab-chapter.now .xx-body { color: @theme_fg_color; }
.xx-ab-chapter .xx-body { color: alpha(@theme_fg_color, 0.82); }

.xx-ab-sheet { padding: 8px 20px 24px 20px; }
.xx-ab-readout { font-size: ${readout}px; font-weight: 300;
  color: @theme_fg_color; }
dialog.xx-ab-dialog sheet, dialog.xx-ab-dialog floating-sheet {
  background-color: @card_bg_color; border-radius: 3px; }
""")

_BASE = {"tab": 10, "heading": 22, "np_title": 22, "label": 11, "meta": 12,
         "ph_s": 15, "ph_m": 26, "ph_l": 44, "tag": 10, "readout": 40}

_provider = None
scale = 1.0


def large_text() -> bool:
    """Text size 1.5x and up: pages stack what sits side by side."""
    return scale >= 1.5


def render(scale: float = 1.0) -> str:
    return CSS.substitute({k: str(round(v * scale))
                           for k, v in _BASE.items()})


def install() -> None:
    """Install once, then follow every live theme/text-size change."""
    global _provider, scale
    import xx_ui
    from gi.repository import Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        xx_ui.on_change(_restyle)
    _restyle(xx_ui.load())


def _restyle(theme) -> None:
    global scale
    scale = theme.scale
    _provider.load_from_data(render(theme.scale).encode("utf-8"))
