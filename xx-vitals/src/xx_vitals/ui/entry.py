"""Manual weight + activity entry. Thin Adw dialogs over store/entries.

Validation mirrors WeightEntryModel/ActivityEntryModel: storage is always
kg/metres, queue kinds stay wire-compatible (`weight`, `weight.update`,
`weight.delete`, `activity`, `daily`). Submits write Room first and enqueue
in the same step, so offline entry round-trips on the next sync.
"""

from __future__ import annotations

from xx_vitals.store import entries

WEIGHT_KG_MIN = 20.0
WEIGHT_KG_MAX = 300.0


def validate_weight_input(raw_value: str, units: str) -> tuple[float | None, str | None]:
    try:
        typed = float(raw_value.strip())
    except (ValueError, AttributeError):
        return None, "Enter a number"
    kg = entries.to_kg(typed, entries.unit_for(units))
    if not WEIGHT_KG_MIN <= kg <= WEIGHT_KG_MAX:
        return None, "Enter a plausible weight"
    return entries.round_for_storage(kg), None


def validate_activity_input(
    type_name: str, duration_raw: str, steps_raw: str,
) -> tuple[dict | None, str | None]:
    cleaned = (type_name or "").strip().lower()
    if not cleaned:
        return None, "Pick an activity type"
    duration = entries.parse_duration_minutes(duration_raw or "")
    if duration is None:
        return None, "Duration must be 1–1440 minutes"
    try:
        steps = entries.parse_optional_steps(steps_raw or "")
    except AttributeError:
        steps = None
    if steps_raw and (steps_raw or "").strip() and steps is None:
        return None, "Steps must be a whole number"
    return {"type": cleaned, "duration_min": duration, "steps": steps}, None


def submit_weight(db, units: str, raw_value: str, measured_at: int) -> tuple[int | None, str | None]:
    from xx_vitals.store.db import WeightSample

    kg, error = validate_weight_input(raw_value, units)
    if error is not None:
        return None, error
    assert kg is not None
    repo = entries.WeightRepository(db)
    row_id = repo.add_weight(
        WeightSample(source_id=entries.SOURCE_MANUAL, measured_at=measured_at, weight_kg=kg),
        entries.weight_payload(entries.SOURCE_MANUAL, measured_at, kg),
    )
    return row_id, None


def submit_activity(
    db, type_name: str, duration_raw: str, steps_raw: str,
    now_ms: int, height_metres: float, weight_kg: float | None,
) -> tuple[int | None, str | None]:
    fields, error = validate_activity_input(type_name, duration_raw, steps_raw)
    if error is not None:
        return None, error
    assert fields is not None
    derived = entries.derive_activity(fields["steps"], fields["duration_min"], height_metres, weight_kg)
    started, ended = entries.activity_window(now_ms, fields["duration_min"])
    repo = entries.ActivityRepository(db)
    row_id = repo.log_activity(
        fields["type"], started, ended,
        derived.distance_m, derived.calories_kcal,
        derived.effort_points, derived.active_min, fields["steps"],
    )
    return row_id, None


#: (segment label, stored activity type): the journal's own filters.
ACTIVITY_TYPES = (("Walk", "walk"), ("Run", "run"), ("Bike", "cycle"),
                  ("Other", "other"))


def _labeled(caption_text: str, entry):
    """Caption over an entry, like the Profile fields."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)
    box.append(widgets.caption(caption_text))
    box.append(entry)
    return box


def fit_dialog(dialog, box) -> None:
    """Size a sheet to the phone canvas: 320 px content, scrolling body."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_propagate_natural_width(True)
    scrolled.set_propagate_natural_height(True)
    scrolled.set_child(box)
    dialog.set_content_width(320)
    dialog.set_child(scrolled)


def open_sheet(window, db, config, kind: str, on_saved=None):
    """Open the add-activity or log-weight sheet. Returns the dialog."""
    import time

    now_ms = int(time.time() * 1000)
    if kind == "weight":
        return build_weight_dialog(window, db, config.units, now_ms,
                                   on_saved=on_saved)
    weight = latest_weight_kg(db)
    return build_activity_dialog(window, db, now_ms, config.height_metres,
                                 weight, on_saved=on_saved)


def latest_weight_kg(db):
    try:
        latest = db.latest_weight()
    except (OSError, AttributeError):
        return None
    return latest.weight_kg if latest is not None else None


def build_weight_dialog(app_window, db, units: str, measured_at: int, on_saved=None):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from xx_ui import widgets

    dialog = Adw.Dialog(title="Log weight")
    box = widgets.dialog_box("Log weight")
    unit = entries.unit_for(units)
    hint = "kg" if unit == entries.WeightUnit.KG else "lb"
    field = widgets.deferred_entry(hint)
    field.set_input_purpose(Gtk.InputPurpose.NUMBER)
    box.append(_labeled("Weight", field))
    error_label = widgets.wrap_label("", "xx-secondary")
    error_label.add_css_class("xx-danger-text")
    box.append(error_label)
    box.append(widgets.primary_button("Save", lambda: on_save()))

    def on_save() -> None:
        row_id, error = submit_weight(db, units, field.get_text(), measured_at)
        if error is not None:
            error_label.set_text(error)
            return
        dialog.close()
        if on_saved is not None:
            on_saved(row_id)

    fit_dialog(dialog, box)
    dialog.present(app_window)
    return dialog


def build_activity_dialog(app_window, db, now_ms: int, height_metres: float,
                           weight_kg: float | None, on_saved=None):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from xx_ui import widgets

    dialog = Adw.Dialog(title="Log activity")
    box = widgets.dialog_box("Log activity")
    picked = {"type": ACTIVITY_TYPES[0][1]}
    holder = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)

    def pick(value: str) -> None:
        picked["type"] = value
        _fill_types()

    def _fill_types() -> None:
        child = holder.get_first_child()
        if child is not None:
            holder.remove(child)
        holder.append(widgets.segmented([
            (label, value == picked["type"], lambda value=value: pick(value))
            for label, value in ACTIVITY_TYPES]))

    _fill_types()
    box.append(holder)
    duration_field = widgets.deferred_entry("min")
    duration_field.set_input_purpose(Gtk.InputPurpose.NUMBER)
    steps_field = widgets.deferred_entry("Optional")
    steps_field.set_input_purpose(Gtk.InputPurpose.NUMBER)
    box.append(_labeled("Duration", duration_field))
    box.append(_labeled("Steps", steps_field))
    error_label = widgets.wrap_label("", "xx-secondary")
    error_label.add_css_class("xx-danger-text")
    box.append(error_label)
    box.append(widgets.primary_button("Save", lambda: on_save()))

    def on_save() -> None:
        row_id, error = submit_activity(
            db, picked["type"], duration_field.get_text(),
            steps_field.get_text(), now_ms, height_metres, weight_kg,
        )
        if error is not None:
            error_label.set_text(error)
            return
        dialog.close()
        if on_saved is not None:
            on_saved(row_id)

    fit_dialog(dialog, box)
    dialog.present(app_window)
    return dialog
