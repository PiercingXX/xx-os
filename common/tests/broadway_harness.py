"""Spawn a private gtk4-broadwayd and run a driver script against it.

Shared by the Broadway tests; skips cleanly when the daemon binary is
missing.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

COMMON = str(Path(__file__).parent.parent)
sys.path.insert(0, COMMON)

from xx_testing import broadway_display  # noqa: E402


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / 'home'
    config = home / '.config' / 'xx-wm' / 'config.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'theme': 'amoled'}), encoding='utf-8')
    return home


def run_driver(name: str, home: Path, timeout: int = 60):
    """Run tests/<name> under a private broadway display."""
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('gtk4-broadwayd is missing')
    # The driver gets its own HOME, and without XDG_RUNTIME_DIR (no login
    # session, as in CI) GTK would look for the socket under $HOME/.cache:
    # daemon and driver share a private runtime dir instead.
    run_dir = home.parent / 'broadway-run'
    run_dir.mkdir(mode=0o700, exist_ok=True)
    try:
        proc, display = broadway_display.start(run_dir=str(run_dir))
    except RuntimeError as exc:
        pytest.fail(f'gtk4-broadwayd did not come up: {exc}')
    try:
        env = dict(os.environ)
        env['XDG_RUNTIME_DIR'] = str(run_dir)
        env['GDK_BACKEND'] = 'broadway'
        env['BROADWAY_DISPLAY'] = display
        env['GSK_RENDERER'] = 'cairo'
        env['HOME'] = str(home)
        env['XDG_CONFIG_HOME'] = str(home / '.config')
        env['PYTHONPATH'] = COMMON + os.pathsep + env.get('PYTHONPATH', '')
        return subprocess.run(
            [sys.executable, str(Path(__file__).parent / name)],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
    finally:
        broadway_display.stop(proc)
