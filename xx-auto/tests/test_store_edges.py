"""Settings store, backup, applog and vcard edge cases."""

from xx_auto import backup as backup_mod
from xx_auto.applog import AppLog, redact
from xx_auto.contacts_client import is_starred, parse_vcard_phones, tel_uri
from xx_auto.favourite_pick import NumberType
from xx_auto.mpris import (display_name, is_suite, parse_metadata,
                           sort_players, Track)
from xx_auto.settings_store import (load_settings, save_settings, Settings)


def test_settings_atomic_and_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    dest = save_settings(Settings(auto_launch=True,
                                  auto_launch_device="AA:BB"))
    assert dest.is_file()
    assert (dest.stat().st_mode & 0o777) == 0o600
    loaded = load_settings(dest)
    assert loaded.auto_launch is True
    assert loaded.auto_launch_device == "AA:BB"
    dest.write_text("{bogus", encoding="utf-8")
    assert load_settings(dest) == Settings()


def test_backup_round_trip_and_reject():
    settings = Settings(surface_calls=True)
    payload = backup_mod.export_backup(settings)
    assert backup_mod.parse_backup(payload) == settings
    assert backup_mod.parse_backup({"app": "xx-auto"}) is None
    assert backup_mod.parse_backup({"app": "xx-auto", "version": 1,
                                     "settings": "nope"}) is None
    assert backup_mod.parse_backup(None) is None


def test_applog_ring_cap_and_redact():
    log = AppLog(size=4)
    for index in range(6):
        log.i("t", f"line {index}")
    assert len(log) == 4
    assert "line 5" in log.dump()
    assert "line 0" not in log.dump()
    assert "+1 555 0142" not in redact("call +1 555 0142 now")
    assert "Bearer ***" in redact("auth Bearer abc123")
    assert "***@***" in redact("mail ada@example.com")


def test_vcard_parse_and_starred():
    vcard = "\n".join(["BEGIN:VCARD", "FN:Alex Morgan",
                       "TEL;TYPE=CELL:+1 555 0142",
                       "TEL;TYPE=HOME:+1 555 0100",
                       "X-STARRED:1", "END:VCARD"])
    name, numbers = parse_vcard_phones(vcard)
    assert name == "Alex Morgan"
    assert numbers[0].type is NumberType.MOBILE
    assert is_starred(vcard)
    assert not is_starred("BEGIN:VCARD\nFN:Bo\nEND:VCARD")
    assert tel_uri("+1 555 0142") == "tel:+15550142"


def test_mpris_sort_and_metadata():
    suite = Track("org.mpris.MediaPlayer2.suite",
                  desktop_entry="io.piercingxx.Audiobook",
                  title="Ch", last_active_ms=1)
    other = Track("org.mpris.MediaPlayer2.vlc", identity="VLC",
                  title="Song", last_active_ms=99)
    ordered = sort_players([other, suite])
    assert ordered[0] is suite
    assert is_suite(suite) and not is_suite(other)
    assert display_name(suite) == "Audiobook"
    title, artist, _art, length = parse_metadata(
        {"xesam:title": "T", "xesam:artist": ["A", "B"],
         "mpris:length": 1000})
    assert (title, artist, length) == ("T", "A", 1000)
    assert parse_metadata({}) == ("", "", "", 0)


def test_mpris_suite_sort_covers_real_radio_and_audiobook():
    from xx_auto import media_apps as media_mod
    from xx_auto.drive_tiles import Tile, tile_for

    radio = Track(media_mod.RADIO_MPRIS_BUS, identity="XX-Radio",
                  desktop_entry=media_mod.RADIO_DESKTOP_ID,
                  title="FM", last_active_ms=1)
    book = Track(media_mod.AUDIOBOOK_MPRIS_BUS, identity="XX-Audiobook",
                 desktop_entry=media_mod.AUDIOBOOK_DESKTOP_ID,
                 title="Ch", last_active_ms=2)
    other = Track("org.mpris.MediaPlayer2.vlc", identity="VLC",
                  title="Song", last_active_ms=99)
    ordered = sort_players([other, radio, book])
    assert [track.bus_name for track in ordered] == [
        media_mod.AUDIOBOOK_MPRIS_BUS, media_mod.RADIO_MPRIS_BUS,
        "org.mpris.MediaPlayer2.vlc"]
    assert tile_for(radio.desktop_entry, radio.identity) is Tile.RADIO
    assert tile_for(book.desktop_entry, book.identity) is Tile.AUDIOBOOK


def test_demo_tracks_use_real_suite_ids_and_actions():
    from xx_auto import demo as demo_mod
    from xx_auto import media_apps as media_mod
    from xx_auto.drive_tiles import Tile, tile_for

    snap = demo_mod.snapshot_for("quick_pick")
    by_bus = {track.bus_name: track for track in snap.tracks}
    radio = by_bus[media_mod.RADIO_MPRIS_BUS]
    book = by_bus[media_mod.AUDIOBOOK_MPRIS_BUS]
    assert radio.desktop_entry == media_mod.RADIO_DESKTOP_ID
    assert book.desktop_entry == media_mod.AUDIOBOOK_DESKTOP_ID
    assert tile_for(radio.desktop_entry, radio.identity) is Tile.RADIO
    assert tile_for(book.desktop_entry, book.identity) is Tile.AUDIOBOOK
    assert [action.action_id
            for action in radio.actions] == ["like", "dislike"]
    assert [action.icon_name
            for action in radio.actions] == [media_mod.THUMB_UP_ICON,
                                             media_mod.THUMB_DOWN_ICON]
    assert [action.action_id for action in book.actions] == [
        "skip-back", "bookmark", "skip-forward"]
    assert all(action.icon_name == "" for action in book.actions)
