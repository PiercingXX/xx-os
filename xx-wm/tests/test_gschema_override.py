"""Session-scoped GSettings defaults (launcher/data/gschema).

squeekboard (server-context-service.c) and xx-keyboard / phosh-osk-stevia
(pos-input-surface.c) only rise for an app's text field while
org.gnome.desktop.a11y.applications screen-keyboard-enabled is true, and
the key defaults to false. Phosh flips it for its own session with a
desktop-scoped override; XX-WM ships the same, scoped to XX-WM so GNOME,
GDM and Phosh sessions never see it.
"""
import configparser
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
DATA = REPO / 'launcher' / 'data'
OVERRIDE = DATA / 'gschema' / '00_io.piercingxx.XXWM.gschema.override'
MESON_BUILD = REPO / 'launcher' / 'meson.build'


def _parse():
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(OVERRIDE, encoding='utf-8')
    return cfg


def test_osk_enabled_for_xx_wm():
    cfg = _parse()
    group = 'org.gnome.desktop.a11y.applications:XX-WM'
    assert cfg[group]['screen-keyboard-enabled'] == 'true'


def test_every_group_is_scoped_to_xx_wm():
    # An unscoped group would change GNOME / GDM / Phosh defaults too.
    for group in _parse().sections():
        assert group.endswith(':XX-WM'), group


def test_scope_matches_the_session_desktop_name():
    desktop = (DATA / 'xx-wm.desktop.in').read_text(encoding='utf-8')
    assert re.search(r'^DesktopNames=XX-WM$', desktop, re.M)
    session = (DATA / 'xx-wm-session.in').read_text(encoding='utf-8')
    assert ': "${XDG_CURRENT_DESKTOP:=XX-WM}"' in session


def test_meson_installs_and_compiles_schemas():
    text = MESON_BUILD.read_text(encoding='utf-8')
    assert "'data/gschema/00_io.piercingxx.XXWM.gschema.override'" in text
    assert "'glib-2.0', 'schemas'" in text
    assert "find_program('glib-compile-schemas', required: false)" in text
    assert 'skip_if_destdir: true' in text


@pytest.mark.skipif(not shutil.which('glib-compile-schemas')
                    or not shutil.which('gsettings'),
                    reason='needs glib-compile-schemas and gsettings')
@pytest.mark.parametrize('desktop,expected', [
    ('XX-WM', 'true'), ('GNOME', 'false'), ('Phosh:GNOME', 'false'),
    ('', 'false'),
])
def test_override_applies_only_in_the_xx_wm_session(tmp_path, desktop,
                                                    expected):
    (tmp_path / 'org.gnome.desktop.a11y.applications.gschema.xml').write_text(
        '<schemalist><schema id="org.gnome.desktop.a11y.applications" '
        'path="/org/gnome/desktop/a11y/applications/">'
        '<key name="screen-keyboard-enabled" type="b">'
        '<default>false</default></key></schema></schemalist>\n')
    shutil.copy(OVERRIDE, tmp_path / OVERRIDE.name)
    subprocess.run(['glib-compile-schemas', '--strict', str(tmp_path)],
                   check=True, capture_output=True)
    env = dict(os.environ, GSETTINGS_BACKEND='memory',
               GSETTINGS_SCHEMA_DIR=str(tmp_path), XDG_CURRENT_DESKTOP=desktop)
    out = subprocess.run(
        ['gsettings', 'get', 'org.gnome.desktop.a11y.applications',
         'screen-keyboard-enabled'],
        check=True, capture_output=True, text=True, env=env).stdout.strip()
    assert out == expected
