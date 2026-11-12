"""Arch/pacman support, runtime deps, and fonts in scripts/install.sh.

The smoke tablet is Arch; the installer must know pacman, pull
python-pywayland (empty switcher without it), and install fonts via
distro packages (never failing the install on a font).
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
APPS_SH = REPO / 'scripts' / 'apps.sh'
SESSION_IN = REPO / 'launcher' / 'data' / 'xx-wm-session.in'


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


def test_pacman_branch_exists(install_script):
    assert 'PKG=pacman' in install_script
    assert 'pacman' in install_script
    assert 'need apk, apt, or pacman' in install_script


def test_pkg_detects_pacman(install_script):
    assert 'PKG=pacman' in install_script
    assert 'need apk, apt, or pacman' in install_script
    assert 'need apk or apt.' not in install_script


def test_pkg_install_pacman(install_script):
    body = _function_body(install_script, 'pkg_install')
    assert 'pacman -S --needed --noconfirm' in body


def test_pkg_install_pacman_is_nonfatal(install_script):
    body = _function_body(install_script, 'pkg_install')
    assert 'pacman -S --needed --noconfirm' in body
    assert 'warn: some packages failed' in body


def test_whiptail_bootstrap_libnewt(install_script):
    assert 'pkg_install libnewt' in install_script
    assert 'pkg_install newt' in install_script
    assert 'pkg_install whiptail' in install_script
    for line in install_script.splitlines():
        if 'pkg_install' in line and 'pacman' in line:
            assert 'whiptail' not in line


def test_whiptail_bootstrap_uses_libnewt_on_pacman(install_script):
    assert 'pkg_install libnewt' in install_script
    assert 'pkg_install newt' in install_script
    assert 'pkg_install whiptail' in install_script
    start = install_script.find('Installing whiptail')
    assert start != -1
    chunk = install_script[start:]
    chunk = chunk[:chunk.find('\nfi') + 3]
    pacman_arm = chunk.split('[ "$PKG" = pacman ]')[1].split('else')[0]
    assert 'pkg_install libnewt' in pacman_arm
    assert 'pkg_install whiptail' not in pacman_arm


def _pacman_install_deps(script):
    body = _function_body(script, 'install_deps')
    start = body.find('[ "$PKG" = pacman ]')
    assert start != -1, 'install_deps missing pacman branch'
    rest = body[start:]
    end = rest.find('\n    else')
    return rest if end == -1 else rest[:end]


def test_install_deps_pacman_list(install_script):
    body = _function_body(install_script, 'install_deps')
    # squeekboard is intentionally gone: xx-keyboard is installed instead
    # (install_xx_keyboard) and squeekboard stays a fallback only.
    for pkg in ('python-gobject', 'gtk4-layer-shell', 'python-pywayland',
                'geoclue', 'networkmanager', 'phoc',
                'brightnessctl'):
        assert pkg in body, f'pacman deps miss {pkg}'


def test_pacman_deps_include_runtime_stack(install_script):
    block = _pacman_install_deps(install_script)
    for name in ('python-gobject', 'gtk4-layer-shell', 'python-pywayland',
                 'geoclue', 'networkmanager', 'phoc',
                 'brightnessctl'):
        assert name in block, f'pacman install_deps missing {name}'


def test_install_deps_apk_apt_brightness(install_script):
    body = _function_body(install_script, 'install_deps')
    assert 'brightnessctl' in body
    assert 'py3-wayland' in install_script
    assert 'py3-pywayland' not in install_script
    assert 'python3-pywayland' in install_script


def test_apk_and_apt_install_brightnessctl(install_script):
    body = _function_body(install_script, 'install_deps')
    assert 'brightnessctl' in body
    assert body.count('pkg_install brightnessctl') >= 2


def test_install_deps_pacman_no_lisgd_package(install_script):
    body = _function_body(install_script, 'install_deps')
    for line in body.splitlines():
        if 'pacman' in line:
            assert 'lisgd' not in line
    assert '~mil/lisgd' in install_script
    assert 'SKIP' in install_script


def test_pacman_does_not_pacman_s_lisgd(install_script):
    block = _pacman_install_deps(install_script)
    pkg_lines = '\n'.join(
        line for line in block.splitlines() if 'pkg_install' in line)
    assert 'lisgd' not in pkg_lines
    assert '~mil/lisgd' in install_script
    assert 'SKIP:' in install_script


def test_install_fonts_defined_and_wired(install_script):
    body = _function_body(install_script, 'install_fonts')
    assert body, 'install_fonts not defined'
    assert 'ttf-jetbrains-mono-nerd' in body
    assert 'ttf-space-mono-nerd' in body
    assert 'fc-cache' in body
    assert 'install_fonts' in _function_body(install_script, 'build_install')


def test_install_fonts_is_wired_and_nonfatal(install_script):
    body = _function_body(install_script, 'install_fonts')
    assert body, 'install_fonts is missing'
    assert 'ttf-jetbrains-mono-nerd' in body
    assert 'ttf-space-mono-nerd' in body
    assert 'install_fonts' in _function_body(install_script, 'build_install')
    # Never abort the install because a font package is missing.
    assert 'return 1' not in body


def test_install_fonts_never_fails(install_script):
    body = _function_body(install_script, 'install_fonts')
    assert 'return 1' not in body


def test_elogind_dropin_installed_when_present(install_script):
    body = _function_body(install_script, 'install_logind_dropin')
    assert body, 'install_logind_dropin not defined'
    assert '/etc/elogind/logind.conf.d' in body
    assert 'install_logind_dropin' in _function_body(install_script, 'build_install')


def test_apps_sh_understands_pacman():
    text = APPS_SH.read_text(encoding='utf-8')
    assert 'PKG=pacman' in text
    assert 'pacman -S --needed --noconfirm' in text


def test_session_has_no_fontconfig_path():
    text = SESSION_IN.read_text()
    assert 'FONTCONFIG_PATH' not in text


def test_script_syntax_parses(install_script):
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True,
        check=False)
    assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
