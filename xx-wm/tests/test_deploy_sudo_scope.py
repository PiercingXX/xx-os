"""The smoke tablet's passwordless sudo covers only the deploy sync (P4).

grant-tablet-sudo.sh used to leave `NOPASSWD: ALL` behind. It now
installs scripts/deploy-sync.sh as a root-owned helper and grants only
that command; the helper copies one private staging dir the caller owns
into /usr/share/xx-wm and refuses everything else. deploy.sh uses it.
"""
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
GRANT = REPO / 'scripts' / 'grant-tablet-sudo.sh'
SYNC_SH = REPO / 'scripts' / 'deploy-sync.sh'
DEPLOY = REPO / 'scripts' / 'deploy.sh'
SYNC_PATH = '/usr/local/libexec/xx-wm-deploy-sync'


class TestGrant:
    def test_no_blanket_grant(self):
        text = GRANT.read_text(encoding='utf-8')
        code = [line for line in text.splitlines()
                if not line.lstrip().startswith('#')]
        assert not [line for line in code if 'NOPASSWD: ALL' in line]
        assert 'RULE="${USER_NAME} ALL=(root) NOPASSWD: ${SYNC}"' in text
        assert 'SYNC=%s' % SYNC_PATH in text

    def test_dry_run_shows_the_scoped_rule(self):
        env = dict(os.environ, PIERCING_DEVICE='device.invalid',
                   XX_WM_USER='tester')
        proc = subprocess.run(['sh', str(GRANT), '--dry-run'],
                              capture_output=True, text=True, env=env,
                              timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert 'tester ALL=(root) NOPASSWD: %s' % SYNC_PATH in proc.stdout
        assert 'NOPASSWD: ALL' not in proc.stdout
        assert 'deploy-sync.sh' in proc.stdout

    def test_revoke_removes_the_helper_too(self):
        env = dict(os.environ, PIERCING_DEVICE='device.invalid',
                   XX_WM_USER='tester')
        proc = subprocess.run(['sh', str(GRANT), '--revoke', '--dry-run'],
                              capture_output=True, text=True, env=env,
                              timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert SYNC_PATH in proc.stdout

    def test_installs_the_helper_root_owned(self):
        text = GRANT.read_text(encoding='utf-8')
        assert 'install -o root -g root -m 0755 ${REMOTE_TMP}/deploy-sync.sh ${SYNC}' in text


class TestDeployUsesTheHelper:
    def test_same_helper_path_and_stage_prefix(self):
        deploy = DEPLOY.read_text(encoding='utf-8')
        assert 'SYNC=%s' % SYNC_PATH in deploy
        assert '"sudo -n -l ${SYNC}"' in deploy
        assert '"sudo -n ${SYNC} ${STAGE}"' in deploy
        assert "mktemp -d /tmp/xx-wm-deploy.XXXXXXXX" in deploy
        assert 'case $src in\n    /tmp/xx-wm-deploy.*)' in \
            SYNC_SH.read_text(encoding='utf-8')


@pytest.fixture
def sync(tmp_path):
    """deploy-sync.sh with its fixed PATH pointed at a recording rsync."""
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for tool in ('id', 'stat'):
        (bindir / tool).symlink_to(shutil.which(tool))
    record = tmp_path / 'rsync.log'
    rsync = bindir / 'rsync'
    rsync.write_text('#!/bin/sh\n{ pwd; printf "%%s\\n" "$@"; } > "%s"\n' % record)
    rsync.chmod(0o755)
    text = SYNC_SH.read_text(encoding='utf-8')
    text, count = re.subn(r'^PATH=.*$', 'PATH=%s' % bindir, text, flags=re.M)
    assert count == 1
    script = tmp_path / 'sync.sh'
    script.write_text(text)
    made = []

    def stage(mode=0o700):
        path = Path('/tmp/xx-wm-deploy.' + secrets.token_hex(4))
        path.mkdir(mode=mode)
        path.chmod(mode)
        made.append(path)
        return path

    def run(arg, uid=None):
        env = {'SUDO_UID': str(os.getuid() if uid is None else uid)}
        if uid == '':
            env = {}
        proc = subprocess.run([shutil.which('sh'), str(script), str(arg)],
                              capture_output=True, text=True, env=env,
                              timeout=30)
        ran = record.read_text().splitlines() if record.exists() else None
        return proc, ran

    yield stage, run
    for path in made:
        if path.is_symlink():
            path.unlink()
        else:
            path.chmod(0o700)
            shutil.rmtree(path, ignore_errors=True)


class TestSyncHelper:
    def test_copies_a_private_stage_into_the_datadir(self, sync):
        stage, run = sync
        src = stage()
        proc, ran = run(src)
        assert proc.returncode == 0, proc.stderr
        assert ran[0] == str(src)
        args = ran[1:]
        assert args[-2:] == ['./', '/usr/share/xx-wm/']
        assert '--safe-links' in args
        assert '--chmod=ug-s,go-w' in args
        assert '--chown=root:root' in args
        assert '-a' not in args

    @pytest.mark.parametrize('arg', [
        '/etc', '/tmp/other', '/tmp/xx-wm-deploy.', '/tmp/xx-wm-deploy.ab/../../etc',
        '/tmp/xx-wm-deploy.ab cd', '/tmp/xx-wm-deploy.ab_cd',
        '/tmp/xx-wm-deploy.doesnotexist1'])
    def test_refuses_anything_but_a_stage(self, sync, arg):
        _stage, run = sync
        proc, ran = run(arg)
        assert proc.returncode == 2
        assert ran is None

    def test_refuses_without_a_sudo_caller(self, sync):
        stage, run = sync
        src = stage()
        for uid in ('', 0):
            proc, ran = run(src, uid=uid)
            assert proc.returncode == 2
            assert ran is None

    def test_refuses_someone_elses_stage(self, sync):
        stage, run = sync
        proc, ran = run(stage(), uid=os.getuid() + 1)
        assert proc.returncode == 2
        assert ran is None

    def test_refuses_a_shared_stage(self, sync):
        stage, run = sync
        proc, ran = run(stage(mode=0o755))
        assert proc.returncode == 2
        assert ran is None

    def test_refuses_a_symlinked_stage(self, sync, tmp_path):
        stage, run = sync
        target = tmp_path / 'elsewhere'
        target.mkdir(mode=0o700)
        link = stage()
        link.rmdir()
        link.symlink_to(target)
        proc, ran = run(link)
        assert proc.returncode == 2
        assert ran is None
