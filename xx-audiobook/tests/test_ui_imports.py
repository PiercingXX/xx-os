"""The UI imports display-less and exposes the five-tab shell."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


def test_main_imports_without_display():
    from xx_audiobook import main as main_module

    assert [tab for tab, _label, _text in main_module.TABS] == [
        "library", "playing", "downloads", "zones", "setup"]


def test_unbuilt_tabs_show_one_user_facing_line():
    from xx_audiobook import main as main_module

    lines = {tab: text for tab, _label, text in main_module.TABS}
    for tab in ("downloads", "zones", "setup"):
        assert lines[tab].strip().endswith(".")
    for _tab, label, text in main_module.TABS:
        lowered = (label + text).lower()
        for banned in ("lane", "milestone", "todo", "coming"):
            assert banned not in lowered


def test_every_tab_names_itself_for_screen_readers():
    from xx_audiobook.ui import shell

    spoken = [s for _tab, _label, s in shell.TABS]
    assert spoken == ["Library", "Now Playing", "Downloads", "Zones",
                      "Setup"]


def test_app_css_renders_for_every_text_scale():
    from xx_audiobook.ui import style

    for scale in (0.5, 1.0, 2.0):
        css = style.render(scale)
        assert "${" not in css
        assert "@theme_fg_color" in css
    assert "font-size: 44px" in style.render(2.0)


def test_meson_installs_the_whole_package():
    # Same rule as xx-auto: no hand-kept module list to fall out of date.
    from pathlib import Path
    meson = (Path(__file__).resolve().parent.parent / 'meson.build').read_text()
    assert "install_subdir(\n  join_paths('src', 'xx_audiobook')" in meson
    assert "'src/xx_audiobook/" not in meson
