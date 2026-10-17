"""The GUI's player model: service state, restore, local commands."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from xx_audiobook import api as api_module  # noqa: E402
from xx_audiobook import restore as restore_module  # noqa: E402
from xx_audiobook import timeline as timeline_module  # noqa: E402
from xx_audiobook.ui.context import (  # noqa: E402
    AppContext, NOT_SIGNED_IN_LINE, OFFLINE_LINE, error_line)

STATE = {"has_book": True, "item_id": "it-1", "title": "Book",
         "author": "Ann", "status": "Playing", "position_us": 90_000_000,
         "duration_us": 600_000_000, "rate": 1.5,
         "sleep_remaining_ms": 0, "sleep_end_of_chapter": False}


def _model():
    ctx = AppContext(api_module.AudiobookApi())
    player = ctx.player
    player.chapters = [timeline_module.Chapter(0, "One", 0, 60_000),
                       timeline_module.Chapter(1, "Two", 60_000, 600_000)]
    player._detail_for = "it-1"
    return player


def test_service_state_maps_microseconds_and_chapter():
    player = _model()
    player.apply_service(STATE)
    assert (player.position_ms, player.duration_ms) == (90_000, 600_000)
    assert player.playing and player.chapter().title == "Two"
    assert player.chapter_end_ms() == 600_000


def test_tick_extrapolates_at_the_current_speed():
    player = _model()
    player.apply_service(STATE)
    player._tick()
    assert player.position_ms == 91_500


def test_restore_stays_until_the_service_holds_a_book():
    player = _model()
    ui = restore_module.PlayerUi("it-1", "Book", position_ms=5_000,
                                 duration_ms=60_000)
    player.item_id, player.restored = ui.item_id, True
    player.apply_service({"has_book": False})
    assert player.has_book and player.restored
    player.apply_service(STATE)
    assert not player.restored


def test_local_commands_without_a_service():
    player = _model()
    player.apply_service(dict(STATE, status="Paused"))
    player.play_pause()
    assert player.playing
    player.skip(-30)
    assert player.position_ms == 60_000
    player.chapter_step(False)
    assert player.position_ms == 60_000
    player.sleep_minutes(15)
    assert player.sleep_remaining_ms == 15 * 60_000
    player.cancel_sleep()
    assert player.sleep_remaining_ms == 0


def test_error_lines_are_one_muted_sentence():
    assert error_line(api_module.NotSignedIn()) == NOT_SIGNED_IN_LINE
    assert error_line(api_module.Unreachable("x")) == OFFLINE_LINE
    assert error_line(api_module.Http(409, "not_playable", "Damaged.")) \
        == "Damaged."
    assert error_line(None) is None
