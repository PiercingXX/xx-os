"""Metainfo: description, releases and content rating are complete."""

import xml.etree.ElementTree as ET
from pathlib import Path

DATA = Path(__file__).parent.parent / "data" / "io.piercingxx.XXRadio.metainfo.xml"


def _root():
    return ET.parse(DATA).getroot()


def test_description_releases_and_rating():
    root = _root()
    assert root.find("id").text == "io.piercingxx.XXRadio"
    paragraphs = root.findall("description/p")
    assert len(paragraphs) >= 1
    assert all((part.text or "").strip() for part in paragraphs)
    releases = root.findall("releases/release")
    assert releases and all(item.get("version") and item.get("date")
                            for item in releases)
    rating = root.find("content_rating")
    assert rating is not None and rating.get("type") == "oars-1.1"
    attributes = {item.get("id"): (item.text or "").strip()
                  for item in rating.findall("content_attribute")}
    assert len(attributes) >= 20
    assert set(attributes.values()) <= {"none", "mild", "moderate", "intense"}


def test_launchable_and_no_screenshot_placeholders():
    root = _root()
    launchable = root.find("launchable")
    assert launchable is not None
    assert launchable.text == "io.piercingxx.XXRadio.desktop"
    assert root.find("screenshots") is None
