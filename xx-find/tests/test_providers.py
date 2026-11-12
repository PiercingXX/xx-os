"""Lane 3: GeoClue2 client + ModemManager fallback over injected buses.

Fakes mirror real Gio semantics per XXWM_RULES traps: replies unpack
to a tuple of out-args, `reply_type` must be a GLib.VariantType, and
signal handlers receive all 7 args. No test touches the real bus, GPS,
modem, network or keyring.
"""

from __future__ import annotations

import importlib.util
import sys
from inspect import signature
from pathlib import Path

import pytest
from gi.repository import GLib

from xx_find import agent as agent_mod
from xx_find import config, crypto, location, protocol
from xx_find import providers as prov

NOW = 1_800_000_000
CLIENT = "/org/freedesktop/GeoClue2/Client/7"
LOC = "/org/freedesktop/GeoClue2/Location/3"
MODEM = "/org/freedesktop/ModemManager1/Modem/0"

MMCLI_SAMPLE = """\
----------------------------------
  Location |   capabilities: '3gpp-lac-ci, gps-raw, gps-nmea'
           |        enabled: '3gpp-lac-ci, gps-nmea'
           |        signals: 'no'
----------------------------------
  3GPP location | Mobile country code: '214'
                | Mobile network code: '3'
                |  Location area code: '21071'
                |             Cell ID: '7033737'
----------------------------------
  GPS NMEA traces | '$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47'
                  | '$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A'
"""


class FakeReply:
    def __init__(self, payload):
        self._payload = payload

    def unpack(self):
        return self._payload


class FakeBus:
    """Programmable stand-in for Gio.DBusConnection (sync subset)."""

    def __init__(self):
        self.calls = []
        self.subs = {}
        self.unsubscribed = []
        self._next_sub = 1
        self.handlers = {}

    def on(self, iface, method, handler):
        self.handlers[(iface, method)] = handler
        return self

    def call_sync(self, name, path, iface, method, params, reply_type,
                  flags, timeout, cancellable):
        if reply_type is not None and not isinstance(
                reply_type, GLib.VariantType):
            raise TypeError(
                "argument reply_type: Expected GLib.VariantType, but got %s"
                % type(reply_type).__name__)
        self.calls.append({"name": name, "path": path, "iface": iface,
                           "method": method,
                           "params": params.unpack() if params is not None
                           else None})
        handler = self.handlers.get((iface, method))
        if handler is None:
            raise GLib.Error("no such method %s" % method)
        return handler(params)

    def signal_subscribe(self, sender, iface, member, path, arg0, flags,
                         callback, user_data=None):
        sub_id = self._next_sub
        self._next_sub += 1
        self.subs[sub_id] = {"sender": sender, "iface": iface,
                             "member": member, "path": path,
                             "callback": callback,
                             "user_data": user_data}
        try:
            signature(callback).bind(None, None, None, None, None, None,
                                     None)
        except TypeError as exc:
            raise TypeError("signal handler must take 7 args") from exc
        return sub_id

    def signal_unsubscribe(self, sub_id):
        self.unsubscribed.append(sub_id)
        self.subs.pop(sub_id, None)

    def emit(self, member, path, params, sender=":1.42"):
        for sub in list(self.subs.values()):
            if sub["member"] != member or sub["path"] != path:
                continue
            if sub["sender"] is not None and sub["sender"] != sender:
                continue
            sub["callback"](self, sender, path, sub["iface"], member,
                            params, sub["user_data"])


def _location_props(**overrides):
    props = {"Latitude": GLib.Variant("d", 48.1173),
             "Longitude": GLib.Variant("d", 11.5167),
             "Accuracy": GLib.Variant("d", 12.5),
             "Timestamp": GLib.Variant("t", NOW)}
    props.update(overrides)
    return props


