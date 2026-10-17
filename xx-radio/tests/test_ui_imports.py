"""UI modules import display-less; user-facing text is plain and final."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

BANNED = ("lane", "milestone", "todo", "coming", "scaffold", "stub")


def test_main_and_shell_import_without_display():
    from xx_music.ui import shell
    from xx_radio import main as main_module

    assert main_module.SPEC.name == "xx-radio"
    assert set(shell.FIRST_RUN_TEXT) == {"home", "search", "library"}


def test_first_run_text_is_plain():
    from xx_music import engine
    from xx_music.appspec import CORE_TAB_TITLES
    from xx_music.ui import shell

    texts = [*shell.FIRST_RUN_TEXT.values(), *CORE_TAB_TITLES.values(),
             *engine.ERROR_TEXT.values(),
             *engine.COMPONENT_STATE_TEXT.values()]
    for text in texts:
        assert text.strip()
        for word in BANNED:
            assert word not in text.lower(), text
