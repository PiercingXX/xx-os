"""Profile tab: goals, height, units, tab toggles, sync identity, batches.

Field validation lives in core/profile_edit; sync lines in sync/status;
batch history renders through Gtk.ListView from GET /v1/batches. "Sync now"
and fabric sign-in run on worker threads; 401 surfaces as a re-login line
and 507 as a storage-full line while the queue is kept.
"""

from __future__ import annotations

from xx_vitals.core import applog
from xx_vitals.core import profile_edit
from xx_vitals.sync import auth as auth_module
from xx_vitals.sync import status as status_model


def apply_goal_edits(config, steps_raw=None, effort_raw=None, weekly_raw=None) -> dict:
    errors: dict[str, str] = {}
    if steps_raw is not None:
        value = profile_edit.parse_steps_goal(steps_raw)
        if value is None:
            errors["steps"] = "Enter 1–100000"
        else:
            config.steps_goal = value
    if effort_raw is not None:
        value = profile_edit.parse_effort_goal(effort_raw)
        if value is None:
            errors["effort"] = "Enter 1–10000"
        else:
            config.effort_goal = value
    if weekly_raw is not None:
        value = profile_edit.parse_weekly_effort_goal(weekly_raw)
        if value is None:
            errors["weekly"] = "Enter 1–100000"
        else:
            config.weekly_effort_goal = value
    return errors


def apply_height_edit(config, raw: str) -> str | None:
    metres = profile_edit.parse_height_metres(raw, config.units)
    if metres is None:
        return "Enter a plausible height"
    config.height_metres = metres
    return None


def apply_birth_year_edit(config, raw: str, current_year: int) -> str | None:
    year = profile_edit.parse_birth_year(raw, current_year)
    if year is None:
        return "Enter a four-digit year"
    config.birth_year = year
    return None


def sync_lines(credentials, last_pull_ms, last_push_ms, pending_count: int) -> dict:
    return {
        "host": status_model.describe_host(credentials.nas_host if credentials else None),
        "login": status_model.describe_login(credentials),
        "last_pull": status_model.describe_when(last_pull_ms),
        "last_push": status_model.describe_when(last_push_ms),
        "pending": status_model.describe_pending(pending_count),
    }


def collect_status(config, hardware_available: bool = False,
                   accelerometer_available: bool = False) -> dict:
    from xx_vitals.collect import status as collect_model

    described = collect_model.describe(
        bool(getattr(config, "collect_enabled", False)),
        hardware_available, accelerometer_available,
    )
    return {
        "title": described.title, "detail": described.detail,
        "enabled": described.enabled, "has_source": described.has_source,
        "source": described.source,
    }


def set_collect_enabled(config, enabled: bool, save=None) -> dict:
    config.collect_enabled = bool(enabled)
    if save is not None:
        save(config)
    else:
        from xx_vitals import prefs as prefs_module

        try:
            prefs_module.update(collect_enabled=config.collect_enabled)
        except OSError:
            pass
    return collect_status(config)


def systemd_user_available() -> bool:
    import shutil

    return shutil.which("systemctl") is not None


def set_count_while_screen_off(config, enabled: bool, save=None) -> dict:
    config.count_while_screen_off = bool(enabled)
    if save is not None:
        save(config)
    else:
        from xx_vitals import prefs as prefs_module

        try:
            prefs_module.update(
                count_while_screen_off=config.count_while_screen_off)
        except OSError:
            pass
    return collect_status(config)


def screen_off_copy() -> str:
    return (
        "Count steps while screen is off (uses more battery). "
        "Without a hardware step counter, steps are only counted "
        "while the phone is awake."
    )


def _refresh_baseline(config, state_path=None) -> None:
    """Overlay the collector's fresh baseline onto the app-open config.

    Without this, "Sample now" counts from when the app opened while the
    collector has since moved on, and the delta is counted twice.
    """
    from xx_vitals.collect import service as collect_service

    fresh = collect_service.load_state_baseline(state_path)
    if fresh is not None:
        config.step_baseline = fresh


def tick_collect_now(db, config, hardware=None, now_ms: int = 0,
                     today: str = "", elapsed_ms: int = 0, wall_ms: int = 0,
                     _state_path=None):
    """Immediate hardware-counter pass; the resident sampler owns the rest."""
    from xx_vitals.collect import service as collect_service

    _refresh_baseline(config, _state_path)
    return collect_service.run_once(
        db, config, now_ms=now_ms, today=today or _today(),
        elapsed_ms=elapsed_ms, wall_ms=wall_ms or now_ms,
        hardware=hardware,
    )


