"""deploy.sh must update the installed shell, not a stale home copy.

Meson installs to /usr/share/xx-wm/ and the wrapper execs that path, so
rsyncing to ~/xx-wm/src/ is a no-op on an installed device. Restart must
not start a second shell under GDM: only restart the user unit when it is
active, else SIGUSR1 the unique python child (the wrapper respawns it).
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
DEPLOY_SH = REPO / 'scripts' / 'deploy.sh'


@pytest.fixture(scope='module')
def deploy_script():
    assert DEPLOY_SH.is_file(), f'missing {DEPLOY_SH}'
    return DEPLOY_SH.read_text()


def test_default_user_is_dr3k(deploy_script):
    assert 'XX_WM_USER:-dr3k' in deploy_script
    assert 'XX_WM_USER:-user}' not in deploy_script


def test_targets_installed_datadir(deploy_script):
    assert '/usr/share/xx-wm/' in deploy_script
    assert 'mktemp -d' in deploy_script
    assert 'STAGE=/tmp/xx-wm-deploy' not in deploy_script
    assert '~/xx-wm/src' not in deploy_script
    assert ':xx-wm/src/' not in deploy_script


def test_rsyncs_to_usr_share_via_staging(deploy_script):
    assert '/usr/share/xx-wm' in deploy_script
    assert 'mktemp -d' in deploy_script
    assert 'STAGE=/tmp/xx-wm-deploy' not in deploy_script
    assert '~/xx-wm/src' not in deploy_script
    assert ':xx-wm/src/' not in deploy_script
    assert '--chown=root:root' in deploy_script


def test_no_delete_on_datadir(deploy_script):
    assert '--chown=root:root' in deploy_script
    for line in deploy_script.splitlines():
        if '/usr/share/xx-wm' in line and 'rsync' in line:
            assert '--delete' not in line


def test_datadir_rsync_does_not_delete(deploy_script):
    # Staging may --delete; the datadir copy must not.
    assert 'sudo rsync -a --chown=root:root' in deploy_script
    assert 'sudo rsync -a --delete' not in deploy_script
    assert 'sudo rsync -a --chown=root:root /tmp/xx-wm-deploy/ /usr/share/xx-wm/' in deploy_script \
        or 'sudo rsync -a --chown=root:root ${STAGE}/ ${DEST}/' in deploy_script


def test_tight_pgrep_pattern(deploy_script):
    assert '[p]ython3 /usr/share/xx-wm/main.py' in deploy_script


def test_restart_sigusr1s_unique_python_child(deploy_script):
    assert '/usr/share/xx-wm/main.py' in deploy_script
    assert 'comm=' in deploy_script
    assert '[ "$c" = python3 ]' in deploy_script
    assert 'kill -USR1' in deploy_script
    assert 'kill -TERM' not in deploy_script
    assert 'kill -15' not in deploy_script
    assert 'kill -HUP' not in deploy_script
    assert 'DBUS_SESSION_BUS_ADDRESS' in deploy_script
    assert 'is-active' in deploy_script


def test_sigusr1_not_term_hup(deploy_script):
    assert 'kill -USR1' in deploy_script
    assert 'kill -TERM' not in deploy_script
    assert 'kill -15' not in deploy_script
    assert 'kill -HUP' not in deploy_script


def test_dbus_address_exported(deploy_script):
    assert 'DBUS_SESSION_BUS_ADDRESS' in deploy_script


def test_dry_run_exists(deploy_script):
    assert '--dry-run' in deploy_script


def test_dry_run_prints_real_paths():
    text = DEPLOY_SH.read_text(encoding='utf-8')
    assert '--dry-run' in text
    assert '[dry-run]' in text
    env = os.environ.copy()
    env['PIERCING_DEVICE'] = '192.168.1.129'
    result = subprocess.run(
        ['sh', str(DEPLOY_SH), '--dry-run'],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert '/usr/share/xx-wm/' in out
    assert 'mktemp -d' in out
    assert '/tmp/xx-wm-deploy/' not in out
    assert 'xx-wm/src/' not in out
    assert 'SIGUSR1' in out
    assert '/usr/share/xx-wm/main.py' in out


def test_ships_every_protocol_xml(deploy_script):
    """Each vendored protocol XML the shell generates bindings from lives
    next to the modules on the device: a missing one (phosh-private.xml)
    leaves that feature without bindings after a deploy."""
    import re
    source = (REPO / 'launcher' / 'src' / 'wayland_bindings.py').read_text(
        encoding='utf-8')
    names = set(re.findall(r"^[A-Z_]+_XML_NAME = '([^']+\.xml)'", source,
                           re.M))
    assert 'phosh-private.xml' in names and len(names) >= 6
    stanza = deploy_script.replace('\\\n', ' ')
    shipped = [line for line in stanza.splitlines()
               if line.lstrip().startswith('rsync -az')]
    assert shipped, 'no protocol XML rsync in deploy.sh'
    dry = [line for line in stanza.splitlines()
           if '[dry-run] rsync -az' in line]
    for name in sorted(names):
        assert (REPO / 'launcher' / 'data' / name).is_file(), name
        assert any('launcher/data/%s' % name in cmd for cmd in shipped), name
        assert any('launcher/data/%s' % name in cmd for cmd in dry), name


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(DEPLOY_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
