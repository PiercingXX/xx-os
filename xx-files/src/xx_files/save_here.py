""""Save here" naming: a collision-free destination inside cwd.

Port of the Android ``SaveHere`` JVM seam (ACTION_SEND Save-here):
pick a display name (or derive one from the MIME type), then resolve
it against the files already in cwd — never a silent overwrite.
"""
from __future__ import annotations

import os

from .clip import unique_name

DEFAULT_NAME = "shared.bin"

_EXTENSION_FOR_MIME = {
    "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
    "image/webp": "webp", "application/pdf": "pdf",
    "text/plain": "txt", "video/mp4": "mp4",
}


def extension_for_mime(mime_type: str | None) -> str | None:
    if mime_type is None:
        return None
    return _EXTENSION_FOR_MIME.get(mime_type.lower())


def file_name(display_name: str | None,
              mime_type: str | None = None) -> str:
    raw = None
    if display_name:
        tail = display_name.split("/")[-1].strip()
        if tail and tail != "." and "/" not in tail:
            raw = tail
    if raw is not None:
        return raw
    ext = extension_for_mime(mime_type)
    return f"shared.{ext}" if ext else DEFAULT_NAME


def destination(cwd: str, existing: list[str],
                display_name: str | None,
                mime_type: str | None = None) -> str:
    return os.path.join(cwd, unique_name(existing, file_name(display_name,
                                                             mime_type)))
