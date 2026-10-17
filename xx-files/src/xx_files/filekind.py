"""File-kind classification by name, for the row glyph.

Port of the Android ``FileKind`` JVM seam. Folders are decided by the
caller; everything else by extension. ``has_thumbnail`` marks the
kinds a future thumbnail pass may render as a picture.
"""
from __future__ import annotations

from enum import Enum

_BY_EXTENSION: dict[str, "FileKind"] = {}


class FileKind(Enum):
    FOLDER = "folder"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    TEXT = "text"
    ARCHIVE = "archive"
    CODE = "code"
    PACKAGE = "package"
    OTHER = "other"

    @property
    def has_thumbnail(self) -> bool:
        return self in (FileKind.IMAGE, FileKind.VIDEO)


def _fill(exts: list[str], kind: "FileKind") -> None:
    for ext in exts:
        _BY_EXTENSION[ext] = kind


_fill(["jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp",
       "avif", "dng", "tif", "tiff", "svg"], FileKind.IMAGE)
_fill(["mp4", "mkv", "webm", "mov", "3gp", "m4v", "avi", "ts",
       "mpg", "mpeg"], FileKind.VIDEO)
_fill(["mp3", "flac", "ogg", "oga", "opus", "m4a", "aac", "wav",
       "wma", "aiff", "amr", "mid", "m4b"], FileKind.AUDIO)
_fill(["pdf", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ods",
       "ppt", "pptx", "odp", "epub", "mobi"], FileKind.DOCUMENT)
_fill(["txt", "md", "log", "csv", "tsv", "ini", "cfg", "conf",
       "srt", "vtt", "nfo"], FileKind.TEXT)
_fill(["zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz",
       "zst", "jar"], FileKind.ARCHIVE)
_fill(["kt", "kts", "java", "py", "sh", "bash", "zsh", "js", "ts",
       "json", "xml", "yaml", "yml", "toml", "html", "css", "c",
       "h", "cpp", "rs", "go", "rb", "lua", "sql"], FileKind.CODE)
_fill(["apk", "apks", "xapk", "aab"], FileKind.PACKAGE)


def from_name(name: str) -> FileKind:
    dot = name.rfind(".")
    if dot < 0 or dot == len(name) - 1:
        return FileKind.OTHER
    return _BY_EXTENSION.get(name[dot + 1:].lower(), FileKind.OTHER)


def of(name: str, is_dir: bool) -> FileKind:
    return FileKind.FOLDER if is_dir else from_name(name)
