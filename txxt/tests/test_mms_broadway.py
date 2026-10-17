"""Broadway MMS smoke: thread with photos, attach pipeline, tap-to-fetch.

Seeds a temp store with an MMS thread carrying one downloaded photo and
one ``[Photo]``-until-tapped row (Android-style millisecond ids), builds
the real :class:`TxxtWindow` + thread page under Broadway, stages a photo
through the real attach path, and pumps the main loop without exceptions
or Gtk warnings. Skips only when ``gtk4-broadwayd`` is missing.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

import pytest  # noqa: E402

CID_MMS = 1792142434343
MID_WITH_PHOTO = 1792142434401
MID_TAPPED = 1792142434402


def _seed(store, photo_bytes: bytes):
    from txxt.store import ConversationRow, MessageRow

    now = int(time.time() * 1000)
    store.upsert_conversation(ConversationRow(
        id=CID_MMS, participant_addresses="+15551230009"))
    store.upsert_message(MessageRow(
        id=MID_WITH_PHOTO, conversation_id=CID_MMS, direction="INCOMING",
        transport="MMS", body="[Photo]", timestamp_millis=now - 10000,
        sender_address="+15551230009", is_read=False, sent=True,
        content_location="http://example.com/mms/9",
        media_path="", pending_delete=False,
    ))
    store.upsert_message(MessageRow(
        id=MID_TAPPED, conversation_id=CID_MMS, direction="INCOMING",
        transport="MMS", body="[Photo]", timestamp_millis=now - 5000,
        sender_address="+15551230009", is_read=False, sent=True,
        content_location="http://example.com/mms/10",
    ))
    row = store.get_message(MID_WITH_PHOTO)
    assert row is not None
    _ = photo_bytes


def _child_main():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from txxt.main import TxxtWindow
    from txxt.thread_page import thumbnail_pixbuf

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

    window = None
    app = None
    try:
        app = Adw.Application(
            application_id="io.piercingxx.TxxT.MmsSmoke",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        app.register(None)
        window = TxxtWindow(app)
        window.present()
        pump_until(lambda: Gdk.Display.get_default() is not None
                   and window._list_page._model.get_n_items() > 0,
                   message="mms smoke: conversation list never loaded")
        assert Gdk.Display.get_default() is not None
        window.open_thread(CID_MMS)
        pump_until(lambda: bool(window._thread_pages),
                   message="mms smoke: mms thread page never opened")
        assert window._thread_pages, "mms thread page never opened"
        thread = window._thread_pages[-1]
        from txxt.thread_page import MessageItem
        photos = [
            thread._model.get_item(i)
            for i in range(thread._model.get_n_items())
            if isinstance(thread._model.get_item(i), MessageItem)
        ]
        assert len(photos) == 2
        assert thread._attach_button is not None
        assert thread._staged_row is not None
        assert thread._staged_row.get_visible() is False
        assert all(item.is_photo for item in photos)
        assert sum(1 for item in photos if item.media_path) == 1
        assert sum(1 for item in photos if not item.media_path) == 1
        with_media = next(
            item for item in photos if item.media_path)
        pixbuf = thumbnail_pixbuf(with_media.media_path)
        assert pixbuf is not None, "downloaded photo must thumbnail"
        assert max(pixbuf.get_width(), pixbuf.get_height()) <= 320

        staged_source = os.environ.get("TXXT_MMS_STAGE_SRC", "")
        assert staged_source and os.path.isfile(staged_source), \
            "stage fixture missing"
        thread._stage_photo(staged_source)
        pump_until(lambda: thread._staged_path is not None
                   and thread._staged_row.get_visible() is True,
                   message="mms smoke: staged photo never appeared")
        assert thread._staged_path is not None
        assert thread._staged_row.get_visible() is True
        thread._on_staged_clear()
        assert thread._staged_row.get_visible() is False

        views = []

        def walk(widget):
            if isinstance(widget, Gtk.ListView):
                views.append(widget)
            child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(window)
        assert views, "no Gtk.ListView found under the window"
        for view in views:
            assert view.get_single_click_activate() is True
            assert isinstance(view.get_model(), Gtk.NoSelection), type(view.get_model())
        assert errors == []
        assert gtk_logs == [], f"Gtk warnings during mms smoke: {gtk_logs!r}"
    finally:
        GLib.log_remove_handler("Gtk", handler_id)
        sys.excepthook = old_excepthook
        threading.excepthook = old_threadhook
        if window is not None:
            try:
                window._store.close()
            except Exception:
                pass
            try:
                window.close()
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
    print("MMS-SMOKE-OK", flush=True)


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


def _wait_broadway_port(port: int, proc) -> bool:
    """True once broadwayd listens on ``port`` with ``proc`` alive."""
    from xx_testing.broadway_wait import poll_until

    def _listening() -> bool:
        if proc.poll() is not None:
            return True
        try:
            with socket.create_connection(("127.0.0.1", port),
                                          timeout=1):
                return True
        except OSError:
            return False

    if not poll_until(_listening, timeout_s=15.0, interval_s=0.1):
        return False
    return proc.poll() is None


def _real_jpeg_bytes():
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 48)
    pixbuf.fill(0x3366CCFF)
    ok, raw = pixbuf.save_to_bufferv("jpeg", ["quality"], ["85"])
    assert ok and raw, "jpeg encoder unavailable"
    return bytes(raw)


def test_mms_broadway_attach_and_thumbnails():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    from txxt.main import ensure_data_dir
    from txxt.store import Store

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tempfile.TemporaryDirectory(prefix="txxt-mms-broadway")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = tmp.name
    proc = None
    try:
        photo_bytes = _real_jpeg_bytes()
        seed_store = Store(str(ensure_data_dir() / "txxt.db"))
        try:
            _seed(seed_store, photo_bytes)
            photo_dir = (
                __import__("pathlib").Path(tmp.name) / "txxt" / "mms"
                / str(MID_WITH_PHOTO))
            photo_dir.mkdir(parents=True, exist_ok=True)
            (photo_dir / "photo-0.jpg").write_bytes(photo_bytes)
            seed_store.set_media_path(
                MID_WITH_PHOTO, str(photo_dir / "photo-0.jpg"))
        finally:
            seed_store.close()
        stage_src = os.path.join(tmp.name, "stage-me.jpg")
        with open(stage_src, "wb") as handle:
            handle.write(photo_bytes)
        number = None
        for _ in range(5):
            candidate = _free_display()
            if candidate is None:
                break
            proc = subprocess.Popen(
                ["gtk4-broadwayd", f":{candidate}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if _wait_broadway_port(8080 + candidate, proc):
                number = candidate
                break
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
            proc = None
        if number is None:
            pytest.fail("could not start gtk4-broadwayd on any display 5..99")
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{number}"
        env["XDG_DATA_HOME"] = tmp.name
        env["TXXT_MMS_STAGE_SRC"] = stage_src
        common_root = os.path.join(os.path.dirname(repo_root), "common")
        env["PYTHONPATH"] = os.path.join(repo_root, "src") + os.pathsep + common_root + os.pathsep + env.get(
            "PYTHONPATH", "")
        child = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child"],
            env=env, capture_output=True, text=True, timeout=120,
        )
        assert child.returncode == 0, (
            f"mms broadway child failed (exit {child.returncode}):\n"
            f"--- stdout ---\n{child.stdout}\n--- stderr ---\n{child.stderr}")
        assert "MMS-SMOKE-OK" in child.stdout
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg
        tmp.cleanup()


if __name__ == "__main__" and "--child" in sys.argv:
    _child_main()
