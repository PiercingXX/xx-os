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


def overflow(mode: str, has_clip: bool = False,
             trash_count: int = 0) -> list[tuple[str, str]]:
    """Overflow entries per screen, in order, as (label, action name).

    Paste shows only with something staged, Empty Trash only with
    something in the Trash. Back already leads from the Trash to the
    Volumes, so the Trash has no Volumes entry, and an empty Trash has
    no overflow at all.
    """
    if mode == "browse":
        out = [("New folder", "new-folder")]
        if has_clip:
            out.append(("Paste", "paste"))
        out += [("View", "view"), ("Trash", "trash"),
                ("Volumes", "volumes")]
        return out
    if mode == "trash":
        return [("Empty Trash", "empty-trash")] if trash_count else []
    return [("Trash", "trash")]
