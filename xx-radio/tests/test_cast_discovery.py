"""Discovery parsing for Avahi and zeroconf records. No network."""

from xx_music.cast import discovery as d
from xx_music.cast.queueing import (fill_after, load_window,
                                    local_index_after_cast, should_reload)


def test_parse_txt_reads_friendly_name_model_and_group():
    txt = d.parse_txt([b"fn=Living Room", b"md=Google Home",
                       b"id=abc123"])
    assert txt == {"fn": "Living Room", "md": "Google Home",
                   "id": "abc123"}
    assert d.parse_txt([b"no-equals", b"=x", b""]) == {}


def test_device_from_avahi():
    device = d.device_from_avahi("Living Room._googlecast._tcp.local.",
                                 "192.168.1.40", 8009,
                                 [b"fn=Living Room", b"md=Chromecast",
                                  b"id=dev-1"])
    assert device is not None
    assert (device.id, device.name, device.host, device.port) == \
        ("dev-1", "Living Room", "192.168.1.40", 8009)
    assert device.group is False


def test_device_from_avahi_marks_groups():
    device = d.device_from_avahi("Upstairs._googlecast._tcp.local.",
                                 "192.168.1.41", 8009,
                                 [b"fn=Upstairs", b"md=Google Cast Group",
                                  b"id=dev-2"])
    assert device is not None
    assert device.group is True
    assert device.name == "Upstairs · group"


def test_device_from_avahi_without_txt_name_uses_service_name():
    device = d.device_from_avahi("Kitchen._googlecast._tcp.local.",
                                 "192.168.1.42", 8009, [])
    assert device is not None
    assert device.name == "Kitchen"


def test_device_from_zeroconf():
    device = d.device_from_zeroconf(
        "Bedroom._googlecast._tcp.local.",
        ["192.168.1.43"], 8009,
        {b"fn": b"Bedroom", b"md": b"Google Nest Mini", b"id": b"dev-3"},
        "bedroom.local.")
    assert device is not None
    assert device.name == "Bedroom"
    assert device.host == "192.168.1.43"
    assert device.model == "Google Nest Mini"


def test_device_id_falls_back_without_txt_id():
    device = d.device_from_zeroconf(
        "Office._googlecast._tcp.local.", ["192.168.1.44"], 8009, {},
        "")
    assert device is not None
    assert device.id == "Office:8009"


def test_discovery_reports_devices_through_idle_add():
    seen: list = []

    def probe():
        return [d.CastDevice("a", "Alpha", "192.168.1.2", 8009),
                d.CastDevice("b", "Beta", "192.168.1.3", 8009)]

    browser = d.Discovery(on_update=lambda devs, busy: seen.append(
        ([x.name for x in devs], busy)),
        idle_add=lambda fn, *a: fn(*a),
        avahi_probe=probe)
    browser.refresh()
    browser._thread.join(timeout=5)
    assert seen
    names, searching = seen[-1]
    assert names == ["Alpha", "Beta"]
    assert searching is False


def test_load_window_fill_after_and_handoff_helpers():
    assert load_window(["a", "b", "c"], 1) == ["b", "c"]
    assert load_window(["a", "b", "c"], 0, limit=2) == ["a", "b"]
    assert load_window(["a", "b"], 0, ready=lambda x: x == "b") == ["b"]
    assert fill_after(["a", "b", "c"], "a", "b") == 0
    assert fill_after(["a", "b", "c"], None, "c") == 2
    assert fill_after(["a"], None, "zzz") == -1
    assert should_reload("a", "b") is True
    assert should_reload("a", "a") is False
    assert should_reload("a", "") is False
    assert local_index_after_cast(["a", "b"], 0, "b") == 1
    assert local_index_after_cast(["a"], 0, "zzz") == 0