def make_geoclue_bus(*, location_path=LOC, props=None, auto_emit=True,
                     raise_on=frozenset()):
    bus = FakeBus()
    sets = {}

    def _get_client(params):
        del params
        return FakeReply((CLIENT,))

    def _set(params):
        iface, prop, value = params.unpack()
        sets[(iface, prop)] = value.unpack() \
            if isinstance(value, GLib.Variant) else value
        return None

    def _get(params):
        iface, prop = params.unpack()
        assert (iface, prop) == (prov.GEOCLUE_CLIENT_IFACE, "Location")
        return FakeReply((GLib.Variant("o", location_path),))

    def _get_all(params):
        del params
        return FakeReply((dict(props if props is not None
                               else _location_props()),))

    def _start(params):
        del params
        if auto_emit:
            bus.emit("LocationUpdated", CLIENT,
                     GLib.Variant("(oo)", ("/", location_path)))
        return None

    bus.on(prov.GEOCLUE_MGR_IFACE, "GetClient", _get_client)
    bus.on(prov.PROPS_IFACE, "Set", _set)
    bus.on(prov.PROPS_IFACE, "Get", _get)
    bus.on(prov.PROPS_IFACE, "GetAll", _get_all)
    bus.on(prov.GEOCLUE_CLIENT_IFACE, "Start", _start)
    bus.on(prov.GEOCLUE_CLIENT_IFACE, "Stop", lambda params: None)
    for key in raise_on:
        def _boom(params, _key=key):
            del params
            raise GLib.Error("injected %s failure" % _key)
        bus.on(*key, _boom)
    bus.set_props = sets
    return bus


def make_modem_bus(*, with_modem=True, loc_dict=None, raise_on=frozenset()):
    bus = FakeBus()
    setups = []

    def _objects(params):
        del params
        if not with_modem:
            return FakeReply(({},))
        return FakeReply(({
            MODEM: {prov.MM_LOCATION_IFACE: {},
                    "org.freedesktop.ModemManager1.Modem": {}}},))

    def _setup(params):
        setups.append(params.unpack())
        return None

    def _get_location(params):
        del params
        if loc_dict is not None:
            return FakeReply((loc_dict,))
        return FakeReply(({
            prov.MM_SOURCE_3GPP: GLib.Variant(
                "s", "214,03,21071,7033737"),
            prov.MM_SOURCE_GPS_NMEA: GLib.Variant(
                "s", "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,"
                     "545.4,M,46.9,M,,*47")},))

    bus.on(prov.OBJMGR_IFACE, "GetManagedObjects", _objects)
    bus.on(prov.MM_LOCATION_IFACE, "Setup", _setup)
    bus.on(prov.MM_LOCATION_IFACE, "GetLocation", _get_location)
    for key in raise_on:
        def _boom(params, _key=key):
            del params
            raise GLib.Error("injected %s failure" % _key)
        bus.on(*key, _boom)
    bus.setups = setups
    return bus


def test_geoclue_full_flow_sets_client_properties_and_stops():
    bus = make_geoclue_bus()
    fix = prov.geoclue_fix(bus, timeout_s=5, now=lambda: NOW)
    assert fix == {"lat": 48.1173, "lon": 11.5167, "acc_m": 12.5,
                   "epoch": NOW, "source": "geoclue"}
    methods = [(c["iface"], c["method"]) for c in bus.calls]
    assert methods[0] == (prov.GEOCLUE_MGR_IFACE, "GetClient")
    assert (prov.GEOCLUE_CLIENT_IFACE, "Start") in methods
    assert methods[-1] == (prov.GEOCLUE_CLIENT_IFACE, "Stop")
    assert bus.unsubscribed == [1]
    assert bus.set_props == {
        (prov.GEOCLUE_CLIENT_IFACE, "DesktopId"): prov.GEOCLUE_DESKTOP_ID,
        (prov.GEOCLUE_CLIENT_IFACE, "RequestedAccuracyLevel"): 8,
        (prov.GEOCLUE_CLIENT_IFACE, "DistanceThreshold"): 0}


def test_geoclue_subscription_matches_unique_name_senders():
    bus = make_geoclue_bus()
    prov.geoclue_fix(bus, timeout_s=5, now=lambda: NOW)
    assert len(bus.subs) == 0
    assert bus.unsubscribed == [1]


def test_geoclue_sender_filter_is_none_not_well_known():
    bus = FakeBus()
    seen = []
    sub = bus.signal_subscribe(
        "org.freedesktop.GeoClue2", "some.Iface", "Changed", "/x",
        None, 0, lambda *args: seen.append(args))
    bus.emit("Changed", "/x", GLib.Variant("(s)", ("y",)), sender=":1.9")
    assert seen == []
    bus.signal_unsubscribe(sub)


