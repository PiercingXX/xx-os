"""Static guards: no network, honest D-Bus reply types, valid GTK signals."""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "xx_clock"
SOURCES = list(SRC.glob("*.py")) + list((SRC / "ui").glob("*.py"))


def test_no_network_code():
    banned = re.compile(r"\b(urllib|requests|http\.client|socket|ssl)\b")
    hits = []
    for path in SOURCES:
        for index, line in enumerate(path.read_text().splitlines(), 1):
            if banned.search(line):
                hits.append(f"{path.name}:{index}:{line.strip()}")
    assert hits == [], f"network code found: {hits}"


def test_no_plain_string_reply_type():
    for path in SOURCES:
        text = path.read_text()
        for match in re.finditer(r"call_sync\((?:[^()]|\([^()]*\))*"
                                 r"reply_type\s*=\s*\"",
                                 text):
            raise AssertionError(f"plain-string reply_type: {path.name}:{match.group(0)[:60]}")
        assert 'VariantType.new("' not in text or "GLib.VariantType" in text


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
    for path in SOURCES:
        for match in re.finditer(r'''\.connect\(\s*["']([\w-]+)["']''',
                                 path.read_text()):
            used.add(match.group(1))
    unknown = {name for name in used
               if name not in known and not name.startswith("notify::")}
    assert unknown == set(), f"unknown GTK signals: {sorted(unknown)}"


def test_meson_installs_the_whole_package():
    # A hand-kept module list missed media_apps.py in xx-auto, so features
    # read "not installed" on a real phone. Install the package directory.
    meson = (Path(__file__).resolve().parent.parent / 'meson.build').read_text()
    assert "install_subdir(\n  join_paths('src', 'xx_clock')" in meson
    assert "'src/xx_clock/" not in meson


def test_daemon_bus_name_differs_from_app_id():
    from xx_clock import DAEMON_BUS_NAME, __app_id__
    assert DAEMON_BUS_NAME != __app_id__
    daemon_source = (SRC / "daemon.py").read_text(encoding="utf-8")
    assert __app_id__ not in daemon_source.replace(DAEMON_BUS_NAME, "")


def test_activation_and_unit_files_installed():
    meson = (Path(__file__).resolve().parent.parent / 'meson.build').read_text()
    for name in ("io.piercingxx.XXClock.service.in",
                 "io.piercingxx.XXClock.Daemon.service.in",
                 "xx-clock-daemon.service.in"):
        assert name in meson, name
    for rel in ("data/io.piercingxx.XXClock.service.in",
                "data/io.piercingxx.XXClock.Daemon.service.in",
                "data/systemd/xx-clock-daemon.service.in"):
        assert (Path(__file__).resolve().parent.parent / rel).is_file(), rel
