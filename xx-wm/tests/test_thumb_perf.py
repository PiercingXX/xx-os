"""Recents thumbnails without per-pixel Python loops (same pixels as the
reference nearest-neighbour), and a hidden switcher that does not rebuild
its cards on every title change or new thumbnail."""
import os
import random
import time
import types
from pathlib import Path

import pytest

import thumbnail_store
from thumbnail_store import downsample_shm

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


def _reference(data, width, height, stride, shm_format, y_invert, max_edge):
    scale = max(1.0, max(width, height) / max_edge)
    tw, th = max(1, int(width / scale)), max(1, int(height / scale))
    xbgr = shm_format in (thumbnail_store._XBGR8888,
                          thumbnail_store._ABGR8888)
    opaque = shm_format in (thumbnail_store._XRGB8888,
                            thumbnail_store._XBGR8888)
    out = bytearray()
    for dy in range(th):
        sy = int(dy * scale)
        if y_invert:
            sy = height - 1 - sy
        for dx in range(tw):
            off = sy * stride + int(dx * scale) * 4
            b, g, r, a = data[off:off + 4]
            if xbgr:
                r, b = b, r
            out += bytes((r, g, b, 255 if opaque else a))
    return tw, th, bytes(out)


@pytest.mark.parametrize('shm_format', [
    thumbnail_store._ARGB8888, thumbnail_store._XRGB8888,
    thumbnail_store._XBGR8888, thumbnail_store._ABGR8888])
@pytest.mark.parametrize('y_invert', [False, True])
def test_same_pixels_as_the_reference(shm_format, y_invert):
    rng = random.Random(shm_format + y_invert)
    width, height, pad = 97, 211, 12
    stride = width * 4 + pad
    data = bytes(rng.randrange(256) for _ in range(stride * height))
    thumb = downsample_shm(data, width, height, stride, shm_format,
                           y_invert=y_invert, max_edge=40)
    assert (thumb.width, thumb.height, thumb.rgba) == _reference(
        data, width, height, stride, shm_format, y_invert, 40)


def test_grim_ppm_matches_and_is_fast(tmp_path):
    from output_capture import _ppm_to_thumb
    width, height = 720, 1600
    rgb = os.urandom(width * height * 3)
    path = tmp_path / 'grab.ppm'
    path.write_bytes(b'P6\n%d %d\n255\n' % (width, height) + rgb)
    started = time.perf_counter()
    thumb = _ppm_to_thumb(str(path))
    elapsed = time.perf_counter() - started
    xrgb = bytearray(width * height * 4)
    xrgb[0::4] = rgb[2::3]
    xrgb[1::4] = rgb[1::3]
    xrgb[2::4] = rgb[0::3]
    want = _reference(bytes(xrgb), width, height, width * 4,
                      thumbnail_store._XRGB8888, False, 240)
    assert (thumb.width, thumb.height, thumb.rgba) == want
    # The old per-pixel pack took ~0.2 s here (seconds on the phone).
    assert elapsed < 0.1


class TestHiddenSwitcher:
    def test_changes_while_hidden_rebuild_nothing(self):
        from app_switcher import AppSwitcher
        rebuilt = []
        fake = types.SimpleNamespace(
            get_visible=lambda: False,
            refresh=lambda apps=None: rebuilt.append(1))
        AppSwitcher._refresh_if_visible(fake)
        assert rebuilt == []
        fake.get_visible = lambda: True
        AppSwitcher._refresh_if_visible(fake)
        assert rebuilt == [1]

    def test_manager_and_thumbnails_wire_to_the_visible_check(self):
        src = (SRC / 'app_switcher.py').read_text(encoding='utf-8')
        assert '_wire_change(self._manager, self.refresh)' not in src
        assert '_wire_change(manager, self.refresh)' not in src
        assert 'thumbnails.on_change(self.refresh)' not in src
        assert src.count('self._refresh_if_visible)') == 3
