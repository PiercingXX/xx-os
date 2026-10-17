"""Row/overflow action model for long-press and selection.

Port of the Android ``MenuActions`` JVM seam (todo.md WS3): files get
Open, Share, Copy, Cut, Rename, Delete, Properties; folders the same
minus Share. Overflow entries (new folder, paste, show-hidden, trash)
and trash-row actions are separate lists, never row actions.
"""
from __future__ import annotations

from enum import Enum


class Action(Enum):
    OPEN = "open"
    SHARE = "share"
    COPY = "copy"
    CUT = "cut"
    RENAME = "rename"
    DELETE = "delete"
    PROPERTIES = "properties"
    NEW_FOLDER = "new_folder"
    PASTE = "paste"
    SHOW_HIDDEN = "show_hidden"
    TRASH = "trash"
    RESTORE = "restore"
    DELETE_FOREVER = "delete_forever"


def for_type(is_dir: bool) -> list[Action]:
    actions = [Action.OPEN, Action.SHARE, Action.COPY, Action.CUT,
               Action.RENAME, Action.DELETE, Action.PROPERTIES]
    if is_dir:
        actions.remove(Action.SHARE)
    return actions


def for_trash() -> list[Action]:
    return [Action.RESTORE, Action.DELETE_FOREVER]
