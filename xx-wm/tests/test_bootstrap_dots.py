"""Owner update-system.sh installer in scripts/bootstrap-dots.sh.

The script fetches piercing-dots and installs update-system.sh where the
owner's sync_scripts puts it. DEST safety still holds: the tablet may
already have ~/piercing-dots, which is reused as-is — rm -rf is allowed
only on ~/.cache/piercing-dots. Live runs below use a stub git and a
fake HOME, so no network and no real checkout are touched.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / 'scripts' / 'bootstrap-dots.sh'

SCRIPT_REL = ('resources/.scripts/PiercingXX-Settings-Menu'
              '/update-system.sh')
LIB_REL = 'resources/.scripts/lib/common.sh'
TARGET_REL = '.scripts/PiercingXX-Settings-Menu/update-system.sh'
LIB_TARGET_REL = '.scripts/lib/common.sh'


def _write_stub_git(bindir: Path, log: Path) -> None:
    """A git that only fakes `clone`: it materializes a piercing-dots
    tree with the two files the installer copies."""
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / 'git').write_text(
        '#!/bin/sh\n'
        'echo "git $*" >> "%s"\n'
        'dest=""\n'
        'for a in "$@"; do dest="$a"; done\n'
        'mkdir -p "$dest/%s" "$dest/%s"\n'
        'printf "#!/bin/sh\\n# owner update-system fixture\\n" '
        '> "$dest/%s"\n'
        'printf "# fixture common\\n" > "$dest/%s"\n'
        % (log,
           os.path.dirname(SCRIPT_REL), os.path.dirname(LIB_REL),
           SCRIPT_REL, LIB_REL))
    (bindir / 'git').chmod(0o755)


def _write_failing_git(bindir: Path, log: Path) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / 'git').write_text(
        '#!/bin/sh\necho "git $*" >> "%s"\nexit 1\n' % log)
    (bindir / 'git').chmod(0o755)


def _run_script(home: Path, bindir: Path | None = None,
                extra_env: dict | None = None):
    env = dict(os.environ)
    env['HOME'] = str(home)
    if bindir is not None:
        env['PATH'] = '%s:%s' % (bindir, env.get('PATH', '/usr/bin:/bin'))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ['sh', str(SCRIPT)], capture_output=True, text=True, env=env,
        timeout=60)


@pytest.fixture(scope='module')
def script_text():
    assert SCRIPT.is_file(), f'missing {SCRIPT}'
    return SCRIPT.read_text()


def test_existing_clone_is_reused(script_text):
    assert '${HOME}/piercing-dots' in script_text or '$HOME/piercing-dots' in script_text
    assert 'DEST="${HOME}/piercing-dots"' in script_text or \
        "DEST=\"$HOME/piercing-dots\"" in script_text or \
        'DEST="${HOME}/piercing-dots"' in script_text


def test_existing_home_clone_is_the_dest(script_text):
    assert '${HOME}/piercing-dots' in script_text
    assert '-d "${HOME}/piercing-dots"' in script_text


def test_no_rm_rf_on_existing_clone(script_text):
    for line in script_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('rm -rf'):
            assert 'piercing-dots' not in line or '.cache' in line, \
                f'rm -rf must only target the cache clone: {line}'
    assert 'rm -rf' in script_text


def test_rm_rf_is_only_for_cache_clone(script_text):
    assert 'rm -rf "${HOME}/piercing-dots"' not in script_text
    assert '${HOME}/.cache/piercing-dots' in script_text
    assert re.search(r'rm -rf "\$DEST"', script_text)
    start = script_text.find('if [ -d "${HOME}/piercing-dots" ]')
    assert start != -1
    block = script_text[start:]
    then_arm, rest = block.split('else', 1)
    cache_arm, after = rest.split('\nfi', 1)
    assert 'rm -rf' not in then_arm
    dest_at = cache_arm.find('DEST="${HOME}/.cache/piercing-dots"')
    rm_at = cache_arm.find('rm -rf')
    assert dest_at != -1
    assert rm_at != -1
    assert dest_at < rm_at
    assert 'rm -rf "$DEST"' in cache_arm
    # Code after this if/fi must not grow an rm of the home clone.
    assert 'rm -rf "${HOME}/piercing-dots"' not in after


def test_cache_clone_still_fresh(script_text):
    assert '.cache/piercing-dots' in script_text


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_no_phone_profile_claim(script_text):
    assert '--profile phone' not in script_text
    assert 'phone profile' not in script_text


def test_installs_owner_script_from_fresh_clone(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    bindir = tmp_path / 'bin'
    log = tmp_path / 'git.log'
    _write_stub_git(bindir, log)
    result = _run_script(home, bindir)
    assert result.returncode == 0, result.stderr
    target = home / TARGET_REL
    assert target.is_file()
    assert target.read_text().startswith('#!/bin/sh')
    assert bool(os.stat(target).st_mode & stat.S_IXUSR)
    lib = home / LIB_TARGET_REL
    assert lib.is_file()
    assert 'clone' in log.read_text()
    assert 'phone profile' not in result.stdout + result.stderr


def test_second_run_succeeds_again(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    bindir = tmp_path / 'bin'
    _write_stub_git(bindir, tmp_path / 'git.log')
    assert _run_script(home, bindir).returncode == 0
    again = _run_script(home, bindir)
    assert again.returncode == 0, again.stderr
    assert (home / TARGET_REL).is_file()


def test_opt_out_leaves_home_alone(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    bindir = tmp_path / 'bin'
    log = tmp_path / 'git.log'
    _write_stub_git(bindir, log)
    result = _run_script(home, bindir,
                         {'XX_WM_NO_UPDATE_SCRIPT': '1'})
    assert result.returncode == 0
    assert not (home / '.scripts').exists()
    assert not log.exists()


def test_existing_home_clone_is_reused_without_clone(tmp_path):
    home = tmp_path / 'home'
    clone = home / 'piercing-dots'
    (clone / os.path.dirname(SCRIPT_REL)).mkdir(parents=True)
    (clone / os.path.dirname(LIB_REL)).mkdir(parents=True)
    (clone / SCRIPT_REL).write_text('#!/bin/sh\n# user clone\n')
    (clone / LIB_REL).write_text('# user lib\n')
    bindir = tmp_path / 'bin'
    log = tmp_path / 'git.log'
    _write_failing_git(bindir, log)
    result = _run_script(home, bindir)
    assert result.returncode == 0, result.stderr
    assert not log.exists()
    assert (home / TARGET_REL).read_text() == '#!/bin/sh\n# user clone\n'


def test_symlinked_target_is_never_overwritten(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    bindir = tmp_path / 'bin'
    _write_stub_git(bindir, tmp_path / 'git.log')
    dev = tmp_path / 'dev-checkout.sh'
    dev.write_text('#!/bin/sh\n# dev\n')
    link = home / TARGET_REL
    link.parent.mkdir(parents=True)
    link.symlink_to(dev)
    result = _run_script(home, bindir)
    assert result.returncode == 0, result.stderr
    assert link.is_symlink()
    assert link.read_text() == '#!/bin/sh\n# dev\n'


def test_missing_git_fails_loudly(tmp_path):
    import shutil
    home = tmp_path / 'home'
    home.mkdir()
    empty = tmp_path / 'empty-path'
    empty.mkdir()
    env = {'HOME': str(home), 'PATH': str(empty)}
    result = subprocess.run(
        [shutil.which('sh'), str(SCRIPT)], capture_output=True, text=True,
        env=env, timeout=60)
    assert result.returncode != 0
    assert 'git is required' in result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
