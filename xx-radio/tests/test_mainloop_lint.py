"""The main-loop lint runs green over the player sources."""

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).parent.parent / "tools" / "check_mainloop_blocks.py"


def test_mainloop_lint_passes():
    proc = subprocess.run([sys.executable, str(TOOL)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout
