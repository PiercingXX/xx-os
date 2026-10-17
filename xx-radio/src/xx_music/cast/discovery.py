"""Find Cast devices on the LAN without blocking the main loop.

The daemon over D-Bus (``org.freedesktop.Avahi``) comes first; when it
is absent the optional ``zeroconf`` package is the fallback. Both paths
are asynchronous: browser work runs on a worker thread and results come
back through ``idle_add``. The parsing helpers are pure so tests cover
them with recorded records and no network.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


SERVICE_TYPE = "_googlecast._tcp"


@dataclass(frozen=True)
class CastDevice:
    id: str
    name: str
    host: str = ""
    port: int = 8009
    model: str = ""
    group: bool = False


def parse_txt(records: list[bytes]) -> dict[str, str]:
    """Split Avahi-style ``[b"k=v", ...]`` TXT records into text."""
    found: dict[str, str] = {}
    for record in records:
        try:
            text = bytes(record).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - one bad record never blocks
            continue
        key, sep, value = text.partition("=")
        if not sep or not key:
            continue
        found[key.strip()] = value.strip()
    return found


def decode_properties(properties: dict) -> dict[str, str]:
    """Split zeroconf-style ``{b"k": b"v"}`` properties into text."""
    found: dict[str, str] = {}

    def _text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)

    for key, value in (properties or {}).items():
        name = _text(key).strip()
        if name:
            found[name] = _text(value).strip()
    return found


def describe(txt: dict[str, str], fallback_name: str = "") -> dict:
    """Friendly name, model and group flag from TXT fields."""
    name = txt.get("fn", "").strip() or fallback_name.strip()
    model = txt.get("md", "").strip()
    group = "group" in model.casefold() or "group" in name.casefold()
    return {"name": name, "model": model, "group": group}


def short_name(service_name: str) -> str:
    """Strip the ``_googlecast._tcp`` suffix from a service name."""
    suffix = "." + SERVICE_TYPE
    name = (service_name or "").strip()
    if suffix in name:
        name = name.split(suffix)[0]
    return name.strip().rstrip(".")


def device_id_for(txt: dict[str, str], service_name: str, host: str,
                  port: int) -> str:
    for key in ("id", "ve"):
        value = txt.get(key, "").strip()
        if value:
            return value
    base = short_name(service_name) or host or "speaker"
    return f"{base}:{int(port or 8009)}"


def device_from_avahi(name: str, address: str, port: int,
                      txt_records: list[bytes]) -> CastDevice | None:
    txt = parse_txt(txt_records or [])
    info = describe(txt, short_name(name))
    if not info["name"]:
        return None
    return CastDevice(
        id=device_id_for(txt, name, address, port),
        name=info["name"] + (" · group" if info["group"] else ""),
        host=address or "", port=int(port or 8009),
        model=info["model"], group=info["group"])


def device_from_zeroconf(service_name: str, addresses: list[str],
                         port: int, properties: dict,
                         server: str = "") -> CastDevice | None:
    txt = decode_properties(properties or {})
    host = next((a for a in (addresses or []) if a), "") or (server or "")
    host = host.rstrip(".")
    info = describe(txt, short_name(service_name))
    if not info["name"]:
        if not host:
            return None
        info = {"name": host, "model": info["model"],
                "group": info["group"]}
    return CastDevice(
        id=device_id_for(txt, service_name, host, port),
        name=info["name"] + (" · group" if info["group"] else ""),
        host=host, port=int(port or 8009),
        model=info["model"], group=info["group"])


class AvahiBrowser:
    """Browse Cast devices through the avahi daemon, all async.

    Every D-Bus call uses the async ``call`` with a callback, so the
    GTK loop and the player service never block. Resolution results
    come back through ``idle_add``. Without a daemon the browser
    reports unavailable and the caller falls back to zeroconf.
    """

    BUS = "org.freedesktop.Avahi"
    SERVER_PATH = "/"
    SERVER_IFACE = "org.freedesktop.Avahi.Server"
    BROWSER_IFACE = "org.freedesktop.Avahi.ServiceBrowser"
    RESOLVER_IFACE = "org.freedesktop.Avahi.ServiceResolver"

    def __init__(self, on_update=None, idle_add=None,
                 settle_s: float = 4.0) -> None:
        self._on_update = on_update
        self._idle_add = idle_add or (lambda fn, *a: fn(*a))
        self._settle_s = float(settle_s)
        self._lock = threading.Lock()
        self._conn = None
        self._browser_path: str | None = None
        self._subs: list = []
        self._resolvers: dict[str, str] = {}
        self._devices: dict[str, CastDevice] = {}
        self._searching = False
        self.available: bool | None = None

    @property
    def devices(self) -> list[CastDevice]:
        with self._lock:
            return sorted(self._devices.values(),
                          key=lambda d: d.name)

    def refresh(self) -> None:
        with self._lock:
            self._searching = True
        self._emit()
        settle = threading.Thread(target=self._settle, daemon=True,
                                  name="xx-cast-avahi-settle")
        settle.start()
        try:
            from gi.repository import Gio
        except ImportError:
            self._unavailable()
            return
        if self._conn is not None:
            self._browse()
            return
        try:
            Gio.bus_get(Gio.BusType.SYSTEM, None, self._on_system_bus)
        except Exception:  # noqa: BLE001 - no system bus here
            self._unavailable()

    def stop(self) -> None:
        with self._lock:
            path = self._browser_path
            self._browser_path = None
            self._searching = False
        if path is not None and self._conn is not None:
            try:
                self._conn.call(
                    self.BUS, path, self.BROWSER_IFACE, "Free", None,
                    None, 0, 2000, None, None)
            except Exception:  # noqa: BLE001 - stop is best effort
                pass

    def _settle(self) -> None:
        done = threading.Event()
        done.wait(max(0.5, self._settle_s))
        with self._lock:
            if not self._searching:
                return
            self._searching = False
        self._emit()

    def _unavailable(self) -> None:
        self.available = False
        with self._lock:
            self._searching = False
        self._emit()

    def _on_system_bus(self, _source, result) -> None:
        try:
            from gi.repository import Gio, GLib
        except ImportError:
            self._unavailable()
            return
        try:
            self._conn = Gio.bus_get_finish(result)
        except Exception:  # noqa: BLE001 - doubt means no daemon
            self._unavailable()
            return
        try:
            self._conn.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner",
                GLib.Variant("(s)", (self.BUS,)),
                GLib.VariantType.new("(b)"), 0, 2000, None,
                self._on_owner)
        except Exception:  # noqa: BLE001 - doubt means no daemon
            self._unavailable()

    def _on_owner(self, connection, result) -> None:
        try:
            (owned,) = connection.call_finish(result).unpack()
        except Exception:  # noqa: BLE001 - doubt means no daemon
            owned = False
        if not owned:
            self._unavailable()
            return
        self.available = True
        self._browse()

    def _browse(self) -> None:
        from gi.repository import GLib

        self.stop()
        with self._lock:
            self._searching = True
        try:
            self._conn.call(
                self.BUS, self.SERVER_PATH, self.SERVER_IFACE,
                "ServiceBrowserNew",
                GLib.Variant("(iissu)", (-1, -1, SERVICE_TYPE, "", 0)),
                GLib.VariantType.new("(o)"), 0, 3000, None,
                self._on_browser)
        except Exception:  # noqa: BLE001 - a dead daemon is a fallback
            self._unavailable()

    def _on_browser(self, connection, result) -> None:
        from gi.repository import Gio

        try:
            (path,) = connection.call_finish(result).unpack()
        except Exception:  # noqa: BLE001 - a dead daemon is a fallback
            self._unavailable()
            return
        with self._lock:
            self._browser_path = path
        for member in ("ItemNew", "ItemRemove"):
            try:
                self._subs.append(connection.signal_subscribe(
                    self.BUS, self.BROWSER_IFACE, member, path, None,
                    Gio.DBusSignalFlags.NONE, self._on_browser_signal))
            except Exception:  # noqa: BLE001 - a dead daemon is fallback
                pass

    def _on_browser_signal(self, connection, _sender, _path, _iface,
                           member, params) -> None:
        try:
            _interface, _protocol, name, kind, _domain, _flags = \
                params.unpack()
        except Exception:  # noqa: BLE001 - a bad signal never breaks us
            return
        if kind != SERVICE_TYPE and not kind.endswith(SERVICE_TYPE):
            return
        if member == "ItemRemove":
            with self._lock:
                dropped = [key for key, device in self._devices.items()
                           if device.name == short_name(name)]
                for key in dropped:
                    self._devices.pop(key, None)
            self._emit()
            return
        self._resolve(name)

    def _resolve(self, name: str) -> None:
        from gi.repository import GLib

        if self._conn is None:
            return
        try:
            self._conn.call(
                self.BUS, self.SERVER_PATH, self.SERVER_IFACE,
                "ServiceResolverNew",
                GLib.Variant("(iissisi)", (-1, -1, name, SERVICE_TYPE,
                                           "", -1, 0)),
                GLib.VariantType.new("(o)"), 0, 3000, None,
                lambda conn, result, wanted=name: self._on_resolver(
                    conn, result, wanted))
        except Exception:  # noqa: BLE001 - one failure never breaks us
            pass

    def _on_resolver(self, connection, result, name: str) -> None:
        from gi.repository import Gio

        try:
            (path,) = connection.call_finish(result).unpack()
        except Exception:  # noqa: BLE001 - one failure never breaks us
            return
        with self._lock:
            self._resolvers[name] = path
        try:
            connection.signal_subscribe(
                self.BUS, self.RESOLVER_IFACE, "Found", path, None,
                Gio.DBusSignalFlags.NONE, self._on_found)
        except Exception:  # noqa: BLE001 - one failure never breaks us
            pass

    def _on_found(self, _connection, _sender, _path, _iface, _signal,
                  params) -> None:
        try:
            (_interface, _protocol, name, _kind, _domain, _host,
             _aprotocol, address, port, txt, _flags) = params.unpack()
        except Exception:  # noqa: BLE001 - a bad signal never breaks us
            return
        device = device_from_avahi(name, address, port, list(txt or []))
        if device is None:
            return
        with self._lock:
            self._devices[device.id] = device
        self._emit()

    def _emit(self) -> None:
        callback = self._on_update
        if callback is None:
            return
        devices = self.devices
        with self._lock:
            searching = self._searching
        try:
            self._idle_add(callback, devices, searching)
        except Exception:  # noqa: BLE001 - a gone UI never breaks browse
            pass


class Discovery:
    """Async device browser; never blocks the caller.

    ``idle_add`` posts updates to the main loop (GLib.idle_add in the
    app, synchronous in tests). ``avahi_probe`` and ``zeroconf_scan``
    are injectable so tests drive updates with recorded records.
    """

    def __init__(self, on_update=None, idle_add=None, avahi_probe=None,
                 zeroconf_scan=None) -> None:
        self._on_update = on_update
        self._idle_add = idle_add or (lambda fn, *a: fn(*a))
        self._avahi_probe = avahi_probe
        self._zeroconf_scan = zeroconf_scan
        self._lock = threading.Lock()
        self._devices: dict[str, CastDevice] = {}
        self._searching = False
        self._thread: threading.Thread | None = None

    @property
    def devices(self) -> list[CastDevice]:
        with self._lock:
            return sorted(self._devices.values(), key=lambda d: d.name)

    @property
    def searching(self) -> bool:
        with self._lock:
            return self._searching

    def refresh(self) -> None:
        with self._lock:
            if self._searching:
                return
            self._searching = True
        self._emit()
        worker = threading.Thread(target=self._browse, daemon=True,
                                  name="xx-cast-discover")
        self._thread = worker
        worker.start()

    def _browse(self) -> None:
        found: list[CastDevice] = []
        try:
            if self._avahi_probe is not None:
                found = list(self._avahi_probe() or [])
            elif self._zeroconf_scan is not None:
                found = list(self._zeroconf_scan() or [])
            else:
                found = list(self._system_scan() or [])
        except Exception:  # noqa: BLE001 - discovery never raises
            found = []
        with self._lock:
            self._devices = {d.id: d for d in found if d is not None}
            self._searching = False
        self._emit()

    def _system_scan(self) -> list[CastDevice]:
        scanner = _zeroconf_module_scan()
        if scanner is not None:
            try:
                return list(scanner() or [])
            except Exception:  # noqa: BLE001 - fall through to no devices
                return []
        return []

    def _emit(self) -> None:
        callback = self._on_update
        if callback is None:
            return
        devices = self.devices
        searching = self.searching
        try:
            self._idle_add(callback, devices, searching)
        except Exception:  # noqa: BLE001 - a gone UI never breaks browse
            pass


def _zeroconf_module_scan():
    try:
        import zeroconf as zc  # noqa: F401
    except ImportError:
        return None

    def _scan(timeout_s: float = 4.0) -> list[CastDevice]:
        import zeroconf as _zc

        found: list[CastDevice] = []
        seen: dict[str, CastDevice] = {}

        class _Listener(_zc.ServiceListener):
            def add_service(self, kind: str, name: str) -> None:
                info = _zc.ServiceInfo(kind, name)
                device = device_from_zeroconf(
                    name, [], info.port or 8009,
                    decode_properties(
                        {k: v for k, v in
                         (info.properties or {}).items()}),
                    info.server or "")
                if device is not None:
                    seen[device.id] = device

            def remove_service(self, kind: str, name: str) -> None:
                pass

            def update_service(self, kind: str, name: str) -> None:
                pass

        browser = _zc.Zeroconf()
        try:
            listener = _Listener()
            _zc.ServiceBrowser(browser, SERVICE_TYPE + ".local.",
                               listener)
            done = threading.Event()
            done.wait(timeout=float(timeout_s))
            found.extend(seen.values())
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - close is best effort
                pass
        return found

    return _scan
