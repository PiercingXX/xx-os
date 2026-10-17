"""xx-keyboard install/session wiring (lane kb_brand).

install.sh builds and installs xx-keyboard and no longer installs the
squeekboard package; the layout symlink stays for the squeekboard
runtime fallback. xx-wm.in starts xx-keyboard first and only falls back
to squeekboard when xx-keyboard is absent. Shell functions are exercised
by text (the whole script is never executed).
"""
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
WRAPPER_IN = REPO / 'launcher' / 'data' / 'xx-wm.in'


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


@pytest.fixture(scope='module')
def wrapper():
    assert WRAPPER_IN.is_file(), f'missing {WRAPPER_IN}'
    return WRAPPER_IN.read_text()


def _function_body(script, name):
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    assert m, f'shell function {name} not found in install.sh'
    return m.group(1)


def test_no_squeekboard_package(install_script):
    for line in install_script.splitlines():
        assert not (line.strip().startswith('pkg_install')
                    and 'squeekboard' in line), line


def test_squeekboard_symlink_kept_for_fallback(install_script):
    body = _function_body(install_script, 'link_squeekboard_layouts')
    assert 'squeekboard/keyboards' in body
    build = _function_body(install_script, 'build_install')
    assert 'link_squeekboard_layouts' in build


def test_xx_keyboard_deps_apk(install_script):
    body = _function_body(install_script, 'install_xx_keyboard_deps')
    apk = body.split('[ "$PKG" = apk ]')[1].split('elif')[0]
    for pkg in ('gtk+3.0-dev', 'libhandy1-dev', 'gmobile-dev',
                'feedbackd-dev', 'gnome-desktop-dev', 'json-glib-dev',
                'libxkbcommon-dev', 'dconf-dev'):
        assert pkg in apk, pkg


def test_xx_keyboard_deps_pacman(install_script):
    body = _function_body(install_script, 'install_xx_keyboard_deps')
    pacman = body.split('[ "$PKG" = pacman ]')[1].split('else')[0]
    for pkg in ('gtk3', 'libhandy', 'gmobile', 'feedbackd',
                'gnome-desktop', 'json-glib', 'libxkbcommon', 'dconf'):
        assert pkg in pacman, pkg


def test_xx_keyboard_deps_apt(install_script):
    body = _function_body(install_script, 'install_xx_keyboard_deps')
    apt = body.split('else')[-1]
    for pkg in ('libgtk-3-dev', 'libhandy-1-dev', 'libgmobile-dev',
                'libfeedback-dev', 'libgnome-desktop-3-dev',
                'libjson-glib-dev', 'libxkbcommon-dev', 'libdconf-dev'):
        assert pkg in apt, pkg


def test_install_xx_keyboard_builds_sibling(install_script):
    body = _function_body(install_script, 'install_xx_keyboard')
    assert '../xx-keyboard' in body
    assert 'meson setup build --prefix=/usr' in body
    assert 'meson install -C build' in body
    assert 'warn:' in body


def test_build_install_runs_keyboard(install_script):
    build = _function_body(install_script, 'build_install')
    assert 'install_xx_keyboard_deps' in build
    assert 'install_xx_keyboard' in build
    deps_at = build.find('install_xx_keyboard_deps')
    build_at = build.find('install_xx_keyboard\n')
    assert deps_at != -1 and build_at != -1 and deps_at < build_at


def test_session_starts_xx_keyboard_first(wrapper):
    kb_at = wrapper.find('command -v xx-keyboard')
    sq_at = wrapper.find('command -v squeekboard')
    assert kb_at != -1 and sq_at != -1 and kb_at < sq_at


def test_squeekboard_is_fallback_only(wrapper):
    assert 'elif command -v squeekboard' in wrapper


def _first_boot():
    import first_boot
    return first_boot


def test_keyboard_step_prefers_xx_keyboard(monkeypatch):
    first_boot = _first_boot()
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/xx-keyboard'
                        if name == 'xx-keyboard' else None)
    assert first_boot.keyboard_step_available() is True


def test_keyboard_step_absent_without_keyboards(monkeypatch):
    first_boot = _first_boot()
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    assert first_boot.keyboard_step_available() is False


def test_keyboard_step_squeekboard_fallback(monkeypatch, tmp_path):
    first_boot = _first_boot()
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/squeekboard'
                        if name == 'squeekboard' else None)
    monkeypatch.setenv('HOME', str(tmp_path))
    assert first_boot.keyboard_step_available() is False
    layouts = tmp_path / '.local' / 'share' / 'squeekboard' / 'keyboards'
    layouts.mkdir(parents=True)
    assert first_boot.keyboard_step_available() is True


def test_every_distro_gets_msgfmt_for_the_keyboard(install_script):
    # FuriOS ships without gettext: meson stopped at "Program 'msgfmt' not
    # found" and the keyboard never installed (seen on the FLX1)
    body = _function_body(install_script, 'install_xx_keyboard_deps')
    for arm in body.split('elif')[:3]:
        assert 'gettext' in arm
