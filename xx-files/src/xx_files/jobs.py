"""Cancellable file jobs on a worker thread; results via GLib.idle_add.

Every file operation — listing, copy, move, trash, delete-forever,
restore, mkdir, rename — runs here. The main loop only schedules and
receives. ``run_sync`` powers the headless tests without GTK.
"""
from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field


class Cancelled(Exception):
    pass


@dataclass
class Job:
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


def _post(callback, *args) -> None:
    try:
        from gi.repository import GLib

        GLib.idle_add(lambda: (callback(*args), False)[1])
    except Exception:
        callback(*args)


def submit(func: Callable, *args, on_done: Callable | None = None,
           on_error: Callable | None = None, job: Job | None = None,
           daemon: bool = True) -> Job:
    own = job or Job()

    def runner() -> None:
        try:
            result = func(own, *args)
        except Cancelled:
            if on_error is not None:
                _post(on_error, Cancelled())
            return
        except Exception as exc:  # noqa: BLE001 - forwarded honestly
            if on_error is not None:
                _post(on_error, exc)
            return
        if on_done is not None:
            _post(on_done, result)

    threading.Thread(target=runner, daemon=daemon).start()
    return own


def run_sync(func: Callable, *args, **kwargs):
    """Run a job function synchronously (tests, no threads)."""
    return func(Job(), *args, **kwargs)


def list_dir(job: Job, path: str) -> list[os.DirEntry]:
    with os.scandir(path) as it:
        entries = list(it)
    if job.cancelled:
        raise Cancelled()
    return entries


def job_copy(job: Job, source: str, target: str,
             on_progress: Callable[[int, int], None] | None = None) -> str:
    from .transfer import copy_tree

    def progress(done: int, total: int) -> None:
        if on_progress is not None:
            on_progress(done, total)

    copy_tree(source, target, on_progress=progress,
              cancelled=lambda: job.cancelled)
    if job.cancelled:
        raise Cancelled()
    return target


def job_move(job: Job, source: str, target: str,
             on_progress: Callable[[int, int], None] | None = None) -> str:
    from .transfer import move_path

    try:
        move_path(source, target)
        return target
    except OSError:
        pass
    dest = os.path.join(target, os.path.basename(source)) \
        if os.path.isdir(target) else target
    job_copy(job, source, dest, on_progress)
    if os.path.isdir(source) and not os.path.islink(source):
        shutil.rmtree(source, ignore_errors=True)
    else:
        try:
            os.unlink(source)
        except OSError:
            pass
    return dest
