"""Dotfile rule: hidden unless the persisted toggle is on.

Port of the Android ``Hidden`` JVM seam. The freedesktop Trash has no
on-disk marker inside browsed folders, so there is no trash-dir name
to filter here; dotfiles are the whole rule.
"""
from __future__ import annotations


def should_show(name: str, show_hidden: bool) -> bool:
    if show_hidden:
        return True
    return not name.startswith(".")
