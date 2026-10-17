"""Blocking refresh orchestration. Runs on a worker thread; the window
posts results back to the main loop. All network goes through the
injected ``get`` callable so tests use fixtures, never the network."""

from __future__ import annotations

import os
import time
from typing import Callable


def _offline() -> bool:
    return os.environ.get("XX_WEATHER_OFFLINE") == "1"


def make_get(user_agent: str | None = None):
    from xx_phone.weather import http_get

    def _get(url: str, timeout_ms: int) -> str:
        if _offline():
            raise OSError("offline (XX_WEATHER_OFFLINE=1)")
        return http_get(url, user_agent, timeout_ms)

    return _get


def refresh_place(store, place, force: bool = False,
                  get: Callable | None = None,
                  clock_ms: Callable[[], int] | None = None,
                  budget_ms: int | None = None):
    """NWS first, Open-Meteo enrichment, OM standalone fallback."""
    from xx_phone.weather import (NWS_UA, RefreshFailure, RefreshSuccess,
                                  assemble_nws, fetch_nws_bodies, is_fresh,
                                  merge_extras, openmeteo_extras_url,
                                  openmeteo_forecast_url, parse_openmeteo,
                                  parse_openmeteo_extras)

    from xx_weather.store import (NWS_DAILY, NWS_HOURLY, NWS_OBS, OM_BODY)

    clock = clock_ms or (lambda: int(time.time() * 1000))
    deadline = None if budget_ms is None else clock() + budget_ms
    if get is None:
        get = make_get()

        def _nws(url: str, timeout_ms: int) -> str:
            if _offline():
                raise OSError("offline (XX_WEATHER_OFFLINE=1)")
            from xx_phone.weather import http_get

            return http_get(url, NWS_UA, timeout_ms)
    else:
        _nws = get

    if not force:
        cached = store.load_cached(place)
        if cached is not None and is_fresh(cached.updated_at_ms, clock()):
            return RefreshSuccess(cached, True)

    try:
        bodies = fetch_nws_bodies(place, _nws, deadline_ms=deadline)
        fetched_at = clock()
        data = assemble_nws(place, bodies, fetched_at)
        store.write_cache(place.zip, NWS_DAILY, bodies.daily)
        store.write_cache(place.zip, NWS_HOURLY, bodies.hourly)
        if bodies.obs is not None:
            store.write_cache(place.zip, NWS_OBS, bodies.obs)
        store.delete_cache(place.zip, OM_BODY)
        store.write_stamp(place.zip, fetched_at)
        store.update_labels(place.zip, data.place.city, data.place.state)
        try:
            extra_body = get(openmeteo_extras_url(place), 12000)
            extra = parse_openmeteo_extras(extra_body, place)
            store.write_extras(place.zip, extra)
            data = merge_extras(data, extra)
        except Exception:  # noqa: BLE001 - enrichment is best-effort
            pass
        return RefreshSuccess(data, False)
    except Exception:  # noqa: BLE001 - NWS failure falls through to OM
        pass

    try:
        body = get(openmeteo_forecast_url(place), 12000)
        fetched_at = clock()
        data = parse_openmeteo(place, body, fetched_at)
        store.write_cache(place.zip, OM_BODY, body)
        store.delete_cache(place.zip, NWS_DAILY)
        store.delete_cache(place.zip, NWS_HOURLY)
        store.delete_cache(place.zip, NWS_OBS)
        store.write_stamp(place.zip, fetched_at)
        return RefreshSuccess(data, False)
    except Exception as exc:  # noqa: BLE001 - report, keep stale on screen
        message = str(exc) or "Could not reach weather services."
        return RefreshFailure(message, store.load_cached(place))


def add_place(store, raw_zip: str, fetch: Callable[[str], str] | None = None):
    from xx_phone.weather import geocode_zip

    if fetch is None:
        plain = make_get()
        fetch = lambda url: plain(url, 12000)  # noqa: E731
    place = geocode_zip(raw_zip, fetch)
    existed = any(p.zip == place.zip for p in store.places())
    store.add_place(place)
    if not existed:
        store.clear_caches(place.zip)
    return place
