"""Honest directory reads: blocked and empty are different states.

Port of the Android ``DirectoryContents`` JVM seam (todo.md F4): an
unreadable folder must not paint as ``Empty``.
"""
from __future__ import annotations

from dataclasses import dataclass

from .io_error import message as io_message

BLOCKED_COPY = "Could not open this folder."


@dataclass(frozen=True)
class Entries:
    names: tuple[str, ...]


@dataclass(frozen=True)
class Empty:
    pass


@dataclass(frozen=True)
class Blocked:
    pass


@dataclass(frozen=True)
class Failed:
    message: str


def classify(names: list[str] | None, error: Exception | None = None):
    if error is not None:
        return Failed(io_message(error))
    if names is None:
        return Blocked()
    if not names:
        return Empty()
    return Entries(tuple(names))


def read_list(dir_path: str, list_names=None):
    """Read a directory's child names; ``list_names`` is the injectable IO."""
    import os

    try:
        names = (list_names(dir_path) if list_names is not None
                 else os.listdir(dir_path))
    except PermissionError:
        return Blocked()
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            return Failed(io_message(exc))
        if exc.errno in (13, 20):
            return Blocked()
        return Failed(io_message(exc))
    except Exception as exc:  # noqa: BLE001 - honest message, never a crash
        return Failed(io_message(exc))
    return classify(names)
