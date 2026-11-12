"""Scrolling-screenshot stitching: pure image logic, no GTK dependency.

Frames arrive as (width, height, rgb) triples of flat RGB bytes. Overlap is
found by row-hash matching on downscaled grayscale rows: each row is reduced
to _HASH_WIDTH luma samples so per-pixel noise (subpixel text rendering,
video) still matches while real content changes do not.

Sticky bars (a status header or bottom nav that never scrolls) would fake an
overlap everywhere, so rows identical across ALL frames are detected first
and excluded from matching. They are emitted once in the output.

PNG decode/encode goes through GdkPixbuf (PIL is not packaged everywhere);
those two wrappers are the only functions that touch GTK, and they report
failure instead of raising when GdkPixbuf is unavailable.
"""

from __future__ import annotations

_HASH_WIDTH = 48
_MIN_OVERLAP_ROWS = 8


def _luma(rgb: bytes, x: int, width: int) -> int:
    base = x * 3
    return (rgb[base] * 299 + rgb[base + 1] * 587 + rgb[base + 2] * 114) // 1000


def row_hash(rgb_row: bytes, width: int) -> bytes:
    """Downscaled grayscale hash of one RGB row (width * 3 bytes)."""
    if width <= 0:
        return b''
    out = bytearray(_HASH_WIDTH)
    for i in range(_HASH_WIDTH):
        x0 = (i * width) // _HASH_WIDTH
        x1 = max(x0 + 1, ((i + 1) * width) // _HASH_WIDTH)
        total = 0
        for x in range(x0, x1):
            total += _luma(rgb_row, x, width)
        out[i] = total // (x1 - x0)
    return bytes(out)


def frame_row_hashes(width: int, height: int, rgb: bytes) -> list[bytes]:
    """One row hash per frame row, top to bottom."""
    stride = width * 3
    return [
        row_hash(rgb[y * stride:(y + 1) * stride], width)
        for y in range(height)
    ]


def detect_sticky_bars(all_hashes: list[list[bytes]]) -> tuple[int, int]:
    """Count of top/bottom rows identical across every frame.

    Returns (top_rows, bottom_rows): the contiguous run from the top (and
    from the bottom) whose hash never changes between frames. Middle rows
    are never sticky, however stable they look.
    """
    if not all_hashes:
        return (0, 0)
    height = len(all_hashes[0])
    if any(len(hashes) != height for hashes in all_hashes):
        return (0, 0)
    top = 0
    while top < height and all(hashes[top] == all_hashes[0][top]
                               for hashes in all_hashes):
        top += 1
    bottom = 0
    while bottom < height - top and all(
            hashes[height - 1 - bottom] == all_hashes[0][height - 1 - bottom]
            for hashes in all_hashes):
        bottom += 1
    return (top, bottom)


def find_overlap(
    prev_hashes: list[bytes],
    next_hashes: list[bytes],
    min_overlap: int = _MIN_OVERLAP_ROWS,
) -> int:
    """Largest k where the last k rows of prev equal the first k of next.

    Callers strip sticky bars before calling, so every row here is content.
    Zero when nothing overlaps (or the overlap is below min_overlap).
    """
    ceiling = min(len(prev_hashes), len(next_hashes))
    for k in range(ceiling, min_overlap - 1, -1):
        if prev_hashes[len(prev_hashes) - k:] == next_hashes[:k]:
            return k
    return 0


def frames_equal(first: tuple[int, int, bytes],
                 second: tuple[int, int, bytes]) -> bool:
    """True when two decoded frames are pixel-identical (scrolling stop)."""
    return (first[0] == second[0] and first[1] == second[1]
            and first[2] == second[2])


def stitch(frames: list[tuple[int, int, bytes]]) -> tuple[int, int, bytes]:
    """Stitch scrolled frames into one tall (width, height, rgb) frame.

    The first frame contributes fully; each later frame contributes only
    its rows below the overlap with the accumulated content. Identical
    consecutive frames contribute nothing. All frames must share a width.
    """
    if not frames:
        raise ValueError('no frames to stitch')
    width = frames[0][0]
    if any(w != width for w, _h, _rgb in frames):
        raise ValueError('frame widths differ')
    all_hashes = [frame_row_hashes(w, h, rgb) for w, h, rgb in frames]
    sticky_top, sticky_bottom = detect_sticky_bars(all_hashes)
    stride = width * 3

    def content(hashes: list[bytes]) -> list[bytes]:
        end = len(hashes) - sticky_bottom if sticky_bottom else len(hashes)
        return hashes[sticky_top:end]

    def content_rows(frame: tuple[int, int, bytes]) -> list[bytes]:
        _w, h, rgb = frame
        rows = [rgb[y * stride:(y + 1) * stride] for y in range(h)]
        end = h - sticky_bottom if sticky_bottom else h
        return rows[sticky_top:end]

    kept_hashes: list[bytes] = list(content(all_hashes[0]))
    kept_rows: list[bytes] = content_rows(frames[0])
    for hashes, frame in zip(all_hashes[1:], frames[1:], strict=True):
        rows = content_rows(frame)
        overlap = find_overlap(kept_hashes, content(hashes))
        kept_hashes.extend(content(hashes)[overlap:])
        kept_rows.extend(rows[overlap:])

    top_rows = [frames[0][2][y * stride:(y + 1) * stride]
                for y in range(sticky_top)]
    bottom_rows: list[bytes] = []
    if sticky_bottom:
        h = frames[0][1]
        bottom_rows = [frames[0][2][y * stride:(y + 1) * stride]
                       for y in range(h - sticky_bottom, h)]
    out_rows = top_rows + kept_rows + bottom_rows
    return (width, len(out_rows), b''.join(out_rows))


def load_png(path: str) -> tuple[int, int, bytes] | None:
    """Decode a PNG file to (width, height, rgb) via GdkPixbuf.

    None when GdkPixbuf is unavailable or the file cannot be decoded —
    the scrolling capture degrades to keeping the single frames.
    """
    try:
        from gi.repository import GdkPixbuf
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        stride = pixbuf.get_rowstride()
        data = bytes(pixbuf.get_pixels())
        channels = pixbuf.get_n_channels()
        rows = []
        for y in range(height):
            row = data[y * stride:y * stride + width * channels]
            if channels == 4:
                row = b''.join(
                    row[x * 4:x * 4 + 3] for x in range(width))
            rows.append(bytes(row))
        return (width, height, b''.join(rows))
    except Exception:
        return None


def save_png(width: int, height: int, rgb: bytes, path: str) -> bool:
    """Encode (width, height, rgb) to a PNG file via GdkPixbuf."""
    try:
        from gi.repository import GdkPixbuf
        if len(rgb) != width * height * 3:
            return False
        pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            bytes(rgb), GdkPixbuf.Colorspace.RGB, False, 8,
            width, height, width * 3)
        return bool(pixbuf.savev(path, 'png', [], []))
    except Exception:
        return False
