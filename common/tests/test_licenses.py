"""The shared About/licenses builders: one data table per app, one page.

Pure parts run headless; the GTK page/dialog construction is covered by
the apps' Broadway suites.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xx_ui import widgets  # noqa: E402


def test_table_lists_the_app_and_what_it_uses():
    entries = widgets.standard_license_entries("TxxT")
    assert entries[0].name == "TxxT"
    assert entries[0].spdx.startswith("GPL-3.0")
    by_name = {entry.name: entry for entry in entries}
    assert by_name["phonenumbers"].spdx == "Apache-2.0"
    assert by_name["GTK 4"].spdx.startswith("LGPL")
    assert by_name["libadwaita"].spdx.startswith("LGPL")
    assert by_name["PyGObject"].spdx.startswith("LGPL")
    assert by_name["JetBrains Mono / Space Mono"].spdx == "OFL-1.1"
    assert by_name["Nerd Fonts glyphs"].spdx == "MIT"
    assert by_name["Adwaita symbolic icons"].spdx == "CC-BY-SA-3.0"
    for entry in entries:
        assert entry.name and entry.spdx and entry.url


def test_system_copy_wins_over_fallback(tmp_path):
    root = tmp_path / "licenses"
    (root / "spdx").mkdir(parents=True)
    (root / "spdx" / "Apache-2.0.txt").write_text("system apache text")
    entry = widgets.standard_license_entries("X")[4]
    assert widgets.license_text(entry, str(root)) == "system apache text"


def test_package_copy_used_when_no_spdx_file(tmp_path):
    root = tmp_path / "licenses"
    (root / "python-phonenumbers").mkdir(parents=True)
    (root / "python-phonenumbers" / "LICENSE").write_text(
        "vendored apache text")
    entry = widgets.standard_license_entries("X")[4]
    assert widgets.license_text(entry, str(root)) == "vendored apache text"


def test_missing_system_copy_shows_spdx_and_url(tmp_path):
    entry = widgets.standard_license_entries("X")[4]
    assert widgets.license_text(entry, str(tmp_path / "nope")) == (
        "Apache-2.0\n"
        "https://github.com/daviddrysdale/python-phonenumbers"
    )


def test_app_license_matches_metainfo_and_can_be_overridden():
    # The apps' AppStream metainfo says GPL-3.0-only.
    assert widgets.standard_license_entries("X")[0].spdx == "GPL-3.0-only"
    entries = widgets.standard_license_entries("X", spdx="MIT")
    assert entries[0].spdx == "MIT"
    assert entries[1:] == widgets.standard_license_entries("X")[1:]
