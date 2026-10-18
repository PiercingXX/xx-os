"""xx-share entry point: GUI, --send FILE…, and --service headless mode.

--service runs without a display (systemd user unit) and owns the
``io.piercingxx.XXShare1`` session-bus name. --send opens the window
with files preloaded. The .desktop MimeType handler routes
"Send with xx-share" here. When the receiver already owns the bus name,
the window drives it through the bus client instead of starting a
second in-process server.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _ensure_deps() -> None:
    try:
        import xx_ui  # noqa: F401
        return
    except ImportError:
        pass
    common = Path(__file__).resolve().parent.parent.parent / "common"
    if (common / "xx_ui" / "__init__.py").exists():
        sys.path.insert(0, str(common))


_ensure_deps()

APP_ID = "io.piercingxx.XXShare"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="xx-share")
    parser.add_argument("--send", nargs="*", default=[],
                        help="files to send (opens the share window)")
    parser.add_argument("--service", action="store_true",
                        help="run the background receive service (no window)")
    return parser.parse_args(argv)


def build_service(config_dir: str | Path | None = None,
                  downloads: str | Path | None = None,
                  staging: str | Path | None = None) -> object:
    from certs import cert_fingerprint, ensure_certpair
    from config import ShareConfig
    from service import ShareService, thread_scheduler

    config = ShareConfig(config_dir) if config_dir else ShareConfig()
    state_dir = Path.home() / ".local" / "share" / "xx-share"
    state_dir.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = ensure_certpair(state_dir)
    fingerprint = cert_fingerprint(cert_path)
    return ShareService(config, cert_path, key_path, fingerprint,
                        schedule=thread_scheduler,
                        downloads=downloads, staging=staging)


class BusBackend:
    """ShareWindow backend driving the receiver over the session bus.

    Local config/history files are shared with the receiver process, so
    favorites, PIN and history stay consistent across both sides.
    """

    def __init__(self, config: object, client: object) -> None:
        import threading

        from history import HistoryStore

        self.config = config
        self.client = client
        self.history = HistoryStore()
        self.on_signal: Callable[[str, tuple], None] = lambda _n, _a: None
        self._lock = threading.Lock()
        self._pending: dict[str, Callable[[bool, str], None]] = {}
        try:
            client.subscribe(  # type: ignore[union-attr]
                lambda args: self.on_signal("TransferProgress", tuple(args)),
                lambda args: self.on_signal("IncomingRequest", tuple(args)),
                self._transfer_done)
        except Exception as error:
            logger.warning("bus subscribe failed: %s", error)

    def _transfer_done(self, args: tuple) -> None:
        transfer_id = str(args[0]) if args else ""
        ok = bool(args[1]) if len(args) > 1 else False
        message = str(args[2]) if len(args) > 2 else ""
        with self._lock:
            callback = self._pending.pop(transfer_id, None)
        try:
            self.on_signal("TransferDone", (transfer_id, ok, message))
        except Exception as error:
            logger.warning("transfer done signal failed: %s", error)
        if callback is not None:
            callback(ok, message)

    def set_visible(self, visible: bool, _addrs: list[str] | None = None) -> None:
        self.client.set_visible(bool(visible), lambda _ok, _res: None)  # type: ignore[union-attr]

    def list_peers_async(self, callback: Callable[[bool, object], None]) -> None:
        self.client.list_peers(callback)  # type: ignore[union-attr]

    def list_peers(self) -> list[dict]:
        return []

    def peers(self) -> list:
        return []

    def start_send_async(self, fingerprint: str, files: list[str], pin: str,
                         on_started: Callable[[bool, str], None],
                         on_done: Callable[[bool, str], None]) -> None:
        def _sent(ok: bool, result: object) -> None:
            if not ok:
                on_started(False, str(result))
                return
            transfer_id = str(result)
            with self._lock:
                self._pending[transfer_id] = on_done
            on_started(True, transfer_id)
            self._arm_timeout(transfer_id)

        self.client.send(str(fingerprint), list(files), str(pin or ""), _sent)  # type: ignore[union-attr]

    def _arm_timeout(self, transfer_id: str) -> None:
        import threading

        def _expired() -> None:
            with self._lock:
                callback = self._pending.pop(transfer_id, None)
            if callback is not None:
                callback(False, "Send timed out")

        timer = threading.Timer(150.0, _expired)
        timer.daemon = True
        timer.start()

    def start_send(self, fingerprint: str, files: list[str], pin: str = "",
                   on_done: Callable[[bool, str], None] | None = None) -> str:
        self.start_send_async(fingerprint, files, pin,
                              lambda _ok, _tid: None,
                              on_done or (lambda _ok, _msg: None))
        return ""

    def cancel_send(self, transfer_id: str) -> bool:
        try:
            self.client.cancel(str(transfer_id), lambda _ok, _res: None)  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("cancel send failed: %s", error)
            return False
        return True


def bus_receiver_running() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", ("io.piercingxx.XXShare1",)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
        return bool(reply.unpack()[0])
    except GLib.Error:
        return False


def run_service() -> int:
    from netif import allowed_bind_addrs

    service = build_service()
    addrs = allowed_bind_addrs()
    if not addrs:
        print("xx-share: no usable non-cellular interface", file=sys.stderr)
        return 1
    try:
        from gi.repository import GLib

        from share_bus import BusDispatcher, BusServer

        dispatcher = BusDispatcher(service)
        bus_server = BusServer(dispatcher)
        previous = service.on_signal  # type: ignore[union-attr]

        def chained(name: str, args: tuple) -> None:
            try:
                previous(name, args)
            except Exception as error:
                logger.warning("service signal chain failed: %s", error)
            bus_server.emit(name, args)

        service.on_signal = chained  # type: ignore[union-attr]
        bus_server.start()
    except ImportError:
        pass
    except GLib.Error as error:
        logger.warning("bus server unavailable: %s", error)
    service.set_visible(True, addrs)  # type: ignore[union-attr]
    print(f"xx-share: receiving as {service.info.alias}")  # type: ignore[union-attr]
    try:
        from gi.repository import GLib

        loop = GLib.MainLoop()
        loop.run()
    except (ImportError, KeyboardInterrupt):
        try:
            import threading

            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    service.stop()  # type: ignore[union-attr]
    return 0


def run_gui(initial_files: list[str]) -> int:
    import threading

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio

    service = build_service()
    existing = [item for item in initial_files if Path(item).is_file()]
    windows: list[object] = []

    def probe_receiver() -> None:
        from gi.repository import GLib

        if not windows or not bus_receiver_running():
            return

        def switch() -> bool:
            try:
                from share_bus import BusClient

                backend = BusBackend(service.config, BusClient())  # type: ignore[union-attr]
                windows[0].set_backend(backend)  # type: ignore[union-attr]
            except Exception as error:
                logger.warning("bus backend switch failed: %s", error)
            return False

        GLib.idle_add(switch)

    def on_activate(app: object) -> None:
        from ui import ShareWindow

        window = ShareWindow(app, service, existing)
        windows.append(window)
        window.present()
        threading.Thread(target=probe_receiver, daemon=True).start()

    app = Adw.Application(application_id=APP_ID,
                           flags=Gio.ApplicationFlags.HANDLES_OPEN)
    app.connect("activate", on_activate)
    return app.run([])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.service:
        return run_service()
    return run_gui(args.send)


if __name__ == "__main__":
    sys.exit(main())
