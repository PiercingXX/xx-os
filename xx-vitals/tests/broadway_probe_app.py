"""Fresh-interpreter driver for the Broadway smoke test.

Run in a subprocess with GDK_BACKEND=broadway, BROADWAY_DISPLAY and
XDG_RUNTIME_DIR already pointing at a live gtk4-broadwayd (see
test_broadway_profile.py). Builds the REAL production window over a seeded
database, pumps the main loop, then prints LABEL:/FOCUS:/CRITICAL: lines and
exits 0 only when every expectation holds. A fresh interpreter is deliberate:
it mirrors a real launch and is immune to whatever the test runner imported
first (importing Gtk/Adw headless first can wedge later display opens).
"""

from __future__ import annotations

import sys
import threading


def main(argv: list[str]) -> int:
    db_path = argv[1]
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from xx_vitals import prefs as prefs_module
    from xx_vitals.store import entries
    from xx_vitals.store.db import Activity, DailyMetric, Database, SyncState
    from xx_vitals.sync import auth as auth_module
    from xx_vitals.ui import shell

    if Gtk.init_check() is not True or Gdk.Display.get_default() is None:
        print("FAIL: no Broadway display")
        return 1

    errors: list = []
    criticals: list = []
    labels: list[str] = []
    focus_in_entry = False
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    sys.excepthook = lambda exc_type, value, _tb: errors.append(repr(value))
    threading.excepthook = lambda args: errors.append(repr(args.exc_value))

    def log_handler(_domain, level, message, _data) -> None:
        if level & (GLib.LogLevelFlags.LEVEL_CRITICAL
                    | GLib.LogLevelFlags.LEVEL_ERROR):
            criticals.append(str(message))

    levels = (GLib.LogLevelFlags.LEVEL_CRITICAL
              | GLib.LogLevelFlags.LEVEL_ERROR)
    handler_ids = [
        GLib.log_set_handler(domain, levels, log_handler, None)
        for domain in ("Gtk", "Gdk", "Adwaita", "GLib", "Gio")
    ]
    try:
        measured_at_ms = 1785574800000
        db = Database(db_path)
        db.init()
        db.upsert_weight(entries.WeightSample(
            source_id="manual", measured_at=measured_at_ms, weight_kg=80.0))
        db.upsert_weight(entries.WeightSample(
            source_id="manual", measured_at=measured_at_ms - 86400000,
            weight_kg=79.5))
        db.upsert_daily(DailyMetric(
            source_id="manual", date="2026-08-01", steps=9000,
            derived_mask=0))
        db.insert_activity(Activity(
            source_id="manual", type="walk",
            started_at=measured_at_ms - 3600000, ended_at=measured_at_ms))
        db.pending_insert(entries.PendingWrite(
            kind="weight",
            payload_json=entries.weight_payload(
                "manual", measured_at_ms, 80.0),
            created_at=measured_at_ms))
        db.pending_insert(entries.PendingWrite(
            kind="daily",
            payload_json=entries.daily_payload(DailyMetric(
                source_id="manual", date="2026-08-01", steps=9000,
                derived_mask=0)),
            created_at=measured_at_ms))
        db.sync_upsert(SyncState(
            id=1, last_pull_at=measured_at_ms,
            last_push_at=measured_at_ms - 1000,
            last_pull_iso="2026-08-01T09:00:00+00:00"))
        cfg = prefs_module.AppConfig(
            server_url="http://127.0.0.1:9",
            xxchat_url="http://127.0.0.1:9")
        secrets = auth_module.MemorySecretBackend()
        secrets.store_token("http://127.0.0.1:9", "tok")

        app = Adw.Application(
            application_id="io.piercingxx.XXVitals",
            flags=Gio.ApplicationFlags.NON_UNIQUE)
        if app.register(None) is not True:
            print("FAIL: app registration failed")
            return 1
        window = shell.build_window(app, db, cfg, secrets)
        window.present()

        try:
            from xx_testing.broadway_wait import pump_until as _wait_until
        except ImportError:  # pragma: no cover
            _wait_until = None

        def _labels_settled() -> bool:
            try:
                texts = _label_texts(window)
                return ("Queue: 2 queued" in texts
                        and any(line.startswith("Last pull: 2026-")
                                for line in texts))
            except Exception:
                return False

        if _wait_until is not None:
            if not _wait_until(_labels_settled, timeout_s=10.0):
                errors.append("vitals probe: labels never settled")
        else:  # pragma: no cover
            loop = GLib.MainLoop()
            GLib.timeout_add(3000, lambda: (loop.quit(), False)[1])
            loop.run()

        labels = _label_texts(window)
        for text in labels:
            print(f"LABEL:{text}")
        focus = window.get_focus()
        focus_name = type(focus).__name__ if focus is not None else "None"
        print(f"FOCUS:{focus_name}")
        focus_in_entry = "Entry" in focus_name
        window.destroy()
    finally:
        for domain, handler_id in zip(
                ("Gtk", "Gdk", "Adwaita", "GLib", "Gio"), handler_ids):
            GLib.log_remove_handler(domain, handler_id)
        sys.excepthook = old_hook
        threading.excepthook = old_thread_hook

    for message in criticals:
        print(f"CRITICAL:{message}")
    for message in errors:
        print(f"ERROR:{message}")
    failed = bool(criticals or errors or focus_in_entry)
    if focus_in_entry:
        print("FAIL: a text entry holds focus at startup")
    for line in ("NAS host: http://127.0.0.1:9",
                 "Sign-in: signed in",
                 "Queue: 2 queued",
                 "Batch history unavailable right now"):
        if line not in labels:
            print(f"FAIL: missing line {line!r}")
            failed = True
    for prefix in ("Last pull: 2026-", "Last push: 2026-"):
        if not any(line.startswith(prefix) for line in labels):
            print(f"FAIL: missing line starting with {prefix!r}")
            failed = True
    return 1 if failed else 0


def _label_texts(widget) -> list[str]:
    from gi.repository import Gtk

    texts: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            texts.append(child.get_label())
        texts.extend(_label_texts(child))
        child = child.get_next_sibling()
    return texts


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
