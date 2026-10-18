"""Phone-user details: plain error lines, back, tiles with a reading."""
from xx_weather.main import (REFRESH_ERROR, back_target, detail_tiles,
                             zip_error_text)


def test_zip_errors_are_plain_lines():
    assert zip_error_text("Enter a valid 5-digit ZIP code.") \
        == "Enter a valid 5-digit ZIP code."
    assert zip_error_text("ZIP code not found: 99999") \
        == "No place found for that ZIP code."
    raw = ("HTTP fetch failed for https://api.zippopotam.us/us/80202: "
           "<urlopen error [Errno -3] Temporary failure>")
    assert zip_error_text(raw) == "Couldn't look up that ZIP code."
    assert "http" not in zip_error_text(raw).lower()


def test_refresh_error_is_one_plain_line():
    assert "http" not in REFRESH_ERROR.lower()
    assert REFRESH_ERROR.endswith(".")


def test_back_goes_up_one_level():
    assert back_target("day") == "forecast"
    assert back_target("licenses") == "settings"
    assert back_target("places") == "forecast"
    assert back_target("settings") == "forecast"
    # The forecast is home: back there is the shell's to handle.
    assert back_target("forecast") is None


def test_tiles_without_a_reading_are_dropped():
    rows = [("FEELS LIKE", "78°", ""), ("UV INDEX", "--", ""),
            ("PRESSURE", "--", "Sea level"), ("WIND", "11 mph", "SW")]
    assert [r[0] for r in detail_tiles(rows)] == ["FEELS LIKE", "WIND"]
