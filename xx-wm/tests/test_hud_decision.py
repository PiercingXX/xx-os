"""Tests for the wob-vs-in-shell HUD decision (plan T1).

The shell owns every surface it draws (see design.md "System surfaces"), so the
volume/brightness HUD is built in-shell as a GTK4 layer-shell window instead of
delegating to the external `wob` daemon. This test pins that decision across the
docs and the installer: the docs must describe an in-shell HUD, and wob must no
longer be installed or referenced anywhere the task names.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _read(rel):
    path = ROOT / rel
    assert path.is_file(), f'missing {rel}'
    return path.read_text()


def test_in_shell_hud_named_in_docs():
    """The docs must describe the HUD as in-shell, not wob-backed."""
    assert 'in-shell HUD' in _read('README.md')
    assert 'volume HUD (in-shell' in _read('design.md')


def test_wob_removed_from_installer():
    """wob must no longer be a dependency the installer pulls in."""
    script = _read('scripts/install.sh')
    assert 'wob' not in script


def test_wob_removed_from_docs():
    """No wob reference may survive in the docs the task names."""
    for rel in ('README.md', 'design.md', 'launcher/README.md'):
        assert 'wob' not in _read(rel), f'stale wob reference in {rel}'


def test_installer_still_parses():
    """The wob removal must not break the POSIX sh script."""
    import subprocess
    result = subprocess.run(
        ['sh', '-n', str(ROOT / 'scripts' / 'install.sh')],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr