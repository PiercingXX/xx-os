"""MIME from extension, for Properties and open-with display.

Port of the Android ``Mime`` JVM seam: the ``ACTION_VIEW``/Share
intent type, falling back to ``application/octet-stream``.
"""
from __future__ import annotations

_MIME_BY_EXTENSION = {
    "txt": "text/plain", "md": "text/markdown", "log": "text/plain",
    "csv": "text/csv", "tsv": "text/tab-separated-values",
    "srt": "application/x-subrip", "vtt": "text/vtt",
    "json": "application/json", "xml": "text/xml",
    "html": "text/html", "htm": "text/html", "css": "text/css",
    "js": "text/javascript", "kt": "text/x-kotlin",
    "java": "text/x-java", "py": "text/x-python",
    "sh": "application/x-sh", "yaml": "text/yaml", "yml": "text/yaml",
    "toml": "text/plain",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "heic": "image/heic",
    "heif": "image/heif", "bmp": "image/bmp", "avif": "image/avif",
    "dng": "image/x-adobe-dng", "tif": "image/tiff",
    "tiff": "image/tiff", "svg": "image/svg+xml",
    "mp4": "video/mp4", "m4v": "video/mp4",
    "mkv": "video/x-matroska", "webm": "video/webm",
    "mov": "video/quicktime", "3gp": "video/3gpp",
    "avi": "video/x-msvideo", "ts": "video/mp2t",
    "mpg": "video/mpeg", "mpeg": "video/mpeg",
    "mp3": "audio/mpeg", "flac": "audio/flac", "ogg": "audio/ogg",
    "oga": "audio/ogg", "opus": "audio/ogg", "m4a": "audio/mp4",
    "m4b": "audio/mp4", "aac": "audio/aac", "wav": "audio/wav",
    "wma": "audio/x-ms-wma", "aiff": "audio/aiff",
    "amr": "audio/amr", "mid": "audio/midi",
    "pdf": "application/pdf", "doc": "application/msword",
    "docx": ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
    "odt": "application/vnd.oasis.opendocument.text",
    "rtf": "application/rtf", "xls": "application/vnd.ms-excel",
    "xlsx": ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet"),
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": ("application/vnd.openxmlformats-officedocument"
             ".presentationml.presentation"),
    "odp": "application/vnd.oasis.opendocument.presentation",
    "epub": "application/epub+zip",
    "zip": "application/zip", "7z": "application/x-7z-compressed",
    "rar": "application/vnd.rar", "tar": "application/x-tar",
    "gz": "application/gzip", "tgz": "application/gzip",
    "bz2": "application/x-bzip2", "xz": "application/x-xz",
    "zst": "application/zstd", "jar": "application/java-archive",
    "apk": "application/vnd.android.package-archive",
    "apks": "application/vnd.android.package-archive",
    "xapk": "application/vnd.android.package-archive",
}

FALLBACK = "application/octet-stream"


def from_extension(name: str) -> str:
    dot = name.rfind(".")
    if dot < 0 or dot == len(name) - 1:
        return FALLBACK
    return _MIME_BY_EXTENSION.get(name[dot + 1:].lower(), FALLBACK)
