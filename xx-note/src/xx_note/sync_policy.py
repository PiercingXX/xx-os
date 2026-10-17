"""Sync verdicts (port of ``core/SyncPolicy.kt`` + ``Verdict.kt``).

``decide`` is pure and total: every (base, local, remote) combination
resolves toward preservation — deletes go to trash, conflicts fork, an
edit always outranks a delete. Bodies are WHOLE-FILE text (frontmatter +
Markdown), so metadata-only edits dirty the snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

PULL = "pull"
PUSH = "push"
MERGE = "merge"
FORK = "fork"
TRASH = "trash"
RESURRECT = "resurrect"
NOTHING = "nothing"
REPLAN = "replan"


@dataclass(frozen=True)
class NoteState:
    body: str | None = None
    trashed: bool = False

    @property
    def present(self) -> bool:
        return self.body is not None


@dataclass(frozen=True)
class BaseSnapshot:
    body: str
    etag: str | None = None


def decide(base: BaseSnapshot | None, local: NoteState, remote: NoteState) -> str:
    if not local.present and remote.present and remote.trashed:
        return TRASH
    if base is None:
        local_live = local.present and not local.trashed
        remote_live = remote.present and not remote.trashed
        if local_live and remote_live:
            return FORK if local.body != remote.body else NOTHING
        if local_live:
            return PUSH
        if remote_live:
            return FORK if local.trashed else PULL
        return NOTHING
    local_dirty = local.body != base.body
    remote_dirty = remote.body != base.body
    remote_gone = not remote.present or remote.trashed
    if local.trashed:
        if not remote.present or remote.trashed:
            return NOTHING
        if remote_dirty:
            return RESURRECT
        return TRASH
    if not local.present:
        return NOTHING if remote_gone else PULL
    if remote_gone:
        return RESURRECT if local_dirty else TRASH
    if not local_dirty and not remote_dirty:
        return NOTHING
    if local_dirty and not remote_dirty:
        return PUSH
    if not local_dirty and remote_dirty:
        return PULL
    return MERGE


def decide_push_rejection(rounds_completed: int) -> str:
    return REPLAN if rounds_completed < 3 else FORK