def collect_tick_in_background(db, config, on_done=None, readers=None,
                               state_path=None):
    from xx_vitals.sync import worker as worker_module

    def work():
        import time

        from xx_vitals.collect import sensors as sensor_backend
        from xx_vitals.collect import service as collect_service

        _refresh_baseline(config, state_path)
        if readers is not None:
            hardware = readers.get("hardware")
            elapsed = readers.get("elapsed_ms", 0)
            wall = readers.get("wall_ms", 0)
            day = readers.get("today") or _today()
            moment = readers.get("now_ms", 0)
        else:
            hardware = sensor_backend.read_hardware_counter()
            elapsed = int(time.monotonic() * 1000)
            wall = int(time.time() * 1000)
            day = _today()
            moment = wall
        return collect_service.run_once(
            db, config, now_ms=moment, today=day,
            elapsed_ms=elapsed, wall_ms=wall, hardware=hardware,
            on_save=lambda cfg: collect_service.persist_collector_state(
                cfg, state_path=state_path),
        )

    return worker_module.run_in_background(work, on_done)


class ResidentHandle:
    """A resident sampler on a worker thread; stop it when the window closes."""

    def __init__(self, thread, stop) -> None:
        self.thread = thread
        self._stop = stop

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout=timeout)


def start_resident_in_background(db, config, on_done=None, _deps=None) -> ResidentHandle:
    """Run the motion-gated sampler off the GTK loop while the window is open.

    `_deps` injects reader/wait overrides for headless tests; production
    reads raw iio sysfs on the sampler's own cadence.
    """
    import threading

    from xx_vitals.collect import sensors as sensor_backend
    from xx_vitals.collect import service as collect_service

    deps = _deps or {}
    stop = threading.Event()
    reader = deps.get("reader") or sensor_backend.sysfs_sample_reader()

    def _persist(cfg) -> None:
        collect_service.persist_collector_state(cfg)

    sampler = collect_service.build_resident_sampler(
        db, config, save_config=_persist,
        inhibitor=deps.get("inhibitor"),
    )
    kwargs = {
        "still_poll_s": deps.get("still_poll_s", collect_service.STILL_POLL_S),
        "moving_batch_s": deps.get("moving_batch_s", 1.0),
    }
    if "clock" in deps:
        kwargs["clock"] = deps["clock"]

    def work() -> None:
        collect_service.run_resident(
            db, config, read_sample=reader, stop=stop, sampler=sampler,
            save_config=_persist, **kwargs,
        )

    from xx_vitals.sync import worker as worker_module

    thread = worker_module.run_in_background(work, on_done)
    return ResidentHandle(thread, stop)


def maybe_start_fallback(db, config, window):
    """Documented degradation when `systemctl --user` is unavailable.

    No hardware counter: the motion-gated sampler runs on a worker
    thread while the window is open. Hardware counter: a 15-minute
    GTK-tick samples it while open. Returns the resident handle (or
    None); the caller stops it on close.
    """
    if systemd_user_available():
        return None
    from xx_vitals.collect import sensors as sensor_backend

    try:
        source = sensor_backend.probe_source(probe_dbus=False)
    except OSError:
        return None
    if source == "hardware":
        from gi.repository import GLib

        def tick() -> bool:
            if getattr(config, "collect_enabled", False):
                tick_collect_now(db, config)
            return True

        GLib.timeout_add_seconds(900, tick)
        return None
    if source == "burst" and getattr(config, "collect_enabled", False):
        handle = start_resident_in_background(db, config)
        window.connect("close-request", lambda *_args: (handle.stop(), False)[1])
        return handle
    return None


def _today() -> str:
    from datetime import datetime

    return datetime.now().astimezone().date().isoformat()


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def save_nas_host(config, raw_url: str) -> str | None:
    parsed = auth_module.parse_host_url(raw_url)
    if isinstance(parsed, auth_module.HostError):
        return parsed.message
    config.server_url = parsed.url
    _persist_config(config)
    return None


def save_xxchat_host(config, raw_url: str) -> str | None:
    parsed = auth_module.parse_host_url(raw_url)
    if isinstance(parsed, auth_module.HostError):
        return parsed.message
    config.xxchat_url = parsed.url
    _persist_config(config)
    return None


def _persist_config(config) -> None:
    from xx_vitals import prefs as prefs_module

    try:
        prefs_module.save(config)
    except OSError:
        pass


