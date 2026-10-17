"""Tests for the 20.5 per-device phoc.ini scale.

The tablet's panel reports as ``DSI-1`` (wanting scale 1.5) and collides with
the FP5's ``DSI-1`` (scale 2.5), so a single phoc.ini cannot serve both
devices. install.sh must therefore pick a per-device phoc.ini fragment and
copy it over the meson-installed default. This is a shell script, so the test
reads the script text and the fragment files and asserts the selection logic
is present, correct, and actually wired into the install path (not just
defined).
"""
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
PHOC_DIR = REPO / 'launcher' / 'data' / 'phoc'

# device name -> (output section, expected scale)
EXPECTED = {
    'fairphone-5': ('DSI-1', '2.5'),
    'furiphone-flx1': ('HWCOMPOSER-1', '1.2'),
    'librem-5': ('DSI-1', '2'),
    'tablet': ('DSI-1', '1.5'),
    'qemu': ('Virtual-1', '1.5'),
}


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    start = script.find(name + '() {')
    assert start != -1, f'{name} not defined'
    depth = 0
    for i in range(start, len(script)):
        if script[i] == '{':
            depth += 1
        elif script[i] == '}':
            depth -= 1
            if depth == 0 and script[i - 1] == '\n':
                return script[start:i]
    return ''


def test_per_device_fragments_exist():
    """Every supported device must have a phoc.ini fragment shipped."""
    for dev in EXPECTED:
        assert (PHOC_DIR / f'{dev}.ini').is_file(), f'missing {dev}.ini'


def test_fragment_scale_matches_device():
    """Each fragment must set the documented scale on the right output."""
    import configparser
    for dev, (output, scale) in EXPECTED.items():
        cfg = configparser.ConfigParser()
        cfg.read(PHOC_DIR / f'{dev}.ini')
        section = f'output:{output}'
        assert section in cfg, f'{dev}.ini missing [{section}] section'
        assert cfg[section]['scale'] == scale, \
            f'{dev}.ini scale != {scale}'


def test_fragment_disables_xwayland():
    """The fragments must keep the core hardening from the default file."""
    import configparser
    for dev in EXPECTED:
        cfg = configparser.ConfigParser()
        cfg.read(PHOC_DIR / f'{dev}.ini')
        assert cfg['core'].getboolean('xwayland') is False


def test_install_script_selects_fragment(install_script):
    """install.sh must copy the chosen device's fragment over phoc.ini."""
    body = _function_body(install_script, 'select_phoc_scale')
    helper = _function_body(install_script, '_install_phoc_fragment')
    combined = body + helper
    assert 'whiptail' in body or '_ask_device' in body
    assert 'launcher/data/phoc' in combined
    assert '.ini' in combined
    assert 'cp' in combined
    assert '/usr/share/xx-wm/phoc.ini' in combined


def test_detects_or_requires_device(install_script):
    """Cancel must not silently keep the Fairphone 5 DSI-1 scale 2.5."""
    body = _function_body(install_script, 'select_phoc_scale')
    detect = _function_body(install_script, 'detect_phoc_device')
    assert '1200x1920' in detect or '1200x1920' in body
    assert '/sys/class/drm' in detect or '/sys/class/drm' in body
    assert '-t 0' in body
    assert 'return 1' in body
    assert '|| return 0' not in body
    assert 'detect' in body


def test_detect_probes_drm_modes(install_script):
    """detect_phoc_device must probe DRM sysfs (no wlr-randr on device)."""
    assert '1200x1920' in install_script
    assert '/sys/class/drm' in install_script
    body = _function_body(install_script, 'detect_phoc_device')
    assert body, 'detect_phoc_device not defined'
    assert 'HWCOMPOSER-1' in body


def test_cancel_does_not_keep_default(install_script):
    """Cancelling the prompt must not silently keep the FP5 scale."""
    body = _function_body(install_script, 'select_phoc_scale')
    assert '|| return 0' not in body
    assert 'detect' in body
    assert '1200x1920' in body


def test_non_tty_empty_detection_fails(install_script):
    """Without a TTY and without detection, fail instead of spinning."""
    body = _function_body(install_script, 'select_phoc_scale')
    assert '-t 0' in body
    assert 'return 1' in body


def test_installed_fragment_fallback(install_script):
    """Post-install (no repo tree) the fragment ships under the datadir."""
    body = _function_body(install_script, 'select_phoc_scale')
    assert '/usr/share/xx-wm/phoc/' in body


def test_wired_into_install_path(install_script):
    """select_phoc_scale must run from build_install (shared Install/Update
    path), not just be defined — otherwise the scale is never applied."""
    assert 'select_phoc_scale' in _function_body(install_script, 'build_install')


def test_script_syntax_parses(install_script):
    """The script must still parse as valid POSIX sh (sh -n)."""
    import subprocess
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr