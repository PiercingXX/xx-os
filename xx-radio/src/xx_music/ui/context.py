"""What every music screen gets: the engine, the worker, the player, the
art loader, the window's store, the health monitor and the app's hooks.

``Hooks`` is how an app built on xx_music adds its own screens to the
core ones without the core knowing the source: account and engine
sections in Settings, a first-run screen, the About text, banner
actions, and where the On this phone list comes from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from xx_music.engine import (EngineError, ErrorKind, Rating, Track)
from xx_music.health import HealthMonitor
from xx_music.tasks import Generation, Worker
from xx_music.ui.store import UiStore

#: Download row states, in the order Library shows them.
ACTIVE = "active"
QUEUED = "queued"
FAILED = "failed"
DONE = "done"


@dataclass(frozen=True)
class DownloadRow:
    track: Track
    state: str = DONE
    progress: float = 0.0
    note: str = ""
    size_bytes: int | None = None


@dataclass(frozen=True)
class SettingsSection:
    """An app section in Settings. ``build(ui)`` returns a widget and
    runs on the main loop; slow work goes through ``ui.run``."""
    key: str
    build: Callable[[object], object]


@dataclass
class FirstRun:
    """A screen shown over the shell until the app says it is done.

    ``needed(ui)`` runs on the worker; ``build(ui, done)`` builds the
    screen on the main loop and calls ``done()`` to reveal the shell.
    """
    needed: Callable[[object], bool]
    build: Callable[[object, Callable[[], None]], object]


@dataclass
class Welcome:
    """A one-time sheet after the first run (the sign-in offer).

    ``needed(ui)`` runs on the worker; ``show(ui)`` presents it on the
    main loop and remembers the person's choice itself.
    """
    needed: Callable[[object], bool]
    show: Callable[[object], None]


@dataclass
class Hooks:
    #: Name used in banners ("<source> changed something...").
    source_name: str = ""
    #: Sections before Downloads in Settings (account, engine...). A
    #: section with key "engine" replaces the generic engine rows.
    settings: tuple[SettingsSection, ...] = ()
    #: About: version, a short disclaimer, license entries callable.
    version: str = ""
    disclaimer: str = ""
    licenses: Callable[[], list] | None = None
    first_run: FirstRun | None = None
    welcome: Welcome | None = None
    #: Banner action id -> handler(ui) ("update-engine", "sign-in"...).
    actions: dict[str, Callable[[object], None]] = field(default_factory=dict)
    #: The On this phone rows (worker thread).
    downloads: Callable[[object], list[DownloadRow]] | None = None
    #: Diagnostics block lines about the engine (worker thread).
    engine_lines: Callable[[object], list[str]] | None = None


class UiContext:
    def __init__(self, spec, engine, worker: Worker | None = None,
                 player=None, art=None, store: UiStore | None = None,
                 hooks: Hooks | None = None) -> None:
        from xx_music.ui.player import PlayerModel

        self.spec = spec
        self.engine = engine
        self.paths = spec.paths()
        self.worker = worker or Worker()
        self.player = player or PlayerModel()
        self.art = art
        self.store = store or UiStore(self.paths, engine.key)
        self.hooks = hooks or Hooks()
        self.health = HealthMonitor(self.hooks.source_name or engine.name)
        self.shell = None
        self.signed_in = False
        self.account_name = ""
        self.downloaded: set[str] = set()
        self.downloads: list[DownloadRow] = []
        #: The settings as last read or written (filled on the worker).
        self.prefs: dict = {}
        self._health_listeners: list[Callable[[], None]] = []
        self._account_listeners: list[Callable[[], None]] = []
        self._downloads_listeners: list[Callable[[], None]] = []

    # -- engine calls with health bookkeeping ------------------------------------
    def run(self, capability: str, fn: Callable, on_done=None,
            on_error=None, generation: Generation | None = None):
        """``fn`` on the worker; its outcome feeds the health monitor and
        then ``on_done`` / ``on_error`` on the main loop."""
        def work():
            try:
                result = fn()
            except EngineError as error:
                self.health.note_error(capability, error.kind)
                raise
            except Exception as error:  # noqa: BLE001 - a bug is OTHER
                self.health.note_error(capability, ErrorKind.OTHER)
                raise EngineError(ErrorKind.OTHER, str(error)[:200]) \
                    from error
            self.health.note_success(capability)
            return result

        def done(result):
            self.emit_health()
            if on_done is not None:
                on_done(result)

        def failed(error):
            self.emit_health()
            if on_error is not None:
                on_error(error)

        return self.worker.run(work, done, failed, generation)

    def io(self, fn: Callable, on_done=None, on_error=None):
        """Disk work on the worker (no health bookkeeping)."""
        return self.worker.run(fn, on_done, on_error)

    def supports(self, capability: str) -> bool:
        return self.engine.supports(capability)

    # -- listeners ----------------------------------------------------------------
    def on_health(self, callback) -> None:
        self._health_listeners.append(callback)

    def emit_health(self) -> None:
        for callback in list(self._health_listeners):
            callback()

    def on_account(self, callback) -> None:
        self._account_listeners.append(callback)

    def set_account(self, signed_in: bool, name: str = "") -> None:
        changed = (signed_in, name) != (self.signed_in, self.account_name)
        self.signed_in, self.account_name = signed_in, name
        if changed:
            for callback in list(self._account_listeners):
                callback()

    def on_downloads(self, callback) -> None:
        self._downloads_listeners.append(callback)

    def refresh_downloads(self) -> None:
        from xx_music.ui.downloads import default_rows

        rows_for = self.hooks.downloads or default_rows

        def done(rows) -> None:
            self.downloads = rows
            self.downloaded = {r.track.id for r in rows if r.state == DONE}
            for callback in list(self._downloads_listeners):
                callback()

        self.io(lambda: rows_for(self), done, lambda _e: None)

    # -- actions --------------------------------------------------------------------
    def action(self, action_id: str) -> None:
        handler = self.hooks.actions.get(action_id)
        if handler is not None:
            handler(self)
        elif action_id == "retry" and self.shell is not None:
            self.shell.retry()

    def rate(self, track: Track, rating: Rating) -> None:
        """Like / Dislike from any screen: through the player service
        (engine rating when signed in) and into the local list."""
        self.player.rate(track.id, rating)
        value = self.player.rating(track.id)
        self.io(lambda: self.store.set_rating(track, value))
