"""Suite identity for the shell and the keyboard.

The XX-WM session entry stays ``XX-WM`` while every suite app follows
the ``xx-<noun>`` brand; both ship an ``Icon=`` that meson installs to
the hicolor theme so no tile renders blank.
"""

import configparser
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

REPO = Path(__file__).parent.parent
LANE = REPO.parent

WM_DESKTOP = REPO / "launcher" / "data" / "io.piercingxx.XXWM.desktop.in"
WM_ICON = REPO / "launcher" / "data" / "icons" / "io.piercingxx.XXWM.svg"
WM_BUILD = REPO / "launcher" / "meson.build"

KB = LANE / "xx-keyboard"
KB_DESKTOPS = [
    KB / "data" / "io.piercingxx.XXKeyboard.desktop.in.in",
    KB / "data" / "sm.puri.OSK0.desktop.in.in",
]
KB_METAINFO = KB / "data" / "io.piercingxx.XXKeyboard.metainfo.xml.in"
KB_ICON = KB / "data" / "icons" / "io.piercingxx.XXKeyboard.svg"
KB_BUILD = KB / "data" / "meson.build"


def _read_desktop(path: Path) -> configparser.SectionProxy:
    parser = configparser.ConfigParser(interpolation=None)
    assert path.is_file(), f"missing {path}"
    parser.read(path, encoding="utf-8")
    return parser["Desktop Entry"]


def test_shell_session_entry_stays_xx_wm() -> None:
    entry = _read_desktop(WM_DESKTOP)
    assert entry["Name"] == "XX-WM"
    assert entry["Icon"] == "io.piercingxx.XXWM"
    assert WM_ICON.is_file()
    assert "viewBox" in WM_ICON.read_text(encoding="utf-8")
    build = WM_BUILD.read_text(encoding="utf-8")
    assert WM_ICON.name in build
    assert "scalable" in build and "apps" in build


def test_keyboard_desktops_use_suite_name_and_icon() -> None:
    if not KB.is_dir():
        pytest.skip("xx-keyboard sibling checkout not present")
    for desktop in KB_DESKTOPS:
        entry = _read_desktop(desktop)
        assert entry["Name"] == "xx-keyboard", desktop
        assert entry["Icon"] == "io.piercingxx.XXKeyboard", desktop


def test_keyboard_metainfo_and_icon_installed() -> None:
    if not KB.is_dir():
        pytest.skip("xx-keyboard sibling checkout not present")
    root = ET.parse(KB_METAINFO).getroot()
    assert root.find("name").text == "xx-keyboard"
    assert KB_ICON.is_file()
    assert "viewBox" in KB_ICON.read_text(encoding="utf-8")
    build = KB_BUILD.read_text(encoding="utf-8")
    assert KB_ICON.name in build
    assert "scalable" in build and "apps" in build