def format_login_message(result) -> str:
    if isinstance(result, auth_module.LoginSuccess):
        identity = auth_module.token_user_id(result.token)
        return f"Signed in{(' · ' + identity) if identity else ''}"
    return result.message


def sync_setup_copy(credentials) -> str | None:
    if credentials is not None and credentials.can_sync:
        return None
    return "Set the NAS host and sign in to sync."


def refresh_sync_state(db, config, secrets) -> dict:
    credentials = secrets_lookup(config, secrets)
    try:
        state = db.sync_get()
    except (OSError, AttributeError):
        state = None
    last_pull = state.last_pull_at if state is not None else None
    last_push = state.last_push_at if state is not None else None
    try:
        pending = db.pending_count()
    except (OSError, AttributeError):
        pending = 0
    lines = sync_lines(credentials, last_pull, last_push, pending)
    lines["can_sync"] = bool(credentials.can_sync)
    return lines


def _defer_focus_until_tapped(entry) -> None:
    try:
        from gi.repository import Gtk
    except ImportError:
        return
    try:
        entry.set_can_focus(False)
        tap = Gtk.GestureClick.new()
        tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tap.connect("pressed", lambda *_args: entry.set_can_focus(True))
        entry.add_controller(tap)
    except AttributeError:
        pass


def _build_import_rows(parsed):
    """Validate parsed rows into (dailies, weights, activities, skipped).

    Weights/activities are (row, queue-payload, queue-created-at) triples
    for `Database.import_batch`; dailies queue implicitly by date.
    """
    from xx_vitals.core import takeout as takeout_module
    from xx_vitals.store import entries as entries_module
    from xx_vitals.store.db import Activity, DailyMetric, WeightSample
    from xx_vitals.ui.entry import WEIGHT_KG_MAX, WEIGHT_KG_MIN

    dailies: list = []
    weights: list = []
    activities: list = []
    skipped = parsed.rows_skipped
    for day in parsed.days:
        present = (day.steps, day.distance_m, day.active_min,
                   day.effort_points, day.calories_kcal)
        if any(v is not None and v < 0 for v in present):
            skipped += 1
            continue
        dailies.append(DailyMetric(
            source_id=takeout_module.SOURCE_TAKEOUT, date=day.date,
            steps=day.steps, distance_m=day.distance_m,
            active_min=day.active_min, effort_points=day.effort_points,
            calories_kcal=day.calories_kcal, hr_avg=day.hr_avg,
            hr_min=day.hr_min, hr_max=day.hr_max, derived_mask=0,
        ))
    for sample in parsed.weights:
        if not WEIGHT_KG_MIN <= sample.weight_kg <= WEIGHT_KG_MAX:
            skipped += 1
            continue
        rounded = round(sample.weight_kg, 2)
        weights.append((
            WeightSample(
                source_id=entries_module.SOURCE_MANUAL,
                measured_at=sample.measured_at_ms, weight_kg=rounded,
            ),
            entries_module.weight_payload(
                entries_module.SOURCE_MANUAL, sample.measured_at_ms, rounded,
            ),
            sample.measured_at_ms,
        ))
    for session in parsed.activities:
        if session.ended_at_ms <= session.started_at_ms or not session.type:
            skipped += 1
            continue
        row = Activity(
            source_id=takeout_module.SOURCE_TAKEOUT, type=session.type,
            started_at=session.started_at_ms, ended_at=session.ended_at_ms,
            distance_m=session.distance_m, calories_kcal=session.calories_kcal,
            hr_avg=session.hr_avg, hr_max=session.hr_max,
            track_geojson=session.track_geojson,
        )
        activities.append((
            row,
            entries_module.activity_payload(
                row.source_id, row.type, row.started_at, row.ended_at,
                row.distance_m, row.calories_kcal, None, None, None,
            ),
            row.ended_at,
        ))
    return dailies, weights, activities, skipped


def _summarize_import(dailies, weights, activities, skipped: int) -> str:
    return (f"Imported {_count(len(dailies), 'day')} and "
            f"{_count(len(weights), 'weight')}, "
            f"{_count(len(activities), 'activity', 'activities')} "
            f"({_count(skipped, 'row')} skipped).")


