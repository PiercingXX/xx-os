"""Synchronous copy/move primitives; the worker owns the threads.

Port of the Android ``Transfer`` JVM seam: cut prefers a rename and
falls back to copy+delete across volumes. ``copy_tree`` reports byte
progress and honors cancel so big copies show progress with Cancel.
All of this runs on the job worker, never the main loop.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable


def copy_file(source: str, target: str) -> None:
    try:
        with open(source, "rb") as src, open(target, "xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 256)
        shutil.copystat(source, target)
    except Exception:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise


def move_path(source: str, target: str) -> None:
    try:
        os.rename(source, target)
        return
    except OSError:
        pass
    if os.path.isdir(source) and not os.path.islink(source):
        shutil.copytree(source, target, symlinks=True)
        shutil.rmtree(source, ignore_errors=False)
    else:
        copy_file(source, target)
        os.unlink(source)


def _walk_bytes(source: str) -> int:
    total = 0
    if os.path.islink(source) or not os.path.isdir(source):
        try:
            return os.path.getsize(source)
        except OSError:
            return 0
    for root, _dirs, files in os.walk(source):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def copy_tree(source: str, target: str,
              on_progress: Callable[[int, int], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> None:
    """Copy file/dir with progress; raises Cancelled when cancelled."""
    from .jobs import Cancelled

    total = _walk_bytes(source)
    done = [0]

    def report() -> None:
        if on_progress is not None:
            on_progress(done[0], total)

    def check() -> None:
        if cancelled is not None and cancelled():
            raise Cancelled()

    if os.path.isdir(source) and not os.path.islink(source):
        os.makedirs(target, exist_ok=False)
        try:
            _copy_dir_contents(source, target, check, report, done,
                               on_progress)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        try:
            shutil.copystat(source, target)
        except OSError:
            pass
        return
    check()
    copy_file(source, target)
    done[0] = total
    report()


def _copy_dir_contents(source: str, target: str, check, report, done,
                       _on_progress) -> None:
    for root, dirs, files in os.walk(source):
        check()
        rel = os.path.relpath(root, source)
        dest_root = target if rel == "." else os.path.join(target, rel)
        for name in dirs:
            try:
                os.makedirs(os.path.join(dest_root, name), exist_ok=True)
            except OSError:
                pass
        for name in files:
            check()
            src = os.path.join(root, name)
            dst = os.path.join(dest_root, name)
            copy_file(src, dst)
            done[0] += os.path.getsize(dst)
            report()
