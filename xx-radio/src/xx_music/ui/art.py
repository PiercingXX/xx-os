"""Artwork for tiles and rows: sized, cached on disk, fetched off-thread.

``ArtLoader.request(url, px, deliver)`` asks the engine for the url at
the size needed, serves the disk cache when present, and otherwise
fetches on its own small pool. Identical requests in flight share one
fetch. ``deliver(texture)`` runs on the main loop; nothing arrives for
an empty url (the screens keep their initials tile).
"""

from __future__ import annotations

import hashlib
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

MAX_BYTES = 4 * 2**20
TIMEOUT_S = 15


def _http_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "xx-music"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read(MAX_BYTES + 1)[:MAX_BYTES]


def _post_main(callback: Callable[[], None]) -> None:
    from gi.repository import GLib

    GLib.idle_add(lambda: (callback(), False)[1])


class ArtLoader:
    def __init__(self, cache_dir: Path | str | None,
                 sizer: Callable[[str, int], str] | None = None,
                 fetch: Callable[[str], bytes] | None = None,
                 post: Callable[[Callable[[], None]], None] | None = None,
                 workers: int = 4) -> None:
        self._dir = Path(cache_dir) if cache_dir else None
        self._sizer = sizer or (lambda url, _px: url)
        self._fetch = fetch or _http_fetch
        self._post = post or _post_main
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="xx-art")
        self._lock = threading.Lock()
        self._waiting: dict[str, list[Callable]] = {}
        self._memory: dict[str, object] = {}
        self.fetches = 0

    def _path(self, key: str) -> Path | None:
        if self._dir is None:
            return None
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self._dir / digest

    def request(self, url: str, px: int,
                deliver: Callable[[object], None]) -> None:
        if not url:
            return
        if url.startswith("file://"):
            sized = url
        else:
            sized = self._sizer(url, px)
        key = sized
        with self._lock:
            texture = self._memory.get(key)
            if texture is None:
                waiting = self._waiting.get(key)
                if waiting is not None:
                    waiting.append(deliver)
                    return
                self._waiting[key] = [deliver]
        if texture is not None:
            deliver(texture)
            return
        self._pool.submit(self._work, key, sized)

    def _work(self, key: str, url: str) -> None:
        data = None
        path = self._path(key)
        try:
            if url.startswith("file://"):
                data = Path(url[7:]).read_bytes()
            elif path is not None and path.is_file():
                data = path.read_bytes()
            else:
                with self._lock:
                    self.fetches += 1
                data = self._fetch(url)
                if path is not None and data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(data)
                    tmp.replace(path)
        except Exception:  # noqa: BLE001 - no art is not an error
            data = None
        texture = self._texture(data) if data else None
        self._post(lambda: self._finish(key, texture))

    def _finish(self, key: str, texture) -> None:
        with self._lock:
            waiting = self._waiting.pop(key, [])
            if texture is not None:
                self._memory[key] = texture
                if len(self._memory) > 256:
                    self._memory.pop(next(iter(self._memory)))
        if texture is None:
            return
        for deliver in waiting:
            deliver(texture)

    @staticmethod
    def _texture(data: bytes):
        try:
            from gi.repository import Gdk, GLib

            return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except Exception:  # noqa: BLE001 - undecodable art
            return None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
