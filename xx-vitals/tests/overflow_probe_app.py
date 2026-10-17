"""Fresh-interpreter driver for the 320 px overflow audit.

Builds the REAL production window requesting 320x640 (the smallest
supported phone screen), visits every tab plus both entry dialogs with
settle time, then prints per surface:
  WINDOW:<width>                      the actual toplevel width
  CONTENT_MIN:<surface>:<minimum>     negotiated content minimum width
  WIDTH:<surface>:<class>:<width>     every widget allocation
  MIN:<surface>:<class>:<min>:<text>  every widget minimum above 320
gtk4-broadwayd clamps toplevels to a 360 px minimum width (verified with
a bare window), so the 320 gate is enforced on minimums, not
allocations: a surface fits 320 iff nothing in it forces wider.
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
    from xx_vitals.ui import entry as entry_page
    from xx_vitals.ui import shell

    if Gtk.init_check() is not True or Gdk.Display.get_default() is None:
        print("FAIL: no Broadway display")
        return 1

    errors: list = []
    criticals: list = []
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
    failed = False
    try:
        measured_at_ms = 1785574800000
        db = Database(db_path)
        db.init()
        db.upsert_weight(entries.WeightSample(
            source_id="manual", measured_at=measured_at_ms, weight_kg=80.0))
        db.upsert_daily(DailyMetric(
            source_id="manual", date="2026-08-01", steps=9000,
            distance_m=6500.5, active_min=45, effort_points=60,
            calories_kcal=320.25, derived_mask=0))
        db.insert_activity(Activity(
            source_id="manual", type="walk",
            started_at=measured_at_ms - 3600000, ended_at=measured_at_ms,
            distance_m=2500.0, steps=3000))
        db.pending_insert(entries.PendingWrite(
            kind="weight",
            payload_json=entries.weight_payload("manual", measured_at_ms, 80.0),
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
        window.set_default_size(320, 640)
        window.present()

        stack = _find_stack(window)
        if stack is None:
            print("FAIL: no Gtk.Stack in window")
            return 1
        nav = getattr(window, "_vitals_nav", None)
        if nav is None:
            print("FAIL: no _vitals_nav hooks on window")
            return 1

        try:
            from xx_testing.broadway_wait import iterate_until as _wait_until
        except ImportError:  # pragma: no cover
            _wait_until = None

        def _surface_ready(root) -> bool:
            try:
                return bool(root.get_mapped())
            except Exception:
                return False

        plan: list = []
        for tab in ("home", "journal", "browse", "profile"):
            plan.append(("tab", tab))
        plan.append(("metric-detail", None))
        plan.append(("weight-dialog", None))
        plan.append(("activity-dialog", None))

        state = {"index": 0, "dialog": None}
        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(30, loop.quit)

        def run_next() -> bool:
            if state["index"] >= len(plan):
                _finish()
                return False
            kind, name = plan[state["index"]]
            state["index"] += 1
            if kind == "tab":
                nav["show"](name)
                GLib.timeout_add(400, lambda: settle(name))
            elif kind == "metric-detail":
                nav["show"]("browse")
                nav["open_metric"]("steps")
                GLib.timeout_add(400, lambda: settle("metric-detail"))
            elif kind == "weight-dialog":
                state["dialog"] = entry_page.build_weight_dialog(
                    window, db, cfg.units, measured_at_ms)
                GLib.timeout_add(400, lambda: settle("weight-dialog"))
            else:
                state["dialog"] = entry_page.build_activity_dialog(
                    window, db, measured_at_ms, cfg.height_metres, None)
                GLib.timeout_add(400, lambda: settle("activity-dialog"))
            return False

        def settle(surface: str) -> bool:
            root = (state["dialog"].get_child()
                    if state["dialog"] is not None else window.get_child())
            if _wait_until is not None:
                if not _wait_until(lambda: _surface_ready(root),
                                   timeout_s=5.0):
                    print(f"FAIL: surface {surface} never mapped")
                    _finish()
                    return False
            _dump_surface(root, surface)
            if state["dialog"] is not None:
                state["dialog"].close()
                state["dialog"] = None
            run_next()
            return False

        def _finish() -> None:
            print(f"WINDOW:{window.get_width()}", flush=True)
            window.destroy()
            loop.quit()

        GLib.timeout_add(400, run_next)
        loop.run()
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
    failed = bool(criticals or errors)
    return 1 if failed else 0


def _find_stack(widget):
    from gi.repository import Gtk

    if isinstance(widget, Gtk.Stack):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find_stack(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _dump_surface(root, surface: str) -> None:
    from gi.repository import Gtk

    try:
        minimum, _natural, _min_base, _nat_base = root.measure(
            Gtk.Orientation.HORIZONTAL, -1)
    except TypeError:
        minimum = 0
    print(f"CONTENT_MIN:{surface}:{minimum}", flush=True)
    _dump_widths(root, surface)


def _dump_widths(widget, surface: str, hscroll_ancestor: bool = False) -> None:
    from gi.repository import Gtk

    if isinstance(widget, Gtk.ScrolledWindow):
        try:
            hscroll_ancestor = (hscroll_ancestor
                                or widget.get_policy()[0] != Gtk.PolicyType.NEVER)
        except TypeError:
            pass
    width = widget.get_width()
    if width <= 0:
        pass
    else:
        text = ""
        if isinstance(widget, Gtk.Label):
            text = (widget.get_label() or "")[:40].replace("\n", " ")
        elif isinstance(widget, Gtk.Button):
            text = (widget.get_label() or "")[:40]
        print(f"WIDTH:{surface}:{type(widget).__name__}:{width}:{text}",
              flush=True)
        # Content inside a horizontal scroller scrolls by design and can
        # never widen the window; gating it would ban ChipBars outright.
        if not hscroll_ancestor:
            try:
                minimum, _natural, _min_base, _nat_base = widget.measure(
                    Gtk.Orientation.HORIZONTAL, -1)
            except TypeError:
                minimum = 0
            if minimum > 320:
                print(f"MIN:{surface}:{type(widget).__name__}:{minimum}:{text}",
                      flush=True)
    child = widget.get_first_child()
    while child is not None:
        _dump_widths(child, surface, hscroll_ancestor)
        child = child.get_next_sibling()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
