"""Lane sm_storage: storage cleanup, file backup, security scan.

Pure-logic and helper tests with fakes throughout: no package manager,
service, firewall or clamscan is ever executed on this machine. Helper
``run`` stand-ins assert the argv-list contract (no shell); filesystem
roots (HOME, /etc, /media, /run/media) are tmp dirs.
"""
import os
import shutil
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))

import xx_wm_helper as helper

# settings_page before its sections: the sections import it back, so a
# sections-first import would catch it partially initialized.
import settings_page  # noqa: E402

UID = 1000


@pytest.fixture()
def layout(tmp_path):
    cg = tmp_path / 'cg'
    (cg / 'user.slice' / ('user-%d.slice' % UID)).mkdir(parents=True)
    mounts = tmp_path / 'mounts'
    mounts.write_text('cgroup2 %s cgroup2 rw,nosuid,nodev 0 0\n' % cg)
    return SimpleNamespace(
        cg=str(cg), mounts=str(mounts),
        state=str(tmp_path / 'state.json'),
        etc=str(tmp_path / 'etc'),
        run=str(tmp_path / 'run'),
    )


class FakeRun:
    """Recording stand-in for subprocess.run: argv lists only, no shell."""

    def __init__(self, missing=()):
        self.cmds = []
        self.missing = set(missing)

    def __call__(self, argv):
        assert isinstance(argv, list), argv
        assert argv and all(isinstance(a, str) for a in argv), argv
        self.cmds.append(list(argv))
        if argv[0] in self.missing:
            return SimpleNamespace(returncode=127, stdout='',
                                   stderr='%s: not found\n' % argv[0])
        return SimpleNamespace(returncode=0, stdout='', stderr='')


def make_core(layout, run=None, **over):
    kw = dict(sysfs=str(layout.cg), cgroup_root=layout.cg,
              mounts_file=layout.mounts, state_file=layout.state,
              nft_bin='nft', run=run or FakeRun())
    kw.update(over)
    return helper.HelperCore(**kw)


@pytest.fixture()
def present_binaries(monkeypatch):
    """Pretend the cache-tool binaries exist; FakeRun answers the calls."""
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, 'which',
        lambda name: '/usr/bin/%s' % name
        if name in ('apk', 'paccache', 'apt-get', 'journalctl') else
        real_which(name))


# ------------------------------------------------------- helper CleanSystem

