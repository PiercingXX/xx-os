"""Filesystem operations for xx-files: worker bodies + Gio bridges.

Listing, counting, trashing, restoring, copying and deleting all run
on a ``jobs`` worker thread; only ``launch_*``/``share_*`` touch Gio
from the main thread (fast, non-blocking launches). Every function
taking a ``Job`` honors cancel.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable

from .jobs import Cancelled, Job
from .rename import next_available_name
from .trash_info import list_entries as trash_list_entries
from .trash_info import parse_trashinfo, restore_destination
from .volumes import Volume

FIXED_FOLDERS = ("Documents", "Downloads", "Pictures", "Music")


def discover_roots(home: str,
                   mounts: list[tuple[str, str]]) -> list[Volume]:
    """Fixed home roots plus removable mounts (already filtered)."""
    roots = [Volume("Home", home, True, False)]
    for name in FIXED_FOLDERS:
        path = os.path.join(home, name)
        if os.path.isdir(path):
            roots.append(Volume(name, path, False, False))
    for label, path in mounts:
        if os.path.isdir(path):
            roots.append(Volume(label, path, False, True))
    return roots


def removable_mounts() -> list[tuple[str, str]]:
    """(label, path) for inserted removable media via Gio.VolumeMonitor."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    out: list[tuple[str, str]] = []
    for mount in Gio.VolumeMonitor.get().get_mounts():
        try:
            if mount.is_shadowed():
                continue
            root = mount.get_root()
            path = root.get_path()
            if path is None:
                continue
            volume = mount.get_volume()
            removable = False
            if volume is not None:
                drive = volume.get_drive()
                if drive is not None:
                    try:
                        removable = bool(drive.is_removable())
                    except Exception:
                        removable = False
            under_media = path.startswith(("/media/", "/run/media/",
                                           "/mnt/"))
            if removable or under_media:
                out.append((mount.get_name() or "Removable", path))
        except Exception:
            continue
    return out


def list_dir_sync(job: Job, path: str, show_hidden: bool = False) -> dict:
    """One directory read: rows + per-folder counts, all off-thread."""
    from .hidden import should_show
    from .io_error import message

    rows: list[dict] = []
    try:
        with os.scandir(path) as it:
            scanned = list(it)
    except PermissionError:
        return {"status": "blocked"}
    except FileNotFoundError as exc:
        return {"status": "failed", "message": message(exc)}
    except OSError as exc:
        if exc.errno in (13, 20):
            return {"status": "blocked"}
        return {"status": "failed", "message": message(exc)}
    if job.cancelled:
        raise Cancelled()
    for entry in scanned:
        if job.cancelled:
            raise Cancelled()
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        name = entry.name
        if not should_show(name, show_hidden):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
            size = stat.st_size
            modified = stat.st_mtime
        except OSError:
            size, modified = 0, 0.0
        rows.append({"name": name, "path": entry.path, "is_dir": is_dir,
                     "size": size, "modified": modified,
                     "count": None, "hidden": 0})
    for row in rows:
        if not row["is_dir"]:
            continue
        if job.cancelled:
            raise Cancelled()
        count, hidden = count_visible(row["path"], show_hidden)
        row["count"], row["hidden"] = count, hidden
    return {"status": "ok", "rows": rows}


def count_visible(dirpath: str, show_hidden: bool) -> tuple[int, int]:
    shown, hidden = 0, 0
    try:
        with os.scandir(dirpath) as it:
            for entry in it:
                if entry.name.startswith(".") and not show_hidden:
                    hidden += 1
                else:
                    shown += 1
    except OSError:
        pass
    return shown, hidden


def do_trash(job: Job, paths: list[str]) -> None:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    for path in paths:
        if job.cancelled:
            raise Cancelled()
        Gio.File.new_for_path(path).trash(None)


def do_delete_forever(job: Job, paths: list[str]) -> None:
    for path in paths:
        if job.cancelled:
            raise Cancelled()
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.unlink(path)
            except OSError:
                pass


