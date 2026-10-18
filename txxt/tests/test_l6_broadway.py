"""Broadway L6 smoke: settings page, blocking + quarantine pages.

Seeds a temp store with realistic data (Android-style millisecond ids,
a blocked number, keyword/phrase terms, one quarantined thread), builds
the real :class:`TxxtWindow`, opens the settings page (which carries no
appearance controls — the shell theme is the single theme source), the
blocking page and the quarantine page, routes ``sms:`` links onto the
matching or a fresh thread, and pumps the main loop without exceptions
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

CID_Q = 1792142432343
MID_Q_IN = 1792142432401
CID_A = 1792142430343
MID_A_IN = 1792142430401


def _seed(store):
    from txxt.store import ConversationRow, MessageRow

    now = int(time.time() * 1000)
    store.upsert_conversation(ConversationRow(
        id=CID_A, participant_addresses="+15551230001",
    ))
    store.upsert_conversation(ConversationRow(
        id=CID_Q, participant_addresses="+15559990001", is_quarantined=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_A_IN, conversation_id=CID_A, direction="INCOMING",
        transport="SMS", body="hello inbound", timestamp_millis=now - 60000,
        sender_address="+15551230001", is_read=False, sent=True,
    ))
    store.upsert_message(MessageRow(
        id=MID_Q_IN, conversation_id=CID_Q, direction="INCOMING",
        transport="SMS", body="spammy hello", timestamp_millis=now - 40000,
        sender_address="+15559990001", is_read=False, sent=True,
    ))
    store.blocklist_add("sender", "+15550001111")
    store.blocklist_add("keyword", "loan")
    store.blocklist_add("phrase", "act now")


def _child_main():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gio, GLib, Gtk

    from txxt.main import TxxtWindow

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

    def focus_inside_entry(window):
        widget = window.get_focus()
        while widget is not None:
            if isinstance(widget, Gtk.Editable):
                return True
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None
        return False

    def _label_texts(widget):
        texts = []
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                texts.append(child.get_text())
            texts.extend(_label_texts(child))
            child = child.get_next_sibling()
        return texts

    window = None
    app = None
    try:
        app = Adw.Application(
            application_id="io.piercingxx.TxxT.L6Smoke",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        app.register(None)
        window = TxxtWindow(app)
        window.present()
        pump_until(
            lambda: window._list_page._quarantine_banner.get_visible()
            is True,
            message="l6 smoke: quarantine banner never appeared")
        assert window._list_page._quarantine_banner.get_visible() is True
        assert window._list_page._banner_label.get_text() == "1 held for review"
        assert focus_inside_entry(window) is False

        page = window.open_settings()
        pump_until(lambda: window._navigation.get_visible_page() is page,
                   message="l6 smoke: settings page never shown")
        page.refresh_subtitles()
        assert page.blocking_subtitle() == "1 number, 2 terms"
        assert page.quarantine_subtitle() == "1 held"
        assert not hasattr(page, "_auto_sync_row"), \
            "appearance lives in the shell theme, not in TxxT settings"
        assert "Follow the shell theme" not in _label_texts(page)
        window._navigation.pop()
        pump_until(lambda: window._navigation.get_visible_page()
                   is window._list_page,
                   message="l6 smoke: never popped back to the list")

        from txxt.list_presenter import parse_sms_uri
        from txxt.thread_page import ThreadPage

        known = parse_sms_uri("sms:+15551230001?body=hello%20there")
        assert known is not None
        window.open_sms_target(known)
        pump_until(
            lambda: isinstance(window._navigation.get_visible_page(),
                               ThreadPage)
            and window._navigation.get_visible_page().conversation_id
            == CID_A,
            message="l6 smoke: sms: never opened the existing thread")
        top = window._navigation.get_visible_page()
        assert isinstance(top, ThreadPage)
        assert top.conversation_id == CID_A, \
            "sms: opens the existing thread on canonical match"
        assert top._entry.get_text() == "hello there"
        fresh = parse_sms_uri("sms:+15550009999?body=ping")
        assert fresh is not None
        window.open_sms_target(fresh)
        pump_until(
            lambda: isinstance(window._navigation.get_visible_page(),
                               ThreadPage)
            and window._navigation.get_visible_page().conversation_id
            != CID_A,
            message="l6 smoke: sms: never started a new conversation")
        top = window._navigation.get_visible_page()
        assert isinstance(top, ThreadPage)
        assert top.conversation_id != CID_A, \
            "sms: to an unknown number starts a new conversation"
        assert top._entry.get_text() == "ping"
        window._navigation.pop_to_page(window._list_page)
        pump_until(lambda: window._navigation.get_visible_page()
                   is window._list_page,
                   message="l6 smoke: never returned to the list")

        import json as _json

        from xx_ui import theme as _xx_theme

        config_path = os.path.join(
            os.environ["XDG_CONFIG_HOME"], "xx-wm", "config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as handle:
            _json.dump({"theme": "paper"}, handle)
        pump(1.5)
        assert _xx_theme.load().key == "paper"
        window._navigation.pop()
        pump_until(lambda: window._navigation.get_visible_page()
                   is window._list_page,
                   message="l6 smoke: never returned to the list")

        window.open_blocking()
        pump_until(
            lambda: window._blocking_page is not None
            and window._blocking_page._lists["sender"].get_row_at_index(
                0) is not None,
            message="l6 smoke: blocking page never loaded its rows")
        assert window._blocking_page is not None
        assert window._blocking_page._lists["sender"].get_row_at_index(0) is not None
        assert window._blocking_page._lists["keyword"].get_row_at_index(0) is not None
        assert window._blocking_page._lists["phrase"].get_row_at_index(0) is not None
        assert focus_inside_entry(window) is False

        window.open_quarantine()
        pump_until(
            lambda: window._quarantine_page is not None
            and window._quarantine_page._model.get_n_items() == 1,
            message="l6 smoke: quarantine page never showed its row")
        assert window._quarantine_page is not None
        assert window._quarantine_page._model.get_n_items() == 1
        item = window._quarantine_page._model.get_item(0)
        assert int(item.conversation_id) == CID_Q > 2_147_483_647
        assert item.title.strip() != ""
        assert focus_inside_entry(window) is False

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


def test_broadway_l6_settings_blocking_quarantine():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    from txxt.main import ensure_data_dir
    from txxt.store import Store

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tempfile.TemporaryDirectory(prefix="txxt-l6-broadway")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    old_config = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_DATA_HOME"] = tmp.name
    os.environ["XDG_CONFIG_HOME"] = tmp.name
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
        env["XDG_CONFIG_HOME"] = tmp.name
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
        if old_config is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old_config
        tmp.cleanup()


if __name__ == "__main__" and "--child" in sys.argv:
    _child_main()