class TestCleanSystemAllowlist:
    def test_fixed_categories_only(self):
        assert set(helper.CLEAN_SYSTEM_ALLOWLIST) == {
            'apk-cache', 'paccache-keep', 'paccache-uninstalled',
            'apt-clean', 'journal-vacuum',
        }

    def test_apk_cache_clean_argv(self, layout, tmp_path, present_binaries):
        apk_cache = Path(layout.etc) / 'apk' / 'cache'
        apk_cache.mkdir(parents=True)
        systemd = Path(layout.run) / 'systemd' / 'system'
        run = FakeRun()
        core = make_core(layout, run)
        plan = core.clean_steps(
            ['apk-cache'], apk_cache_dir=str(apk_cache),
            systemd_dir=str(systemd))
        assert plan == {'steps': [('apk-cache', ['apk', 'cache', 'clean'])],
                        'skipped': []}
        assert run.cmds == []

    def test_apk_cache_skipped_without_cache_dir(self, layout):
        run = FakeRun()
        core = make_core(layout, run)
        plan = core.clean_steps(
            ['apk-cache'],
            apk_cache_dir=os.path.join(layout.etc, 'apk', 'cache'),
            systemd_dir=os.path.join(layout.run, 'systemd', 'system'))
        assert plan == {'steps': [], 'skipped': ['apk-cache']}
        assert run.cmds == []

    def test_paccache_pair_and_apt(self, layout, present_binaries):
        run = FakeRun()
        core = make_core(layout, run)
        plan = core.clean_steps(
            ['paccache-keep', 'paccache-uninstalled', 'apt-clean'],
            apk_cache_dir=os.path.join(layout.etc, 'apk', 'cache'),
            systemd_dir=os.path.join(layout.run, 'systemd', 'system'))
        assert plan['steps'] == [
            ('paccache-keep', ['paccache', '-rk2']),
            ('paccache-uninstalled', ['paccache', '-ruk0']),
            ('apt-clean', ['apt-get', 'clean'])]

    def test_paccache_missing_binary_skips(self, layout, monkeypatch):
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        core = make_core(layout, FakeRun())
        plan = core.clean_steps(
            ['paccache-keep'],
            apk_cache_dir=os.path.join(layout.etc, 'apk', 'cache'),
            systemd_dir=os.path.join(layout.run, 'systemd', 'system'))
        assert plan == {'steps': [], 'skipped': ['paccache-keep']}

    def test_journal_needs_systemd(self, layout):
        run = FakeRun()
        core = make_core(layout, run)
        out = core.clean_steps(
            ['journal-vacuum'],
            apk_cache_dir=os.path.join(layout.etc, 'apk', 'cache'),
            systemd_dir=os.path.join(layout.run, 'systemd', 'system'))
        assert out == {'steps': [], 'skipped': ['journal-vacuum']}
        assert run.cmds == []

    def test_journal_vacuum_argv(self, layout, present_binaries):
        systemd = Path(layout.run) / 'systemd' / 'system'
        systemd.mkdir(parents=True)
        run = FakeRun()
        core = make_core(layout, run)
        out = core.clean_steps(
            ['journal-vacuum'],
            apk_cache_dir=os.path.join(layout.etc, 'apk', 'cache'),
            systemd_dir=str(systemd))
        assert out == {'steps': [('journal-vacuum', [
            'journalctl', '--vacuum-time=2weeks', '--vacuum-size=500M'])],
            'skipped': []}

    def test_unknown_category_is_invalid_args(self, layout):
        with pytest.raises(helper.HelperError) as exc:
            make_core(layout).clean_steps(
                ['orphans'],
                apk_cache_dir=os.path.join(layout.etc, 'apk', 'cache'),
                systemd_dir=os.path.join(layout.run, 'systemd', 'system'))
        assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_never_removes_packages(self, layout):
        for argv in helper.CLEAN_SYSTEM_ALLOWLIST.values():
            joined = ' '.join(argv)
            assert ' -R' not in joined and 'remove' not in joined, argv
            assert 'autoremove' not in joined, argv


class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def _allow(conn, sender, action, interactive, callback):
    _allow.seen.append(action)
    callback(True, UID, None)


_allow.seen = []


def _deny(conn, sender, action, interactive, callback):
    callback(False, UID, None)


class TestCleanSystemService:
    def test_dispatched_with_own_action(self, layout):
        _allow.seen.clear()
        apk_cache = Path(layout.etc) / 'apk' / 'cache'
        apk_cache.mkdir(parents=True)
        spawned = []
        service = helper.HelperService(
            connection=object(),
            core=make_core(layout, FakeRun(),
                           spawn=lambda argv, on_exit: spawned.append(argv)),
            authorize=_allow)
        service.core.clean_steps = lambda cats: {
            'steps': [(c, ['apt-get', 'clean']) for c in cats],
            'skipped': []}
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'CleanSystem',
            SimpleNamespace(unpack=lambda: (['apt-clean'],)), invocation)
        assert invocation.error is None
        assert _allow.seen == [helper.ACTION_CLEAN]
        assert invocation.value.get_type_string() == '(s)'
        assert spawned == [['apt-get', 'clean']]

    def test_denied_maps_to_not_authorized(self, layout):
        service = helper.HelperService(connection=object(),
                                       core=make_core(layout),
                                       authorize=_deny)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'CleanSystem',
            SimpleNamespace(unpack=lambda: (['apt-clean'],)), invocation)
        assert invocation.error[0] == helper.ERROR_NOT_AUTHORIZED

    def test_invalid_category_maps_to_invalid_args(self, layout):
        service = helper.HelperService(connection=object(),
                                       core=make_core(layout),
                                       authorize=_allow)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'CleanSystem',
            SimpleNamespace(unpack=lambda: (['/bin/rm'],)), invocation)
        assert invocation.error[0] == helper.ERROR_INVALID_ARGS

    def test_introspection_lists_clean_system(self):
        assert 'name="CleanSystem"' in helper.INTROSPECT_XML
        assert 'type="as"' in helper.INTROSPECT_XML

    def test_policy_action_is_non_interactive_safe(self):
        policy = Path(__file__).parent.parent / 'launcher' / 'data' \
            / 'polkit-1' / 'actions' / 'io.piercingxx.XXWM.Helper.policy'
        text = policy.read_text(encoding='utf-8')
        assert 'io.piercingxx.XXWM.Helper1.clean-system' in text
        section = text.split('clean-system')[1].split('</action>')[0]
        assert '<allow_active>yes</allow_active>' in section
        assert '<allow_any>no</allow_any>' in section