def _count(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def import_takeout(db, filename: str, text: str, now_ms: int, on_progress=None) -> str:
    from xx_vitals.core import takeout as takeout_module

    kind = takeout_module.detect_kind(filename or "", text)
    if kind == "daily_csv":
        parsed = takeout_module.parse_daily_csv_stream(
            text.splitlines(),
            on_progress=(lambda done, _total: on_progress(done, None)
                         if on_progress is not None else None),
        )
    elif kind == "weight_json":
        parsed = takeout_module.parse_weight_json(text)
    elif kind == "healthconnect_json":
        parsed = takeout_module.parse_healthconnect_json(text)
    elif kind == "tcx":
        parsed = takeout_module.parse_tcx(text)
    else:
        return "That file is not a Takeout daily-metrics CSV, TCX, or weight export."
    dailies, weights, activities, skipped = _build_import_rows(parsed)
    db.import_batch(dailies, weights, activities, now_ms=now_ms)
    return _summarize_import(dailies, weights, activities, skipped)


def import_takeout_stream(db, filename: str, lines, now_ms: int, on_progress=None) -> str:
    """Streaming CSV import: lines are consumed one by one, one transaction."""
    from xx_vitals.core import takeout as takeout_module

    lowered = (filename or "").lower()
    if lowered.endswith(".csv"):
        parsed = takeout_module.parse_daily_csv_stream(
            lines,
            on_progress=(lambda done, _total: on_progress(done, None)
                         if on_progress is not None else None),
        )
    else:
        return import_takeout(db, filename, "".join(lines), now_ms, on_progress)
    dailies, weights, activities, skipped = _build_import_rows(parsed)
    db.import_batch(dailies, weights, activities, now_ms=now_ms)
    return _summarize_import(dailies, weights, activities, skipped)


def import_takeout_zip(db, zip_path, now_ms: int, on_progress=None) -> str:
    """Import a Takeout archive: CSV streamed, TCX + weight JSON per member."""
    from xx_vitals.core import takeout as takeout_module

    parsed = takeout_module.parse_takeout_zip(zip_path, on_progress=on_progress)
    dailies, weights, activities, skipped = _build_import_rows(parsed)
    if not dailies and not weights and not activities:
        return "No daily metrics, activities, or weights found in that archive."
    db.import_batch(dailies, weights, activities, now_ms=now_ms)
    return _summarize_import(dailies, weights, activities, skipped)


"""Profile page builder: restyled sections over the unchanged logic above."""


def open_takeout_import(window, db, on_notice, on_done=None) -> None:
    """Pick a Takeout file and import it off the main loop."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    dialog = Gtk.FileDialog(title="Import Takeout file")
    try:
        from gi.repository import Gio as GioModule

        filters = GioModule.ListStore.new(Gtk.FileFilter)
        takeout = Gtk.FileFilter()
        takeout.set_name("Takeout export")
        for pattern in ("*.csv", "*.tcx", "*.json", "*.zip"):
            takeout.add_pattern(pattern)
        filters.append(takeout)
        dialog.set_filters(filters)
        dialog.set_default_filter(takeout)
    except AttributeError:
        pass
    dialog.open(window, None, lambda dlg, result: _finish_import(
        dlg, result, db, on_notice, on_done))


def _finish_import(dialog, result, db, on_notice, on_done) -> None:
    from xx_vitals.sync import worker as worker_module

    try:
        chosen = dialog.open_finish(result)
    except Exception:
        return
    on_notice("Importing…")
    name = chosen.get_basename() or ""

    def progress(done: int, total) -> None:
        from gi.repository import GLib as GLibModule

        if total:
            line = f"Importing… {done}/{total} files…"
        else:
            line = f"Importing… {_count(done, 'row')}…"
        try:
            GLibModule.idle_add(on_notice, line)
        except (AttributeError, TypeError):
            on_notice(line)

    def work():
        lowered = name.lower()
        if lowered.endswith(".zip"):
            return import_takeout_zip(
                db, _portal_path(chosen, name), _now_ms(), progress)
        if lowered.endswith(".csv"):
            return import_takeout_stream(
                db, name, _portal_lines(chosen), _now_ms(), progress)
        ok, data, _etag = chosen.load_contents(None)
        text = bytes(data).decode("utf-8", "replace") if ok else ""
        return import_takeout(db, name, text, _now_ms(), progress)

    def done(outcome) -> None:
        if isinstance(outcome, BaseException):
            on_notice("Import failed — the file could not be read.")
            return
        on_notice(outcome)
        if on_done is not None:
            on_done()

    worker_module.run_in_background(work, done)


def _portal_lines(chosen):
    """Yield decoded lines from a portal file without holding it whole."""
    from gi.repository import Gio

    stream = chosen.read(None)
    data_stream = Gio.DataInputStream.new(stream)
    try:
        while True:
            line, _length = data_stream.read_line(None)
            if line is None:
                break
            yield bytes(line).decode("utf-8", "replace")
    finally:
        try:
            data_stream.close(None)
        except Exception:
            pass


def _portal_path(chosen, name: str) -> str:
    """A filesystem path for a portal file, spilling to cache when needed."""
    native = chosen.get_path()
    if native is not None:
        return native
    from xx_vitals.paths import cache_dir, ensure_parent_for

    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    target = cache_dir() / f"import-{(safe or 'takeout').lower()}"
    ensure_parent_for(target)
    ok, data, _etag = chosen.load_contents(None)
    target.write_bytes(bytes(data) if ok else b"")
    return str(target)


def _profile_caption(config) -> str:
    from xx_vitals.core import format as format_model

    bits = [config.units.upper()]
    if config.height_metres:
        bits.append(format_model.format_height(
            config.height_metres, config.units).upper())
    return " · ".join(bits)


def _labeled(caption_text: str, entry):
    """Field-style caption over an entry (entries sit on the window
    background, never inside a surface card where they go invisible)."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)
    box.append(widgets.caption(caption_text))
    box.append(entry)
    return box


def build_profile_page(db, config, secrets, nav, live: dict | None = None,
                       window=None) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gio, Gtk

    from xx_ui import widgets

    from xx_vitals.sync import worker as worker_module
    from xx_vitals.ui import shell as shell_page

    live = live if live is not None else {}
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    body.add_css_class("xx-page-body")
    header_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    state = {"overflow": None}
    refs: dict = {}

    def menu() -> object:
        if state["overflow"] is not None:
            state["overflow"].popup()
            return state["overflow"]
        anchor = header_box.get_first_child()
        popover = shell_page.open_overflow(anchor, [
            ("Import Takeout",
             lambda: open_takeout_import(
                 window, db, refs["import_notice"].set_text,
                 on_done=nav.refresh_all)),
            ("Copy logs", _copy_logs),
        ])
        state["overflow"] = popover
        popover.connect("closed", lambda _p: state.update(overflow=None))
        return popover

    def refresh() -> None:
        while True:
            first = header_box.get_first_child()
            if first is None:
                break
            header_box.remove(first)
        header_box.append(widgets.page_header(
            "Profile", _profile_caption(config), overflow_cb=menu))
        _refresh_step_lines()
        _refresh_weight_line()
        refresh_state()

    live["refresh"] = refresh
    live["menu"] = menu

    # -- Goals ---------------------------------------------------------
    body.append(widgets.section_label("Goals"))
    steps_entry = widgets.deferred_entry("e.g. 10000")
    steps_entry.set_text(str(config.steps_goal))
    steps_entry.set_hexpand(True)
    effort_entry = widgets.deferred_entry("e.g. 90")
    effort_entry.set_text(str(config.effort_goal))
    effort_entry.set_hexpand(True)
    weekly_entry = widgets.deferred_entry("e.g. 150")
    weekly_entry.set_text(str(config.weekly_effort_goal))
    weekly_entry.set_hexpand(True)
    body.append(_labeled("Daily steps goal", steps_entry))
    body.append(_labeled("Daily effort goal", effort_entry))
    body.append(_labeled("Weekly effort goal", weekly_entry))
    goals_error = widgets.wrap_label("", "xx-secondary")
    goals_error.add_css_class("xx-danger-text")
    body.append(goals_error)
    body.append(widgets.outline_button("Save goals", lambda: _save_goals()))

    def _save_goals() -> None:
        errors = apply_goal_edits(
            config, steps_raw=steps_entry.get_text(),
            effort_raw=effort_entry.get_text(),
            weekly_raw=weekly_entry.get_text())
        if errors:
            goals_error.set_text("; ".join(
                f"{name}: {message}" for name, message in errors.items()))
            return
        goals_error.set_text("")
        _persist_config(config)
        nav.refresh_all()

    # -- Body ----------------------------------------------------------
    body.append(widgets.section_label("Body"))
    height_entry = widgets.deferred_entry("Metres, e.g. 1.78")
    height_entry.set_hexpand(True)
    if config.height_metres:
        height_entry.set_text(f"{config.height_metres:.2f}")
    year_entry = widgets.deferred_entry("e.g. 1990")
    year_entry.set_hexpand(True)
    if config.birth_year:
        year_entry.set_text(str(config.birth_year))
    body.append(_labeled("Height", height_entry))
    body.append(_labeled("Birth year", year_entry))
    body_error = widgets.wrap_label("", "xx-secondary")
    body_error.add_css_class("xx-danger-text")
    body.append(body_error)
    body.append(widgets.outline_button("Save", lambda: _save_body()))
    weight_line = widgets.wrap_label("", "xx-secondary")
    body.append(widgets.setting_row(
        "Latest weigh-in", "", weight_line, last=True))
    refs["weight_line"] = weight_line

    def _save_body() -> None:
        from datetime import datetime

        error = apply_height_edit(config, height_entry.get_text())
        if error is None and year_entry.get_text().strip():
            error = apply_birth_year_edit(
                config, year_entry.get_text(), datetime.now().year)
        if error is not None:
            body_error.set_text(error)
            return
        body_error.set_text("")
        _persist_config(config)
        nav.refresh_all()

    def _refresh_weight_line() -> None:
        from xx_vitals.core import format as format_model

        try:
            latest = db.latest_weight()
        except (OSError, AttributeError):
            latest = None
        if latest is None:
            weight_line.set_text("No weigh-ins yet")
        else:
            weight_line.set_text(format_model.format_weight(
                latest.weight_kg, config.units))

    body.append(widgets.section_label("Units"))
    body.append(widgets.segmented([
        ("Metric", config.units == "metric",
         lambda: _set_units("metric")),
        ("Imperial", config.units == "imperial",
         lambda: _set_units("imperial")),
    ]))

    def _set_units(units: str) -> None:
        config.units = units
        _persist_config(config)
        nav.refresh_all()

    # -- Tabs ----------------------------------------------------------
    body.append(widgets.section_label("Tabs"))
    for key, title in (("show_journal_tab", "Journal tab"),
                       ("show_browse_tab", "Browse tab"),
                       ("show_profile_tab", "Profile tab")):
        switch = widgets.make_switch(
            bool(getattr(config, key, True)),
            lambda active, key=key: _set_tab(key, active))
        body.append(widgets.setting_row(title, "", switch))

    def _set_tab(key: str, active: bool) -> None:
        setattr(config, key, bool(active))
        _persist_config(config)
        nav.refresh_tabs()

    # -- Step counting -------------------------------------------------
    body.append(widgets.section_label("Step counting"))
    step_title = widgets.label("", "xx-title")
    step_detail = widgets.wrap_label("", "xx-secondary")
    body.append(step_title)
    body.append(step_detail)
    collect_switch = widgets.make_switch(
        bool(getattr(config, "collect_enabled", False)),
        lambda active: _toggle_collect(active))
    body.append(widgets.setting_row(
        "Count my steps", "", collect_switch, last=True))
    refs["step_title"] = step_title
    refs["step_detail"] = step_detail

    def _refresh_step_lines() -> None:
        line = collect_status(config)
        step_title.set_text(line["title"])
        step_detail.set_text(line["detail"])

    def _toggle_collect(active: bool) -> None:
        set_collect_enabled(config, active)
        _refresh_step_lines()
        nav.refresh_all()

    body.append(widgets.wrap_label(screen_off_copy()))
    screen_switch = widgets.make_switch(
        bool(getattr(config, "count_while_screen_off", False)),
        lambda active: set_count_while_screen_off(config, active))
    body.append(widgets.setting_row(
        "Count while screen is off", "", screen_switch, last=True))
    sample_line = widgets.wrap_label("", "xx-secondary")
    sample_button = widgets.outline_button("Sample now", lambda: _sample_now())
    body.append(sample_button)
    body.append(sample_line)

    def _sample_now() -> None:
        sample_line.set_text("Sampling…")

        def done(result) -> None:
            if isinstance(result, BaseException):
                sample_line.set_text(
                    "Sample failed — will retry on the next tick.")
                return
            line = collect_status(config)
            if getattr(result, "outcome", "") == "no-source":
                sample_line.set_text(line["detail"])
            elif getattr(result, "steps", 0):
                sample_line.set_text(
                    f"Counted {result.steps} steps just now. {line['detail']}")
            else:
                sample_line.set_text(line["detail"])

        collect_tick_in_background(db, config, done)

    if not systemd_user_available():
        body.append(widgets.wrap_label(
            "Systemd user timers are unavailable here — "
            "sampling runs while the app is open."))

    # -- Sync ----------------------------------------------------------
    body.append(widgets.section_label("Sync"))
    nas_line = widgets.wrap_label("", "xx-body")
    body.append(nas_line)
    host_entry = widgets.deferred_entry("NAS host URL")
    host_entry.set_text(config.server_url)
    host_entry.set_hexpand(True)
    host_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    host_row.append(host_entry)
    host_row.append(widgets.outline_button("Save", lambda: _save_host()))
    body.append(host_row)
    login_line = widgets.wrap_label("", "xx-body")
    pull_line = widgets.wrap_label("", "xx-body")
    push_line = widgets.wrap_label("", "xx-body")
    pending_line = widgets.wrap_label("", "xx-body")
    for line in (login_line, pull_line, push_line, pending_line):
        body.append(line)
    xxchat_entry = widgets.deferred_entry("Sign-in host URL")
    xxchat_entry.set_text(getattr(config, "xxchat_url", ""))
    xxchat_entry.set_hexpand(True)
    user_entry = widgets.deferred_entry("Username")
    user_entry.set_hexpand(True)
    password_entry = widgets.deferred_entry("Password")
    password_entry.set_visibility(False)
    password_entry.set_hexpand(True)
    for entry in (xxchat_entry, user_entry, password_entry):
        body.append(entry)
    signin_button = widgets.primary_button("Sign in", lambda: _sign_in())
    login_message = widgets.wrap_label("", "xx-secondary")
    login_message.set_hexpand(True)
    signin_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    signin_row.append(signin_button)
    signin_row.append(login_message)
    body.append(signin_row)
    setup_label = widgets.wrap_label("", "xx-secondary")
    body.append(setup_label)
    notice = widgets.wrap_label("", "xx-secondary")
    sync_button = widgets.primary_button("Sync now", lambda: _sync_now())
    body.append(sync_button)
    body.append(notice)
    refs["notice"] = notice

    batch_store = Gio.ListStore.new(Gtk.StringObject)
    factory = Gtk.SignalListItemFactory()
    factory.connect("setup", lambda _f, item: item.set_child(
        widgets.label("", "xx-secondary")))
    factory.connect("bind", lambda _f, item: item.get_child().set_text(
        item.get_item().get_string()))
    batches = Gtk.ListView.new(Gtk.NoSelection.new(batch_store), factory)
    batches.set_single_click_activate(True)
    batch_scrolled = Gtk.ScrolledWindow()
    batch_scrolled.set_min_content_height(120)
    batch_scrolled.set_child(batches)
    batch_scrolled.set_visible(False)
    body.append(batch_scrolled)

    def refresh_state() -> None:
        def work():
            return refresh_sync_state(db, config, secrets)

        def done(outcome) -> None:
            if isinstance(outcome, BaseException):
                return
            nas_line.set_text(f"NAS host: {outcome['host']}")
            login_line.set_text(f"Sign-in: {outcome['login']}")
            pull_line.set_text(f"Last pull: {outcome['last_pull']}")
            push_line.set_text(f"Last push: {outcome['last_push']}")
            pending_line.set_text(f"Queue: {outcome['pending']}")
            _update_signin_sensitive()
            sync_button.set_sensitive(bool(outcome["can_sync"]))
            setup_label.set_text(
                "" if outcome["can_sync"] else sync_setup_copy(None) or "")

        worker_module.run_in_background(work, done)

    def refresh_batches() -> None:
        from xx_vitals.sync import status as status_model

        def work():
            return status_model.batches_outcome(_build_api(config, secrets))

        def done(outcome) -> None:
            if isinstance(outcome, BaseException):
                return
            lines, problem = outcome
            batch_store.remove_all()
            for line in lines:
                batch_store.append(Gtk.StringObject.new(line))
            batch_scrolled.set_visible(bool(lines))
            if problem:
                notice.set_text(problem)

        worker_module.run_in_background(work, done)

    def _save_host() -> None:
        error = save_nas_host(config, host_entry.get_text())
        if error is not None:
            notice.set_text(error)
            return
        notice.set_text("")
        refresh_state()

    def _update_signin_sensitive(*_args) -> None:
        signin_button.set_sensitive(bool(
            xxchat_entry.get_text().strip()
            and user_entry.get_text().strip()
            and password_entry.get_text()))

    for entry in (xxchat_entry, user_entry, password_entry):
        entry.connect("changed", _update_signin_sensitive)
    _update_signin_sensitive()

    def _sign_in() -> None:
        from xx_vitals.sync import auth as auth_module

        host_error = save_nas_host(config, host_entry.get_text())
        if host_error is not None:
            notice.set_text(host_error)
            return
        chat_error = save_xxchat_host(config, xxchat_entry.get_text())
        if chat_error is not None:
            notice.set_text(chat_error)
            return
        username = user_entry.get_text().strip()
        password = password_entry.get_text()
        if not username or not password:
            notice.set_text("Enter the username and password.")
            return
        notice.set_text("")
        login_message.set_text("Signing in…")
        signin_button.set_sensitive(False)

        def done(outcome) -> None:
            if isinstance(outcome, BaseException):
                login_message.set_text("Sign-in failed — will retry.")
            else:
                login_message.set_text(format_login_message(outcome))
                if isinstance(outcome, auth_module.LoginSuccess):
                    password_entry.set_text("")
            _update_signin_sensitive()
            refresh_state()

        worker_module.login_in_background(
            config.xxchat_url, username, password, secrets, done)

    def _sync_now() -> None:
        from xx_vitals.sync import status as status_model

        error = save_nas_host(config, host_entry.get_text())
        if error is not None:
            notice.set_text(error)
            return
        notice.set_text("Syncing…")

        def done(outcome) -> None:
            if isinstance(outcome, BaseException):
                problem = status_model.notice_for_exception(outcome)
                notice.set_text(problem or "Sync failed — will retry.")
            elif outcome.get("paused"):
                notice.set_text(status_model.VOLUME_NOTICE)
            else:
                notice.set_text(status_model.describe_pending(
                    outcome.get("pending", _pending_count(db))))
            refresh_state()
            refresh_batches()

        worker_module.sync_now_in_background(
            lambda: _build_engine(db, config, secrets), done)

    # -- Logs ----------------------------------------------------------
    body.append(widgets.section_label("Logs"))
    logs_path = widgets.wrap_label(str(applog.log_file()))
    logs_path.set_selectable(True)
    body.append(logs_path)
    logs_view = Gtk.TextView()
    logs_view.set_editable(False)
    logs_view.set_cursor_visible(False)
    logs_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    logs_view.set_can_focus(False)
    logs_view.set_hexpand(True)
    logs_scrolled = Gtk.ScrolledWindow()
    logs_scrolled.set_min_content_height(120)
    logs_scrolled.set_max_content_height(200)
    logs_scrolled.set_child(logs_view)
    body.append(logs_scrolled)
    body.append(widgets.outline_button("Copy logs", lambda: _copy_logs()))
    _refresh_log_view(logs_view)

    def _copy_logs() -> None:
        from xx_vitals.core import applog

        lines = applog.log_view_lines()
        logs_view.get_buffer().set_text("\n".join(lines))
        error = shell_page.copy_to_clipboard("\n".join(lines))
        if error is not None:
            notice.set_text(error)
            return
        notice.set_text(f"{len(lines)} log lines copied.")

    # -- Import --------------------------------------------------------
    body.append(widgets.section_label("Import"))
    import_notice = widgets.wrap_label("", "xx-secondary")
    refs["import_notice"] = import_notice
    body.append(widgets.outline_button(
        "Import Takeout file",
        lambda: open_takeout_import(
            window, db, import_notice.set_text, on_done=nav.refresh_all)))
    body.append(import_notice)

    refresh()
    refresh_batches()
    return widgets.page(header_box, body, None)


def _refresh_log_view(logs_view) -> None:
    from xx_vitals.core import applog

    lines = applog.log_view_lines()
    logs_view.get_buffer().set_text("\n".join(lines))


def _build_api(config, secrets):
    from xx_vitals.sync import http_api

    def token_provider():
        return secrets_lookup(config, secrets).token

    return http_api.HttpSyncApi(
        config.server_url, token_provider,
        lambda: set(config.allowed_cleartext_hosts),
        on_auth_expired=lambda: _mark_relogin(config),
    )


def _build_engine(db, config, secrets):
    from xx_vitals.sync.engine import SyncEngine

    return SyncEngine(db, db, db, db, db, _build_api(config, secrets))


def secrets_lookup(config, secrets):
    return auth_module.load_credentials(config, secrets)


def _mark_relogin(config) -> None:
    from xx_vitals import prefs as prefs_module

    try:
        stored = prefs_module.load()
        stored.relogin_required = True
        prefs_module.save(stored)
    except OSError:
        config.relogin_required = True


def _pending_count(db) -> int:
    try:
        return db.pending_count()
    except (OSError, AttributeError):
        return 0
