"""scripts/uninstall.sh removes what install.sh and meson installed (P4).

A real meson install of the launcher into a staged root, plus the extras
install.sh writes (elogind power-key drop-in, OpenRC conf, greetd
autologin with its backup, fonts, the squeekboard link, the state dir and
the input-group record), is removed by the real uninstall.sh run against
that root; files the suite does not own survive.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
UNINSTALL_SH = REPO / 'scripts' / 'uninstall.sh'
COMMON = REPO.parent / 'common'

needs_meson = pytest.mark.skipif(
    not (shutil.which('meson') and shutil.which('ninja')),
    reason='needs meson and ninja')


def _meson_env():
    env = dict(os.environ)
    env['PYTHONPATH'] = str(COMMON) + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


@pytest.fixture
def staged(tmp_path):
    """A repo copy with the launcher installed into a staged root."""
    repo = tmp_path / 'repo'
    shutil.copytree(REPO / 'launcher', repo / 'xx-wm' / 'launcher',
                    ignore=shutil.ignore_patterns('build', '__pycache__'))
    (repo / 'xx-wm' / 'scripts').mkdir(parents=True)
    for script in (INSTALL_SH, UNINSTALL_SH):
        shutil.copy2(script, repo / 'xx-wm' / 'scripts' / script.name)
    (repo / 'common').symlink_to(COMMON)
    root = tmp_path / 'root'
    home = tmp_path / 'home'
    home.mkdir()
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    calls = tmp_path / 'calls.log'
    for name in ('systemctl', 'rc-update', 'glib-compile-schemas',
                 'gtk-update-icon-cache', 'gtk4-update-icon-cache',
                 'update-desktop-database', 'fc-cache', 'gpasswd'):
        stub = bindir / name
        stub.write_text('#!/bin/sh\necho "%s $*" >> "%s"\nexit 0\n'
                        % (name, calls))
        stub.chmod(0o755)
    systemd = tmp_path / 'systemd'
    systemd.mkdir()
    env = _meson_env()
    env.update({
        'PATH': '%s:%s' % (bindir, os.environ['PATH']),
        'HOME': str(home),
        'XX_WM_UNINSTALL_ROOT': str(root),
        'XX_WM_SYSTEMD_RUN_DIR': str(systemd),
        'XX_WM_FONT_DIR': str(home / 'fonts'),
    })

    def install(build_dir):
        subprocess.run(['meson', 'setup', str(build_dir),
                        str(repo / 'xx-wm' / 'launcher'), '--prefix=/usr'],
                       check=True, capture_output=True, text=True,
                       env=_meson_env())
        subprocess.run(['meson', 'install', '-C', str(build_dir)],
                       check=True, capture_output=True, text=True,
                       env=dict(_meson_env(), DESTDIR=str(root)))

    def run(*args):
        return subprocess.run(
            ['sh', str(repo / 'xx-wm' / 'scripts' / 'uninstall.sh'), *args],
            capture_output=True, text=True, env=env, timeout=120)

    return {'repo': repo, 'root': root, 'home': home, 'calls': calls,
            'install': install, 'run': run}


def _extras(staged):
    root, home = staged['root'], staged['home']
    made = {}
    elogind = root / 'etc' / 'elogind' / 'logind.conf.d' / '10-xx-wm-power.conf'
    elogind.parent.mkdir(parents=True)
    elogind.write_text('[Login]\nHandlePowerKey=ignore\n')
    (root / 'etc' / 'conf.d').mkdir(parents=True)
    (root / 'etc' / 'conf.d' / 'xx-wm').write_text('XX_WM_USER=tester\n')
    (root / 'usr' / 'share' / 'xx-wm' / 'deployed_extra.py').write_text('')
    greetd = root / 'etc' / 'greetd' / 'config.toml'
    greetd.parent.mkdir(parents=True)
    greetd.write_text('[terminal]\nvt = 7\n\n[initial_session]\n'
                      'command = "/usr/libexec/xx-wm-session"\nuser = "t"\n')
    (root / 'etc' / 'greetd' / 'config.toml.xx-wm-backup').write_text(
        '[terminal]\nvt = 7\n')
    keep = [root / 'usr' / 'share' / 'applications' / 'other.desktop',
            root / 'etc' / 'systemd' / 'logind.conf.d' / 'other.conf',
            root / 'usr' / 'share' / 'glib-2.0' / 'schemas' / 'org.other.gschema.xml',
            home / 'fonts' / 'MyOwnFont.ttf']
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('keep')
    (home / 'fonts' / 'JetBrainsMonoNerdFont-Regular.ttf').write_text('f')
    (home / 'fonts' / '.xx-wm-fonts').write_text(
        'JetBrainsMonoNerdFont-Regular.ttf\n')
    link = home / '.local' / 'share' / 'squeekboard' / 'keyboards'
    link.parent.mkdir(parents=True)
    link.symlink_to('/usr/share/xx-wm/squeekboard')
    (home / '.cache' / 'piercing-dots').mkdir(parents=True)
    state = root / 'var' / 'lib' / 'xx-apps'
    state.mkdir(parents=True)
    (state / 'installed.json').write_text(json.dumps(
        {'xx-wm': {}, 'xx-keyboard': {}, 'xx-calendar': {}, 'common': {}}))
    me = subprocess.run(['id', '-un'], capture_output=True, text=True).stdout.strip()
    (state / 'input-group-added').write_text(me + '\n')
    made['keep'] = keep
    made['state'] = state
    made['greetd'] = greetd
    made['link'] = link
    return made


def _log_files(build_dir):
    log = build_dir / 'meson-logs' / 'install-log.txt'
    return [Path(line) for line in log.read_text().splitlines()
            if line and not line.startswith('#')]


@needs_meson
class TestUninstall:
    def test_removes_what_was_installed_from_the_install_log(self, staged):
        build = staged['repo'] / 'xx-wm' / 'launcher' / 'build'
        staged['install'](build)
        installed = [p for p in _log_files(build) if p.is_file()]
        assert any(p.name == '10-xx-wm-power.conf' for p in installed)
        assert len(installed) > 100
        extras = _extras(staged)
        proc = staged['run']('--keep-apps')
        assert proc.returncode == 0, proc.stdout + proc.stderr
        left = [p for p in installed if p.exists() or p.is_symlink()]
        assert left == []
        root = staged['root']
        assert not (root / 'usr' / 'share' / 'xx-wm').exists()
        assert not (root / 'etc' / 'elogind' / 'logind.conf.d'
                    / '10-xx-wm-power.conf').exists()
        assert not (root / 'etc' / 'conf.d' / 'xx-wm').exists()
        for path in extras['keep']:
            assert path.read_text() == 'keep', path
        assert extras['greetd'].read_text() == '[terminal]\nvt = 7\n'
        assert not extras['link'].is_symlink()
        home = staged['home']
        assert not (home / 'fonts' / 'JetBrainsMonoNerdFont-Regular.ttf').exists()
        assert not (home / 'fonts' / '.xx-wm-fonts').exists()
        assert not (home / '.cache' / 'piercing-dots').exists()
        data = json.loads((extras['state'] / 'installed.json').read_text())
        assert sorted(data) == ['common', 'xx-calendar']
        assert not (extras['state'] / 'input-group-added').exists()
        calls = staged['calls'].read_text()
        assert 'systemctl --user disable --now xx-wm' in calls
        assert 'systemctl disable --now xx-wm-helper-boot.service' in calls
        assert 'glib-compile-schemas' in calls
        assert 'input group' in proc.stdout

    def test_without_a_build_dir_meson_lists_the_files(self, staged, tmp_path):
        build = tmp_path / 'elsewhere'
        staged['install'](build)
        installed = [p for p in _log_files(build) if p.is_file()]
        proc = staged['run']('--keep-apps')
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert [p for p in installed if p.exists()] == []
        assert not (staged['root'] / 'usr' / 'share' / 'xx-wm').exists()

    def test_dry_run_changes_nothing(self, staged):
        build = staged['repo'] / 'xx-wm' / 'launcher' / 'build'
        staged['install'](build)
        installed = [p for p in _log_files(build) if p.is_file()]
        extras = _extras(staged)
        proc = staged['run']('--dry-run', '--keep-apps')
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert all(p.exists() for p in installed)
        assert extras['link'].is_symlink()
        assert 'would run: rm -f -- ' in proc.stdout
        assert not staged['calls'].exists() or \
            'disable' not in staged['calls'].read_text()

    def test_full_uninstall_forgets_every_app(self, staged):
        build = staged['repo'] / 'xx-wm' / 'launcher' / 'build'
        staged['install'](build)
        extras = _extras(staged)
        proc = staged['run']()
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not extras['state'].exists()
        assert 'xx-calendar: no source tree' in proc.stdout


class TestScript:
    def test_app_list_comes_from_install_sh(self):
        text = UNINSTALL_SH.read_text(encoding='utf-8')
        assert '^XX_APPS="' in text
        assert re.search(r'^XX_APPS="[^"]+"$', INSTALL_SH.read_text(), re.M)

    def test_rejects_unknown_arguments(self):
        proc = subprocess.run(['sh', str(UNINSTALL_SH), '--bogus'],
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode != 0


def _extract(names):
    script = INSTALL_SH.read_text(encoding='utf-8')
    out = []
    for name in names:
        match = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
        assert match, name
        out.append('%s() {%s}\n' % (name, match.group(1)))
    return ''.join(out)


class TestInstallRecords:
    def _stub(self, bindir, name, body):
        path = bindir / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    def test_input_group_addition_is_recorded(self, tmp_path):
        bindir = tmp_path / 'bin'
        bindir.mkdir()
        self._stub(bindir, 'usermod', 'exit 0\n')
        real_id = shutil.which('id')
        self._stub(bindir, 'id',
                   'if [ "$1" = -nG ]; then echo users; exit 0; fi\n'
                   'exec %s "$@"\n' % real_id)
        funcs = tmp_path / 'f.sh'
        funcs.write_text(_extract(['_xx_state_dir', 'add_input_group']))
        state = tmp_path / 'state'
        env = dict(os.environ, PATH='%s:%s' % (bindir, os.environ['PATH']),
                   SUDO='', XX_APPS_STATE_DIR=str(state))
        proc = subprocess.run(['sh', '-c', '. "%s"; add_input_group' % funcs],
                              capture_output=True, text=True, env=env,
                              timeout=30)
        assert proc.returncode == 0, proc.stderr
        me = subprocess.run([real_id, '-un'], capture_output=True,
                            text=True).stdout.strip()
        assert (state / 'input-group-added').read_text() == me + '\n'

    def test_existing_member_is_not_recorded(self, tmp_path):
        bindir = tmp_path / 'bin'
        bindir.mkdir()
        real_id = shutil.which('id')
        self._stub(bindir, 'id',
                   'if [ "$1" = -nG ]; then echo "users input"; exit 0; fi\n'
                   'exec %s "$@"\n' % real_id)
        funcs = tmp_path / 'f.sh'
        funcs.write_text(_extract(['_xx_state_dir', 'add_input_group']))
        state = tmp_path / 'state'
        env = dict(os.environ, PATH='%s:%s' % (bindir, os.environ['PATH']),
                   SUDO='', XX_APPS_STATE_DIR=str(state))
        subprocess.run(['sh', '-c', '. "%s"; add_input_group' % funcs],
                       check=True, env=env, timeout=30)
        assert not (state / 'input-group-added').exists()

    @pytest.mark.skipif(not (shutil.which('unzip') and shutil.which('sha256sum')),
                        reason='needs unzip and sha256sum')
    def test_downloaded_fonts_are_listed(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('JetBrainsMonoNerdFont-Regular.ttf', 'a')
            archive.writestr('sub/JetBrainsMonoNerdFont-Bold.ttf', 'b')
        payload = tmp_path / 'fonts.zip'
        payload.write_bytes(buf.getvalue())
        digest = hashlib.sha256(buf.getvalue()).hexdigest()
        bindir = tmp_path / 'bin'
        bindir.mkdir()
        self._stub(bindir, 'fc-list', 'exit 0\n')
        self._stub(bindir, 'curl',
                   'while [ $# -gt 1 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                   'cp "%s" "$out"\n' % payload)
        fontdir = tmp_path / 'fonts'
        (fontdir).mkdir()
        (fontdir / 'Mine.ttf').write_text('mine')
        funcs = tmp_path / 'f.sh'
        funcs.write_text(_extract(['download_nerd_font_fallback']))
        env = dict(os.environ, PATH='%s:%s' % (bindir, os.environ['PATH']),
                   XX_WM_FONT_DIR=str(fontdir),
                   XX_WM_NERD_FONT_SHA256=digest,
                   XX_WM_NERD_FONT_URL='https://example.invalid/f.zip')
        subprocess.run(['sh', '-c', '. "%s"; download_nerd_font_fallback' % funcs],
                       check=True, env=env, timeout=30)
        listed = sorted((fontdir / '.xx-wm-fonts').read_text().split())
        assert listed == ['JetBrainsMonoNerdFont-Bold.ttf',
                          'JetBrainsMonoNerdFont-Regular.ttf']
        assert (fontdir / 'JetBrainsMonoNerdFont-Bold.ttf').read_text() == 'b'
        assert (fontdir / 'Mine.ttf').read_text() == 'mine'
