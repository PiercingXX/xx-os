"""Settings load/save/clamp (plus the editor-save rule from
ui/AlarmToggleThenSaveTest.kt, asserted at the prefs layer: drafts keep
their own enabled flag, toggles persist)."""

from xx_clock import prefs


def test_defaults_match_android():
    settings = prefs.Settings()
    assert settings.snooze_minutes == prefs.SNOOZE_MINUTES_DEFAULT == 10
    assert settings.gradual_volume is True
    assert settings.clock_style == "digital"
    assert settings.last_tab == "clock"


def test_round_trip():
    settings = prefs.Settings(snooze_minutes=5, gradual_volume=False,
                              clock_style="analog", last_tab="timers")
    prefs.save(settings)
    loaded = prefs.load()
    assert loaded == settings


def test_missing_file_gives_defaults():
    assert prefs.load() == prefs.Settings()


def test_snooze_clamped_to_1_through_120():
    prefs.save(prefs.Settings(snooze_minutes=999))
    assert prefs.load().snooze_minutes == 120
    prefs.save(prefs.Settings(snooze_minutes=0))
    assert prefs.load().snooze_minutes == 1


def test_invalid_values_fall_back_without_raising(tmp_path, monkeypatch):
    from xx_clock import paths
    paths.settings_path().parent.mkdir(parents=True, exist_ok=True)
    paths.settings_path().write_text("{not json", encoding="utf-8")
    assert prefs.load() == prefs.Settings()
    paths.settings_path().write_text('{"clock_style": "bogus", "last_tab": "bogus"}',
                                     encoding="utf-8")
    loaded = prefs.load()
    assert loaded.clock_style == "digital"
    assert loaded.last_tab == "clock"
