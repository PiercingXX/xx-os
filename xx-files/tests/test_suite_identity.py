"""Suite identity: desktop entry, D-Bus activation, app id and icon agree.

``DBusActivatable=true`` without a matching ``<app-id>.service`` breaks
drawer launches (the bus has nothing to start), and a desktop ``Icon=``
that meson never installs renders as a blank tile.
"""

from __future__ import annotations

import configparser
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DESKTOP_ID = "io.piercingxx.XXFiles"
DESKTOP_FILE = DATA / "io.piercingxx.XXFiles.desktop.in"
EXPECTED_NAME = "xx-files"
ACTIVATABLE = True
SERVICE_FILE = DATA / "io.piercingxx.XXFiles.service.in"
LAUNCHER = "xx-files"
APP_ID_SOURCES = ["src/xx_files/__init__.py"]
METAINFO_FILE = DATA / "io.piercingxx.XXFiles.metainfo.xml"
ICON_FILE = DATA / "icons" / "io.piercingxx.XXFiles.svg"
TITLE_NEEDLE = 'self.win.set_title("xx-files")'
TITLE_SOURCE = "src/xx_files/main.py"


def _desktop() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    assert DESKTOP_FILE.is_file(), f"missing {DESKTOP_FILE}"
    parser.read(DESKTOP_FILE, encoding="utf-8")
    return parser


def test_desktop_id_matches_filename() -> None:
    assert DESKTOP_FILE.name.startswith(DESKTOP_ID)


def test_desktop_name_and_generic() -> None:
    entry = _desktop()["Desktop Entry"]
    assert entry["Name"] == EXPECTED_NAME
    assert entry["GenericName"] == "Files"


def test_desktop_icon_names_installed_id() -> None:
    entry = _desktop()["Desktop Entry"]
    assert entry["Icon"] == DESKTOP_ID


def test_desktop_registers_directory_handler() -> None:
    entry = _desktop()["Desktop Entry"]
    mime = entry.get("MimeType", "")
    assert "inode/directory" in mime


def test_activation_has_matching_service() -> None:
    entry = _desktop()["Desktop Entry"]
    if not ACTIVATABLE:
        assert entry.get("DBusActivatable", "false").lower() == "false"
        return
    assert entry.get("DBusActivatable", "").lower() == "true"
    assert SERVICE_FILE.is_file(), f"missing {SERVICE_FILE}"
    service = configparser.ConfigParser(interpolation=None)
    service.read(SERVICE_FILE, encoding="utf-8")
    assert service["D-BUS Service"]["Name"] == DESKTOP_ID
    exec_line = service["D-BUS Service"]["Exec"]
    assert LAUNCHER in exec_line
    assert "--gapplication-service" in exec_line
    build = (ROOT / "meson.build").read_text(encoding="utf-8")
    assert SERVICE_FILE.name in build


def test_application_id_matches_desktop_id() -> None:
    for rel in APP_ID_SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert DESKTOP_ID in text, rel


def test_metainfo_name() -> None:
    root = ET.parse(METAINFO_FILE).getroot()
    assert root.find("name").text == EXPECTED_NAME
    assert root.find("id").text == DESKTOP_ID


def test_metainfo_provides_directory() -> None:
    root = ET.parse(METAINFO_FILE).getroot()
    media = [m.text for m in root.iter("mediatype")]
    assert "inode/directory" in media


def test_icon_installed_by_meson() -> None:
    assert ICON_FILE.is_file(), f"missing {ICON_FILE}"
    text = ICON_FILE.read_text(encoding="utf-8")
    assert "<svg" in text and "viewBox" in text
    build = (ROOT / "meson.build").read_text(encoding="utf-8")
    assert ICON_FILE.name in build
    assert "'scalable', 'apps'" in build or '"scalable", "apps"' in build


def test_window_title() -> None:
    text = (ROOT / TITLE_SOURCE).read_text(encoding="utf-8")
    assert TITLE_NEEDLE in text
