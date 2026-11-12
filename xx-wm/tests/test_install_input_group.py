"""Tests for the 20.3 input-group setup in scripts/install.sh.

lisgd (touchscreen) and DisplayManager (power/volume keys) read evdev directly
and silently do nothing unless the shell user is a member of the `input` group.
The installer must add the user via an idempotent ``usermod -aG input`` and
print a "re-login required" note. This is a shell script, so the test reads the
script text and asserts the setup logic is present, correct, and actually wired
into the install path (not just defined).
"""
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).parent.parent / 'scripts' / 'install.sh'


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    """Return the text of a shell function definition, or '' if absent."""
    import re
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return m.group(1) if m else ''


def test_adds_user_to_input_group(install_script):
    """The group-add must be the idempotent append form, run privileged."""
    body = _function_body(install_script, 'add_input_group')
    assert 'usermod -aG input' in body
    # Must run through the cached privilege helper, not bare.
    assert '$SUDO usermod -aG input' in body


def test_idempotent_membership_check(install_script):
    """Skip the usermod call when the user is already a member."""
    body = _function_body(install_script, 'add_input_group')
    assert 'id -nG' in body
    assert 'grep -qw input' in body


def test_relogin_note(install_script):
    """Print a note that the group takes effect on the next login."""
    body = _function_body(install_script, 'add_input_group')
    assert 're-login' in body


def test_wired_into_install_path(install_script):
    """The function must be invoked by the install path, not just defined.

    build_install() is the shared path for both Install and Update; if the
    call line is missing, the group setup silently never runs on device.
    """
    assert 'add_input_group' in _function_body(install_script, 'build_install')


def test_script_syntax_parses(install_script):
    """The script must still parse as valid POSIX sh (sh -n)."""
    import subprocess
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True,
        check=False)
    assert result.returncode == 0, result.stderr