def test_geoclue_uses_preset_location_without_waiting_for_signal():
    bus = make_geoclue_bus(auto_emit=False)
    fix = prov.geoclue_fix(bus, timeout_s=5, now=lambda: NOW)
    assert fix is not None and fix["source"] == "geoclue"
    assert any((c["iface"], c["method"]) ==
               (prov.GEOCLUE_CLIENT_IFACE, "Stop") for c in bus.calls)


def test_geoclue_timeout_stops_and_returns_none():
    bus = make_geoclue_bus(auto_emit=False, location_path="/")
    assert prov.geoclue_fix(bus, timeout_s=0.05,
                            now=lambda: NOW) is None
    methods = [(c["iface"], c["method"]) for c in bus.calls]
    assert (prov.GEOCLUE_CLIENT_IFACE, "Stop") in methods
    assert bus.unsubscribed == [1]


def test_geoclue_location_read_failure_still_stops():
    bus = make_geoclue_bus(
        raise_on=frozenset({(prov.PROPS_IFACE, "GetAll")}))
    assert prov.geoclue_fix(bus, timeout_s=5,
                            now=lambda: NOW) is None
    methods = [(c["iface"], c["method"]) for c in bus.calls]
    assert (prov.GEOCLUE_CLIENT_IFACE, "Stop") in methods


def test_geoclue_bus_failures_return_none():
    assert prov.geoclue_fix(
        make_geoclue_bus(
            raise_on=frozenset({(prov.GEOCLUE_MGR_IFACE, "GetClient")}),
        ), timeout_s=0.05, now=lambda: NOW) is None
    assert prov.geoclue_fix(
        make_geoclue_bus(
            raise_on=frozenset({(prov.PROPS_IFACE, "Set")}),
        ), timeout_s=0.05, now=lambda: NOW) is None
    assert prov.geoclue_fix(
        make_geoclue_bus(
            raise_on=frozenset({(prov.GEOCLUE_CLIENT_IFACE, "Start")}),
        ), timeout_s=0.05, now=lambda: NOW) is None


def test_geoclue_rejects_bad_coordinates():
    bus = make_geoclue_bus(
        props=_location_props(Latitude=GLib.Variant("d", 91.0)))
    assert prov.geoclue_fix(bus, timeout_s=5,
                            now=lambda: NOW) is None


def test_location_epoch_accepts_pair_and_defaults():
    assert prov.location_epoch((NOW, 500000), 0) == NOW
    assert prov.location_epoch(GLib.Variant("t", NOW), 0) == NOW
    assert prov.location_epoch(None, NOW) == NOW
    assert prov.location_epoch("yesterday", NOW) == NOW
    assert prov.location_epoch(True, NOW) == NOW


def test_no_nested_prebuilt_asv_variant_construction():
    import re

    src = Path(prov.__file__).read_text(encoding="utf-8")
    assert not re.search(r"Variant\s*\(\s*[\"']a\{sv\}", src)
    built = GLib.Variant("(ssv)", ("org.freedesktop.GeoClue2.Client",
                                   "RequestedAccuracyLevel",
                                   GLib.Variant("u", 8)))
    assert built.unpack()[2] == 8


def test_modem_setup_enables_3gpp_and_gps_sources_then_reads():
    bus = make_modem_bus()
    fix = prov.modem_fix(bus, now=lambda: NOW)
    assert bus.setups == [(prov.MM_SETUP_SOURCES, False)]
    assert prov.MM_SETUP_SOURCES == (prov.MM_SOURCE_3GPP
                                     | prov.MM_SOURCE_GPS_RAW
                                     | prov.MM_SOURCE_GPS_NMEA)
    assert fix is not None and fix["source"] == "modem-gps"
    assert abs(fix["lat"] - 48.1173) < 1e-3


def test_modem_prefers_gps_over_cell_via_pick_fix():
    loc = {prov.MM_SOURCE_3GPP: GLib.Variant("s", "214,03,21071,7033737"),
           prov.MM_SOURCE_GPS_NMEA: GLib.Variant(
               "s", "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,"
                    "230394,003.1,W*6A")}
    candidates, cell = prov.modem_candidates_from_location_dict(loc, NOW)
    assert cell == {"mcc": 214, "mnc": 3, "lac": 21071, "cid": 7033737}
    assert location.pick_fix(candidates)["source"] == "modem-gps"


