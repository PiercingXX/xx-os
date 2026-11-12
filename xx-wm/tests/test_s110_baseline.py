"""Ratchet on ``except Exception: pass`` (ruff S110) in launcher/src.

A broad handler whose body is only ``pass`` hides the failure; use
``shell_log.log_exc(where)`` instead. Today's remaining ones are counted
per file in s110_baseline.json. A count may only go down: a new one fails,
and so does a removed one until the baseline is lowered with
``python tests/test_s110_baseline.py --update``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'launcher' / 'src'
BASELINE = Path(__file__).resolve().parent / 's110_baseline.json'


def _ruff() -> list[str]:
    probe = subprocess.run([sys.executable, '-m', 'ruff', '--version'],
                           capture_output=True, check=False)
    if probe.returncode == 0:
        return [sys.executable, '-m', 'ruff']
    found = shutil.which('ruff')
    if found is None:
        raise RuntimeError('ruff not found (pip install ruff)')
    return [found]


def current_counts() -> dict[str, int]:
    proc = subprocess.run(
        [*_ruff(), 'check', '--isolated', '--select', 'S110',
         '--output-format', 'json', '--exit-zero', str(SRC)],
        capture_output=True, text=True, check=True)
    counts = Counter(Path(item['filename']).name
                     for item in json.loads(proc.stdout))
    return dict(sorted(counts.items()))


def test_try_except_pass_only_shrinks():
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    counts = current_counts()
    grew = {name: (baseline.get(name, 0), n) for name, n in counts.items()
            if n > baseline.get(name, 0)}
    assert not grew, (
        'new `except Exception: pass` (ruff S110) in launcher/src; log it '
        'with shell_log.log_exc(where) or narrow the exception. '
        'file: (baseline, now) = %r' % grew)
    shrank = {name: (n, counts.get(name, 0)) for name, n in baseline.items()
              if counts.get(name, 0) < n}
    assert not shrank, (
        'S110 count went down, lower the baseline: '
        'python tests/test_s110_baseline.py --update. '
        'file: (baseline, now) = %r' % shrank)


if __name__ == '__main__':
    if sys.argv[1:] != ['--update']:
        sys.exit('usage: test_s110_baseline.py --update')
    BASELINE.write_text(json.dumps(current_counts(), indent=1) + '\n',
                        encoding='utf-8')
    print('wrote', BASELINE)
