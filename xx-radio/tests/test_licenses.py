"""About screen license table: the app plus its engine pieces."""

from xx_radio import licenses


def test_engine_components_and_their_licenses():
    by_name = {entry.name: entry for entry in licenses.entries("xx-radio")}
    assert by_name["ytmusicapi"].spdx == "MIT"
    assert by_name["yt-dlp"].spdx == "Unlicense"
    assert by_name["bgutil-ytdlp-pot-provider"].spdx == "GPL-3.0-only"
    assert "MIT" in by_name["uv"].spdx and "Apache-2.0" in by_name["uv"].spdx
    assert by_name["GStreamer"].spdx.startswith("LGPL")
    for entry in by_name.values():
        assert entry.name and entry.spdx and entry.url


def test_app_itself_comes_first():
    entries = licenses.entries("xx-radio")
    assert entries[0].name == "xx-radio"
    assert entries[0].spdx.startswith("GPL-3.0")
    names = [entry.name for entry in entries]
    assert names.index("yt-dlp") > names.index("GTK 4")