def trash_dirs() -> tuple[str, str]:
    from .trash_info import trash_home

    base = trash_home()
    return os.path.join(base, "files"), os.path.join(base, "info")


def do_empty_trash(job: Job) -> int:
    files_dir, info_dir = trash_dirs()
    entries = trash_list_entries(files_dir, info_dir)
    do_delete_forever(job, [e.trash_path for e in entries])
    do_delete_forever(job, [e.info_path for e in entries
                            if e.info_path])
    return len(entries)


def do_restore(job: Job, trash_path: str,
               info_path: str | None) -> str:
    original: str | None = None
    if info_path and os.path.isfile(info_path):
        with open(info_path, encoding="utf-8",
                  errors="replace") as handle:
            original, _date = parse_trashinfo(handle.read())
    if not original:
        raise OSError("No original location recorded")
    parent = os.path.dirname(original)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        siblings = os.listdir(parent)
    except OSError:
        siblings = []
    dest = restore_destination(original, siblings)
    if job.cancelled:
        raise Cancelled()
    from .transfer import move_path

    move_path(trash_path, dest)
    if info_path:
        try:
            os.unlink(info_path)
        except OSError:
            pass
    return dest


def do_mkdir(job: Job, path: str) -> str:  # noqa: ARG001
    os.makedirs(path, mode=0o700, exist_ok=False)
    return path


def do_rename(job: Job, source: str, target: str) -> str:  # noqa: ARG001
    os.rename(source, target)
    return target


def do_paste(job: Job, items: list[tuple[str, bool]], cwd: str,
             on_progress: Callable[[int, int], None] | None = None
             ) -> list[str]:
    """Copy/cut staged paths into cwd with unique names; cut removes."""
    from .transfer import copy_tree

    try:
        existing = set(os.listdir(cwd))
    except OSError:
        existing = set()
    total = 0
    sizes: list[int] = []
    for source, _cut in items:
        size = _path_bytes(source)
        sizes.append(size)
        total += size
    done = [0]
    made: list[str] = []
    try:
        for (source, is_cut), size in zip(items, sizes):
            if job.cancelled:
                raise Cancelled()
            name = os.path.basename(source)
            if name in existing:
                name = next_available_name(name, list(existing))
            existing.add(name)
            target = os.path.join(cwd, name)
            if is_cut:
                from .transfer import move_path

                try:
                    move_path(source, target)
                except OSError:
                    copy_tree(source, target,
                              cancelled=lambda: job.cancelled)
                    _remove(source)
            else:
                def report(d: int, _t: int, base=done[0]) -> None:
                    if on_progress is not None:
                        on_progress(base + d, total)

                copy_tree(source, target, on_progress=report,
                          cancelled=lambda: job.cancelled)
            done[0] += size
            if on_progress is not None:
                on_progress(done[0], total)
            made.append(target)
    except Exception:
        raise
    return made


def _path_bytes(path: str) -> int:
    total = 0
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.unlink(path)
        except OSError:
            pass


def launch_default(path: str) -> str | None:
    """Open with the default handler; returns an error line or None."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    try:
        uri = Gio.File.new_for_path(path).get_uri()
        ok = Gio.AppInfo.launch_default_for_uri(uri, None)
        return None if ok else f"Nothing opens {os.path.basename(path)}"
    except Exception as exc:  # noqa: BLE001 - shown, never raised
        from .io_error import message

        return message(exc)


def share_target() -> object | None:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    for desktop_id in ("io.piercingxx.XXShare.desktop",
                       "io.piercingxx.xx-share.desktop"):
        try:
            info = Gio.DesktopAppInfo.new(desktop_id)
        except Exception:
            info = None
        if info is not None:
            return info
    return None


def share_files(paths: list[str]) -> str | None:
    """Send via xx-share's desktop entry; error line or None."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    info = share_target()
    if info is None:
        return "xx-share is not installed"
    try:
        files = [Gio.File.new_for_path(p) for p in paths]
        ok = info.launch(files, None)
        return None if ok else "xx-share did not start"
    except Exception as exc:  # noqa: BLE001 - shown, never raised
        from .io_error import message

        return message(exc)
