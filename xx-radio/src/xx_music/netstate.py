"""Network state for the offline cache: metered and online over D-Bus.

The player service follows NetworkManager on the system bus
(``org.freedesktop.NetworkManager``): ``Metered`` (1 yes and 3 guess-yes
count as metered) and ``Connectivity`` (anything but 1 none counts as
online). Without a NetworkManager the link is treated as unmetered and
online. State changes arrive through ``PropertiesChanged``; nothing here
blocks the main loop.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

BUS_NAME = "org.freedesktop.NetworkManager"
OBJECT_PATH = "/org/freedesktop/NetworkManager"
INTERFACE = "org.freedesktop.NetworkManager"
PROPS_INTERFACE = "org.freedesktop.DBus.Properties"

METERED_YES = 1
METERED_GUESS_YES = 3
CONNECTIVITY_NONE = 1
CONNECTIVITY_FULL = 4

PREFETCH_UNMETERED = 8
PREFETCH_METERED = 2


def is_metered(raw: Any) -> bool:
    """Values 1 (yes) and 3 (guess-yes) count as metered."""
    try:
        return int(raw) in (METERED_YES, METERED_GUESS_YES)
    except (TypeError, ValueError):
        return False


def is_online(connectivity: Any) -> bool:
    """Only an explicit none (1) counts as offline; unknown stays online."""
    try:
        return int(connectivity) != CONNECTIVITY_NONE
    except (TypeError, ValueError):
        return True


def _unwrap(value: Any) -> Any:
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


@dataclass(frozen=True)
class NetSnapshot:
    online: bool = True
    metered: bool = False
    connectivity: int = CONNECTIVITY_FULL


class NetworkMonitor:
    """Thread-safe metered/online state with change callbacks.

    Callbacks run on the thread that delivered the update (the main
    loop for D-Bus changes); they must not block.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = NetSnapshot()
        self._callbacks: list[Callable[[NetSnapshot], None]] = []

    @property
    def snapshot(self) -> NetSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def online(self) -> bool:
        return self.snapshot.online

    @property
    def metered(self) -> bool:
        return self.snapshot.metered

    @property
    def connectivity(self) -> int:
        return self.snapshot.connectivity

    @property
    def prefetch_depth(self) -> int:
        return PREFETCH_METERED if self.snapshot.metered else PREFETCH_UNMETERED

    def subscribe(self, callback: Callable[[NetSnapshot], None]
                  ) -> Callable[[], None]:
        """Call ``callback`` on every change; returns an unsubscribe fn."""
        with self._lock:
            self._callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._callbacks.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def _publish(self, snapshot: NetSnapshot) -> None:
        with self._lock:
            if snapshot == self._snapshot:
                return
            self._snapshot = snapshot
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(snapshot)
            except Exception:
                pass

    def update(self, *, metered_raw: Any = None,
               connectivity_raw: Any = None) -> NetSnapshot:
        """Fold raw NetworkManager values into the state."""
        with self._lock:
            current = self._snapshot
        metered = current.metered
        connectivity = current.connectivity
        if metered_raw is not None:
            metered = is_metered(_unwrap(metered_raw))
        if connectivity_raw is not None:
            raw = _unwrap(connectivity_raw)
            try:
                connectivity = int(raw)
            except (TypeError, ValueError):
                pass
        snapshot = NetSnapshot(online=is_online(connectivity),
                               metered=metered,
                               connectivity=connectivity)
        self._publish(snapshot)
        return snapshot

    def update_from_props(self, props: Mapping[str, Any]) -> NetSnapshot:
        """Fold a GetAll or PropertiesChanged dict into the state."""
        metered_raw = props.get("Metered", None)
        connectivity_raw = props.get("Connectivity", None)
        if metered_raw is None and connectivity_raw is None:
            return self.snapshot
        return self.update(metered_raw=metered_raw,
                           connectivity_raw=connectivity_raw)

    def set_no_service(self) -> NetSnapshot:
        """No NetworkManager here: unmetered and online."""
        snapshot = NetSnapshot(online=True, metered=False,
                               connectivity=CONNECTIVITY_FULL)
        self._publish(snapshot)
        return snapshot


class NMWatcher:
    """Keep a NetworkMonitor in sync with NetworkManager over D-Bus.

    ``connection`` is an open ``Gio.DBusConnection`` (tests hand one to
    a private bus); otherwise ``connection_fn`` opens the system bus.
    The initial read is one async GetAll; later changes arrive through
    PropertiesChanged. A missing service leaves the monitor at its
    unmetered/online default.
    """

    def __init__(self, monitor: NetworkMonitor, *,
                 connection: Any = None,
                 connection_fn: Callable[[], Any] | None = None,
                 bus_name: str = BUS_NAME,
                 object_path: str = OBJECT_PATH) -> None:
        self._monitor = monitor
        self._connection = connection
        self._connection_fn = connection_fn
        self._bus_name = bus_name
        self._object_path = object_path
        self._sub_id = 0
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            connection = self._connection or self._open_system_bus()
        except Exception:
            self._monitor.set_no_service()
            return
        self._connection = connection
        try:
            self._sub_id = connection.signal_subscribe(
                self._bus_name, PROPS_INTERFACE, "PropertiesChanged",
                self._object_path, None,
                self._signal_flags(), self._on_properties_changed)
        except Exception:
            self._sub_id = 0
        try:
            from gi.repository import Gio, GLib
            connection.call(
                self._bus_name, self._object_path, PROPS_INTERFACE,
                "GetAll", GLib.Variant("(s)", (INTERFACE,)),
                GLib.VariantType.new("(a{sv})"),
                Gio.DBusCallFlags.NONE, -1, None, self._on_get_all)
        except Exception:
            self._monitor.set_no_service()

    def stop(self) -> None:
        connection, sub_id = self._connection, self._sub_id
        self._sub_id = 0
        if connection is not None and sub_id:
            try:
                connection.signal_unsubscribe(sub_id)
            except Exception:
                pass

    @staticmethod
    def _signal_flags() -> Any:
        from gi.repository import Gio
        return Gio.DBusSignalFlags.NONE

    def _open_system_bus(self) -> Any:
        if self._connection_fn is not None:
            return self._connection_fn()
        from gi.repository import Gio
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def _on_get_all(self, connection: Any, result: Any) -> None:
        try:
            props = connection.call_finish(result).unpack()[0]
        except Exception:
            self._monitor.set_no_service()
            return
        try:
            self._monitor.update_from_props(props)
        except Exception:
            pass

    def _on_properties_changed(self, connection: Any, sender: str,
                               path: str, interface: str, signal: str,
                               params: Any, *extra: Any) -> None:
        try:
            interface_name, changed, _invalidated = params.unpack()
        except Exception:
            return
        if interface_name != INTERFACE:
            return
        try:
            self._monitor.update_from_props(changed)
        except Exception:
            pass
