"""Per-device GSK renderer: on a libhybris (Halium) phone the session asks
GTK for GL instead of letting every GTK process load the Vulkan driver
and fail vkCreateWaylandSurfaceKHR first; mainline devices keep GTK's
choice; a GSK_RENDERER already set wins; D-Bus-activated apps get it."""
import re
import subprocess
from pathlib import Path

import pytest

DATA = Path(__file__).parent.parent / 'launcher' / 'data'
SESSION = DATA / 'xx-wm-session.in'
WRAPPER = DATA / 'xx-wm.in'


def _snippet() -> str:
    text = SESSION.read_text(encoding='utf-8')
    match = re.search(r'^_xx_libhybris\(\) \{.*?\nfi\n', text, re.M | re.S)
    assert match, 'renderer choice missing from xx-wm-session.in'
    return match.group(0)


def _renderer(root: Path, preset: str | None = None) -> str:
    env = {'PATH': '/usr/bin:/bin', 'XX_WM_LIB_ROOT': str(root)}
    if preset is not None:
        env['GSK_RENDERER'] = preset
    proc = subprocess.run(
        ['sh', '-c', _snippet() + 'printf %s "${GSK_RENDERER:-unset}"'],
        env=env, capture_output=True, text=True, check=True)
    return proc.stdout


@pytest.mark.parametrize('libdir', ['usr/lib/aarch64-linux-gnu', 'usr/lib'])
def test_halium_phone_gets_gl(tmp_path, libdir):
    lib = tmp_path / libdir / 'libhybris-common.so.1'
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b'')
    assert _renderer(tmp_path) == 'gl'


def test_mainline_keeps_gtks_choice(tmp_path):
    (tmp_path / 'usr' / 'lib' / 'aarch64-linux-gnu').mkdir(parents=True)
    assert _renderer(tmp_path) == 'unset'


def test_a_set_renderer_wins(tmp_path):
    lib = tmp_path / 'usr' / 'lib' / 'libhybris-common.so.1'
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b'')
    assert _renderer(tmp_path, preset='cairo') == 'cairo'


def test_activated_apps_inherit_it():
    text = WRAPPER.read_text(encoding='utf-8')
    begin = re.search(r'^_xx_session_begin\(\) \{(.*?)^\}', text,
                      re.M | re.S).group(1)
    assert '${GSK_RENDERER:+GSK_RENDERER}' in begin
