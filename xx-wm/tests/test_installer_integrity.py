"""install.sh: the Nerd Font zip is checked against a pinned sha256.

download_nerd_font_fallback is extracted from install.sh and sourced in
`sh -c` with stubbed curl/fc-list/unzip on PATH, so nothing touches the
network or the real font directory.
"""
import hashlib
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'


def _function_body(name):
    script = INSTALL_SH.read_text()
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    assert m, f'shell function {name} not found in install.sh'
    return m.group(1)


def _write_stub(bindir, name, body):
    path = bindir / name
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o755)


def _run_fallback(tmp_path, zip_path, extra_env=None):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    log = tmp_path / 'calls.log'
    _write_stub(bindir, 'fc-list', 'exit 0\n')
    _write_stub(
        bindir, 'curl',
        'echo "curl $*" >> "%s"\n'
        'while [ $# -gt 0 ]; do case "$1" in\n'
        '  -o) out="$2"; shift 2 ;;\n'
        '  -*) shift ;;\n'
        '  *) url="$1"; shift ;;\n'
        'esac; done\n'
        'cp "${url#file://}" "$out"\n' % log)
    real_unzip = shutil.which('unzip')
    _write_stub(
        bindir, 'unzip',
        'echo "unzip $*" >> "%s"\n' % log
        + ('exec "%s" "$@"\n' % real_unzip if real_unzip else 'exit 0\n'))
    funcs = tmp_path / 'funcs.sh'
    funcs.write_text('download_nerd_font_fallback() {%s}\n'
                     % _function_body('download_nerd_font_fallback'))
    env = dict(os.environ)
    env.update({'PATH': '%s:%s' % (bindir, os.environ['PATH']),
                'HOME': str(tmp_path / 'home'),
                'XX_WM_FONT_DIR': str(tmp_path / 'fonts'),
                'XX_WM_NERD_FONT_URL': 'file://%s' % zip_path})
    env.update(extra_env or {})
    proc = subprocess.run(
        ['sh', '-c', '. "%s"; download_nerd_font_fallback' % funcs],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
        check=False)
    return proc, (log.read_text() if log.exists() else '')


def _font_zip(tmp_path):
    path = tmp_path / 'JetBrainsMono.zip'
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('JetBrainsMonoNerdFont-Regular.ttf', b'fixture font')
    return path


def test_font_zip_sha256_is_pinned_next_to_its_version():
    body = _function_body('download_nerd_font_fallback')
    assert re.search(r'^\s*_zip_ver=\d+\.\d+\.\d+$', body, re.M)
    assert re.search(r'_zip_sha=\$\{XX_WM_NERD_FONT_SHA256:-[0-9a-f]{64}\}',
                     body)
    assert 'v$_zip_ver/JetBrainsMono.zip' in body
    assert body.find('sha256sum') < body.find('unzip -o')


def test_font_zip_checksum_mismatch_installs_nothing(tmp_path):
    proc, calls = _run_fallback(tmp_path, _font_zip(tmp_path))
    assert proc.returncode == 0
    assert 'curl' in calls
    assert 'unzip -o' not in calls
    assert 'checksum mismatch' in proc.stderr
    fonts = tmp_path / 'fonts'
    assert not fonts.exists() or not any(fonts.iterdir())


@pytest.mark.skipif(not shutil.which('unzip'), reason='needs unzip')
def test_font_zip_with_matching_checksum_is_unpacked(tmp_path):
    zip_path = _font_zip(tmp_path)
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    proc, calls = _run_fallback(tmp_path, zip_path,
                                {'XX_WM_NERD_FONT_SHA256': sha})
    assert proc.returncode == 0, proc.stderr
    assert 'checksum mismatch' not in proc.stderr
    assert 'unzip -o' in calls
    assert (tmp_path / 'fonts' / 'JetBrainsMonoNerdFont-Regular.ttf').is_file()
