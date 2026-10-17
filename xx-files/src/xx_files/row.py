"""Row text: glyph id, name, and the second line.

Port of the Android ``Row``/``RowIcon``/``Format`` JVM seams
(todo.md §3.2.7): files show ``12.4 KB · 2026-09-04``, folders show
the item count, and a folder filtered to nothing says so instead of
lying. Icon ids name symbolic icon-theme images; the window maps
kinds to them.
"""
from __future__ import annotations

from datetime import datetime

ICON_FOR_KIND = {
    "folder": "folder-symbolic",
    "folder_open": "folder-open-symbolic",
    "image": "image-x-generic-symbolic",
    "video": "video-x-generic-symbolic",
    "audio": "audio-x-generic-symbolic",
    "document": "x-office-document-symbolic",
    "text": "text-x-generic-symbolic",
    "archive": "package-x-generic-symbolic",
    "code": "text-x-script-symbolic",
    "package": "package-x-generic-symbolic",
    "other": "text-x-generic-symbolic",
    "volume": "drive-harddisk-symbolic",
    "removable": "drive-removable-media-symbolic",
    "trash": "user-trash-symbolic",
}


def icon_for_kind(kind: str, expanded: bool = False) -> str:
    if kind == "folder":
        return ICON_FOR_KIND["folder_open" if expanded else "folder"]
    return ICON_FOR_KIND.get(kind, ICON_FOR_KIND["other"])


def human_size(nbytes: int) -> str:
    if nbytes < 1024:
        return f"{nbytes} B"
    kb = nbytes / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.1f} MB"
    return f"{mb / 1024.0:.1f} GB"


def iso_date(millis: int) -> str:
    return datetime.fromtimestamp(millis / 1000).date().isoformat()


def item_count(count: int, hidden: int = 0) -> str:
    shown = "Empty" if count == 0 else ("1 item" if count == 1
                                        else f"{count} items")
    return f"{shown} · {hidden} hidden" if hidden > 0 else shown


def second_line_file(nbytes: int, modified_ms: int) -> str:
    size = human_size(nbytes)
    return size if modified_ms <= 0 else f"{size} · {iso_date(modified_ms)}"
