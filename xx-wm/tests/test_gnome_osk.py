"""Tests for the 24 GDM Colemak OSK layout in launcher/data/gnome-osk/.

The GDM greeter runs GNOME Shell's own on-screen keyboard, which reads JSON
layouts from /usr/share/gnome-shell/osk-layouts/ — squeekboard YAML does not
apply there. us.json adapts the Colemak layout to that schema
(top-level name/locale/levels; levels carry level/mode/rows; key objects use
strings/label/action/iconName/keyval as upstream does). These tests parse the
shipped layout headlessly, assert schema shape, Colemak letter ordering taken
from data/squeekboard/us+colemak.yaml, and that install.sh only offers the
overwrite on GDM hosts with a one-time backup.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

GNOME_OSK_DIR = Path(__file__).parent.parent / 'launcher' / 'data' / 'gnome-osk'
INSTALL_SH = Path(__file__).parent.parent / 'scripts' / 'install.sh'
MESON_BUILD = Path(__file__).parent.parent / 'launcher' / 'meson.build'

REQUIRED_TOP_LEVEL_KEYS = {'name', 'locale', 'levels'}
REQUIRED_LEVEL_KEYS = {'level', 'mode', 'rows'}
LEVEL_NAMES = ('default', 'shift', 'opt', 'opt+shift')

# From launcher/data/squeekboard/us+colemak.yaml base view row 2 — do not edit.
COLEMAK_HOME_ROW = ['a', 'r', 's', 't', 'd', 'h', 'n', 'e', 'i', 'o']
COLEMAK_TOP_ROW = ['q', 'w', 'f', 'p', 'g', 'j', 'l', 'u', 'y']
COLEMAK_BOTTOM_ROW = ['z', 'x', 'c', 'v', 'b', 'k', 'm']


@pytest.fixture(scope='module')
def layouts():
    """Every shipped layout JSON, parsed."""
    paths = sorted(GNOME_OSK_DIR.glob('*.json'))
    assert paths, f'no .json layouts found in {GNOME_OSK_DIR}'
    return [(p.name, json.loads(p.read_text(encoding='utf-8'))) for p in paths]


@pytest.fixture(scope='module')
def us_layout(layouts):
    by_name = dict(layouts)
    assert 'us.json' in by_name, f'expected us.json, got {sorted(by_name)}'
    return by_name['us.json']


# --- schema shape ------------------------------------------------------------

def test_layout_files_exist():
    assert GNOME_OSK_DIR.is_dir(), f'missing {GNOME_OSK_DIR}'
    assert 'us.json' in [p.name for p in GNOME_OSK_DIR.glob('*.json')]


def test_required_top_level_keys(layouts):
    for name, layout in layouts:
        missing = REQUIRED_TOP_LEVEL_KEYS - set(layout)
        assert not missing, f'{name}: missing top-level keys {missing}'


def test_levels_use_gnome_names_and_modes(us_layout):
    """GNOME identifies levels by name and switches modes exactly like this."""
    got = [(lv['level'], lv['mode']) for lv in us_layout['levels']]
    assert got == [
        ('default', 'default'),
        ('shift', 'latched'),
        ('opt', 'locked'),
        ('opt+shift', 'locked'),
    ]


def test_every_level_has_non_empty_rows(layouts):
    for name, layout in layouts:
        assert layout['levels'], f'{name}: no levels'
        for lv in layout['levels']:
            missing = REQUIRED_LEVEL_KEYS - set(lv)
            assert not missing, f'{name}: level {lv.get("level")} missing {missing}'
            assert lv['rows'], f'{name}: level {lv["level"]} has empty rows'
            for i, row in enumerate(lv['rows']):
                assert row, f'{name}: level {lv["level"]} row {i} is empty'


def _key_content(key):
    """A key object must carry something typeable or actionable.

    Upstream letter keys are {"strings": [...]}, control keys use action /
    label, Enter uses iconName+keyval. A key with none of these would render
    as a dead button.
    """
    if not isinstance(key, dict):
        return False
    if 'action' in key or 'label' in key:
        return True
    if isinstance(key.get('strings'), list) and key['strings']:
        return True
    return 'keyval' in key


def test_no_dead_key_objects(layouts):
    """No key object may lack both a label and an action payload."""
    for name, layout in layouts:
        for lv in layout['levels']:
            for r, row in enumerate(lv['rows']):
                for c, key in enumerate(row):
                    assert _key_content(key), (
                        f'{name}: level {lv["level"]} row {r} col {c} '
                        f'has no label/action payload: {key!r}'
                    )


def test_key_objects_are_dicts(layouts):
    for _name, layout in layouts:
        for lv in layout['levels']:
            for row in lv['rows']:
                assert all(isinstance(key, dict) for key in row)


def test_shift_level_switching(us_layout):
    """Shift toggles default↔shift; ?123 reaches opt; ABC returns home."""
    levels = {lv['level']: lv for lv in us_layout['levels']}
    for row in levels['default']['rows'] + levels['shift']['rows']:
        switches = [k['level'] for k in row
                    if k.get('action') == 'levelSwitch'
                    and k.get('iconName') == 'osk-shift-symbolic']
        if switches:
            assert set(switches) <= {'shift', 'default'}
    opt_rows = levels['default']['rows'][3]
    assert any(k.get('level') == 'opt' for k in opt_rows
               if k.get('action') == 'levelSwitch')
    abc = levels['opt']['rows'][3]
    assert any(k.get('level') == 'default' for k in abc
               if k.get('action') == 'levelSwitch')


def test_backspace_enter_space_on_letter_levels(us_layout):
    levels = {lv['level']: lv for lv in us_layout['levels']}
    for name in ('default', 'shift'):
        flat = [key for row in levels[name]['rows'] for key in row]
        deletes = [k for k in flat if k.get('action') == 'delete']
        enters = [k for k in flat if k.get('keyval') == '0xff0d']
        spaces = [k for k in flat if k.get('strings') == [' ']]
        assert deletes, f'{name}: no backspace (delete) key'
        assert enters, f'{name}: no enter key (keyval 0xff0d)'
        assert spaces, f'{name}: no space key'


# --- Colemak ordering (source of truth: squeekboard us+colemak.yaml) ---------

def _letter_strings(row):
    out = []
    for key in row:
        strings = key.get('strings') or []
        if strings and strings[0] != ' ':
            out.append(strings[0])
    return out


def test_default_home_row_is_colemak(us_layout):
    home = us_layout['levels'][0]['rows'][1]
    letters = _letter_strings(home)
    n = len(COLEMAK_HOME_ROW)
    assert letters[:n] == COLEMAK_HOME_ROW


def test_default_top_row_is_colemak(us_layout):
    top = us_layout['levels'][0]['rows'][0]
    letters = _letter_strings(top)
    n = len(COLEMAK_TOP_ROW)
    assert letters[:n] == COLEMAK_TOP_ROW


def test_default_bottom_row_is_colemak(us_layout):
    bottom = us_layout['levels'][0]['rows'][2]
    letters = _letter_strings(bottom)
    n = len(COLEMAK_BOTTOM_ROW)
    assert letters[:n] == COLEMAK_BOTTOM_ROW


def test_shift_level_uppercases_colemak_home_row(us_layout):
    home = us_layout['levels'][1]['rows'][1]
    letters = _letter_strings(home)
    expected = [c.upper() for c in COLEMAK_HOME_ROW]
    assert letters[:len(expected)] == expected


# --- install.sh wiring -------------------------------------------------------

@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return m.group(1) if m else ''


def test_gdm_osk_step_guarded_on_gdm(install_script):
    body = _function_body(install_script, 'gdm_detected')
    assert 'command -v gdm' in body
    # A mere gdm binary is not enough: require evidence the DM is wired up
    # (enabled, unit file shipped, or selected by distro config).
    assert 'is-enabled gdm' in body
    assert 'systemctl cat gdm' in body
    assert '/etc/X11/default-display-manager' in body
    assert 'display-manager.service' in body
    # status succeeds even for loaded-but-inactive units — must not be the probe.
    assert 'systemctl status gdm' not in body
    # Non-systemd hosts (pmOS/OpenRC) keep the old binary-only fallback.
    assert 'command -v systemctl' in body


def test_menu_offers_gdm_osk_only_when_detected(install_script):
    menu_body = _function_body(install_script, 'menu')
    assert 'if gdm_detected; then' in menu_body
    assert '"GDM Colemak OSK"' in menu_body


def test_gdm_osk_wired_into_case_dispatch(install_script):
    dispatch = install_script.split('case $choice in', 1)[1]
    assert '"GDM Colemak OSK")' in dispatch
    assert 'install_gdm_osk ;;' in dispatch


def test_gdm_osk_backs_up_original_once(install_script):
    body = _function_body(install_script, 'install_gdm_osk')
    dst = '/usr/share/gnome-shell/osk-layouts/us.json'
    assert dst in body
    # Backup path is composed from dst with a .xx-wm-backup suffix.
    assert 'xx-wm-backup' in body
    # Backup must be skipped when it already exists (idempotent).
    assert '[ ! -f "$bak" ]' in body
    assert '$SUDO cp "$dst" "$bak"' in body
    assert '$SUDO cp "$src" "$dst"' in body


def test_gdm_osk_warns_about_overwrite_and_restore(install_script):
    body = _function_body(install_script, 'install_gdm_osk')
    assert 'OVERWRITES' in body
    assert 'Restore QWERTY' in body


def test_install_script_still_parses(install_script):
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True,
        check=False)
    assert result.returncode == 0, result.stderr


# --- meson wiring ------------------------------------------------------------

def test_meson_installs_gnome_osk_data():
    text = MESON_BUILD.read_text()
    assert "'data/gnome-osk/us.json'" in text
    assert "join_paths(shell_datadir, 'gnome-osk')" in text