class TestCleanSystemClient:
    def test_params_use_as_variant(self):
        gi = pytest.importorskip('gi')
        gi.require_version('GLib', '2.0')
        from gi.repository import GLib
        import helper_client
        seen = {}

        def fake_job(method, params, on_done):
            seen['method'] = method
            seen['type'] = params.get_type_string()
            # JobFinished's tail: the cleaned categories, one per line.
            on_done(True, 'apt-clean\n')

        helper_client._run_job_orig = helper_client._run_job
        helper_client._run_job = fake_job
        try:
            done = []
            helper_client.clean_system(
                ['apt-clean'], lambda ok, res: done.append((ok, res)))
        finally:
            helper_client._run_job = helper_client._run_job_orig
        assert seen == {'method': 'CleanSystem', 'type': '(as)'}
        assert done == [(True, ['apt-clean'])]
        assert GLib.Variant('(as)', (['apt-clean'],)).get_type_string()


# ------------------------------------------------------------- storage pure

class TestStorageLogic:
    def test_format_bytes(self):
        import settings_storage as store
        assert store.format_bytes(0) == '0 B'
        assert store.format_bytes(1500) == '1.5 kB'
        assert store.format_bytes(2 * 1024 * 1024) == '2.0 MB'

    def test_fs_usage_statvfs(self, tmp_path, monkeypatch):
        import settings_storage as store
        monkeypatch.setattr(os, 'statvfs', lambda _p: SimpleNamespace(
            f_frsize=4096, f_blocks=1000, f_bavail=250))
        total, free = store.fs_usage(str(tmp_path))
        assert (total, free) == (4096 * 1000, 4096 * 250)

    def test_fs_usage_missing_path(self, tmp_path):
        import settings_storage as store
        assert store.fs_usage(str(tmp_path / 'nope')) == (None, None)

    def test_trash_dirs_home_and_volumes(self, tmp_path, monkeypatch):
        import settings_storage as store
        home = tmp_path / 'home'
        (home / '.local' / 'share' / 'Trash').mkdir(parents=True)
        vol = tmp_path / 'media' / 'usb'
        trash = vol / '.Trash-1000'
        trash.mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        found = store.trash_dirs(home=str(home), uid=1000,
                                 media_roots=[str(tmp_path / 'media')])
        assert str(home / '.local' / 'share' / 'Trash') in found
        assert str(trash) in found

    def test_pkg_cache_dirs_apk_gated(self, tmp_path):
        import settings_storage as store
        assert store.pkg_cache_dirs('apk', apk_cache=str(
            tmp_path / 'nope')) == []
        cache = tmp_path / 'apk-cache'
        cache.mkdir()
        assert store.pkg_cache_dirs('apk', apk_cache=str(cache)) == [str(cache)]
        assert store.pkg_cache_dirs(
            'pacman', pacman_cache='/var/cache/pacman/pkg') == [
            '/var/cache/pacman/pkg']

    def test_browser_entries_only_existing_and_closed(self, tmp_path):
        import settings_storage as store
        present = tmp_path / 'mozilla' / 'firefox'
        present.mkdir(parents=True)

        def run(argv):
            assert argv[0] == 'pgrep'
            name = argv[-1]
            code = 0 if name in ('firefox', 'firefox-bin') else 1
            return SimpleNamespace(returncode=code, stdout='', stderr='')

        entries = store.browser_cache_entries(
            cache=str(tmp_path), run=run)
        assert entries == []

    def test_browser_entries_open_browser_kept(self, tmp_path):
        import settings_storage as store
        present = tmp_path / 'mozilla' / 'firefox'
        present.mkdir(parents=True)

        def run(argv):
            return SimpleNamespace(returncode=1, stdout='', stderr='')

        entries = store.browser_cache_entries(
            cache=str(tmp_path), run=run)
        assert [name for name, _p in entries] == ['Firefox']

    def test_user_clean_never_touches_downloads_or_media(
            self, tmp_path, monkeypatch):
        import settings_storage as store
        home = tmp_path / 'home'
        (home / '.cache' / 'thumbnails').mkdir(parents=True)
        (home / '.cache' / 'thumbnails' / 'thumb.png').write_text('x')
        (home / '.local' / 'share' / 'Trash' / 'files').mkdir(parents=True)
        (home / '.local' / 'share' / 'Trash' / 'files' / 'old').write_text(
            'x')
        downloads = home / 'Downloads'
        downloads.mkdir()
        (downloads / 'keep.iso').write_text('x')
        media = home / 'Videos' / 'Screenrecordings'
        media.mkdir(parents=True)
        (media / 'keep.mp4').write_text('x')
        monkeypatch.setenv('HOME', str(home))
        removed = []
        store.clean_user_categories(
            ['thumbnails', 'trash'], home=str(home), uid=1000,
            media_roots=[], run=lambda argv: SimpleNamespace(
                returncode=1, stdout='', stderr=''),
            remove=lambda path: removed.append(path))
        assert (downloads / 'keep.iso').exists()
        assert (media / 'keep.mp4').exists()
        assert removed, 'expected thumbnails/trash removals'

    def test_journal_size_parse(self):
        import settings_storage as store
        assert store.parse_journal_size(
            'Archived and active journals take up 480.0M in the file system.\n'
        ) == '480.0M'
        assert store.parse_journal_size('nope') is None

    def test_unmerged_configs(self, tmp_path):
        import settings_storage as store
        etc = tmp_path / 'etc'
        etc.mkdir()
        (etc / 'foo.apk-new').write_text('x')
        (etc / 'sub').mkdir()
        (etc / 'sub' / 'bar.pacnew').write_text('x')
        (etc / 'live.conf').write_text('x')
        found = store.find_unmerged_configs(etc_root=str(etc))
        assert found == ['foo.apk-new', 'sub/bar.pacnew']

    def test_failed_units_parse(self):
        import settings_storage as store
        out = ('UNIT LOAD ACTIVE SUB DESCRIPTION\n'
               '● foo.service loaded failed failed Foo\n'
               '0 loaded units listed.\n')
        assert store.parse_failed_units(out) == ['foo.service']
        assert store.parse_failed_units('0 loaded units listed.\n') == []

    def test_flatpak_ps_running(self):
        import settings_storage as store
        out = ('NAME APPLICATION\n'
               'App org.example.App\n')
        assert store.parse_flatpak_ps(out) == ['org.example.App']

    def test_root_categories_are_fixed(self):
        import settings_storage as store
        assert set(store.ROOT_CLEAN_CATEGORIES) <= set(
            helper.CLEAN_SYSTEM_ALLOWLIST)


