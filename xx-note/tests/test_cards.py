"""Card surfaces and previews (pure ports of the Android grid card)."""

from xx_note import note_colors as colors
from xx_note import preview


def test_keep_keys_and_aliases():
    assert colors.keep_color_key(None) is None
    assert colors.keep_color_key(" White ") is None
    assert colors.keep_color_key("default") is None
    assert colors.keep_color_key("Banana") == "banana"
    assert colors.keep_color_key("yellow") == "banana"
    assert colors.keep_color_key("brown") == "clay"
    assert colors.keep_color_key("chartreuse") is None


def test_tones_map_twelve_names_onto_six():
    assert colors.tone_for("white") == colors.TONE_INK
    assert colors.tone_for("banana") == colors.TONE_RAISED
    assert colors.tone_for("tomato") == colors.TONE_GRAPHITE
    assert colors.tone_for("sage") == colors.TONE_SLATE
    assert colors.tone_for("peacock") == colors.TONE_HAIR_LEFT
    assert colors.tone_for("grape") == colors.TONE_HAIR_FULL
    assert colors.tone_for("teal") == colors.TONE_HAIR_LEFT
    assert colors.tone_for("") == colors.TONE_INK


def test_card_class_per_mode():
    assert colors.card_class("banana", "keep") == "xx-note-keep-banana"
    assert colors.card_class("banana", "tones") == "xx-note-tone-raised"
    assert colors.card_class(None, "keep") is None
    assert colors.card_class("white", "tones") is None
    # Every Keep key has a dark and a light swatch.
    assert set(colors.KEEP_DARK) == set(colors.KEEP_LIGHT)


def test_preview_dims_markers_and_strikes_done_tasks():
    body = "# Lisbon\n\n- [ ] oat milk\n- [x] bin bags\n**bold** & <x>\n"
    markup = preview.preview_markup(body)
    lines = markup.split("\n")
    assert lines[0] == '<span alpha="45%"># </span>Lisbon'
    assert lines[1].endswith("oat milk")
    assert 'strikethrough="true">bin bags' in lines[2]
    assert "&amp; &lt;x&gt;" in lines[3]
    assert lines[3].count('<span alpha="45%">**</span>') == 2


def test_preview_skips_line_repeating_title_and_caps_lines():
    body = "Shopping\nmilk\n" + "\n".join(f"line {i}" for i in range(10))
    lines = preview.preview_markup(body, title="Shopping").split("\n")
    assert lines[0] == "milk"
    assert len(lines) == preview.PREVIEW_LINES


def test_checklist_progress():
    assert preview.checklist_progress("- [ ] a\n  - [x] b\n- [X] c\n") == (2, 3)
    assert preview.checklist_progress("plain") == (0, 0)


def test_app_css_renders_for_every_preset():
    from xx_ui import theme

    from xx_note.ui import style

    for key in theme.preset_keys():
        preset = theme.THEME_PRESETS.get(key)
        if preset is None:
            continue
        active = theme.Theme(
            key=key, background=preset.background, surface=preset.surface,
            surface_alt=preset.surface_alt, border=preset.border,
            foreground=preset.foreground, muted=preset.muted,
            accent=preset.accent, font_family="Monospace", scale=1.3,
            dark=key not in ("paper", "mist"),
        )
        css = style.render(active)
        assert "${" not in css
        assert "xx-note-keep-banana" in css
        assert "font-size: 21px" in css  # 16 px title at text scale 1.3
        # Sub-page bars keep flush 48 px targets (five editor icons at
        # 320): the shared icon-button rule, no app-local copy.
        assert ".xx-note-bar button.xx-btn-icon" not in css
        from xx_ui import css as xx_css
        shared = xx_css.render(active)
        rule = shared.split(".xx-btn-icon {", 1)[1].split("}", 1)[0]
        assert "min-width: 48px" in rule and "min-height: 48px" in rule
        assert "padding: 0" in rule


def test_folder_problem_reads_plainly():
    from xx_note.ui.settings_view import folder_problem

    assert folder_problem(FileNotFoundError(2, "No such file or directory")) == (
        "Can't use that folder: no such file or directory.")
    assert folder_problem(OSError()) == "Can't use that folder."


def test_notes_folder_shows_home_as_tilde(monkeypatch, tmp_path):
    from xx_note.ui.settings_view import home_short

    monkeypatch.setenv("HOME", str(tmp_path))
    assert home_short(tmp_path / "Documents" / "Notes") == "~/Documents/Notes"
    assert home_short(tmp_path) == "~"
    assert home_short("/srv/notes") == "/srv/notes"
    assert home_short("~/x") == "~/x"
