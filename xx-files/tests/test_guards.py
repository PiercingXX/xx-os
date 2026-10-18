"""Static guards: no network, honest D-Bus reply types, valid GTK signals."""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "xx_files"


def test_no_network_code():
    banned = re.compile(r"\b(urllib|requests|http\.client|socket|ssl)\b")
    hits = []
    for path in SRC.glob("*.py"):
        for index, line in enumerate(path.read_text().splitlines(), 1):
            if banned.search(line):
                hits.append(f"{path.name}:{index}:{line.strip()}")
    assert hits == [], f"network code found: {hits}"


def test_no_plain_string_reply_type():
    hits = []
    for path in SRC.glob("*.py"):
        text = path.read_text()
        for match in re.finditer(r"call_sync\((?:[^()]|\([^()]*\))*"
                                 r"reply_type\s*=\s*\"",
                                 text):
            hits.append(f"{path.name}:{match.group(0)[:60]}")
    assert hits == [], f"plain-string reply_type: {hits}"
    for path in SRC.glob("*.py"):
        text = path.read_text()
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
    for path in SRC.glob("*.py"):
        for match in re.finditer(r'''\.connect\(\s*["']([\w-]+)["']''',
                                 path.read_text()):
            used.add(match.group(1))
    unknown = {name for name in used
               if name not in known and not name.startswith("notify::")}
    assert unknown == set(), f"unknown GTK signals: {sorted(unknown)}"


def test_meson_installs_the_whole_package():
    # A hand-kept module list missed a module on a sibling, so the
    # package directory installs as one unit here.
    meson = (Path(__file__).resolve().parent.parent / 'meson.build').read_text()
    assert "install_subdir(\n  join_paths('src', 'xx_files')" in meson
    assert "'src/xx_files/" not in meson


def test_no_mainloop_blocking_calls():
    banned = re.compile(
        r"\b(subprocess\.(run|check_output|check_call)|time\.sleep|"
        r"call_sync|new_for_uri_sync)\b")
    hits = []
    for path in SRC.glob("*.py"):
        if path.name in ("jobs.py",):
            continue
        for index, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", '"""', "'''")):
                continue
            if banned.search(line):
                hits.append(f"{path.name}:{index}:{stripped}")
    assert hits == [], f"main-loop blocking calls: {hits}"


def test_minimal_ui_no_trash_confirm_or_popover_menu():
    """Delete moves to the Trash at once (Restore undoes it), and the
    overflow is a bottom sheet like every other menu in the app."""
    text = (SRC / "main.py").read_text()
    assert "Move to Trash?" not in text
    assert "Gtk.MenuButton" not in text
    assert "Original unknown" not in text