# ------------------------------------------------------------------ backup

class TestFileBackupLogic:
    def test_volumes_lists_mounts(self, tmp_path):
        import filebackup
        user_vol = tmp_path / 'run' / 'media' / 'user' / 'USB'
        user_vol.mkdir(parents=True)
        sys_vol = tmp_path / 'media' / 'SD'
        sys_vol.mkdir(parents=True)
        found = filebackup.list_volumes(
            user='user', run_media=str(tmp_path / 'run' / 'media' / 'user'),
            media_root=str(tmp_path / 'media'))
        assert found == [str(user_vol), str(sys_vol)]

    def test_dangerous_destinations(self, tmp_path):
        import filebackup
        home = tmp_path / 'home'
        for bad in ('/', '/etc', '/usr', '/var', '/home', '/root'):
            assert filebackup.dangerous_dest(bad, home=str(home)), bad
        assert not filebackup.dangerous_dest(
            str(tmp_path / 'media' / 'USB' / 'bk'), home=str(home))

    def test_mirror_into_home_refused(self, tmp_path):
        import filebackup
        home = tmp_path / 'home'
        home.mkdir()
        ok, _err = filebackup.validate_transfer(
            str(home / 'Pictures'), str(home), mirror=True, home=str(home))
        assert not ok

    def test_add_mode_into_home_subdir_ok(self, tmp_path):
        import filebackup
        home = tmp_path / 'home'
        src = home / 'Pictures'
        src.mkdir(parents=True)
        dest = tmp_path / 'media' / 'USB' / 'bk'
        dest.mkdir(parents=True)
        ok, err = filebackup.validate_transfer(
            str(src), str(dest), mirror=False, home=str(home))
        assert ok, err

    def test_target_is_a_per_folder_dir_never_the_volume(self, tmp_path):
        import filebackup
        vol = str(tmp_path / 'media' / 'SD')
        target = filebackup.backup_target(
            vol, str(tmp_path / 'home' / 'Pictures'), host='phone')
        assert target == os.path.join(vol, 'xx-backup', 'phone', 'Pictures')
        assert filebackup.backup_target(
            vol, str(tmp_path / 'home' / '.local' / 'share' / 'xx-dialer'),
            host='phone').endswith(os.path.join('phone', 'xx-dialer'))
        assert filebackup.backup_target(vol, '', host='phone') == ''
        assert filebackup.backup_target('', '/x/Pictures', host='p') == ''

    def test_host_is_one_safe_component(self, monkeypatch):
        import filebackup
        monkeypatch.setattr(filebackup.socket, 'gethostname',
                            lambda: '../evil/host')
        assert filebackup.backup_host() == 'evil-host'
        monkeypatch.setattr(filebackup.socket, 'gethostname', lambda: '..')
        assert filebackup.backup_host() == 'phone'

    def test_mirror_onto_mount_point_refused(self, tmp_path, monkeypatch):
        import filebackup
        src = tmp_path / 'Pictures'
        src.mkdir()
        dest = tmp_path / 'SD'
        dest.mkdir()
        monkeypatch.setattr(filebackup.os.path, 'ismount',
                            lambda p: os.path.normpath(p) == str(dest))
        ok, err = filebackup.validate_transfer(
            str(src), str(dest), mirror=True, home=str(tmp_path / 'h'))
        assert not ok and 'drive' in err
        ok, _err = filebackup.validate_transfer(
            str(src), str(dest / 'xx-backup' / 'p' / 'Pictures'),
            mirror=True, home=str(tmp_path / 'h'))
        assert ok

    def test_transfer_creates_the_target_dirs(self, tmp_path, monkeypatch):
        import subprocess
        import filebackup
        src = tmp_path / 'Pictures'
        src.mkdir()
        (src / 'a.jpg').write_text('x')
        dest = tmp_path / 'SD' / 'xx-backup' / 'p' / 'Pictures'
        argvs = []

        class _Proc:
            stdout = iter(())

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(subprocess, 'Popen',
                            lambda argv, **_k: argvs.append(argv) or _Proc())
        ok, err = filebackup.run_transfer(str(src), str(dest), False, 0,
                                          lambda f: None)
        assert ok, err
        assert dest.is_dir()
        assert argvs[0][-1] == str(dest) + '/'

    def test_settings_backup_targets_the_folder_dir(self, tmp_path,
                                                    monkeypatch):
        import settings_page
        import filebackup
        monkeypatch.setattr(filebackup, 'backup_host', lambda: 'phone')
        section = settings_page.BackupSection()
        said = []
        section._say = said.append
        home = tmp_path / 'home'
        (home / 'Pictures').mkdir(parents=True)
        vol = tmp_path / 'SD'
        vol.mkdir()
        (vol / 'unrelated.txt').write_text('keep')
        section._source = str(home / 'Pictures')
        section._dest = str(vol)
        monkeypatch.setenv('HOME', str(home))
        src, dest = section._endpoints('Backup')
        assert src == str(home / 'Pictures')
        assert dest == str(vol / 'xx-backup' / 'phone' / 'Pictures')
        assert section._endpoints('Restore') is None
        assert said == ['No backup of Pictures on that storage.']
        (vol / 'xx-backup' / 'phone' / 'Pictures').mkdir(parents=True)
        assert section._endpoints('Restore') == (
            str(vol / 'xx-backup' / 'phone' / 'Pictures'),
            str(home / 'Pictures'))

    def test_real_rsync_progress_moves(self, tmp_path):
        import filebackup
        if shutil.which('rsync') is None:
            pytest.skip('rsync not installed')
        src = tmp_path / 'Pictures'
        src.mkdir()
        for i in range(20):
            (src / ('f%02d.bin' % i)).write_bytes(os.urandom(64 * 1024))
        dest = tmp_path / 'SD' / 'xx-backup' / 'p' / 'Pictures'
        seen = []
        ok, err = filebackup.run_transfer(str(src), str(dest), False,
                                          on_progress=seen.append)
        assert ok, err
        assert len(list(dest.iterdir())) == 20
        assert seen and seen[-1] == 1.0
        assert any(0.0 < f < 1.0 for f in seen) or len(seen) >= 2
        assert seen == sorted(seen)

    def test_progress_comes_from_progress2_lines(self, tmp_path, monkeypatch):
        import subprocess
        import filebackup

        class _Proc:
            stdout = iter(['          1,024  10%    1.00MB/s    0:00:01\n',
                           '        512,000  55%    1.00MB/s    0:00:01\n',
                           '        900,000 100%    1.00MB/s    0:00:01\n'])

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(subprocess, 'Popen', lambda argv, **_k: _Proc())
        seen = []
        ok, _err = filebackup.run_transfer(
            str(tmp_path), str(tmp_path / 'd'), False, on_progress=seen.append)
        assert ok
        assert seen[:3] == [0.10, 0.55, 1.0]

    def test_cancel_stops_rsync(self, tmp_path, monkeypatch):
        import subprocess
        import threading
        import filebackup
        cancel = threading.Event()
        calls = []

        def _lines():
            yield '   10%\n'
            cancel.set()
            yield '   20%\n'
            yield '   30%\n'

        class _Proc:
            stdout = _lines()

            def terminate(self):
                calls.append('terminate')

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(subprocess, 'Popen', lambda argv, **_k: _Proc())
        ok, err = filebackup.run_transfer(
            str(tmp_path), str(tmp_path / 'd'), False, cancel=cancel)
        assert not ok and err == 'Stopped.'
        assert calls == ['terminate']

    def test_confirmed_transfer_does_not_walk_the_tree_again(
            self, tmp_path, monkeypatch):
        import settings_page
        import filebackup
        walks = []
        monkeypatch.setattr(filebackup, 'dry_run_counts',
                            lambda *a, **k: walks.append(a) or (True, '1 file'))
        monkeypatch.setattr(filebackup, 'run_transfer',
                            lambda *a, **k: (True, None))
        monkeypatch.setattr(settings_page, '_run_bg',
                            lambda work, done: done(work()))
        section = settings_page.BackupSection()
        said = []
        section._say = said.append
        section._start_transfer(object(), 'Backup', str(tmp_path),
                                str(tmp_path / 'd'))
        assert walks == []
        assert said[-1] == 'Backup done.'

    def test_self_nesting_excluded(self, tmp_path):
        import filebackup
        src = tmp_path / 'Pictures'
        src.mkdir()
        dest = src / 'backup'
        argv = filebackup.build_rsync_argv(
            str(src), str(dest), mirror=False)
        assert argv[:2] == ['rsync', '-aH']
        assert '--delete' not in argv
        assert any(a.startswith('--exclude=') for a in argv)

    def test_mirror_adds_delete(self, tmp_path):
        import filebackup
        argv = filebackup.build_rsync_argv(
            str(tmp_path), str(tmp_path / 'd'), mirror=True)
        assert '--delete' in argv

    def test_dry_run_counts(self):
        import filebackup
        out = ('>f.st...... a/b\n'
               '>f.st...... a/c\n'
               '*deleting  a/old\n'
               '.d....πx... skip\n')
        assert filebackup.parse_dry_run_counts(out) == (2, 1)

    def test_dry_run_argv_itemize(self, tmp_path):
        import filebackup
        argv = filebackup.build_dry_run_argv(
            str(tmp_path), str(tmp_path / 'd'), mirror=True)
        assert '-aHn' in argv or {'-aH', '-n'} <= set(argv) \
            or '--dry-run' in argv
        assert '--itemize-changes' in argv

    def test_sources_lists_xx_app_data(self, tmp_path, monkeypatch):
        import filebackup
        home = tmp_path / 'home'
        (home / 'Pictures').mkdir(parents=True)
        (home / '.local' / 'share' / 'xx-dialer').mkdir(parents=True)
        (home / '.local' / 'share' / 'other').mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        sources = filebackup.backup_sources(home=str(home))
        assert str(home / 'Pictures') in sources
        assert str(home / '.local' / 'share' / 'xx-dialer') in sources
        assert str(home / '.local' / 'share' / 'other') not in sources


