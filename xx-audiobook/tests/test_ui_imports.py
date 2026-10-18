"""The UI imports display-less and exposes the five-tab shell."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


def test_main_imports_without_display():
    from xx_audiobook import main as main_module

    assert [tab for tab, _label, _text in main_module.TABS] == [
        "library", "playing", "downloads", "zones", "setup"]


def test_every_tab_has_a_built_root_page():
    from xx_audiobook.ui import shell

    for name in ("DownloadsPage", "ZonesPage", "SetupPage", "NoAccessView",
                 "StatsPage"):
        assert hasattr(shell, name)
    assert not hasattr(shell, "PLACEHOLDERS")


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


def test_sheet_css_paints_only_the_card_over_a_scrim():
    # floating-sheet / bottom-sheet span the whole window, dimming
    # included: painting them hid the app behind an opaque slab.
    from xx_audiobook.ui import style

    css = style.render(1.0)
    assert "dialog.xx-ab-dialog floating-sheet {" not in css
    assert "dialog.xx-ab-dialog floating-sheet," not in css
    assert "dialog.xx-ab-dialog sheet {" in css
    assert "bottom-sheet > dimming" in css
    assert "rgba(0, 0, 0, 0.55)" in css


def test_app_icons_ship_with_the_package():
    # The tab bar and the cover glyph use the app's own symbolic SVGs.
    from pathlib import Path

    from xx_audiobook.ui import common, shell

    icons = Path(shell.__file__).parent / "icons"
    names = set(shell.TAB_ICONS.values()) | {common.BOOK_ICON,
                                             "xx-ab-speed-symbolic",
                                             "xx-ab-cast-symbolic"}
    for name in names:
        svg = icons / f"{name}.svg"
        assert svg.is_file(), name
        assert "<path" in svg.read_text()


def test_window_is_named_lowercase():
    from xx_audiobook.ui import setup

    assert setup.APP_NAME == "xx-audiobook"


def test_meson_installs_the_whole_package():
    # Same rule as xx-auto: no hand-kept module list to fall out of date.
    from pathlib import Path
    meson = (Path(__file__).resolve().parent.parent / 'meson.build').read_text()
    assert "install_subdir(\n  join_paths('src', 'xx_audiobook')" in meson
    assert "'src/xx_audiobook/" not in meson
