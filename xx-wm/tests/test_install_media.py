"""Media apps in the suite installer (xx-radio, xx-audiobook).

install.sh installs the suite apps in XX_APPS after its dependency step;
both media apps must be listed there with their per-distro runtime
dependencies, and each app's `meson install` must ship everything it needs
(D-Bus activation, desktop/metainfo/icons, and the audiobook systemd user
units). Shell functions are exercised by extracting them from install.sh
into a temp file and sourcing that file in `sh -c` with stubbed commands
on PATH — the whole script is never executed.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SIBLING = REPO.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return m.group(1) if m else ''


def _extract(names, dest):
    script = INSTALL_SH.read_text()
    chunks = []
    for name in names:
        m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
        assert m, f'shell function {name} not found in install.sh'
        chunks.append('%s() {%s}\n' % (name, m.group(1)))
    dest.write_text(''.join(chunks))


def _run_funcs(names, driver, tmp_path, env=None):
    funcs = tmp_path / 'funcs.sh'
    _extract(names, funcs)
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        ['sh', '-c', '. "%s"; %s' % (funcs, driver)],
        capture_output=True, text=True, env=full_env, cwd=str(tmp_path))


def _media_block(script):
    body = _function_body(script, 'install_media_deps')
    assert body, 'install_media_deps not defined'
    return body


def _branch(block, marker):
    start = block.find(marker)
    assert start != -1, f'media deps missing branch {marker}'
    rest = block[start:]
    end = rest.find('\n    else')
    if marker.startswith('[ "$PKG" = apk ]'):
        end2 = rest.find('\n    elif')
        end = end2 if end2 != -1 else end
    return rest if end == -1 else rest[:end]


# --- 1. XX_APPS ---------------------------------------------------------------

def test_xx_apps_includes_media(install_script):
    m = re.search(r'^XX_APPS="([^"]*)"', install_script, re.M)
    assert m, 'XX_APPS not found in install.sh'
    apps = m.group(1).split()
    assert 'xx-radio' in apps
    assert 'xx-audiobook' in apps
    assert 'xx-find' not in apps


def test_install_apps_iterates_xx_apps(install_script):
    body = _function_body(install_script, 'install_apps')
    assert 'for app in $XX_APPS' in body


# --- 2. media deps wired into the dependency step ------------------------------

def test_media_deps_wired(install_script):
    _media_block(install_script)
    deps_body = _function_body(install_script, 'install_deps')
    assert 'install_media_deps' in deps_body


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- 3. per-distro package names ------------------------------------------------
# Names come from xx-radio PORT_SPEC.md 2.1 (apk search on the pmOS v26.06
# VM, Alpine aports, Arch pacman -Si, Debian madison) and M0-findings.md
# (measured on FuriOS forky). Every name was checked on the three distros
# (apk on the pmOS VM, pacman on the Arch tablet, apt on the FLX1).

def test_apk_media_names(install_script):
    block = _branch(_media_block(install_script), '[ "$PKG" = apk ]')
    for pkg in ('gstreamer', 'gst-plugins-base', 'gst-plugins-good',
                'gst-plugins-bad', 'gst-libav', 'py3-gst', 'libsecret',
                'py3-ytmusicapi', 'yt-dlp', 'nodejs'):
        assert pkg in block, f'apk media deps miss {pkg}'


def test_pacman_media_names(install_script):
    block = _branch(_media_block(install_script), '[ "$PKG" = pacman ]')
    for pkg in ('gstreamer', 'gst-plugins-base', 'gst-plugins-good',
                'gst-plugins-bad', 'gst-libav', 'gst-python', 'libsecret',
                'python-ytmusicapi', 'yt-dlp', 'nodejs', 'npm'):
        assert pkg in block, f'pacman media deps miss {pkg}'


def test_apt_media_names(install_script):
    block = _media_block(install_script)
    else_start = block.find('\n    else')
    assert else_start != -1, 'media deps missing apt branch'
    block = block[else_start:]
    for pkg in ('gstreamer1.0-plugins-base', 'gstreamer1.0-plugins-good',
                'gstreamer1.0-plugins-bad', 'gstreamer1.0-libav',
                'gir1.2-gstreamer-1.0', 'gir1.2-gst-plugins-base-1.0',
                'python3-gst-1.0', 'gir1.2-secret-1',
                'yt-dlp', 'nodejs', 'npm', 'python3-ytmusicapi'):
        assert pkg in block, f'apt media deps miss {pkg}'


def test_apt_ytmusicapi_isolated(install_script):
    # python3-ytmusicapi is not in trixie; its absence must not fail the
    # yt-dlp/nodejs/npm line it would otherwise share.
    block = _media_block(install_script)
    lines = [line.strip() for line in block.splitlines()
             if 'python3-ytmusicapi' in line]
    assert lines, 'python3-ytmusicapi not installed on apt'
    assert any(line == 'pkg_install python3-ytmusicapi' for line in lines)


def test_every_name_confirmed_and_alpine_gets_npm(install_script):
    # All names were checked against apk (pmOS VM), pacman (Arch tablet)
    # and apt (FuriOS FLX1); Alpine's nodejs ships without npm.
    block = _media_block(install_script)
    assert 'TODO' not in block
    apk = block.split('elif [ "$PKG" = pacman ]')[0]
    assert 'nodejs npm' in apk


def test_media_deps_run_per_distro(tmp_path):
    for pkg, want in (
            ('apk', ('gst-plugins-good', 'py3-ytmusicapi', 'nodejs')),
            ('pacman', ('gst-plugins-good', 'python-ytmusicapi', 'npm')),
            ('apt', ('gstreamer1.0-plugins-good', 'python3-ytmusicapi', 'npm'))):
        log = tmp_path / f'{pkg}.log'
        stub = ('pkg_install() { echo "$*" >> "%s"; }\ninstall_media_deps'
                % log)
        proc = _run_funcs(['install_media_deps'], stub,
                          tmp_path, {'PKG': pkg, 'SUDO': ''})
        assert proc.returncode == 0, proc.stderr
        text = log.read_text()
        for name in want:
            assert name in text, f'{pkg} media run misses {name}'


# --- 4. meson install ships everything ------------------------------------------

def _sibling(name):
    path = SIBLING / name
    if not path.is_dir():
        pytest.skip(f'sibling checkout {name} not present')
    return path


def test_radio_meson_packages_and_activation():
    radio = _sibling('xx-radio')
    text = (radio / 'meson.build').read_text()
    assert "'xx_music'" in text and "'xx_radio'" in text
    for service in ('io.piercingxx.XXRadio.service.in',
                    'io.piercingxx.XXRadio.Player.service.in'):
        assert service in text, f'radio meson misses {service}'


def test_audiobook_meson_installs_whole_package():
    # install_subdir ships every module; a hand-kept list once missed one.
    text = (_sibling('xx-audiobook') / 'meson.build').read_text()
    assert "install_subdir(\n  join_paths('src', 'xx_audiobook')" in text
    assert "'src/xx_audiobook/" not in text


def test_audiobook_meson_units_and_activation():
    book = _sibling('xx-audiobook')
    text = (book / 'meson.build').read_text()
    for name in ('io.piercingxx.XXAudiobook.service.in',
                 'io.piercingxx.XXAudiobook.Player.service.in',
                 'xx-audiobook-download@.service.in',
                 'xx-audiobook-sync.service.in',
                 'xx-audiobook-sync.timer'):
        assert name in text, f'audiobook meson misses {name}'


def _staged_install(app, tmp_path):
    build_dir = tmp_path / 'build'
    destdir = tmp_path / 'destdir'
    setup_env = dict(os.environ)
    setup_env['PYTHONPATH'] = str(SIBLING / 'common') + (
        os.pathsep + setup_env['PYTHONPATH']
        if setup_env.get('PYTHONPATH') else '')
    subprocess.run(
        ['meson', 'setup', str(build_dir), str(SIBLING / app),
         '--prefix=/usr'],
        check=True, capture_output=True, text=True, env=setup_env)
    subprocess.run(
        ['meson', 'install', '-C', str(build_dir)],
        check=True, capture_output=True, text=True,
        env={**os.environ, 'DESTDIR': str(destdir)})
    return destdir


@pytest.mark.skipif(
    not (shutil.which('meson') and shutil.which('ninja')),
    reason='needs meson and ninja',
)
def test_destdir_install_radio_files(tmp_path):
    _sibling('xx-radio')
    destdir = _staged_install('xx-radio', tmp_path)
    for rel in (
            'usr/bin/xx-radio',
            'usr/share/applications/io.piercingxx.XXRadio.desktop',
            'usr/share/dbus-1/services/io.piercingxx.XXRadio.service',
            'usr/share/dbus-1/services/io.piercingxx.XXRadio.Player.service',
            'usr/share/metainfo/io.piercingxx.XXRadio.metainfo.xml',
            'usr/share/icons/hicolor/scalable/apps/io.piercingxx.XXRadio.svg',
            'usr/share/xx-radio/xx_radio/main.py',
            'usr/share/xx-radio/xx_music/player/service.py'):
        assert (destdir / rel).is_file(), f'radio install misses {rel}'
    gui = (destdir / 'usr/share/dbus-1/services'
           / 'io.piercingxx.XXRadio.service').read_text()
    player = (destdir / 'usr/share/dbus-1/services'
              / 'io.piercingxx.XXRadio.Player.service').read_text()
    assert 'Name=io.piercingxx.XXRadio\n' in gui
    assert 'Exec=/usr/bin/xx-radio --gapplication-service\n' in gui
    assert 'Name=io.piercingxx.XXRadio.Player\n' in player
    assert 'Exec=/usr/bin/xx-radio --player-service' in player


@pytest.mark.skipif(
    not (shutil.which('meson') and shutil.which('ninja')),
    reason='needs meson and ninja',
)
def test_destdir_install_audiobook_files(tmp_path):
    _sibling('xx-audiobook')
    destdir = _staged_install('xx-audiobook', tmp_path)
    for rel in (
            'usr/bin/xx-audiobook',
            'usr/bin/xx-audiobook-player',
            'usr/bin/xx-audiobook-download',
            'usr/share/applications/io.piercingxx.XXAudiobook.desktop',
            'usr/share/dbus-1/services/io.piercingxx.XXAudiobook.service',
            'usr/share/dbus-1/services/'
            'io.piercingxx.XXAudiobook.Player.service',
            'usr/share/metainfo/io.piercingxx.XXAudiobook.metainfo.xml',
            'usr/share/icons/hicolor/scalable/apps/'
            'io.piercingxx.XXAudiobook.svg',
            'usr/share/systemd/user/xx-audiobook-download@.service',
            'usr/share/systemd/user/xx-audiobook-sync.service',
            'usr/share/systemd/user/xx-audiobook-sync.timer',
            'usr/share/xx-audiobook/xx_audiobook/player.py',
            'usr/share/xx-audiobook/xx_audiobook/download.py',
            'usr/share/xx-audiobook/xx_audiobook/zones.py',
            'usr/share/xx-audiobook/xx_audiobook/service/player_service.py'):
        assert (destdir / rel).is_file(), f'audiobook install misses {rel}'
    player = (destdir / 'usr/share/dbus-1/services'
              / 'io.piercingxx.XXAudiobook.Player.service').read_text()
    assert 'Name=io.piercingxx.XXAudiobook.Player\n' in player
    assert 'Exec=/usr/bin/xx-audiobook-player' in player
    download = (destdir / 'usr/share/systemd/user'
                / 'xx-audiobook-download@.service').read_text()
    assert 'ExecStart=/usr/bin/xx-audiobook-download %i' in download


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