def test_modem_cell_only_yields_cell_record():
    bus = make_modem_bus(loc_dict={
        prov.MM_SOURCE_3GPP: GLib.Variant("(ssss)", ("214", "03", "21071",
                                                     "7033737"))})
    fix = prov.modem_fix(bus, now=lambda: NOW)
    assert fix == {"source": "modem-cell", "epoch": NOW,
                   "cell": {"mcc": 214, "mnc": 3, "lac": 21071,
                            "cid": 7033737}}


def test_modem_without_location_modem_or_on_failure_returns_none():
    assert prov.modem_fix(make_modem_bus(with_modem=False),
                          now=lambda: NOW) is None
    assert prov.modem_fix(make_modem_bus(
        raise_on=frozenset({(prov.MM_LOCATION_IFACE, "Setup")}),
    ), now=lambda: NOW) is None
    assert prov.modem_fix(make_modem_bus(
        raise_on=frozenset({(prov.OBJMGR_IFACE, "GetManagedObjects")}),
    ), now=lambda: NOW) is None
    assert prov.modem_fix(make_modem_bus(loc_dict={}),
                          now=lambda: NOW) is None


def test_modem_location_dict_shapes():
    candidates, cell = prov.modem_candidates_from_location_dict(
        {1: {"mcc": 214, "mnc": 3, "lac": 21071, "cid": 7033737},
         4: ["$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,"
             "M,,*47"]}, NOW)
    assert cell == {"mcc": 214, "mnc": 3, "lac": 21071, "cid": 7033737}
    assert len(candidates) == 1
    candidates, _cell = prov.modem_candidates_from_location_dict(
        {"2": {"lat": 1.5, "lon": -0.5}}, NOW)
    assert candidates[0]["lat"] == 1.5
    assert prov.modem_candidates_from_location_dict("junk", NOW) == ([], None)


def test_pick_fix_on_real_mmcli_shaped_sample():
    candidates, cell = prov.parse_mmcli_location_get(MMCLI_SAMPLE, NOW)
    assert cell == {"mcc": 214, "mnc": 3, "lac": 21071, "cid": 7033737}
    best = location.pick_fix(candidates)
    assert best is not None and best["source"] == "modem-gps"
    assert abs(best["lat"] - 48.1173) < 1e-3
    assert abs(best["lon"] - 11.5167) < 1e-3


def test_mmcli_sample_without_gps_leaves_cell_only():
    text = "\n".join(line for line in MMCLI_SAMPLE.splitlines()
                      if "GPS NMEA" not in line and "$GP" not in line
                      and "$GPRMC" not in line)
    candidates, cell = prov.parse_mmcli_location_get(text, NOW)
    assert candidates == []
    assert cell == {"mcc": 214, "mnc": 3, "lac": 21071, "cid": 7033737}
    assert prov.parse_mmcli_location_get("", NOW) == ([], None)
    assert prov.parse_mmcli_location_get(None, NOW) == ([], None)


def _paired_cfg():
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    cfg = config.AgentConfig(
        enabled=True, server_url="http://nas:8080", device_id="phone-1",
        owner_public_key_hex=verify_hex)
    return cfg, signing_hex


def _locate_envelope(signing_hex, counter=1):
    cmd = protocol.build_command("phone-1", counter, NOW + 120, "locate")
    return protocol.sign_envelope(cmd, signing_hex)


def test_build_read_fix_prefers_geoclue_then_modem():
    read = prov.build_read_fix(
        system_bus_factory=lambda: make_geoclue_bus(),
        modem_bus_factory=lambda: make_modem_bus(),
        now=lambda: NOW, geoclue_timeout_s=5, modem_timeout_s=5)
    assert read()["source"] == "geoclue"
    dead = make_geoclue_bus(
        raise_on=frozenset({(prov.GEOCLUE_MGR_IFACE, "GetClient")}))
    read = prov.build_read_fix(
        system_bus_factory=lambda: dead,
        modem_bus_factory=lambda: make_modem_bus(),
        now=lambda: NOW, geoclue_timeout_s=0.05, modem_timeout_s=5)
    assert read()["source"] == "modem-gps"


