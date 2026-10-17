"""App CSS on top of xx_ui: the Android xx-note look in GTK.

Mirrors the Compose app and its design mock: hairline note cards in a
two-column grid (Space Mono titles, dimmed Markdown previews, small caps
label chips, ``3 / 7`` checklist progress), Keep pastels or D12 surface
tones as card fills, a capture bar instead of a FAB, a borderless Space
Mono title in the editor and a 44 px format toolbar with hairline
separators. Rendered from the active theme on every change, so all
presets repaint live and sizes follow the shell's text scale.
"""

from __future__ import annotations

from string import Template

from xx_note import note_colors as _colors

CSS = Template("""
.xx-note-root button { font-weight: 400; }

/* -- cards ------------------------------------------------------------- */
button.xx-note-card {
  background: none; background-color: transparent;
  border: 1px solid ${border}; border-radius: 12px;
  padding: 12px; min-height: 0; }
button.xx-note-card:active { background-color: ${surface}; }
button.xx-note-card.selected {
  border-color: ${fg}; box-shadow: inset 0 0 0 1px ${fg}; }
.xx-note-card-title { font-size: ${t16}px; color: ${fg}; }
.xx-note-card-preview { font-size: ${t13}px; color: alpha(${fg}, 0.8); }
.xx-note-card-progress { font-size: ${t13}px; color: ${muted}; }
.xx-note-chip {
  font-size: ${t10}px; letter-spacing: 0.1em; color: ${muted};
  border: 1px solid ${border}; border-radius: 3px; padding: 1px 5px; }
.xx-note-chips, .xx-note-chips flowboxchild {
  padding: 0; min-height: 0; min-width: 0; background: none; }
${fills}
.xx-section-label.xx-note-first { margin-top: 16px; }

/* -- top: search, label filter --------------------------------------- */
.xx-note-search { padding: 0 20px 8px 20px; }
.xx-note-filter { padding: 0 20px 8px 20px; }
button.xx-note-filter-chip { padding: 0 6px 0 14px; }
button.xx-note-filter-chip image { color: ${bg}; }

/* -- capture bar (Android: no FAB) ------------------------------------ */
.xx-note-capture {
  border-top: 1px solid ${border}; padding: 8px 12px 8px 20px; }

/* -- list rows: archive, trash, labels -------------------------------- */
button.xx-note-action {
  min-height: 44px; padding: 0 8px; font-size: ${t11}px;
  letter-spacing: 0.12em; color: ${fg}; }
.xx-note-meta { font-size: ${t13}px; color: ${muted}; }

/* -- editor ----------------------------------------------------------- */
entry.xx-note-title-entry, .xx-note-title-entry {
  background: none; background-color: transparent; border: none;
  box-shadow: none; outline: none; padding: 0; min-height: 0;
  font-size: ${t26}px; color: ${fg}; caret-color: ${fg}; }
.xx-note-title-entry placeholder { color: ${muted}; }
.xx-note-editor-title { padding: 8px 20px 12px 20px;
  border-bottom: 1px solid ${border}; }
textview.xx-note-body, textview.xx-note-body text {
  background-color: ${bg}; color: ${fg}; font-size: ${t16}px;
  caret-color: ${fg}; }
.xx-note-read { font-size: ${t16}px; color: ${fg}; }
.xx-note-read-heading { font-size: ${t20}px; color: ${fg}; }
.xx-note-read-done { color: ${muted}; }
.xx-note-toolbar { border-top: 1px solid ${border}; }
.xx-note-select { padding: 0 4px; }
.xx-note-select button.xx-btn-icon { padding: 0; min-width: 44px; }
button.xx-note-tool {
  min-width: 44px; min-height: 48px; padding: 0; border-radius: 12px;
  color: ${fg}; font-size: ${t15}px; }
button.xx-note-tool:active { background-color: ${surface}; }
.xx-note-tool-sep { min-width: 1px; min-height: 20px;
  background-color: ${border}; margin: 0 6px; }
.xx-note-hl { background-color: alpha(${fg}, 0.22); border-radius: 2px;
  padding: 0 2px; }
button.xx-btn-icon.xx-note-on { background-color: ${fg}; color: ${bg};
  border-radius: 12px; }
button.xx-btn-icon.xx-note-on image { color: ${bg}; }

/* -- settings / sync -------------------------------------------------- */
.xx-note-field { margin-top: 20px; }
.xx-note-field .xx-caption { margin-bottom: 8px; }
.xx-note-status { font-size: ${t13}px; color: ${muted}; margin-top: 12px; }
.xx-note-seg button { padding: 0 6px; }
.xx-note-actions { margin-top: 20px; }
""")

_TONE_MIX = {
    _colors.TONE_RAISED: 0.05,
    _colors.TONE_GRAPHITE: 0.09,
    _colors.TONE_SLATE: 0.14,
}


def _fills(bg: str, fg: str, muted: str, dark: bool) -> str:
    rules: list[str] = []
    table = _colors.KEEP_DARK if dark else _colors.KEEP_LIGHT
    for key, hexval in sorted(table.items()):
        rules.append(
            f"button.xx-note-card.xx-note-keep-{key} {{ "
            f"background-color: {hexval}; border-color: {hexval}; }}"
        )
    for tone, frac in _TONE_MIX.items():
        mix = f"mix({bg}, {fg}, {frac})"
        rules.append(
            f"button.xx-note-card.xx-note-tone-{tone} {{ "
            f"background-color: {mix}; border-color: {mix}; }}"
        )
    rules.append(
        f"button.xx-note-card.xx-note-tone-{_colors.TONE_HAIR_LEFT} {{ "
        f"border-left: 3px solid {muted}; }}"
    )
    rules.append(
        f"button.xx-note-card.xx-note-tone-{_colors.TONE_HAIR_FULL} {{ "
        f"border-color: {muted}; }}"
    )
    return "\n".join(rules)


def render(theme) -> str:  # type: ignore[no-untyped-def]
    scale = float(getattr(theme, "scale", 1.0) or 1.0)

    def px(base: float) -> str:
        return str(round(base * scale))

    return CSS.substitute(
        bg=theme.background, fg=theme.foreground, muted=theme.muted,
        surface=theme.surface, border=theme.border,
        fills=_fills(theme.background, theme.foreground, theme.muted,
                     bool(theme.dark)),
        t10=px(10), t11=px(11), t13=px(13), t15=px(15), t16=px(16),
        t20=px(20), t26=px(26),
    )


_provider = None


def install(theme) -> None:  # type: ignore[no-untyped-def]
    """(Re)load the app stylesheet for ``theme`` on the default display."""
    global _provider
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )
    _provider.load_from_data(render(theme).encode("utf-8"))
