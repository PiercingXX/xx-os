"""OpenRC hosts start the session through a PAM login (review P2).

polkit grants the helper's actions only to callers in an active logind
session. The xx-wm OpenRC service starts the session without PAM, so
elogind registers nothing and every helper call is refused. install.sh
now starts the session through tinydm or greetd (both open a PAM session
that Alpine's base-session hands to pam_elogind), installs the elogind
build of polkit, and keeps the raw service only as a loud last resort.
"""
import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
OPENRC = REPO / 'launcher' / 'data' / 'openrc' / 'xx-wm'
HELPER_MD = REPO / 'docs' / 'helper.md'


def _extract(names):
    script = INSTALL_SH.read_text(encoding='utf-8')
    chunks = []
    for name in names:
        match = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
        assert match, name
        chunks.append('%s() {%s}\n' % (name, match.group(1)))
    return ''.join(chunks)


def _stub(bindir, name, body='exit 0\n'):
    path = bindir / name
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o755)


def _openrc_host(tmp_path, *, tinydm=False, greetd=False, elogind=True):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    log = tmp_path / 'calls.log'
    record = 'echo "$(basename "$0") $*" >> "%s"\nexit 0\n' % log
    for name in ('openrc-run', 'rc-update', 'tee', 'apk'):
        _stub(bindir, name, record)
    if tinydm:
        _stub(bindir, 'tinydm-set-session', record)
    initd = tmp_path / 'init.d'
    initd.mkdir()
    if elogind:
        (initd / 'elogind').write_text('')
    funcs = tmp_path / 'funcs.sh'
    funcs.write_text(
        _extract(['is_systemd', 'has_openrc', 'write_openrc_conf',
                  'enable_service', 'pkg_install', 'install_deps'])
        + 'greetd_detected() { return %d; }\n' % (0 if greetd else 1)
        + ''.join('%s() { :; }\n' % name for name in (
            'install_portals', 'install_media_deps', 'install_notes_deps',
            'install_auth_deps')))
    env = dict(os.environ, PATH='%s:%s' % (bindir, os.environ['PATH']),
               SUDO='', PKG='apk', XX_WM_INITD=str(initd),
               XX_WM_SYSTEMD_RUN_DIR=str(tmp_path / 'no-systemd'))

    def run(driver):
        proc = subprocess.run(['sh', '-c', '. "%s"; %s' % (funcs, driver)],
                              capture_output=True, text=True, env=env,
                              timeout=30)
        calls = log.read_text() if log.exists() else ''
        return proc, calls

    return run


class TestSessionStarter:
    def test_tinydm_starts_the_session(self, tmp_path):
        proc, calls = _openrc_host(tmp_path, tinydm=True)('enable_service')
        assert proc.returncode == 0, proc.stderr
        assert ('tinydm-set-session -f -s '
                '/usr/share/wayland-sessions/xx-wm.desktop') in calls
        assert 'rc-update add tinydm default' in calls
        assert 'rc-update add xx-wm default' not in calls
        assert 'rc-update del xx-wm default' in calls
        assert 'rc-update add xx-wm-helper-boot boot' in calls

    def test_greetd_starts_the_session(self, tmp_path):
        proc, calls = _openrc_host(tmp_path, greetd=True)('enable_service')
        assert proc.returncode == 0, proc.stderr
        assert 'rc-update add greetd default' in calls
        assert 'rc-update add xx-wm default' not in calls
        assert 'rc-update del xx-wm default' in calls

    def test_raw_service_is_a_loud_last_resort(self, tmp_path):
        proc, calls = _openrc_host(tmp_path)('enable_service')
        assert proc.returncode == 0, proc.stderr
        assert 'rc-update add xx-wm default' in calls
        assert 'polkit refuses' in proc.stderr

    def test_elogind_is_enabled(self, tmp_path):
        _proc, calls = _openrc_host(tmp_path, greetd=True)('enable_service')
        assert 'rc-update add elogind default' in calls

    def test_elogind_skipped_when_not_installed(self, tmp_path):
        _proc, calls = _openrc_host(tmp_path, greetd=True,
                                    elogind=False)('enable_service')
        assert 'elogind' not in calls


class TestPolkitBuild:
    def test_openrc_installs_the_elogind_polkit(self, tmp_path):
        proc, calls = _openrc_host(tmp_path)('install_deps')
        assert proc.returncode == 0, proc.stderr
        assert 'apk add elogind polkit-elogind' in calls

    def test_systemd_keeps_the_plain_polkit(self, tmp_path):
        run = _openrc_host(tmp_path)
        systemd = tmp_path / 'systemd'
        systemd.mkdir()
        proc, calls = run('XX_WM_SYSTEMD_RUN_DIR="%s"; install_deps' % systemd)
        assert proc.returncode == 0, proc.stderr
        assert 'apk add polkit' in calls
        assert 'polkit-elogind' not in calls


class TestDocumented:
    def test_init_script_says_it_has_no_session(self):
        text = OPENRC.read_text(encoding='utf-8')
        assert 'ewarn' in text
        assert 'tinydm or greetd' in text

    def test_helper_doc_explains_and_verifies(self):
        text = HELPER_MD.read_text(encoding='utf-8')
        assert '## Sessions and polkit' in text
        section = text.split('## Sessions and polkit', 1)[1].split('\n## ', 1)[0]
        for needle in ('loginctl', 'pkcheck', 'polkit-elogind', 'tinydm',
                       'greetd'):
            assert needle in section, needle
