"""State API for the Settings › Playback engine screen.

Wraps :mod:`xx_radio.setup` (the private venv, the token helper) and
:mod:`xx_radio.youtube.probe` (what is installed) into one snapshot the
screen paints, plus the three mutating actions. Everything blocking here
runs on the caller's worker thread; construct cheaply on the main loop.

States: ``ready`` (everything usable), ``token-missing`` (the helper is
not set up, so uncached streams pause), ``updating`` (an update is
running), ``update-failed`` (the last update failed; the live engine is
untouched). ``snapshot()`` never raises: a failed probe degrades to
``update-failed`` or ``token-missing`` with the error kept.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
import threading
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable

from xx_music.engine import Component
from xx_radio import setup
from xx_radio.setup import RunResult, SetupError
from xx_radio.youtube import probe

READY = "ready"
TOKEN_MISSING = "token-missing"
UPDATING = "updating"
UPDATE_FAILED = "update-failed"

#: Progress step ids reported to ``update()``'s ``on_progress`` callback.
STEP_UV = "ensure-uv"
STEP_VENV = "create-venv"
STEP_INSTALL = "install-packages"
STEP_SELF_CHECK = "self-check"
STEP_ONLINE_CHECK = "online-check"
STEP_PROMOTE = "promote"
STEPS = (STEP_UV, STEP_VENV, STEP_INSTALL, STEP_SELF_CHECK, STEP_ONLINE_CHECK,
         STEP_PROMOTE)

Progress = Callable[[str], None]
OnlineCheck = Callable[[Path], None]


@dataclass(frozen=True)
class PackageVersions:
    """One engine package: the system copy, the private copy, the active."""

    name: str
    system: str = ""
    engine: str = ""

    @property
    def active(self) -> str:
        return self.engine or self.system


@dataclass(frozen=True)
class Snapshot:
    """Everything the Playback engine screen shows."""

    state: str
    components: tuple[Component, ...] = ()
    packages: tuple[PackageVersions, ...] = ()
    venv_active: bool = False
    has_previous: bool = False
    last_error: str = ""
    can_browse: bool = False
    can_play: bool = False


def _default_runner(args: list[str], *,
                    cwd: Path | str | None = None) -> RunResult:
    done = subprocess.run(args, capture_output=True, text=True, check=False,
                          cwd=str(cwd) if cwd else None, timeout=600)
    return RunResult(tuple(args), done.returncode, done.stdout or "",
                     done.stderr or "")


_VERSION_SNIPPET = (
    "import importlib.metadata as m\n"
    f"for p in {setup.ENGINE_PACKAGES!r}:\n"
    "    try:\n"
    "        print(p, m.version(p))\n"
    "    except Exception:\n"
    "        print(p, '')\n")


def system_versions(
        packages: tuple[str, ...] = setup.ENGINE_PACKAGES) -> dict[str, str]:
    """Installed system versions (``""`` when a package is absent)."""
    versions: dict[str, str] = {}
    for name in packages:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = ""
    return versions


def venv_versions(engine_dir: Path | str,
                  runner: setup.Runner | None = None) -> dict[str, str]:
    """Package versions inside the live private venv (``{}`` without one)."""
    live = setup.venv_dir(engine_dir)
    if not setup.venv_ok(live):
        return {}
    run = runner or _default_runner
    try:
        done = run([str(setup.venv_python(live)), "-c", _VERSION_SNIPPET])
    except OSError:
        return {}
    if done.returncode != 0:
        return {}
    versions: dict[str, str] = {}
    for line in done.stdout.splitlines():
        name, _, version = line.partition(" ")
        name, version = name.strip(), version.strip()
        if name:
            versions[name] = version
    return versions


class PlaybackEngine:
    """The engine screen's backend: snapshots plus update/undo/drop."""

    def __init__(self, engine_dir: Path | str, *,
                 runner: setup.Runner | None = None,
                 fetch: setup.Fetcher | None = None,
                 probe_fn: Callable[[], probe.Installed] | None = None,
                 today: _dt.date | None = None,
                 system: dict[str, str] | None = None) -> None:
        self._engine_dir = Path(engine_dir)
        self._runner = runner
        self._fetch = fetch
        self._probe_fn = probe_fn
        self._today = today
        self._system = system
        self._lock = threading.Lock()
        self._updating = False
        self._last_error = ""

    # -- snapshot ------------------------------------------------------

    def snapshot_async(self, callback) -> None:
        """snapshot() on a worker; callback(Snapshot) on the GTK loop.
        This is what the screen calls: snapshot() spawns the venv's
        python to read versions and must stay off the main loop."""
        def work() -> None:
            snap = self.snapshot()
            from gi.repository import GLib
            GLib.idle_add(lambda: (callback(snap), False)[1])

        threading.Thread(target=work, daemon=True,
                         name="engine-snapshot").start()

    def snapshot(self) -> Snapshot:
        """Worker thread only (spawns a process); see snapshot_async."""
        with self._lock:
            updating = self._updating
            last_error = self._last_error
        try:
            found = (self._probe_fn() if self._probe_fn is not None
                     else probe.probe(self._engine_dir))
            status = probe.status_from(
                found, self._today or _dt.date.today())
        except Exception as exc:
            return Snapshot(
                UPDATE_FAILED if last_error else TOKEN_MISSING,
                last_error=(last_error or str(exc)[:300]))
        system = (dict(self._system) if self._system is not None
                  else system_versions())
        try:
            engine = venv_versions(self._engine_dir, self._runner)
        except Exception:
            engine = {}
        packages = tuple(PackageVersions(name, system.get(name, ""),
                                         engine.get(name, ""))
                         for name in setup.ENGINE_PACKAGES)
        venv_active = setup.venv_ok(setup.venv_dir(self._engine_dir))
        has_previous = setup.prev_venv_dir(self._engine_dir).exists()
        token = next((part for part in status.components
                      if part.key == "token"), None)
        if updating:
            state = UPDATING
        elif last_error:
            state = UPDATE_FAILED
        elif token is not None and token.state != probe.ComponentState.OK:
            state = TOKEN_MISSING
        else:
            state = READY
        return Snapshot(state, status.components, packages, venv_active,
                        has_previous, last_error, status.can_browse,
                        status.can_play)

    # -- actions -------------------------------------------------------

    def update(self, *, on_progress: Progress | None = None,
               pre_release: bool = False,
               online_check: OnlineCheck | None = None,
               uv_checksums: dict[str, str] | None = None,
               ) -> dict[str, str]:
        """Run the full engine update on this thread, reporting progress.

        Each step id from :data:`STEPS` is reported before it runs. Any
        failure records ``last_error`` (visible as ``update-failed``) and
        re-raises :class:`SetupError`; the live engine stays untouched.
        """
        with self._lock:
            if self._updating:
                raise SetupError("an engine update is already running")
            self._updating = True
            self._last_error = ""
        emit = on_progress or (lambda _step: None)
        try:
            emit(STEP_UV)
            setup.ensure_uv(
                self._engine_dir, fetch=self._fetch,
                checksums=(setup.UV_SHA256 if uv_checksums is None
                           else uv_checksums))
            emit(STEP_VENV)
            setup.create_venv(self._engine_dir, runner=self._runner)
            emit(STEP_INSTALL)
            setup.install_packages(self._engine_dir, runner=self._runner,
                                   pre_release=pre_release)
            emit(STEP_SELF_CHECK)
            versions = setup.self_check(self._engine_dir,
                                        runner=self._runner)
            emit(STEP_ONLINE_CHECK)
            if online_check is not None:
                try:
                    online_check(setup.venv_python(
                        setup.new_venv_dir(self._engine_dir)))
                except SetupError:
                    raise
                except Exception as exc:
                    raise SetupError(
                        f"the new engine failed online: {exc}") from exc
            emit(STEP_PROMOTE)
            setup.promote(self._engine_dir)
            return versions
        except SetupError as exc:
            with self._lock:
                self._last_error = str(exc)[:300]
            raise
        finally:
            with self._lock:
                self._updating = False

    def undo(self) -> None:
        """Swap the previous engine back into place."""
        setup.undo(self._engine_dir)
        with self._lock:
            self._last_error = ""

    def drop_to_system(self) -> None:
        """Remove every staged venv; system packages become the engine."""
        setup.drop_to_system(self._engine_dir)
        with self._lock:
            self._last_error = ""
