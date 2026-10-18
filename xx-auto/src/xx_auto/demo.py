"""Demo snapshots for screenshots and smoke tests (no D-Bus needed)."""

from __future__ import annotations

from .mpris import CustomAction, Track


def _radio_track() -> Track:
    return Track(
        bus_name="org.mpris.MediaPlayer2.xx_radio",
        identity="XX-Radio", desktop_entry="io.piercingxx.XXRadio",
        title="Slow Morning Light", artist="Harbor FM · The Quiet Hours",
        length_us=0, position_us=0, status="Playing",
        can_play=True, can_pause=True, can_next=True, can_prev=True,
        actions=[CustomAction("like", "Like",
                              "xx-auto-thumb-up-symbolic", True),
                 CustomAction("dislike", "Dislike",
                              "xx-auto-thumb-down-symbolic", True)],
        last_active_ms=3)


def _audiobook_track() -> Track:
    return Track(
        bus_name="org.mpris.MediaPlayer2.xx_audiobook",
        identity="XX-Audiobook", desktop_entry="io.piercingxx.XXAudiobook",
        title="Chapter 7: The Long Road North",
        artist="The Cartographer's Daughter",
        length_us=2310 * 1_000_000, position_us=754 * 1_000_000,
        status="Playing", can_play=True, can_pause=True,
        can_next=True, can_prev=True, can_seek=True,
        actions=[CustomAction("skip-back", "Back 30s", "", True),
                 CustomAction("bookmark", "Bookmark", "", True),
                 CustomAction("skip-forward", "Forward 30s", "", True)],
        last_active_ms=2)


def _paused_radio() -> Track:
    track = _radio_track()
    track.status = "Paused"
    return track


def demo_settings():
    from .settings_store import Settings

    return Settings(surface_calls=True, auto_launch=True,
                    auto_launch_device="AA:BB:CC:DD:EE:FF")


def snapshot_for(scenario: str):
    from .main import DriveSnapshot

    if scenario == "nothing_installed":
        return DriveSnapshot(tracks=[], installed={},
                             maps_title="Maps", maps_sub="not installed")
    if scenario == "nothing_playing":
        return DriveSnapshot(tracks=[],
                             installed={"radio": True, "audiobook": True})
    if scenario == "radio":
        return DriveSnapshot(tracks=[_radio_track()],
                             installed={"radio": True, "audiobook": True})
    if scenario == "audiobook":
        return DriveSnapshot(tracks=[_audiobook_track(), _paused_radio()],
                             installed={"radio": True, "audiobook": True})
    if scenario == "paused":
        return DriveSnapshot(tracks=[_paused_radio()],
                             installed={"radio": True, "audiobook": True})
    if scenario == "playing":
        return DriveSnapshot(tracks=[_audiobook_track()],
                             installed={"radio": True, "audiobook": True})
    if scenario in ("quick_pick", "players"):
        return DriveSnapshot(tracks=[_radio_track(), _audiobook_track()],
                             installed={"radio": True, "audiobook": True})
    if scenario in ("favourites", "settings", "licenses", "car"):
        return DriveSnapshot(tracks=[_radio_track()],
                             installed={"radio": True, "audiobook": True})
    return DriveSnapshot(tracks=[],
                         installed={"radio": True, "audiobook": True})


def demo_favourites() -> list:
    from .contacts_client import Favourite

    return [Favourite("uid-alex", "Alex Morgan", "+1 555 0142"),
            Favourite("uid-home", "Home", "+1 555 0100"),
            Favourite("uid-sam", "Sam Rivera", "+1 555 0187")]


def demo_car_devices() -> list:
    from .bluez import CarDevice

    return [CarDevice("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
                      "AA:BB:CC:DD:EE:FF", "Car Audio",
                      connected=False, paired=True),
            CarDevice("/org/bluez/hci0/dev_11_22_33_44_55_66",
                      "11:22:33:44:55:66", "Headphones",
                      connected=False, paired=True)]
