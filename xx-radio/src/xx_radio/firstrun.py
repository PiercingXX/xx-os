"""First-run engine state: is the private venv present and healthy?

The distro packages can be too old to stream (M0: Debian yt-dlp answers
403 past 256 KB, ytmusicapi crashes on watch playlists), so first run
offers "Set up playback engine" via :mod:`xx_radio.setup` on a worker.
Until then the catalog keeps working on the system packages where it
can, while playback resolves to ``HELPER_MISSING``/``SOURCE_CHANGED``.

Worker rules: :meth:`FirstRunState.snapshot` spawns processes and never
runs on the GTK loop; use ``snapshot_async`` and ``setup_async`` from
the UI, which deliver on the main loop.
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from xx_music.engine import Component
from xx_radio import engine_status, setup

READY = "ready"
NEEDS_SETUP = "needs-setup"
UPDATING = "updating"
FAILED = "setup-failed"

Progress = Callable[[str], None]
Done = Callable[[dict], None]
Failed = Callable[[Exception], None]


@dataclass(frozen=True)
class FirstRunSnapshot:
    """What the first-run screen shows."""

    state: str
    venv_active: bool = False
    healthy: bool = False
    can_browse: bool = False
    can_play: bool = False
    last_error: str = ""
    components: tuple[Component, ...] = ()


def _healthy(packages) -> bool:
    by_name = {package.name: package.engine for package in packages}
    return bool(by_name.get("ytmusicapi") and by_name.get("yt-dlp"))


class FirstRunState:
    """Venv presence plus worker-driven setup for the first-run UI."""

    def __init__(self, engine_dir: Path | str, *,
                 runner: setup.Runner | None = None,
                 fetch: setup.Fetcher | None = None,
                 probe_fn: Callable | None = None,
                 today: _dt.date | None = None,
                 system: dict[str, str] | None = None) -> None:
        self._engine_dir = Path(engine_dir)
        self._backend = engine_status.PlaybackEngine(
            engine_dir, runner=runner, fetch=fetch, probe_fn=probe_fn,
            today=today, system=system)

    def venv_present(self) -> bool:
        """Cheap file check, safe on the main loop."""
        return setup.venv_ok(setup.venv_dir(self._engine_dir))

    def snapshot(self) -> FirstRunSnapshot:
        """Worker thread only: spawns the venv python and runtime probes."""
        snap = self._backend.snapshot()
        healthy = bool(snap.venv_active and _healthy(snap.packages))
        if snap.state == engine_status.UPDATING:
            state = UPDATING
        elif snap.state == engine_status.UPDATE_FAILED:
            state = FAILED
        elif healthy:
            state = READY
        else:
            state = NEEDS_SETUP
        return FirstRunSnapshot(state, snap.venv_active, healthy,
                                snap.can_browse, snap.can_play,
                                snap.last_error, snap.components)

    def snapshot_async(self, callback) -> None:
        """snapshot() on a worker; callback(snapshot) on the GTK loop."""
        def work() -> None:
            snap = self.snapshot()
            from gi.repository import GLib
            GLib.idle_add(lambda: (callback(snap), False)[1])

        threading.Thread(target=work, daemon=True,
                         name="radio-firstrun").start()

    def setup_async(self, *, on_progress: Progress | None = None,
                    on_done: Done | None = None,
                    on_error: Failed | None = None,
                    pre_release: bool = False,
                    online_check=None,
                    uv_checksums: dict[str, str] | None = None) -> None:
        """Run the engine setup on a worker; callbacks on the GTK loop."""
        from gi.repository import GLib

        emit = on_progress or (lambda _step: None)

        def progress(step: str) -> None:
            GLib.idle_add(lambda: (emit(step), False)[1])

        def work() -> None:
            try:
                versions = self._backend.update(
                    on_progress=progress, pre_release=pre_release,
                    online_check=online_check, uv_checksums=uv_checksums)
            except Exception as exc:  # noqa: BLE001 - delivered to the UI
                if on_error is not None:
                    GLib.idle_add(
                        lambda exc=exc: (on_error(exc), False)[1])
                return
            if on_done is not None:
                GLib.idle_add(lambda: (on_done(versions), False)[1])

        threading.Thread(target=work, daemon=True,
                         name="radio-firstrun-setup").start()
