"""Broadway smoke test: real window over Android-style millisecond ids.

Seeds a temp ``XDG_DATA_HOME`` store through the real :class:`Store` API
(inbound + outbound + quarantined + unsent/held + long + MMS ``[Photo]``
rows, all with ids above the 32-bit ``gint`` range), builds the real
:class:`TxxtWindow` list page, opens a thread page, opens in-thread
search, and pumps the main loop ~1 s per step. Fails on any Python
exception inside GLib callbacks and on Gtk CRITICAL/WARNING logs.

The window runs in a child process: the Broadway backend is selected
from ``GDK_BACKEND``/``BROADWAY_DISPLAY`` when GTK first loads, so it
must be set before this module is ever imported. Skips only when
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

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

import pytest  # noqa: E402
from gi.repository import GLib  # noqa: E402

CID_A = 1792142430343
CID_B = 1792142431343
CID_Q = 1792142432343
MID_A_IN = 1792142430401
MID_A_OUT = 1792142430402
MID_A_HELD = 1792142430403
MID_A_LONG = 1792142430404
MID_A_PHOTO = 1792142430405
MID_B_IN = 1792142431401
MID_Q_IN = 1792142432401
LONG_BODY = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 40).strip()


def test_android_style_ids_fit_gobject_and_dbus():
    from txxt.conversations import ConversationItem
    from txxt.thread_page import MessageItem

    assert ConversationItem(conversation_id=CID_A).conversation_id == CID_A
    assert MessageItem(message_id=MID_A_IN).message_id == MID_A_IN
    assert GLib.Variant("(x)", (CID_A,)).unpack() == (CID_A,)


def _seed(store):
    from txxt.store import ConversationRow, MessageRow

    now = int(time.time() * 1000)
    store.upsert_conversation(ConversationRow(id=CID_A, participant_addresses="+15551230001"))
    store.upsert_conversation(ConversationRow(id=CID_B, participant_addresses="+15551230002"))
    store.upsert_conversation(ConversationRow(
        id=CID_Q, participant_addresses="+15559990001", is_quarantined=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_A_IN, conversation_id=CID_A, direction="INCOMING", transport="SMS",
        body="hello inbound", timestamp_millis=now - 60000,
        sender_address="+15551230001", is_read=False, sent=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_A_OUT, conversation_id=CID_A, direction="OUTGOING", transport="SMS",
        body="reply outbound", timestamp_millis=now - 30000,
        is_read=True, sent=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_A_HELD, conversation_id=CID_A, direction="OUTGOING", transport="SMS",
        body="held unsent, please retry", timestamp_millis=now - 20 * 60 * 1000,
        is_read=True, sent=False,
    ))
    store.upsert_message(MessageRow(
        id=MID_A_LONG, conversation_id=CID_A, direction="INCOMING", transport="SMS",
        body=LONG_BODY, timestamp_millis=now - 20000,
        sender_address="+15551230001", is_read=False, sent=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_A_PHOTO, conversation_id=CID_A, direction="INCOMING", transport="MMS",
        body="[Photo]", timestamp_millis=now - 10000,
        sender_address="+15551230001", is_read=False, sent=True,
        content_location="http://example.com/mms/1",
    ))
    store.upsert_message(MessageRow(
        id=MID_B_IN, conversation_id=CID_B, direction="INCOMING", transport="SMS",
        body="second thread hi", timestamp_millis=now - 50000,
        sender_address="+15551230002", is_read=False, sent=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_Q_IN, conversation_id=CID_Q, direction="INCOMING", transport="SMS",
        body="spammy hello", timestamp_millis=now - 40000,
        sender_address="+15559990001", is_read=False, sent=True,
    ))


def _child_main():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from txxt.main import TxxtWindow

    try:
        from xx_testing.broadway_wait import iterate_until as _wait_until
    except ImportError:  # pragma: no cover
        _wait_until = None

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

    def pump_until(predicate, timeout_s: float = 10.0,
                   message: str = "condition never held") -> None:
        if _wait_until is None:  # pragma: no cover
            pump(timeout_s)
            assert predicate(), message
            return
        assert _wait_until(predicate,
                           timeout_s=timeout_s) is True, message
        assert errors == [], f"exceptions during main-loop pump: {errors!r}"

    def focus_inside_entry(window):
        widget = window.get_focus()
        while widget is not None:
            if isinstance(widget, Gtk.Editable):
                return True
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None
        return False

    def list_views(window):
        found = []

        def walk(widget):
            if isinstance(widget, Gtk.ListView):
                found.append(widget)
            child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(window)
        return found

    def assert_list_rules(window):
        views = list_views(window)
        assert views, "no Gtk.ListView found under the window"
        for view in views:
            assert view.get_single_click_activate() is True
            assert isinstance(view.get_model(), Gtk.NoSelection), type(view.get_model())

    window = None
    app = None
    try:
        app = Adw.Application(
            application_id="io.piercingxx.TxxT.BroadwaySmoke",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        app.register(None)
        window = TxxtWindow(app)
        window.present()
        pump_until(
            lambda: window._list_page._model.get_n_items() == 2,
            message="txxt smoke: conversation list never showed 2 rows")
        assert Gdk.Display.get_default() is not None
        assert window._list_page._model.get_n_items() == 2
        for index in range(window._list_page._model.get_n_items()):
            item = window._list_page._model.get_item(index)
            assert int(item.conversation_id) > 2_147_483_647
        assert focus_inside_entry(window) is False
        assert_list_rules(window)
        window.open_thread(CID_A)
        pump_until(lambda: bool(window._thread_pages),
                   message="txxt smoke: thread page never opened")
        assert window._thread_pages, "thread page never opened"
        thread = window._thread_pages[-1]
        from txxt.thread_page import MessageItem, SeparatorItem
        rows = [
            thread._model.get_item(i)
            for i in range(thread._model.get_n_items())
        ]
        messages = [row for row in rows if isinstance(row, MessageItem)]
        assert len(messages) == 5
        assert any(isinstance(row, SeparatorItem) for row in rows)
        bodies = [row.body for row in messages]
        assert any(len(body) > 500 for body in bodies)
        assert "[Photo]" in bodies
        states = [row.state for row in messages]
        assert any(state.startswith("Held") for state in states)
        assert any(row.show_retry for row in messages)
        assert_list_rules(window)
        thread._search_bar.set_search_mode(True)
        thread._search_entry.set_text("Lorem")
        pump_until(lambda: len(thread._matches) == 1,
                   message="txxt smoke: in-thread search never matched")
        assert thread._search_bar.get_search_mode() is True
        assert len(thread._matches) == 1
        window._navigation.pop()
        pump_until(lambda: focus_inside_entry(window) is False,
                   message="txxt smoke: entry kept focus after pop")
        assert focus_inside_entry(window) is False
        assert_list_rules(window)
        assert errors == []
        assert gtk_logs == [], f"Gtk warnings during smoke: {gtk_logs!r}"
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


def test_broadway_launch_thread_search_and_focus():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    from txxt.main import ensure_data_dir
    from txxt.store import Store

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tempfile.TemporaryDirectory(prefix="txxt-broadway")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = tmp.name
    proc = None
    try:
        seed_store = Store(str(ensure_data_dir() / "txxt.db"))
        try:
            _seed(seed_store)
        finally:
            seed_store.close()
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
        common_root = os.path.join(os.path.dirname(repo_root), "common")
        env["PYTHONPATH"] = os.path.join(repo_root, "src") + os.pathsep + common_root + os.pathsep + env.get(
            "PYTHONPATH", "")
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
        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg
        tmp.cleanup()


if __name__ == "__main__" and "--child" in sys.argv:
    _child_main()
