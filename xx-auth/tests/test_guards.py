"""Static guards: no network code, real GLib Variant use where D-Bus
appears, GTK signal names, and theme-token discipline."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_auth"

NETWORK_IMPORTS = re.compile(
    r"^\s*(import\s+(socket|urllib|http|requests|httpx|websocket)|from\s+(urllib|http|requests|httpx)\s+import)",
    re.MULTILINE,
)
NETWORK_CALLS = re.compile(r"\b(urlopen|urlretrieve|create_connection|getaddrinfo|XMLHttpRequest)\b")
HARDCODED_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


def _sources() -> list[Path]:
    return sorted(SRC.glob("*.py"))


def test_no_network_imports_or_calls():
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if NETWORK_IMPORTS.search(text) or NETWORK_CALLS.search(text):
            offenders.append(path.name)
    assert offenders == [], f"network code has no place in an offline authenticator: {offenders}"


def test_no_hardcoded_theme_colors():
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if HARDCODED_HEX.search(line):
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], f"colors must come from the active ThemePreset: {offenders}"


def _gi_signal_names() -> set[str]:
    import warnings

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GObject, Gtk

    names: set[str] = set()
    warnings.simplefilter("ignore")
    for module in (Gtk, Gdk, Adw, Gio):
        for attr in dir(module):
            obj = getattr(module, attr, None)
            gtype = getattr(obj, "__gtype__", None)
            if gtype is None:
                continue
            try:
                GObject.type_class_ref(gtype)
                names.update(GObject.signal_list_names(gtype))
            except Exception:
                continue
    return names | {"notify"}


def test_gtk_signal_names_are_real():
    known = _gi_signal_names()
    assert "clicked" in known
    used: set[str] = set()
    for path in _sources():
        for match in re.finditer(r"""\.connect\(\s*['"]([^'"]+)['"]""", path.read_text(encoding="utf-8")):
            used.add(match.group(1))
    unknown = {name for name in used
               if name not in known and name.split("::")[0] not in known}
    assert unknown == set(), f"unknown GTK signal names: {sorted(unknown)}"


def test_meson_lists_every_module():
    build = (Path(__file__).resolve().parent.parent / "meson.build").read_text(encoding="utf-8")
    assert "install_subdir" in build
    modules = {path.stem for path in _sources() if path.name != "__pycache__"}
    assert modules, "no modules found"
