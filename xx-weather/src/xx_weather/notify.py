"""Alert evaluation + desktop notifications. Gtk-free except inside
``send_fire`` so tests import this without a display."""

from __future__ import annotations


def pending_fires(store, data, now_ms: int):
    from xx_phone.weather import evaluate_alerts

    if not store.alerts_enabled():
        return []
    triggers = store.triggers_for(data.place.zip)
    if triggers is None or not triggers.any_enabled:
        return []
    return evaluate_alerts(
        zip_code=data.place.zip, city=data.place.city, hourly=list(data.hourly),
        current_temp_f=data.current.temp_f,
        fetched_at_ms=data.updated_at_ms, now_ms=now_ms, triggers=triggers,
        already_notified=set(store.dedupe_keys()))


def record_fires(store, fires) -> None:
    store.add_dedupe_keys([fire.key for fire in fires])


def send_fire(app, fire) -> None:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    notification = Gio.Notification.new(fire.title)
    notification.set_body(fire.text)
    app.send_notification(fire.key, notification)