def test_locate_through_providers_emits_one_privacy_event():
    cfg, signing_hex = _paired_cfg()
    events, reports = [], []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        read_fix=prov.build_read_fix(
            system_bus_factory=lambda: make_geoclue_bus(),
            modem_bus_factory=lambda: make_modem_bus(),
            now=lambda: NOW, geoclue_timeout_s=5, modem_timeout_s=5),
        send_report=lambda c, fix: reports.append(fix),
        on_privacy_event=lambda kind, app: events.append((kind, app)))
    out = agent_mod.Agent(cfg, deps).handle_command(
        _locate_envelope(signing_hex))
    assert out == {"ok": True, "action": "locate"}
    assert events == [("location", "xx-find-agent")]
    assert reports[0]["source"] == "geoclue"


def test_locate_through_modem_fallback_emits_privacy_event():
    cfg, signing_hex = _paired_cfg()
    events = []
    dead = make_geoclue_bus(
        raise_on=frozenset({(prov.GEOCLUE_MGR_IFACE, "GetClient")}))
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        read_fix=prov.build_read_fix(
            system_bus_factory=lambda: dead,
            modem_bus_factory=lambda: make_modem_bus(),
            now=lambda: NOW, geoclue_timeout_s=0.05, modem_timeout_s=5),
        send_report=lambda c, fix: None,
        on_privacy_event=lambda kind, app: events.append((kind, app)))
    out = agent_mod.Agent(cfg, deps).handle_command(
        _locate_envelope(signing_hex))
    assert out == {"ok": True, "action": "locate"}
    assert events == [("location", "xx-find-agent")]


def test_failed_location_read_emits_no_privacy_event():
    cfg, signing_hex = _paired_cfg()
    events = []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        read_fix=prov.build_read_fix(
            system_bus_factory=lambda: make_geoclue_bus(
                raise_on=frozenset(
                    {(prov.GEOCLUE_MGR_IFACE, "GetClient")})),
            modem_bus_factory=lambda: make_modem_bus(with_modem=False),
            now=lambda: NOW, geoclue_timeout_s=0.05, modem_timeout_s=5),
        send_report=lambda c, fix: None,
        on_privacy_event=lambda kind, app: events.append((kind, app)))
    out = agent_mod.Agent(cfg, deps).handle_command(
        _locate_envelope(signing_hex))
    assert out == {"ok": False, "error": "no fix"}
    assert events == []


def _load_shell_privacy_monitor():
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "ref" / "xx-wm" / "launcher" / "src" / "privacy_monitor.py",
        root.parent / "xx-wm" / "launcher" / "src" / "privacy_monitor.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location(
                "shell_privacy_monitor", path)
            module = importlib.util.module_from_spec(spec)
            # Installed, the module sits beside the shell's other modules
            # (shell_log); load it the same way.
            sys.path.insert(0, str(path.parent))
            try:
                spec.loader.exec_module(module)
            finally:
                sys.path.remove(str(path.parent))
            return module
    pytest.skip("shell reference tree not present")


def test_privacy_indicator_lights_in_shell_harness(tmp_path):
    shell_pm = _load_shell_privacy_monitor()
    cfg, signing_hex = _paired_cfg()
    seen_apps: set[str] = set()
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        read_fix=prov.build_read_fix(
            system_bus_factory=lambda: make_geoclue_bus(),
            modem_bus_factory=lambda: make_modem_bus(),
            now=lambda: NOW, geoclue_timeout_s=5, modem_timeout_s=5),
        send_report=lambda c, fix: None,
        on_privacy_event=lambda kind, app: seen_apps.add(app)
        if kind == "location" else None)
    assert agent_mod.Agent(cfg, deps).handle_command(
        _locate_envelope(signing_hex))["ok"] is True
    usage = shell_pm.UsageLog(path=tmp_path / "usage.json",
                              clock=lambda: NOW)
    monitor = shell_pm.PrivacyMonitor(
        location_provider=lambda: sorted(seen_apps), usage_log=usage)
    monitor.refresh_location()
    assert monitor.snapshot["location"] == {"xx-find-agent"}
    entries = usage.recent(now=NOW)
    assert {"kind": "location", "app": "xx-find-agent",
            "at": NOW} in entries
