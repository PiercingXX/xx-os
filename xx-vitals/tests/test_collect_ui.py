"""Milestone 4 UI contract: thin widgets, phone lessons, install wiring."""

import threading
from pathlib import Path

from xx_vitals import prefs
from xx_vitals.ui import home, profile

SRC = Path(__file__).parent.parent / "src" / "xx_vitals"
ROOT = Path(__file__).parent.parent


def test_collect_wrappers_stay_thin_and_headless():
    assert callable(home.collect_status)
    assert callable(profile.collect_status)
    assert callable(profile.set_collect_enabled)
    assert callable(profile.tick_collect_now)
    assert callable(profile.collect_tick_in_background)
    assert callable(profile.systemd_user_available)
    assert callable(profile.set_count_while_screen_off)
    assert callable(profile.start_resident_in_background)
    assert callable(profile.maybe_start_fallback)
    # A row subtitle, not a paragraph (the minimal rule).
    assert profile.screen_off_copy() == "Uses more battery"
    cfg = prefs.AppConfig()
    assert home.collect_status(cfg)["title"] == "Step counting is off"
    assert profile.collect_status(cfg, accelerometer_available=True)["title"] == (
        "Step counting is off"
    )


def test_opt_in_persists_and_defaults_off():
    cfg = prefs.AppConfig()
    assert cfg.count_while_screen_off is False
    saved = []
    line = profile.set_collect_enabled(cfg, True, save=saved.append)
    assert cfg.collect_enabled is True and saved == [cfg]
    assert line["enabled"] is True
    profile.set_collect_enabled(cfg, False, save=lambda _c: None)
    assert cfg.collect_enabled is False
    profile.set_count_while_screen_off(cfg, True, save=saved.append)
    assert cfg.count_while_screen_off is True and saved[-1] is cfg


def test_tick_runs_off_the_calling_thread():
    from xx_vitals.store.db import Database

    db = Database(":memory:")
    db.init()
    cfg = prefs.AppConfig(collect_enabled=True)
    thread = profile.collect_tick_in_background(
        db, cfg, None,
        readers={"hardware": None, "now_ms": 0, "today": "2026-08-27"},
    )
    assert isinstance(thread, threading.Thread)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert cfg.step_baseline is None


def test_phone_window_has_no_titlebar_chrome():
    # The xx_ui restyle dropped the Adw HeaderBar/ToolbarView shell: a
    # plain undecorated window carries its own PageHeader per page.
    src = (SRC / "ui" / "shell.py").read_text(encoding="utf-8")
    assert "set_decorated(False)" in src
    assert "Gtk.ApplicationWindow" in src
    assert "Adw.HeaderBar" not in src
    assert "Adw.ToolbarView" not in src
    assert "set_titlebar" not in src
    assert "bottom_tabs" in src


def test_display_css_come_from_widgets_not_the_app():
    for name in ("shell.py", "home.py", "profile.py", "browse.py", "journal.py", "entry.py"):
        src = (SRC / "ui" / name).read_text(encoding="utf-8")
        assert "grab_focus" not in src, name
        assert "get_display()" not in src, name
        assert "xx_ui" in src or "widgets" in src, name
    assert "Gdk.Display.get_default()" in (SRC / "ui" / "shell.py").read_text(encoding="utf-8")


def test_profile_collect_uses_switch_and_worker_thread():
    src = (SRC / "ui" / "profile.py").read_text(encoding="utf-8")
    # Switches come from the design system (inverted, never Adwaita blue).
    assert "make_switch" in src
    assert "run_in_background" in src
    assert "Sample now" in src
    assert "start_resident_in_background" in src
    assert "screen_off_copy()" in src
    for token in ("subprocess.run", "subprocess.check_output", "time.sleep", "call_sync"):
        assert token not in src, token


def test_shell_starts_fallback_while_window_open():
    src = (SRC / "ui" / "shell.py").read_text(encoding="utf-8")
    assert "maybe_start_fallback" in src
    assert "close-request" in (SRC / "ui" / "profile.py").read_text(encoding="utf-8")


def test_collect_install_wiring():
    meson = (ROOT / "meson.build").read_text(encoding="utf-8")
    for rider in ("burst.py", "inhibit.py", "resident.py", "sensors.py", "service.py", "status.py"):
        assert rider in meson, rider
    assert "xx-vitals-collect-hw.service.in" in meson
    launcher = (ROOT / "data" / "xx-vitals-collect.in").read_text(encoding="utf-8")
    # Run as a module with the datadir on PYTHONPATH: running the file by
    # path put xx_vitals/ itself on sys.path and the import failed.
    assert "-m xx_vitals.collect.service" in launcher
    assert 'PYTHONPATH="@app_dir@' in launcher
    ui = (ROOT / "data" / "xx-vitals.in").read_text(encoding="utf-8")
    assert "-m xx_vitals " in ui and "__main__.py" not in ui
    assert "sys.exit(0)" not in launcher
    timer = (ROOT / "data" / "systemd" / "xx-vitals-collect.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=15min" in timer
    assert "Unit=xx-vitals-collect-hw.service" in timer
    service = (ROOT / "data" / "systemd" / "xx-vitals-collect.service.in").read_text(
        encoding="utf-8"
    )
    assert "Type=simple" in service
    assert "Restart=on-failure" in service
    assert "Nice=10" in service
    assert "BusName" not in service
    hw_service = (ROOT / "data" / "systemd" / "xx-vitals-collect-hw.service.in").read_text(
        encoding="utf-8"
    )
    assert "Type=oneshot" in hw_service and "--one-shot" in hw_service
    config_doc = (ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    assert "collect_enabled" in config_doc
    assert "count_while_screen_off" in config_doc
    assert "systemctl --user" in config_doc
    assert "while the phone is awake" in config_doc
