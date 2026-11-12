"""Last-seen recents stills. Capture is output-sized; we keep a tiny RGBA.

Keyed by app_id (one window per app is the phone case). Own surfaces are
never stored. LRU-capped so a long session cannot grow without bound.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass

_OWN_APP_ID = 'io.piercingxx.XXWM'
_MAX_ITEMS = 8
_MAX_EDGE = 240

# wl_shm.format
_ARGB8888 = 0
_XRGB8888 = 1
_XBGR8888 = 0x34324258
_ABGR8888 = 0x34324142


@dataclass(frozen=True)
class Thumbnail:
    width: int
    height: int
    rgba: bytes  # tightly packed RGBA8888


def downsample_shm(
    data: bytes,
    width: int,
    height: int,
    stride: int,
    shm_format: int,
    y_invert: bool = False,
    max_edge: int = _MAX_EDGE,
) -> Thumbnail:
    """Nearest-neighbour downscale + convert to RGBA. No extra deps."""
    if width <= 0 or height <= 0 or stride < 4 or len(data) < stride:
        raise ValueError('invalid shm buffer')
    view = memoryview(data)

    def _row(sy: int) -> memoryview:
        start = sy * stride
        return view[start:start + width * 4]
    return _downsample(width, height, _row,
                       xbgr=shm_format in (_XBGR8888, _ABGR8888),
                       opaque=shm_format in (_XRGB8888, _XBGR8888),
                       y_invert=y_invert, max_edge=max_edge)


def downsample_rgb(rgb: bytes, width: int, height: int,
                   max_edge: int = _MAX_EDGE) -> Thumbnail:
    """Same as downsample_shm for packed 24-bit RGB (a grim PPM). Only
    the sampled rows are widened to 32 bits: packing every pixel of the
    grab first cost ~0.2 s per thumbnail on a desktop CPU."""
    if width <= 0 or height <= 0 or len(rgb) < width * height * 3:
        raise ValueError('invalid rgb buffer')
    view = memoryview(rgb)

    def _row(sy: int) -> bytes:
        src = view[sy * width * 3:(sy + 1) * width * 3]
        row = bytearray(width * 4)
        row[0::4] = src[0::3]
        row[1::4] = src[1::3]
        row[2::4] = src[2::3]
        return bytes(row)
    return _downsample(width, height, _row, xbgr=True, opaque=True,
                       y_invert=False, max_edge=max_edge)


def _downsample(width: int, height: int, row_at, xbgr: bool, opaque: bool,
                y_invert: bool, max_edge: int) -> Thumbnail:
    # Pixels move as whole 32-bit words; the channel swap and alpha fill
    # are slice assignments over the finished image (no per-byte work).
    scale = max(width, height) / max_edge
    if scale < 1:
        scale = 1.0
    tw = max(1, int(width / scale))
    th = max(1, int(height / scale))
    cols = [int(dx * scale) for dx in range(tw)]
    words = array('I')
    for dy in range(th):
        sy = int(dy * scale)
        if y_invert:
            sy = height - 1 - sy
        row = memoryview(row_at(sy)).cast('I')
        words.extend([row[c] for c in cols])
    src = words.tobytes()
    if xbgr:
        out = bytearray(src)
    else:
        out = bytearray(len(src))
        out[0::4] = src[2::4]
        out[1::4] = src[1::4]
        out[2::4] = src[0::4]
        out[3::4] = src[3::4]
    if opaque:
        out[3::4] = b'\xff' * (tw * th)
    return Thumbnail(tw, th, bytes(out))


class ThumbnailStore:
    def __init__(self, max_items: int = _MAX_ITEMS) -> None:
        self._max = max_items
        self._items: dict[str, Thumbnail] = {}
        self._order: list[str] = []
        self._callbacks: list[object] = []

    def get(self, app_id: str) -> Thumbnail | None:
        if not app_id or app_id == _OWN_APP_ID:
            return None
        return self._items.get(app_id)

    def put(self, app_id: str, thumb: Thumbnail) -> None:
        if not app_id or app_id == _OWN_APP_ID:
            return
        if app_id in self._items:
            self._order.remove(app_id)
        self._items[app_id] = thumb
        self._order.append(app_id)
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._items.pop(old, None)
        for cb in list(self._callbacks):
            try:
                cb()  # type: ignore[operator]
            except Exception:
                pass

    def on_change(self, callback: object) -> None:
        self._callbacks.append(callback)
