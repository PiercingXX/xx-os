"""Store: prefs, selection, units, triggers, caches. No network."""
import json
import os
import stat

from conftest import make_store


def test_dirs_created_0700(tmp_path):
    from xx_weather.store import Store

    store = Store(config_dir=tmp_path / "c", data_dir=tmp_path / "d",
                  cache_dir=tmp_path / "k")
    for path in (store.config_dir, store.data_dir, store.cache_dir):
        assert path.is_dir()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_empty_store_selects_nothing():
    store = make_store()
    assert store.places() == []
    assert store.selected() is None
    assert store.selected_zip() is None


def test_add_and_select_place():
    from xx_phone.weather import Place

    store = make_store()
    denver = Place("80202", "Denver", "CO", 39.73, -104.99)
    austin = Place("78701", "Austin", "TX", 30.26, -97.74)
    store.add_place(denver)
    assert store.selected_zip() == "80202"
    store.add_place(austin)
    assert store.selected_zip() == "78701"
    assert [p.zip for p in store.places()] == ["80202", "78701"]
    store.set_selected_zip("80202")
    assert store.selected().city == "Denver"


def test_duplicate_add_updates_and_selects():
    from xx_phone.weather import Place

    store = make_store()
    store.add_place(Place("80202", "Denver", "CO", 39.73, -104.99))
    store.add_place(Place("80202", "LoDo", "CO", 39.75, -105.0))
    assert len(store.places()) == 1
    assert store.places()[0].city == "LoDo"


def test_remove_place_clears_selection_and_caches():
    from xx_phone.weather import Place

    store = make_store()
    store.add_place(Place("80202", "Denver", "CO", 39.73, -104.99))
    store.write_cache("80202", "cache_fetched_at.txt", "123")
    store.remove_place("80202")
    assert store.places() == []
    assert store.selected_zip() is None
    assert store.read_cache("80202", "cache_fetched_at.txt") is None


def test_units_round_trip():
    from xx_phone.weather import Units

    store = make_store()
    assert store.units() == Units.FAHRENHEIT
    store.set_units(Units.CELSIUS)
    assert store.units() == Units.CELSIUS


def test_alert_prefs_round_trip():
    from xx_phone.weather import ZipAlertTriggers

    store = make_store()
    assert store.alerts_enabled() is False
    store.set_alerts_enabled(True)
    assert store.alerts_enabled() is True
    assert store.triggers_for("80202") is None
    triggers = store.ensure_triggers("80202")
    assert triggers.precip_enabled is True
    store.set_triggers("80202", ZipAlertTriggers(temp_enabled=True,
                                                 temp_at_or_below_f=20))
    assert store.triggers_for("80202").temp_at_or_below_f == 20
    store.remove_triggers("80202")
    assert store.triggers_for("80202") is None


def test_dedupe_keys_ordered_and_bounded():
    store = make_store()
    assert store.dedupe_keys() == []
    store.add_dedupe_keys(["a", "b"])
    store.add_dedupe_keys(["c"])
    assert store.dedupe_keys() == ["a", "b", "c"]


def test_corrupt_prefs_read_as_empty(tmp_path, monkeypatch):
    from xx_weather.store import Store

    config = tmp_path / "config" / "xx-weather"
    config.mkdir(parents=True)
    (config / "settings.json").write_text("not json", encoding="utf-8")
    store = Store(config_dir=config)
    assert store.places() == []
    assert store.units().value == "F"


def test_cache_write_read_delete():
    store = make_store()
    assert store.read_cache("80202", "x.json") is None
    store.write_cache("80202", "x.json", "body")
    assert store.read_cache("80202", "x.json") == "body"
    store.delete_cache("80202", "x.json")
    assert store.read_cache("80202", "x.json") is None


def test_stamp_defaults_zero_and_round_trips():
    store = make_store()
    assert store.read_stamp("80202") == 0
    store.write_stamp("80202", 1700000000000)
    assert store.read_stamp("80202") == 1700000000000


def test_load_cached_empty_without_caches():
    from xx_phone.weather import Place

    store = make_store()
    place = Place("80202", "Denver", "CO", 39.73, -104.99)
    assert store.load_cached(place) is None


def test_load_cached_from_demo_seed():
    from xx_weather import demo

    store = make_store()
    demo.seed_current(store)
    data = store.load_cached(store.selected())
    assert data is not None
    assert data.source == "NWS"
    assert len(data.hourly) == 24
    assert data.daily
    assert data.current.temp_f > 0


def test_load_cached_survives_corrupt_body():
    from xx_phone.weather import Place
    from xx_weather.store import NWS_DAILY, NWS_HOURLY

    store = make_store()
    place = Place("80202", "Denver", "CO", 39.73, -104.99)
    store.write_cache("80202", NWS_DAILY, "garbage")
    store.write_cache("80202", NWS_HOURLY, "garbage")
    store.write_stamp("80202", 1700000000000)
    assert store.load_cached(place) is None


def test_prefs_file_is_plain_json():
    from xx_phone.weather import Place

    store = make_store()
    store.add_place(Place("80202", "Denver", "CO", 39.73, -104.99))
    raw = (store.config_dir / "settings.json").read_text(encoding="utf-8")
    assert json.loads(raw)["selected_zip"] == "80202"
    assert "token" not in raw and "password" not in raw.lower()


def test_home_env_selects_store_root(monkeypatch, tmp_path):
    from xx_weather.store import Store

    home = tmp_path / "elsewhere"
    (home / ".config").mkdir(parents=True)
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".cache").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    store = Store()
    assert str(store.config_dir).startswith(str(home))
    assert os.environ["HOME"] == str(home)