# ----------------------------------------------------------------- security

class TestSecurityLogic:
    def test_clamscan_argv_limits_no_clamd(self, tmp_path):
        import settings_security as sec
        argv = sec.build_clamscan_argv(
            str(tmp_path), recursive=True, nice=True, ionice=True)
        assert argv[:3] == ['nice', '-n', '19']
        assert 'ionice' in argv
        assert 'clamdscan' not in ' '.join(argv)
        assert 'clamscan' in argv
        for flag in ('--max-filesize=1024M', '--max-scansize=2048M',
                     '--max-recursion=32', '--max-files=20000',
                     '--alert-exceeds-max=yes', '--cross-fs=no'):
            assert flag in argv, flag

    def test_parse_results_skips_limit_lines(self):
        import settings_security as sec
        out = ('/a/b: Eicar FOUND\n'
               '/a/big.iso: Heuristics.Limits.Exceeded FOUND\n'
               '/a/ok: OK\n')
        assert sec.parse_clamscan_output(out) == [('/a/b', 'Eicar')]

    def test_quarantine_moves_chmods_writes_meta(self, tmp_path):
        import settings_security as sec
        quar = tmp_path / 'quarantine'
        quar.mkdir()
        target = tmp_path / 'evil.bin'
        target.write_text('x')
        dest = sec.quarantine_file(
            str(target), 'Eicar', quarantine_dir=str(quar))
        assert not target.exists()
        assert stat.S_IMODE(os.stat(dest).st_mode) == 0o400
        meta = Path(str(dest).replace('.quar', '.meta')).read_text()
        assert 'signature=Eicar' in meta
        assert 'sha256=' in meta
        assert 'path=%s' % target in meta

    def test_baseline_first_run_saves(self, tmp_path):
        import settings_security as sec
        state = tmp_path / 'state'
        changed, _lines = sec.baseline_diff(
            'listening', ['tcp 1.2.3.4:22'], state_dir=str(state))
        assert changed is False
        changed, _lines = sec.baseline_diff(
            'listening', ['tcp 1.2.3.4:22'], state_dir=str(state))
        assert changed is False
        changed, lines = sec.baseline_diff(
            'listening', ['tcp 1.2.3.4:22', 'tcp 0.0.0.0:80'],
            state_dir=str(state))
        assert changed is True
        assert any('0.0.0.0:80' in line for line in lines)

    def test_ss_parse(self):
        import settings_security as sec
        out = ('State Recv-Q Send-Q Local Address:Port Peer Address:Port\n'
               'LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n')
        assert sec.parse_ss_listening(out) == ['tcp 0.0.0.0:22']

    def test_package_verify_selection(self):
        import settings_security as sec
        assert sec.package_verify_argv(which=lambda _b: None) is None
        assert sec.package_verify_argv(
            which=lambda b: '/sbin/apk' if b == 'apk' else None) == [
            'apk', 'audit', '--system']
        assert sec.package_verify_argv(
            which=lambda b: '/usr/bin/pacman' if b == 'pacman' else None) == [
            'pacman', '-Qkk']

    def test_quarantine_dir_default(self, tmp_path, monkeypatch):
        import settings_security as sec
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('XDG_DATA_HOME', raising=False)
        assert sec.quarantine_dir() == str(
            tmp_path / '.local' / 'share' / 'piercingxx' / 'quarantine')


