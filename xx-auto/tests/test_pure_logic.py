"""Pure-logic ports of the Android JVM tests (SessionPick, Elapsed,
FavouritePick, CarConnectDecision, SettingsMapper, MapsLaunch,
DriveTiles)."""
from xx_auto.car_decision import Outcome, decide
from xx_auto.drive_tiles import Tile, tile_for, visible
from xx_auto.elapsed import format_ms, row
from xx_auto.favourite_pick import Number, NumberType, primary_number
from xx_auto.maps_resolve import Target, resolve
from xx_auto.session_pick import PlayerState, active
from xx_auto.settings_store import Settings, parse_settings


def test_session_pick_playing_wins():
    states = [PlayerState(False, True), PlayerState(True, True)]
    assert active(states) == 1


def test_session_pick_most_recent_item_wins():
    states = [PlayerState(False, False), PlayerState(False, True)]
    assert active(states) == 1


def test_session_pick_none():
    assert active([PlayerState(False, False)]) is None
    assert active([]) is None


def test_elapsed_format():
    assert format_ms(0) == "0:00"
    assert format_ms(754000) == "12:34"
    assert format_ms(2310000) == "38:30"
    assert format_ms(3723000) == "1:02:03"
    assert format_ms(-5000) == "0:00"


def test_elapsed_row_clamps():
    assert row(754000, 2310000) == "12:34 / 38:30"
    assert row(9999000, 2310000) == "38:30 / 38:30"


def test_primary_number_prefers_mobile():
    home = Number("+1 555 0100", NumberType.HOME)
    mobile = Number("+1 555 0142", NumberType.MOBILE)
    assert primary_number([home, mobile]) is mobile
    assert primary_number([home]) is home
    assert primary_number([]) is None


def test_car_decision_open_close_ignore():
    assert decide("connected", "AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff",
                  True, False) is Outcome.OPEN
    assert decide("disconnected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, True) is Outcome.CLOSE
    assert decide("disconnected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, False) is Outcome.IGNORE
    assert decide("connected", "11:22", "AA:BB:CC:DD:EE:FF",
                  True, False) is Outcome.IGNORE
    assert decide("connected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  False, False) is Outcome.IGNORE
    assert decide("connected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, False, screen_in_front=True) is Outcome.IGNORE
    assert decide("bogus", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, False) is Outcome.IGNORE


def test_settings_defaults_and_round_trip():
    settings = parse_settings({})
    assert settings == Settings()
    assert settings.auto_launch is False
    assert settings.surface_calls is False
    assert settings.always_ink is True
    back = parse_settings(settings.to_dict())
    assert back == settings
    assert "auto_launch_device" not in Settings().to_dict()
    assert parse_settings({"auto_launch": "yes"}).auto_launch is False
    assert parse_settings(None) == Settings()


def test_maps_resolve_order():
    assert resolve(True, False) is Target.DRIVE
    assert resolve(False, True) is Target.LAUNCHER
    assert resolve(False, False) is Target.NOT_INSTALLED


def test_maps_tile_subtitle_names_the_app_it_opens():
    from xx_auto.maps_launch import tile_subtitle

    class Info:
        def get_name(self):
            return "GNOME Maps"

    class Desktop:
        @staticmethod
        def new(desktop_id):
            if desktop_id != "org.gnome.Maps.desktop":
                raise TypeError("constructor returned NULL")  # PyGObject 3.52
            return Info()

    assert tile_subtitle(Target.NOT_INSTALLED, None, Desktop) == "Get xx-maps"
    assert tile_subtitle(Target.LAUNCHER, "org.gnome.Maps.desktop",
                         Desktop) == "GNOME Maps"
    assert tile_subtitle(Target.LAUNCHER, "gone.desktop", Desktop) == "Maps"


def test_drive_tiles_gating():
    assert visible(True, True, False) == {Tile.RADIO, Tile.AUDIOBOOK,
                                          Tile.MAPS}
    assert visible(False, False, False) == set()
    assert Tile.CALLS in visible(True, True, True)
    assert Tile.CALLS not in visible(True, True, False)


def test_drive_tile_for_player():
    assert tile_for("io.piercingxx.Radio") is Tile.RADIO
    assert tile_for("io.piercingxx.Audiobook", "xx-audiobook") is Tile.AUDIOBOOK
    assert tile_for("io.piercingxx.XXRadio", "XX-Radio") is Tile.RADIO
    assert tile_for("io.piercingxx.XXAudiobook",
                    "XX-Audiobook") is Tile.AUDIOBOOK
    assert tile_for("", "Harbor Radio") is Tile.RADIO
    assert tile_for("org.mpris.vlc", "VLC") is None


def test_media_apps_installed_by_desktop_id():
    from xx_auto import media_apps as media_mod

    assert media_mod.RADIO_DESKTOP_ID == "io.piercingxx.XXRadio"
    assert media_mod.AUDIOBOOK_DESKTOP_ID == "io.piercingxx.XXAudiobook"
    assert media_mod.RADIO_MPRIS_BUS == "org.mpris.MediaPlayer2.xx_radio"
    assert media_mod.AUDIOBOOK_MPRIS_BUS == \
        "org.mpris.MediaPlayer2.xx_audiobook"

    class Info:
        def __init__(self, desktop_id):
            self._desktop_id = desktop_id

        def launch(self, *_args):
            return True

    present = {media_mod.RADIO_DESKTOP_FILE}

    class Desktop:
        @staticmethod
        def new(desktop_id):
            if desktop_id in present:
                return Info(desktop_id)
            raise TypeError("constructor returned NULL")  # PyGObject 3.52

    assert media_mod.installed(Desktop) == {"radio": True,
                                            "audiobook": False}
    assert media_mod.is_installed(media_mod.RADIO_DESKTOP_FILE,
                                  Desktop) is True
    assert media_mod.launch_radio(Desktop) is True
    assert media_mod.launch_audiobook(Desktop) is False
    assert media_mod.launch("gone.desktop", Desktop) is False


def test_thumb_glyphs_cover_radio_like_dislike():
    from pathlib import Path

    from xx_auto import media_apps as media_mod

    icons = Path(__file__).parent.parent / "src" / "xx_auto" / "icons"
    for name in (media_mod.THUMB_UP_ICON, media_mod.THUMB_DOWN_ICON):
        assert (icons / f"{name}.svg").is_file()

    radio_main = Path(__file__).parent.parent.parent / "xx-radio" / \
        "src" / "xx_radio" / "main.py"
    if not radio_main.is_file():
        import pytest

        pytest.skip("xx-radio tree is not next to xx-auto")
    spec_text = radio_main.read_text(encoding="utf-8")
    assert media_mod.THUMB_UP_ICON in spec_text
    assert media_mod.THUMB_DOWN_ICON in spec_text
