"""Broadway smoke test: the real ShareWindow on a seeded temp HOME.

Seeds through the real store APIs (ShareConfig, HistoryStore,
favorites, demo files), builds the real window, flips through every
tab, selects a peer, opens the receive prompt, replays a transfer
progress burst, switches the live theme, and pumps the main loop.
Fails on any Python exception inside GLib callbacks and on Gtk
CRITICAL/WARNING logs.

The window runs in a child process: the Broadway backend is selected
from ``GDK_BACKEND``/``BROADWAY_DISPLAY`` when GTK first loads, so it
must be set before GTK is ever imported. Skips only when
``gtk4-broadwayd`` is missing.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

PEERS = [
    {"alias": "Mara's Pixel", "fingerprint": "9f2ac41107be4d5aa91c2f83dd40e6b1",
     "addr": "192.168.1.21", "device_type": "mobile", "port": 53317},
    {"alias": "Living-Room TV", "fingerprint": "41bd9c0e88f04a2d9b77aa10c3f55e90",
     "addr": "192.168.1.34", "device_type": "desktop", "port": 53317},
]


def _seed(home: str) -> list[str]:
    from config import ShareConfig
    from favorites import set_favorite
    from history import HistoryStore

    config = ShareConfig(os.path.join(home, ".config", "xx-share"))
    config.data["alias"] = "My Phone"
    config.data["require_pin"] = "2468"
    config.save()
    set_favorite(config, PEERS[0]["fingerprint"], True)

    store = HistoryStore()
    store.record("Mara's Pixel", PEERS[0]["fingerprint"],
                 "IMG_20240612_184410.jpg", 2412388, "/tmp/IMG.jpg")
    store.record("Living-Room TV", PEERS[1]["fingerprint"],
                 "movie-night.mkv", 210763776, "/tmp/movie.mkv")

    demo_dir = os.path.join(home, "demo-files")
    os.makedirs(demo_dir, exist_ok=True)
    files = []
    for name, size in (("hello.jpg", 1024), ("notes.pdf", 2048)):
        path = os.path.join(demo_dir, name)
        with open(path, "wb") as handle:
            handle.truncate(size)
        files.append(path)
    return files


def _child_main():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    import xx_ui
    from main import build_service
    from ui import ShareWindow

    errors: list = []
    gtk_logs: list = []
    old_excepthook = sys.excepthook
    old_threadhook = threading.excepthook
    sys.excepthook = lambda t, v, tb: errors.append(f"{t.__name__}: {v}")
    threading.excepthook = lambda args: errors.append(
        f"{type(args.exc_value).__name__}: {args.exc_value}")

    def _on_log(domain, level, message, _data):
        gtk_logs.append(f"{domain} {level}: {message}")

    handler_id = GLib.log_set_handler(
        "Gtk",
        GLib.LogLevelFlags.LEVEL_CRITICAL
        | GLib.LogLevelFlags.LEVEL_WARNING
        | GLib.LogLevelFlags.LEVEL_ERROR,
        _on_log,
    )

    def pump(seconds):
        context = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.02)
            context.iteration(False)
        assert errors == [], f"exceptions during main-loop pump: {errors!r}"

    try:
        from xx_testing.broadway_wait import iterate_until as _wait_until
    except ImportError:  # pragma: no cover
        _wait_until = None

    def pump_until(predicate, timeout_s: float = 10.0,
                   message: str = "condition never held") -> None:
        if _wait_until is None:  # pragma: no cover
            pump(timeout_s)
            assert predicate(), message
            return
        assert _wait_until(predicate,
                           timeout_s=timeout_s) is True, message
        assert errors == [], f"exceptions during main-loop pump: {errors!r}"

    def rows(box):
        found = []
        child = box.get_first_child()
        while child is not None:
            found.append(child)
            child = child.get_next_sibling()
        return found

    def focus_inside_entry(window):
        widget = window.get_focus()
        while widget is not None:
            if isinstance(widget, Gtk.Editable):
                return True
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None
        return False

    window = None
    app = None
    try:
        app = Adw.Application(
            application_id="io.piercingxx.XXShare.BroadwaySmoke",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        app.register(None)
        service = build_service()
        files = [os.path.join(os.environ["HOME"], "demo-files", name)
                 for name in ("hello.jpg", "notes.pdf")]
        window = ShareWindow(app, service, files)
        window.present()
        pump_until(
            lambda: (window.window.get_width(),
                     window.window.get_height()) == (360, 720),
            message="share smoke: window never sized 360x720")
        assert Gdk.Display.get_default() is not None
        assert window.window.get_width() == 360
        assert window.window.get_height() == 720
        for name in ("share", "history", "favorites", "settings"):
            assert window.stack.get_child_by_name(name) is not None, name

        def _contains(root, target) -> bool:
            if root is target:
                return True
            child = root.get_first_child()
            while child is not None:
                if _contains(child, target):
                    return True
                child = child.get_next_sibling()
            return False

        share_kids = rows(window.stack.get_child_by_name("share"))
        assert len(share_kids) == 3, "share page keeps a fixed action bar"
        assert _contains(share_kids[-1], window.send_button)
        assert _contains(share_kids[-1], window.cancel_button)
        assert _contains(share_kids[-1], window.progress_bar)
        assert _contains(share_kids[-1], window.status_label)
        assert not _contains(share_kids[1], window.send_button), \
            "Send stays pinned above the tabs, out of the scroll body"
        for header in (window.share_header, window.history_header,
                       window.favorites_header):
            top = header.get_first_child()
            assert "xx-page-top" in top.get_css_classes(), \
                "every PageHeader shares one title-row height"
        assert focus_inside_entry(window.window) is False
        assert len(rows(window.file_box)) == 2
        assert len(rows(window.history_box)) == 2
        window._peers = [dict(peer) for peer in PEERS]
        window._render_peers()
        assert len(rows(window.device_box)) == 2
        window._select_peer(PEERS[0]["fingerprint"])
        assert window.selected_fp == PEERS[0]["fingerprint"]
        window.refresh_favorites()
        assert len(rows(window.favorites_box)) == 1
        for name in ("history", "favorites", "settings", "share"):
            window._show_tab(name)
            pump_until(
                lambda _name=name: window.stack.get_visible_child_name()
                == _name,
                message=f"share smoke: tab {name} never shown")
            assert window.stack.get_visible_child_name() == name
            assert window.window.get_width() == 360
            assert window.window.get_height() == 720
        assert focus_inside_entry(window.window) is False
        window.show_incoming("sess-1", "Mara's Pixel",
                             ["IMG_20240612_184410.jpg"], 2412388)
        pump_until(lambda: window._incoming_dialog is not None,
                   message="share smoke: incoming dialog never opened")
        assert window._incoming_dialog is not None
        window._resolve_incoming("sess-1", "xx-share-decline")
        pump_until(lambda: window._incoming_dialog is None,
                   message="share smoke: incoming dialog never closed")
        assert window._incoming_dialog is None
        window.current_transfer = "tx-demo"
        window._idle_bus_progress("tx-demo", 512, 1024)
        pump(0.3)
        window._idle_done("Sent to Mara's Pixel", True)
        pump(0.3)
        assert window.current_transfer is None
        config_path = os.path.join(
            os.environ.get("XDG_CONFIG_HOME",
                           os.path.join(os.environ["HOME"], ".config")),
            "xx-wm", "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write('{"theme": "paper"}')
        xx_ui.reload()
        pump(0.5)
        assert errors == []
        assert gtk_logs == [], f"Gtk warnings during smoke: {gtk_logs!r}"
    finally:
        GLib.log_remove_handler("Gtk", handler_id)
        sys.excepthook = old_excepthook
        threading.excepthook = old_threadhook
        if window is not None:
            try:
                window._close_incoming()
            except Exception:
                pass
            try:
                window.window.close()
            except Exception:
                pass
        if app is not None:
            try:
                app.quit()
            except Exception:
                pass
        context = GLib.MainContext.default()
        for _ in range(20):
            if not context.pending():
                break
            context.iteration(False)
    print("SMOKE-OK", flush=True)


def _free_display():
    for number in range(5, 100):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 8080 + number))
        except OSError:
            probe.close()
            continue
        probe.close()
        return number
    return None


def test_broadway_real_window():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tempfile.TemporaryDirectory(prefix="xx-share-broadway")
    old_home = os.environ.get("HOME")
    old_xdg = os.environ.get("XDG_CONFIG_HOME")
    home = os.path.join(tmp.name, "home")
    os.makedirs(os.path.join(home, ".config", "xx-wm"), exist_ok=True)
    with open(os.path.join(home, ".config", "xx-wm", "config.json"),
              "w", encoding="utf-8") as handle:
        handle.write('{"theme": "amoled"}')
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    proc = None
    try:
        sys.path.insert(0, os.path.join(repo_root, "src"))
        sys.path.insert(0, os.path.join(repo_root, "..", "common"))
        try:
            _seed(home)
        finally:
            sys.path.remove(os.path.join(repo_root, "src"))
            sys.path.remove(os.path.join(repo_root, "..", "common"))
        number = None
        for _ in range(5):
            candidate = _free_display()
            if candidate is None:
                break
            proc = subprocess.Popen(
                ["gtk4-broadwayd", f":{candidate}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)
            if proc.poll() is None:
                number = candidate
                break
            proc = None
        if number is None:
            pytest.fail("could not start gtk4-broadwayd on any display 5..99")
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{number}"
        env["GSK_RENDERER"] = "cairo"
        env["PYTHONPATH"] = os.pathsep.join([
            os.path.join(repo_root, "src"),
            os.path.join(repo_root, "..", "common"),
            env.get("PYTHONPATH", "")])
        child = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child"],
            env=env, capture_output=True, text=True, timeout=120,
        )
        assert child.returncode == 0, (
            f"broadway child failed (exit {child.returncode}):\n"
            f"--- stdout ---\n{child.stdout}\n--- stderr ---\n{child.stderr}")
        assert "SMOKE-OK" in child.stdout
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old_xdg
        tmp.cleanup()


if __name__ == "__main__" and "--child" in sys.argv:
    _child_main()
