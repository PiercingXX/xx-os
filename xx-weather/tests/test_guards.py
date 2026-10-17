"""Static guards: install surface, honest main-loop behavior, valid signals."""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "xx_weather"
APP_ID = "io.piercingxx.XXWeather"


def test_meson_installs_the_whole_package():
    # A hand-kept module list missed a module on a sibling app. Install
    # the package directory instead.
    meson = (Path(__file__).resolve().parent.parent / 'meson.build'
             ).read_text()
    assert "install_subdir(\n  join_paths('src', 'xx_weather')" in meson
    assert "'src/xx_weather/" not in meson


def test_no_main_loop_blockers_in_window():
    banned = re.compile(
        r"\b(subprocess\.(run|check_output)|time\.sleep|call_sync)\b")
    hits = []
    for path in SRC.glob("*.py"):
        if path.name in ("worker.py",):
            continue
        for index, line in enumerate(path.read_text().splitlines(), 1):
            if banned.search(line):
                hits.append(f"{path.name}:{index}:{line.strip()}")
    assert hits == [], f"main-loop blockers: {hits}"


def test_refresh_uses_worker_plus_idle_add():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert "threading.Thread" in text
    assert "GLib.idle_add" in text or "idle_add" in text
    worker = (SRC / "worker.py").read_text(encoding="utf-8")
    assert "GLib." not in worker and "Gtk." not in worker
    assert "import gi" not in worker and "from gi" not in worker


def test_background_service_does_not_steal_app_id():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert "__app_id__" in text
    init = (SRC / "__init__.py").read_text(encoding="utf-8")
    assert APP_ID in init
    data = Path(__file__).resolve().parent.parent / "data"
    services = list(data.glob("*.service.in"))
    assert services == [], f"daemon would steal {APP_ID}: {services}"
    timer = (data / "systemd" / "xx-weather-refresh.timer"
             ).read_text(encoding="utf-8")
    assert "OnUnitActiveSec=15min" in timer
    service = (data / "systemd" / "xx-weather-refresh.service.in"
               ).read_text(encoding="utf-8")
    assert "--refresh-once" in service


def test_gtk_signal_names_valid():
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GObject, Gtk
    except Exception:
        import pytest

        pytest.skip("gi/GTK is missing")
    import warnings

    known: set[str] = set()
    warnings.simplefilter("ignore")
    for module in (Gtk, Adw, Gio):
        for attr in dir(module):
            obj = getattr(module, attr, None)
            gtype = getattr(obj, "__gtype__", None)
            if gtype is None:
                continue
            try:
                GObject.type_class_ref(gtype)
                known.update(GObject.signal_list_names(gtype))
            except Exception:
                continue
    known.add("notify")
    used = set()
    for path in SRC.glob("*.py"):
        for match in re.finditer(r'''\.connect\(\s*["']([\w-]+)["']''',
                                 path.read_text()):
            used.add(match.group(1))
    unknown = {name for name in used
               if name not in known and not name.startswith("notify::")}
    assert unknown == set(), f"unknown GTK signals: {sorted(unknown)}"


def test_notification_tap_action_wired():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert '"open-zip"' in text
    assert "send_notification" in (SRC / "notify.py").read_text()


def test_no_secrets_in_source():
    banned = re.compile(r"\b(token|passwd|imei)\b\s*=", re.IGNORECASE)
    hits = []
    for path in SRC.glob("*.py"):
        for index, line in enumerate(path.read_text().splitlines(), 1):
            if banned.search(line):
                hits.append(f"{path.name}:{index}:{line.strip()}")
    assert hits == [], f"secret-shaped assignments: {hits}"