# ----------------------------------------------------------------- registry

class TestSectionRegistry:
    def test_storage_and_security_registered_gestures_last(self):
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        assert 'storage' in keys
        assert 'security' in keys
        assert 'storage' in settings_page._SETTINGS_SECTION_KEYS
        assert 'security' in settings_page._SETTINGS_SECTION_KEYS
        assert keys[-1] == 'gestures'
        assert len(keys) == len(set(keys))
        for _key, cls in settings_page.SETTINGS_SECTIONS:
            assert issubclass(cls, settings_page._Section)

    def test_new_files_installed(self):
        meson = (Path(__file__).parent.parent / 'launcher'
                 / 'meson.build').read_text(encoding='utf-8')
        for name in ('src/settings_storage.py', 'src/settings_security.py',
                     'src/filebackup.py'):
            assert "'%s'" % name in meson

    def test_rsync_in_install_deps(self):
        text = (Path(__file__).parent.parent / 'scripts'
                / 'install.sh').read_text(encoding='utf-8')
        assert text.count('rsync') >= 3

    def test_no_blocking_calls_in_new_modules(self):
        import subprocess as sp
        proc = sp.run(
            [sys.executable, str(Path(__file__).parent.parent / 'scripts'
                                 / 'check_mainloop_blocks.py')],
            capture_output=True, text=True, timeout=120, check=False)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def _fake_ui(self, monkeypatch, sec):
        monkeypatch.setattr(sec.ui, 'nav_row',
                            lambda title, *_a, **_k: ('row', title))
        monkeypatch.setattr(
            sec.ui, 'group',
            lambda title, rows=(), note=None: [('caption', title),
                                               list(rows), ('note', title)])

    def test_security_check_stays_without_clamscan(self, monkeypatch):
        import settings_security as sec
        monkeypatch.setattr(sec.shutil, 'which', lambda _b: None)
        self._fake_ui(monkeypatch, sec)
        assert sec.clamscan_available() is False
        built = sec.SecuritySection().build(object())
        captions = [w[1] for w in built if isinstance(w, tuple)
                    and w[0] == 'caption']
        assert captions == ['Security check']

    def test_scan_group_with_clamscan(self, monkeypatch):
        import settings_security as sec
        monkeypatch.setattr(sec.shutil, 'which', lambda b: '/usr/bin/' + b)
        self._fake_ui(monkeypatch, sec)
        built = sec.SecuritySection().build(object())
        captions = [w[1] for w in built if isinstance(w, tuple)
                    and w[0] == 'caption']
        assert captions == ['Malware scan', 'Security check']

    def test_scan_gets_more_than_two_minutes(self, monkeypatch):
        import settings_security as sec
        import settings_page
        seen = {}

        def _fake_run(argv, timeout=120):
            seen['timeout'] = timeout
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        monkeypatch.setattr(sec, '_run', _fake_run)
        monkeypatch.setattr(settings_page, '_run_bg',
                            lambda work, done: done(work()))
        section = sec.SecuritySection()
        section._say = lambda text: None
        section._render_results = lambda win: None
        section._start_scan(object(), '/tmp', recursive=True)
        assert seen['timeout'] >= 30 * 60
