"""D-Bus shape guards: real GLib Variants, real reply types, headless service.

Every Variant the player service emits or the client parses is built here
under real GLib (never nest a prebuilt a{sv} Variant inside another
constructor; pass plain dicts of Variants). The introspection XML must
parse, reply_type must always be a GLib.VariantType, and the service must
stay headless (no Gtk/Adw imports) so it runs without a display.
"""

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from xx_audiobook import player as player_module  # noqa: E402
from xx_audiobook import store as store_module  # noqa: E402
from xx_audiobook.player import FakeBackend  # noqa: E402
from xx_audiobook.service import player_service as service_module  # noqa: E402
from xx_audiobook.service.player_service import PlayerService  # noqa: E402


def _service():
    db = store_module.Database.in_memory()
    service = PlayerService(db, None, FakeBackend())
    return db, service


def test_mpris_xml_parses():
    node = Gio.DBusNodeInfo.new_for_xml(service_module.MPRIS_XML)
    names = {iface.name for iface in node.interfaces}
    assert names == {"org.mpris.MediaPlayer2",
                     "org.mpris.MediaPlayer2.Player",
                     "org.freedesktop.DBus.Properties",
                     "io.piercingxx.XXAuto.Actions1"}


def test_player_xml_parses():
    node = Gio.DBusNodeInfo.new_for_xml(service_module.PLAYER_XML)
    names = {iface.name for iface in node.interfaces}
    assert names == {"io.piercingxx.XXAudiobook.Player1",
                     "org.freedesktop.DBus.Properties"}


def test_state_variant_round_trips():
    db, service = _service()
    try:
        variant = service._state_variant(service.state_dict())
        (plain,) = variant.unpack()
        assert plain["status"] == "Stopped"
        assert plain["has_book"] is False
        assert isinstance(plain["position_us"], int)
        assert isinstance(plain["rate"], float)
    finally:
        db.close()


def test_mpris_props_and_metadata_build():
    db, service = _service()
    try:
        root = GLib.Variant("(a{sv})", (service.mpris_root_props(),))
        (plain,) = root.unpack()
        assert plain["DesktopEntry"] == "io.piercingxx.XXAudiobook"
        player = GLib.Variant("(a{sv})", (service.mpris_player_props(),))
        (props,) = player.unpack()
        assert props["PlaybackStatus"] == "Stopped"
        assert props["CanControl"] is True
        changed = GLib.Variant("(sa{sv}as)", (
            "org.mpris.MediaPlayer2.Player",
            service.mpris_player_props(), []))
        assert changed.unpack()[0] == "org.mpris.MediaPlayer2.Player"
        seeked = GLib.Variant("(x)", (30000000,))
        assert seeked.unpack() == (30000000,)
        actions = GLib.Variant("(a(sssb))", (service.action_rows(),))
        (rows,) = actions.unpack()
        assert [row[0] for row in rows] == [
            "skip-back", "bookmark", "skip-forward"]
    finally:
        db.close()


def test_property_get_boxes_single_variant():
    db, service = _service()
    try:
        variant = service._dispatch_props(
            "Get", ("org.mpris.MediaPlayer2", "Identity"))
        (boxed,) = variant.unpack()
        assert boxed == "xx-audiobook"
        with pytest.raises(service_module.ServiceError):
            service._dispatch_props("Get", ("org.mpris.MediaPlayer2",
                                            "Nope"))
    finally:
        db.close()


def test_client_reply_type_is_variant_type():
    assert isinstance(player_module._reply_type("(a{sv})"),
                      GLib.VariantType)


def test_service_stays_headless():
    import pathlib
    import re

    def imports_gtk(path):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        return re.search(
            r"(require_version\(\s*[\"'](Gtk|Adw)[\"']"
            r"|from gi\.repository import .*\b(Gtk|Adw)\b"
            r"|from gi\.repository import \([^)]*\b(Gtk|Adw)\b)", source)

    assert imports_gtk(service_module.__file__) is None
    assert imports_gtk(player_module.__file__) is None
