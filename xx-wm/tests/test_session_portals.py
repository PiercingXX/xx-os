"""systemd session target + portal wiring (postmarketOS VM fix-up).

xdg-desktop-portal.service has Requisite=graphical-session.target, which
nothing starts in an XX-WM session, and the systemd user manager never
sees WAYLAND_DISPLAY — so portals never start and D-Bus-activated
services launch without a display. Covered here:

* xx-wm-session.target pulls graphical-session.target in (BindsTo).
* xx-wm-portals.conf matches the lowercased XDG_CURRENT_DESKTOP and
  prefers gtk, with wlr for screenshot/screencast (phoc speaks
  wlr-screencopy).
* the in-session wrapper (xx-wm.in, which runs inside phoc where
  WAYLAND_DISPLAY is known) imports the display into the user manager
  and starts/stops the target; OpenRC hosts skip all of it.
* meson ships both files; install.sh pulls the broker packages per
  distro when the broker is missing.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
DATA = REPO / 'launcher' / 'data'
WRAPPER = DATA / 'xx-wm.in'
SESSION = DATA / 'xx-wm-session.in'
TARGET = DATA / 'systemd' / 'xx-wm-session.target'
PORTALS_CONF = DATA / 'xx-wm-portals.conf'
MESON_BUILD = REPO / 'launcher' / 'meson.build'
INSTALL_SH = REPO / 'scripts' / 'install.sh'
DESKTOP = DATA / 'xx-wm.desktop.in'

_ENV_VARS = ('WAYLAND_DISPLAY', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE',
             'DISPLAY')


def _function_body(script, name):
    match = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return match.group(1) if match else ''


class TestSessionTarget:
    def test_target_exists_with_session_units(self):
        assert TARGET.is_file()

    def test_binds_graphical_session(self):
        text = TARGET.read_text(encoding='utf-8')
        assert 'BindsTo=graphical-session.target' in text

    def test_orders_after_session_pre(self):
        text = TARGET.read_text(encoding='utf-8')
        assert 'Wants=graphical-session-pre.target' in text
        assert 'After=graphical-session-pre.target' in text

    def test_meson_installs_target_to_user_units(self):
        text = MESON_BUILD.read_text(encoding='utf-8')
        assert 'data/systemd/xx-wm-session.target' in text
        assert ("join_paths(get_option('datadir'), 'systemd', 'user')"
                in text)


class TestPortalsConf:
    def test_filename_matches_lowercased_desktop(self):
        """portals.conf(5) looks up <lowercase-desktop>-portals.conf."""
        desktop_names = None
        for line in DESKTOP.read_text(encoding='utf-8').splitlines():
            if line.startswith('DesktopNames='):
                desktop_names = line.split('=', 1)[1]
        assert desktop_names, 'DesktopNames missing from session entry'
        first = desktop_names.split(':')[0] if desktop_names else ''
        assert PORTALS_CONF.name == f'{first.lower()}-portals.conf'

    def test_default_backend_is_gtk(self):
        text = PORTALS_CONF.read_text(encoding='utf-8')
        assert '[preferred]' in text
        assert 'default=gtk;' in text

    def test_screenshot_and_screencast_use_wlr(self):
        text = PORTALS_CONF.read_text(encoding='utf-8')
        assert 'org.freedesktop.impl.portal.Screenshot=wlr;' in text
        assert 'org.freedesktop.impl.portal.ScreenCast=wlr;' in text

    def test_meson_installs_conf_to_portal_dir(self):
        text = MESON_BUILD.read_text(encoding='utf-8')
        assert 'data/xx-wm-portals.conf' in text
        assert ("join_paths(get_option('datadir'), 'xdg-desktop-portal')"
                in text)


class TestWrapperSessionWiring:
    @pytest.fixture(scope='class')
    def wrapper(self):
        return WRAPPER.read_text(encoding='utf-8')

    @pytest.fixture(scope='class')
    def session(self):
        return SESSION.read_text(encoding='utf-8')

    def test_session_exports_desktop_identity(self, session):
        assert 'XDG_CURRENT_DESKTOP' in session
        assert 'XX-WM' in session
        assert 'XDG_SESSION_TYPE' in session
        assert 'wayland' in session

    def test_wrapper_imports_display_to_user_manager(self, wrapper):
        assert 'dbus-update-activation-environment' in wrapper
        assert '--systemd' in wrapper
        for var in _ENV_VARS:
            assert var in wrapper, f'{var} not imported'
        assert 'command -v dbus-update-activation-environment' in wrapper

    def test_wrapper_starts_session_target(self, wrapper):
        assert 'systemctl --user start xx-wm-session.target' in wrapper

    def test_wrapper_stops_target_on_session_end(self, wrapper):
        body = _function_body(wrapper, '_xx_wm_cleanup')
        assert body, 'TERM trap cleanup missing'
        assert '_xx_session_end' in body
        end = _function_body(wrapper, '_xx_session_end')
        assert 'systemctl --user stop xx-wm-session.target' in end

    def test_systemd_parts_skipped_without_user_manager(self, wrapper):
        """OpenRC devices keep working: every systemctl reach is gated on
        the live systemd marker, never the package manager."""
        begin = _function_body(wrapper, '_xx_session_begin')
        end = _function_body(wrapper, '_xx_session_end')
        assert begin and end
        assert '/run/systemd/system' in wrapper
        assert 'command -v systemctl' in wrapper
        assert 'apk' not in wrapper and 'pacman' not in wrapper

    def test_wrapper_template_parses(self):
        proc = subprocess.run(['sh', '-n', str(WRAPPER)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr

    def test_session_template_parses(self):
        proc = subprocess.run(['sh', '-n', str(SESSION)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr


class TestInstallPortals:
    @pytest.fixture(scope='class')
    def install_script(self):
        assert INSTALL_SH.is_file()
        return INSTALL_SH.read_text(encoding='utf-8')

    def test_portal_packages_per_distro(self, install_script):
        body = _function_body(install_script, 'install_portals')
        assert body, 'install_portals not defined'
        assert 'xdg-desktop-portal-gtk' in body
        assert 'xdg-desktop-portal-wlr' in body
        assert 'apk' in body and 'pacman' in body

    def test_installed_only_when_broker_missing(self, install_script):
        body = _function_body(install_script, 'install_portals')
        assert 'command -v xdg-desktop-portal' in body

    def test_wired_into_install_deps(self, install_script):
        body = _function_body(install_script, 'install_deps')
        assert 'install_portals' in body

    def test_install_script_parses(self):
        proc = subprocess.run(['sh', '-n', str(INSTALL_SH)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr

    @staticmethod
    def _run_override(install_script, tmp_path, halium):
        if halium:
            lib = tmp_path / 'usr' / 'lib' / 'aarch64-linux-gnu'
            lib.mkdir(parents=True)
            (lib / 'libhybris-common.so.1').write_bytes(b'')
        dest = tmp_path / 'etc' / 'xdg' / 'xdg-desktop-portal' / 'xx-wm-portals.conf'
        script = '\n'.join((
            'has_libhybris() {' + _function_body(install_script, 'has_libhybris') + '}',
            'install_portal_override() {'
            + _function_body(install_script, 'install_portal_override') + '}',
            'install_portal_override',
        ))
        subprocess.run(['sh', '-c', script], check=True, env={
            'PATH': '/usr/bin:/bin', 'SUDO': '', 'REPO_DIR': str(REPO),
            'XX_WM_LIB_ROOT': str(tmp_path), 'XX_WM_PORTAL_OVERRIDE': str(dest)})
        return dest

    def test_halium_turns_off_the_wlr_portals(self, install_script, tmp_path):
        """FLX1s: xdg-desktop-portal-wlr exits on the hwcomposer phoc and
        the broker stalled 50 s on it before serving Settings."""
        dest = self._run_override(install_script, tmp_path, halium=True)
        text = dest.read_text(encoding='utf-8')
        assert 'default=gtk;' in text
        assert 'org.freedesktop.impl.portal.Screenshot=none;' in text
        assert 'org.freedesktop.impl.portal.ScreenCast=none;' in text
        assert '=wlr' not in text

    def test_mainline_keeps_the_packaged_conf(self, install_script, tmp_path):
        dest = self._run_override(install_script, tmp_path, halium=False)
        assert not dest.exists()

    def test_override_wired_into_install_and_uninstall(self, install_script):
        assert 'install_portal_override' in _function_body(install_script,
                                                           'build_install')
        uninstall = (REPO / 'scripts' / 'uninstall.sh').read_text(encoding='utf-8')
        assert '/etc/xdg/xdg-desktop-portal/xx-wm-portals.conf' in uninstall


@pytest.mark.skipif(
    not (shutil.which('meson') and shutil.which('ninja')),
    reason='needs meson and ninja',
)
def test_destdir_install_contains_session_and_portals(tmp_path):
    build_dir = tmp_path / 'build'
    destdir = tmp_path / 'destdir'
    # launcher/meson.build requires importable xx_ui (the shell themes
    # through the installed package); point setup at the repo copy.
    common = str(REPO.parent / 'common')
    setup_env = dict(os.environ)
    setup_env['PYTHONPATH'] = common + (
        os.pathsep + setup_env['PYTHONPATH'] if setup_env.get('PYTHONPATH') else '')
    subprocess.run(
        ['meson', 'setup', str(build_dir), str(REPO / 'launcher'),
         '--prefix=/usr'],
        check=True, capture_output=True, text=True, env=setup_env,
    )
    subprocess.run(
        ['meson', 'install', '-C', str(build_dir)],
        check=True, capture_output=True, text=True,
        env={**os.environ, 'DESTDIR': str(destdir)},
    )
    assert (destdir / 'usr' / 'share' / 'systemd' / 'user'
            / 'xx-wm-session.target').is_file()
    assert (destdir / 'usr' / 'share' / 'xdg-desktop-portal'
            / 'xx-wm-portals.conf').is_file()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
