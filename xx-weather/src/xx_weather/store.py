"""Prefs + per-ZIP raw-response caches under XDG dirs.

Layout mirrors the Android port's filesDir split: one JSON prefs file
(replaces SharedPreferences) and per-ZIP raw NWS/Open-Meteo bodies, so a
refresh never clobbers another saved location. Every directory is created
mode 0700 before first use; reads tolerate missing or corrupt files.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

NWS_DAILY = "cache_nws_daily.json"
NWS_HOURLY = "cache_nws_hourly.json"
NWS_OBS = "cache_nws_obs.json"
OM_BODY = "cache_openmeteo.json"
EXTRAS = "cache_extras.json"
FETCH_STAMP = "cache_fetched_at.txt"

_UNITS_VALUES = ("F", "C")


def _ensure_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _base(env_key: str, fallback: Path) -> Path:
    raw = os.environ.get(env_key)
    if raw:
        return Path(raw)
    return fallback


class Store:
    def __init__(self, config_dir: Path | None = None,
                 data_dir: Path | None = None,
                 cache_dir: Path | None = None) -> None:
        home = Path.home()
        self.config_dir = _ensure_dir(
            config_dir or _base("XDG_CONFIG_HOME", home / ".config")
            / "xx-weather")
        self.data_dir = _ensure_dir(
            data_dir or _base("XDG_DATA_HOME", home / ".local" / "share")
            / "xx-weather")
        self.cache_dir = _ensure_dir(
            cache_dir or _base("XDG_CACHE_HOME", home / ".cache")
            / "xx-weather")
        self._prefs_file = self.config_dir / "settings.json"

    # -- prefs ------------------------------------------------------------

    def _load_prefs(self) -> dict:
        try:
            data = json.loads(self._prefs_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_prefs(self, prefs: dict) -> None:
        tmp = self._prefs_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(prefs), encoding="utf-8")
            tmp.replace(self._prefs_file)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    # -- places ------------------------------------------------------------

    def places(self) -> list:
        from xx_phone.weather import decode_places

        return decode_places(self._load_prefs().get("places_json"))

    def _write_places(self, prefs: dict, places: list) -> None:
        from xx_phone.weather import encode_places

        prefs["places_json"] = encode_places(places)
        self._save_prefs(prefs)

    def selected_zip(self) -> str | None:
        prefs = self._load_prefs()
        from xx_phone.weather import decode_places

        stored = decode_places(prefs.get("places_json"))
        selected = prefs.get("selected_zip")
        if selected and any(p.zip == selected for p in stored):
            return selected
        return stored[0].zip if stored else None

    def selected(self):
        places = self.places()
        if not places:
            return None
        selected = self.selected_zip()
        return next((p for p in places if p.zip == selected), places[0])

    def set_selected_zip(self, zip_code: str | None) -> None:
        prefs = self._load_prefs()
        if not zip_code:
            prefs.pop("selected_zip", None)
        else:
            prefs["selected_zip"] = zip_code
        self._save_prefs(prefs)

    def add_place(self, place) -> None:
        prefs = self._load_prefs()
        from xx_phone.weather import decode_places

        current = decode_places(prefs.get("places_json"))
        if any(p.zip == place.zip for p in current):
            current = [place if p.zip == place.zip else p for p in current]
        else:
            current = current + [place]
            self.clear_caches(place.zip)
        self._write_places(prefs, current)
        prefs = self._load_prefs()
        prefs["selected_zip"] = place.zip
        self._save_prefs(prefs)

    def remove_place(self, zip_code: str) -> None:
        prefs = self._load_prefs()
        from xx_phone.weather import decode_places

        current = [p for p in decode_places(prefs.get("places_json"))
                   if p.zip != zip_code]
        self._write_places(prefs, current)
        prefs = self._load_prefs()
        if prefs.get("selected_zip") == zip_code:
            if current:
                prefs["selected_zip"] = current[0].zip
            else:
                prefs.pop("selected_zip", None)
            self._save_prefs(prefs)
        self.remove_triggers(zip_code)
        self.clear_caches(zip_code)

    def update_labels(self, zip_code: str, city: str, state: str) -> None:
        if not city.strip():
            return
        prefs = self._load_prefs()
        from xx_phone.weather import decode_places

        current = [p if p.zip != zip_code
                   else p.__class__(zip=p.zip, city=city, state=state,
                                    lat=p.lat, lon=p.lon)
                   for p in decode_places(prefs.get("places_json"))]
        self._write_places(prefs, current)

    # -- units ---------------------------------------------------------------

    def units(self):
        from xx_phone.weather import Units

        raw = self._load_prefs().get("units", "F")
        return Units.CELSIUS if raw == "C" else Units.FAHRENHEIT

    def set_units(self, units) -> None:
        prefs = self._load_prefs()
        from xx_phone.weather import Units

        prefs["units"] = "C" if units == Units.CELSIUS else "F"
        self._save_prefs(prefs)

    # -- alerts -----------------------------------------------------------------

    def alerts_enabled(self) -> bool:
        return bool(self._load_prefs().get("alerts_enabled", False))

    def set_alerts_enabled(self, enabled: bool) -> None:
        prefs = self._load_prefs()
        prefs["alerts_enabled"] = bool(enabled)
        self._save_prefs(prefs)

    def triggers_for(self, zip_code: str):
        from xx_phone.weather import decode_triggers

        return decode_triggers(
            self._load_prefs().get("alerts_json")).get(zip_code)

    def ensure_triggers(self, zip_code: str):
        from xx_phone.weather import ZipAlertTriggers

        found = self.triggers_for(zip_code)
        if found is not None:
            return found
        defaults = ZipAlertTriggers(precip_enabled=True)
        self.set_triggers(zip_code, defaults)
        return defaults

    def set_triggers(self, zip_code: str, triggers) -> None:
        from xx_phone.weather import decode_triggers, encode_triggers

        prefs = self._load_prefs()
        mapping = decode_triggers(prefs.get("alerts_json"))
        mapping[zip_code] = triggers
        prefs["alerts_json"] = encode_triggers(mapping)
        self._save_prefs(prefs)

    def remove_triggers(self, zip_code: str) -> None:
        from xx_phone.weather import decode_triggers, encode_triggers

        prefs = self._load_prefs()
        mapping = decode_triggers(prefs.get("alerts_json"))
        if zip_code not in mapping:
            return
        del mapping[zip_code]
        if mapping:
            prefs["alerts_json"] = encode_triggers(mapping)
        else:
            prefs.pop("alerts_json", None)
        self._save_prefs(prefs)

    def dedupe_keys(self) -> list:
        raw = self._load_prefs().get("alert_dedupe", [])
        return [k for k in raw if isinstance(k, str)] if isinstance(
            raw, list) else []

    def add_dedupe_keys(self, keys) -> None:
        from xx_phone.weather import remember_keys

        keys = [k for k in keys if k]
        if not keys:
            return
        prefs = self._load_prefs()
        prefs["alert_dedupe"] = remember_keys(self.dedupe_keys(), keys)
        self._save_prefs(prefs)

    # -- per-ZIP caches -----------------------------------------------------------

    def _cache(self, zip_code: str, name: str) -> Path:
        return self.cache_dir / f"{zip_code}_{name}"

    def read_cache(self, zip_code: str, name: str) -> str | None:
        try:
            return self._cache(zip_code, name).read_text(encoding="utf-8")
        except OSError:
            return None

    def write_cache(self, zip_code: str, name: str, body: str) -> None:
        tmp = self.cache_dir / f"{zip_code}_{name}.{time.time_ns()}.tmp"
        try:
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(self._cache(zip_code, name))
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    def delete_cache(self, zip_code: str, name: str) -> None:
        try:
            self._cache(zip_code, name).unlink()
        except OSError:
            pass

    def clear_caches(self, zip_code: str) -> None:
        for name in (NWS_DAILY, NWS_HOURLY, NWS_OBS, OM_BODY, EXTRAS,
                     FETCH_STAMP):
            self.delete_cache(zip_code, name)

    def read_stamp(self, zip_code: str) -> int:
        try:
            return int((self.read_cache(zip_code, FETCH_STAMP) or "").strip())
        except ValueError:
            return 0

    def write_stamp(self, zip_code: str, fetched_at_ms: int) -> None:
        self.write_cache(zip_code, FETCH_STAMP, str(fetched_at_ms))

    def write_extras(self, zip_code: str, extra) -> None:
        payload: dict = {}
        if extra.uv_index is not None:
            payload["uv"] = extra.uv_index
        if extra.visibility_mi is not None:
            payload["vis"] = extra.visibility_mi
        if extra.pressure_inhg is not None:
            payload["pres"] = extra.pressure_inhg
        if extra.dewpoint_f is not None:
            payload["dew"] = extra.dewpoint_f
        if extra.pressure_station_level:
            payload["presStation"] = True
        if extra.sun is not None and extra.sun.sunrise is not None \
                and extra.sun.sunset is not None:
            payload["sunrise"] = extra.sun.sunrise.isoformat()
            payload["sunset"] = extra.sun.sunset.isoformat()
        self.write_cache(zip_code, EXTRAS, json.dumps(payload))

    def read_extras(self, zip_code: str):
        from xx_phone.weather import Extras, SunTimes

        raw = self.read_cache(zip_code, EXTRAS)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None

        def _num(key: str):
            value = payload.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        sun = None
        if payload.get("sunrise") and payload.get("sunset"):
            try:
                sun = SunTimes(datetime.fromisoformat(payload["sunrise"]),
                               datetime.fromisoformat(payload["sunset"]))
            except ValueError:
                sun = None
        return Extras(uv_index=_num("uv"), visibility_mi=_num("vis"),
                      pressure_inhg=_num("pres"), dewpoint_f=_num("dew"),
                      sun=sun,
                      pressure_station_level=bool(
                          payload.get("presStation", False)))

    def load_cached(self, place):
        from xx_phone.weather import (NwsBodies, assemble_nws, merge_extras,
                                      parse_openmeteo)

        fetched_at = self.read_stamp(place.zip)
        daily = self.read_cache(place.zip, NWS_DAILY)
        hourly = self.read_cache(place.zip, NWS_HOURLY)
        if daily is not None and hourly is not None:
            try:
                data = assemble_nws(
                    place,
                    NwsBodies(daily=daily, hourly=hourly,
                              obs=self.read_cache(place.zip, NWS_OBS),
                              city=None, state=None),
                    fetched_at)
                extra = self.read_extras(place.zip)
                return merge_extras(data, extra) if extra else data
            except Exception:  # noqa: BLE001 - corrupt cache reads as empty
                pass
        om_body = self.read_cache(place.zip, OM_BODY)
        if om_body is not None:
            try:
                return parse_openmeteo(place, om_body, fetched_at)
            except Exception:  # noqa: BLE001 - corrupt cache reads as empty
                pass
        return None
