"""Everything the pages share (AppGraph on Android), plus the player model
and the cover loader.

Threading: every DB, network, libsecret and D-Bus call goes through
``AppContext.run`` (a worker pool); results come back on the GTK thread
via GLib.idle_add. Pages never touch the DB or the API directly.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gdk, GLib

from xx_audiobook import api as api_module
from xx_audiobook import auth as auth_module
from xx_audiobook import bookmarks as bookmarks_module
from xx_audiobook import browse as browse_module
from xx_audiobook import download as download_module
from xx_audiobook import paths as paths_module
from xx_audiobook import prefs as prefs_module
from xx_audiobook import repos as repos_module
from xx_audiobook import restore as restore_module
from xx_audiobook import reviews as reviews_module
from xx_audiobook import store as store_module
from xx_audiobook import timeline as timeline_module
from xx_audiobook import zones as zones_module
from xx_audiobook.applog import get_logger

NOT_SIGNED_IN_LINE = "Not connected to the estate server."
OFFLINE_LINE = "Offline. Showing books on this phone."
NO_PLAYER_LINE = "The player isn't running."
NO_SIGN_IN_LINE = "Sign-in isn't available in this build."
QUEUED_LINE = "Downloading. Follow it in Downloads."


def error_line(error) -> str | None:
    """One muted line for an API failure (never a token, never a trace)."""
    if error is None:
        return None
    if isinstance(error, api_module.NotSignedIn):
        return NOT_SIGNED_IN_LINE
    if isinstance(error, api_module.Unreachable):
        return OFFLINE_LINE
    if isinstance(error, api_module.Http):
        return error.message or "The server refused that."
    return "Something went wrong. Try again."


class AppContext:
    """Long-lived objects for one window: API, session, DB, repositories,
    the player model and covers. ``start`` opens the DB off the main loop."""

    def __init__(self, api, session=None, client=None, db_path=None,
                 now_ms=None, scheduler=None,
                 probe_network: bool = False) -> None:
        self.api = api
        self.session = session
        self.client = client
        self.db_path = db_path
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.db = None
        self.library = self.progress = self.home = None
        self.browse = self.collections = self.bookmarks = None
        self.downloads = None
        self.signed_in = False
        self.started = False
        self.auth_resolved = False
        self.access_denied = False
        self.me = None
        self.downloaded: set = set()
        self.ui_prefs = {"library_grid": False}
        self._scheduler = scheduler
        self._probe_network = probe_network
        self._pool = ThreadPoolExecutor(4, thread_name_prefix="xx-ab-ui")
        self._listeners: dict = {}
        self.zones = zones_module.ZonesState(api=api)
        self.reviews = reviews_module.ReviewRepository(
            api, on_changed=self._review_changed)
        self.player = PlayerModel(self)
        self.covers = CoverLoader(self)

    # -- workers and events ---------------------------------------------------

    def run(self, fn, done=None) -> None:
        def _work():
            try:
                result, error = fn(), None
            except Exception as exc:  # noqa: BLE001 - handed to done
                result, error = None, exc
                get_logger().warning("worker: %s", type(exc).__name__)
            GLib.idle_add(lambda: (self._finish(done, result, error),
                                   False)[1])

        self._pool.submit(_work)

    def _finish(self, done, result, error) -> None:
        if done is not None:
            done(result, error)
        self.check_session()

    def check_session(self) -> None:
        """After every worker: follow a 403 not_granted (full-screen no
        access) and a 401 that signed the session out underneath us."""
        session = self.session
        if session is None:
            return
        denied = bool(session.access_denied)
        if denied != self.access_denied:
            self.access_denied = denied
            self.emit("access-changed")
        if self.signed_in and session.session is None:
            self.signed_in = False
            self.me = None
            self.emit("session-changed")

    def connect(self, event: str, callback) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def disconnect(self, event: str, callback) -> None:
        listeners = self._listeners.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    def emit(self, event: str, *args) -> None:
        for callback in list(self._listeners.get(event, [])):
            callback(*args)

    # -- startup ----------------------------------------------------------------

    def start(self) -> None:
        self.run(self._open, self._opened)

    def _open(self):
        db = (store_module.Database(self.db_path) if self.db_path != ":memory:"
              else store_module.Database.in_memory())
        self.db = db
        self.library = repos_module.LibraryRepository(self.api, db)
        self.progress = repos_module.ProgressRepository(self.api, db)
        self.home = browse_module.HomeRepository(self.api, self.library)
        self.browse = browse_module.BrowseRepository(self.api, self.library)
        self.collections = browse_module.CollectionsRepository(
            self.api, self.library)
        self.bookmarks = bookmarks_module.BookmarkRepository(self.api, db)
        self.downloads = download_module.DownloadsState(
            self.api, db, scheduler=(self._scheduler if self._scheduler
                                     is not None
                                     else download_module.RecordingScheduler()))
        self.ui_prefs = prefs_module.load_ui()
        self.downloaded = self.read_downloaded()
        if self.session is not None:
            try:
                self.signed_in = self.session.start() is not None
            except Exception:
                self.signed_in = False
        prefs = prefs_module.load()
        restored = None
        if prefs.last_item_id:
            restored = restore_module.restore(
                prefs.last_item_id, prefs.last_position_s,
                self.library.cached_detail(prefs.last_item_id),
                self.progress.get(prefs.last_item_id))
        return restored

    def _opened(self, restored, error) -> None:
        if error is not None:
            get_logger().warning("startup failed: %s", type(error).__name__)
            # The gate leaves Checking for Sign in rather than hang.
            self.auth_resolved = True
            self.emit("auth-resolved")
            return
        self.player.start(restored)
        self.started = True
        self.auth_resolved = True
        self.emit("ready")
        self.emit("auth-resolved")
        if self.signed_in:
            self._after_sign_in()
        if self._probe_network:
            self.downloads.submit_probe_network(
                lambda _state, _err: self.emit("downloads-changed"))

    def read_downloaded(self) -> set:
        return {row.item_id for row in self.db.downloads().all()
                if row.state == store_module.DownloadState.DONE.value}

    # -- account ---------------------------------------------------------------

    def account(self):
        """The signed-in Session (origin, username, role, source) or None."""
        return self.session.session if self.session is not None else None

    @property
    def is_admin(self) -> bool:
        if self.me is not None and self.me.is_admin:
            return True
        account = self.account()
        return bool(account is not None and account.is_admin)

    @property
    def me_id(self) -> str:
        if self.me is not None and self.me.user_id:
            return self.me.user_id
        account = self.account()
        return account.user_id if account is not None else ""

    def _after_sign_in(self) -> None:
        """Who am I (admin badge, own reviews; a 403 here shows No
        access), settings catch-up, parked downloads resume."""
        downloads = self.downloads

        def _work():
            me = self.api.me()
            try:
                repos_module.SettingsSync(
                    self.api, prefs_module.PrefsStore()).sync()
            except Exception:
                pass
            if downloads is not None:
                downloads.repo.resume_signed_out(downloads.wifi_only())
            return me

        def _done(me, error) -> None:
            if error is None and me is not None:
                self.me = me
                self.emit("account-changed")
            self.emit("settings-changed")

        self.run(_work, _done)

    def recheck_access(self, done=None) -> None:
        """No access → Retry: ask GET /api/me again."""
        def _done(me, error) -> None:
            if error is None and me is not None:
                self.me = me
                self.emit("account-changed")
            if done is not None:
                done(error)

        self.run(self.api.me, _done)

    def sign_in(self, origin: str, username: str, password: str,
                done) -> None:
        """POST {origin}/auth/login on a worker. The password goes only
        into that request body: never stored, never logged."""
        session = self.session
        if session is None:
            done(NO_SIGN_IN_LINE)
            return

        def _done(result, error) -> None:
            if isinstance(result, auth_module.LoginSuccess):
                self._signed_in()
                done(None)
                return
            message = getattr(result, "message", None)
            done(message or "Could not sign in. Try again.")

        self.run(lambda: session.login(origin, username, password), _done)

    def retry_hub(self, done) -> None:
        """'I signed in to xx-apps — try again': hub probe, stored token."""
        session = self.session
        if session is None:
            done(NO_SIGN_IN_LINE)
            return

        def _done(result, error) -> None:
            if error is None and result is not None:
                self._signed_in()
                done(None)
            else:
                done("No xx-apps session on this phone yet. Sign in to "
                     "xx-apps, or sign in here.")

        self.run(session.start, _done)

    def _signed_in(self) -> None:
        self.signed_in = True
        self.access_denied = False
        self.emit("session-changed")
        self.emit("access-changed")
        self._after_sign_in()

    def sign_out(self, done=None) -> None:
        """Halt playback with a final write, forget the token (no wipe:
        downloads and progress stay)."""
        session, client = self.session, self.client

        def _work():
            if client is not None:
                try:
                    client.stop()
                    client.clear_session()
                except Exception:
                    pass
            if session is not None:
                session.sign_out(wipe=False)

        def _done(_result, _error) -> None:
            self.signed_in = False
            self.access_denied = False
            self.me = None
            self.player.signed_out()
            self.emit("session-changed")
            self.emit("access-changed")
            if done is not None:
                done()

        self.run(_work, _done)

    # -- settings ----------------------------------------------------------------

    def save_settings(self, done=None, **changes) -> None:
        """Skip / speed / rewind: saved here first, then PUT
        /api/me/settings (LWW); returns whether the server took it."""
        def _work():
            prefs = prefs_module.load()
            for key, value in changes.items():
                setattr(prefs, key, value)
            prefs_module.save(prefs)
            return repos_module.SettingsSync(
                self.api, prefs_module.PrefsStore()).local_edited()

        def _done(synced, _error) -> None:
            self.emit("settings-changed")
            if done is not None:
                done(bool(synced))

        self.run(_work, _done)

    # -- downloads ---------------------------------------------------------------

    def request_download(self, item_id: str, title: str = "",
                         done=None) -> None:
        """The Book screen's Download: queue it (manifest + storage
        check + the per-book unit); ``done(line)`` gets the one line."""
        downloads = self.downloads
        if downloads is None:
            if done is not None:
                done("Still opening. Try again in a moment.")
            return

        def _work():
            ok, message = downloads.enqueue(item_id, title)
            return ok, message, downloads.block_reason()

        def _done(result, error) -> None:
            if error is not None or result is None:
                line = error_line(error)
            else:
                ok, message, blocked = result
                if not ok:
                    line = message or "Download failed."
                elif blocked:
                    line = "Queued. " + blocked
                else:
                    line = QUEUED_LINE
            self.emit("downloads-changed")
            if done is not None:
                done(line)

        self.run(_work, _done)

    def download_action(self, action: str, item_id: str, done=None) -> None:
        """cancel / retry / delete on a worker, then downloads-changed. A
        deleted book that is playing carries on by streaming."""
        downloads = self.downloads
        if downloads is None:
            return
        playing = (action == "delete" and self.player.item_id == item_id
                   and not self.player.restored)

        def _work():
            getattr(downloads, action)(item_id)
            return self.read_downloaded()

        def _done(downloaded, error) -> None:
            if error is None and downloaded is not None:
                self.downloaded = downloaded
            if playing:
                self.player.reopen_streaming()
            self.emit("downloads-changed")
            self.emit("library-changed")
            if done is not None:
                done(error)

        self.run(_work, _done)

    # -- reviews -----------------------------------------------------------------

    def _review_changed(self, item_id: str) -> None:
        """A review write landed (worker thread): refresh the cached
        detail's star line, then tell the Book page."""
        try:
            if self.library is not None:
                self.library.refresh_detail(item_id)
        except Exception:
            pass
        GLib.idle_add(lambda: (self.emit("rating-changed", item_id),
                               False)[1])

    def bearer(self) -> tuple | None:
        """(api_base, bearer) for the player service, or None."""
        if self.session is None:
            return None
        current = self.session.current()
        if current is None:
            return None
        token, base = current
        return (base, token)

    def save_ui_prefs(self, **values) -> None:
        self.ui_prefs.update(values)
        self.run(lambda: prefs_module.save_ui(values))


def default_context() -> AppContext:
    """Production wiring: libsecret + hub probe, urllib API, D-Bus player,
    systemd download units, NetworkManager for Wi-Fi only."""
    from xx_audiobook.player import PlayerClient

    store = prefs_module.PrefsStore()
    session = auth_module.SessionStore(
        auth_module.AuthCoordinator(store=auth_module.LibsecretBackend()),
        owner_get=lambda: prefs_module.load().owner_user_id,
        owner_set=store.set_owner_user_id)
    return AppContext(api_module.AudiobookApi(
        base_url=session.live_api_base, tokens=session),
                      session=session, client=PlayerClient(),
                      scheduler=download_module.SystemdScheduler(),
                      probe_network=True)


# -- player model ---------------------------------------------------------------


class PlayerModel:
    """What the mini-player and Now Playing show: the service's state
    (StateChanged + a slow poll), extrapolated once a second while
    playing; the cold-start restore until the service holds a book.
    Commands go to the service on a worker; without a service (no
    client) they apply locally, which keeps the UI testable offline."""

    POLL_EVERY_S = 5

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._listeners: list = []
        self.item_id = ""
        self.title = ""
        self.author = ""
        self.status = "Stopped"
        self.position_ms = 0
        self.duration_ms = 0
        self.rate = 1.0
        self.chapter_index = -1
        self.chapters: list = []
        self.sleep_remaining_ms = 0
        self.sleep_end_of_chapter = False
        self.restored = False
        self.busy = False
        self.error: str | None = None
        self._detail_for = ""
        self._ticks = 0
        self._dragging = False

    # -- reading -----------------------------------------------------------------

    @property
    def has_book(self) -> bool:
        return bool(self.item_id)

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    def chapter(self):
        if 0 <= self.chapter_index < len(self.chapters):
            return self.chapters[self.chapter_index]
        return None

    def chapter_end_ms(self) -> int | None:
        current = self.chapter()
        return current.end_ms if current is not None else None

    def connect(self, callback):
        """Listen for changes; returns the disconnect function."""
        self._listeners.append(callback)
        return lambda: callback in self._listeners and \
            self._listeners.remove(callback)

    def changed(self) -> None:
        for callback in list(self._listeners):
            callback()

    # -- feeding -----------------------------------------------------------------

    def start(self, restored) -> None:
        if restored is not None:
            self.item_id = restored.item_id
            self.title = restored.title
            self.author = restored.author
            self.position_ms = restored.position_ms
            self.duration_ms = restored.duration_ms
            self.chapters = list(restored.chapters)
            self.status = "Paused"
            self.restored = True
            self._detail_for = restored.item_id
            self._locate_chapter()
        GLib.timeout_add_seconds(1, self._tick)
        client = self._ctx.client
        if client is not None:
            self._ctx.run(lambda: client._conn(), self._subscribe)
        self.changed()

    def _subscribe(self, _conn, error) -> None:
        if error is not None:
            return
        try:
            self._ctx.client.subscribe(self.apply_service)
        except Exception:
            return
        self.poll()

    def poll(self) -> None:
        client = self._ctx.client
        if client is not None:
            self._ctx.run(lambda: client.get_state(auto_start=False),
                          lambda state, err: err is None
                          and self.apply_service(state))

    def apply_service(self, state: dict) -> None:
        """A StateChanged / GetState dict (microsecond positions)."""
        if not state.get("has_book"):
            if not self.restored:
                self.item_id = ""
                self.status = "Stopped"
                self.changed()
            return
        self.restored = False
        self.item_id = str(state.get("item_id", ""))
        self.title = str(state.get("title", "")) or self.title
        self.author = str(state.get("author", ""))
        self.status = str(state.get("status", "Paused"))
        if not self._dragging:
            self.position_ms = int(state.get("position_us", 0)) // 1000
        self.duration_ms = int(state.get("duration_us", 0)) // 1000
        self.rate = float(state.get("rate", 1.0))
        self.sleep_remaining_ms = int(state.get("sleep_remaining_ms", 0))
        self.sleep_end_of_chapter = bool(
            state.get("sleep_end_of_chapter", False))
        self.error = None
        self._load_chapters()
        self._locate_chapter()
        self.changed()

    def _load_chapters(self) -> None:
        item_id = self.item_id
        if not item_id or item_id == self._detail_for or self._ctx.db is None:
            return
        self._detail_for = item_id

        def _read():
            detail = self._ctx.library.cached_detail(item_id)
            return (timeline_module.chapters_of(detail.chapters)
                    if detail is not None else [])

        def _done(chapters, error) -> None:
            if error is None and self.item_id == item_id:
                self.chapters = sorted(chapters or [],
                                       key=lambda c: c.start_ms)
                self._locate_chapter()
                self.changed()

        self._ctx.run(_read, _done)

    def _locate_chapter(self) -> None:
        self.chapter_index = -1
        for index, chapter in enumerate(self.chapters):
            if chapter.start_ms <= self.position_ms:
                self.chapter_index = index

    def _tick(self) -> bool:
        changed = False
        if self.playing and not self._dragging:
            self.position_ms = min(self.position_ms + int(1000 * self.rate),
                                   self.duration_ms or self.position_ms)
            self._locate_chapter()
            changed = True
        if self.sleep_remaining_ms > 0 and self.playing:
            self.sleep_remaining_ms = max(self.sleep_remaining_ms - 1000, 0)
            changed = True
        self._ticks += 1
        if self.playing and self._ticks % self.POLL_EVERY_S == 0:
            self.poll()
        if changed:
            self.changed()
        return True

    # -- commands ---------------------------------------------------------------

    def _command(self, name: str, *args, local=None) -> None:
        client = self._ctx.client
        if client is None:
            if local is not None:
                local()
            self.changed()
            return

        def _work():
            getattr(client, name)(*args)
            return client.get_state()

        self._ctx.run(_work, self._after)

    def _after(self, state, error) -> None:
        self.busy = False
        if error is not None:
            self.error = NO_PLAYER_LINE
            self.changed()
            return
        self.apply_service(state or {})

    def open_and_play(self, item_id: str, at_ms: int | None = None,
                      title: str = "") -> None:
        """Open (resume point or ``at_ms``) and play, through the service."""
        self.busy = True
        self.error = None
        if item_id != self.item_id:
            self.item_id, self.title, self.author = item_id, title, ""
            self.position_ms, self.chapters, self._detail_for = 0, [], ""
        client = self._ctx.client
        if client is None:
            self.busy, self.restored, self.status = False, False, "Playing"
            if at_ms is not None:
                self.position_ms = at_ms
            self._load_chapters()
            self.changed()
            return
        self.changed()
        session = self._ctx.bearer()

        def _work():
            if session is not None:
                client.set_session(*session)
            client.open(item_id)
            if at_ms is not None:
                client.seek_to_us(at_ms * 1000)
            client.play()
            return client.get_state()

        self._ctx.run(_work, self._after)

    def play_pause(self) -> None:
        if self.restored or (self.has_book and self.status == "Stopped"):
            self.open_and_play(self.item_id, title=self.title)
            return
        self._command("play_pause", local=lambda: setattr(
            self, "status", "Paused" if self.playing else "Playing"))

    def seek_to(self, ms: int) -> None:
        ms = min(max(int(ms), 0), self.duration_ms or int(ms))
        self.position_ms = ms
        self._locate_chapter()
        if self.restored:
            self.changed()
            return
        self._command("seek_to_us", ms * 1000)

    def skip(self, seconds: int) -> None:
        if self.restored or self._ctx.client is None:
            self.seek_to(self.position_ms + seconds * 1000)
            return
        self._command("skip_forward" if seconds > 0 else "skip_back")

    def chapter_step(self, forward: bool) -> None:
        if self.restored or self._ctx.client is None:
            target = self.position_ms
            if forward and self.chapter_index + 1 < len(self.chapters):
                target = self.chapters[self.chapter_index + 1].start_ms
            elif not forward and self.chapter():
                target = self.chapter().start_ms
            self.seek_to(target)
            return
        self._command("next_chapter" if forward else "previous_chapter")

    def set_rate(self, rate: float) -> None:
        self.rate = timeline_module.clamp_speed(rate)
        self._command("set_rate", self.rate)

    def sleep_minutes(self, minutes: int) -> None:
        def _local():
            self.sleep_remaining_ms = minutes * 60000
            self.sleep_end_of_chapter = False
        self._command("sleep", minutes * 60000, local=_local)

    def sleep_end_of_chapter_now(self) -> None:
        def _local():
            end = self.chapter_end_ms()
            self.sleep_end_of_chapter = True
            self.sleep_remaining_ms = max((end or 0) - self.position_ms, 0)
        self._command("sleep_end_of_chapter", local=_local)

    def cancel_sleep(self) -> None:
        def _local():
            self.sleep_remaining_ms = 0
            self.sleep_end_of_chapter = False
        self._command("cancel_sleep", local=_local)

    def pause(self) -> None:
        """Pause only (casting to a zone hands the book over)."""
        if not self.playing:
            return
        self._command("pause", local=lambda: setattr(
            self, "status", "Paused"))

    def reopen_streaming(self) -> None:
        """The downloaded copy of the open book was deleted: reopen it
        from the server at the same position, playing if it was."""
        if not self.has_book or self.restored:
            return
        client = self._ctx.client
        if client is None:
            self.changed()
            return
        item_id, at_ms, was_playing = (self.item_id, self.position_ms,
                                       self.playing)
        session = self._ctx.bearer()

        def _work():
            if session is not None:
                client.set_session(*session)
            client.open(item_id)
            client.seek_to_us(at_ms * 1000)
            if was_playing:
                client.play()
            return client.get_state()

        self._ctx.run(_work, self._after)

    def signed_out(self) -> None:
        """Sign-out halted the service; the mini-player shows it paused."""
        if self.has_book and self.status == "Playing":
            self.status = "Paused"
            self.changed()

    def set_dragging(self, dragging: bool) -> None:
        self._dragging = dragging


# -- covers -----------------------------------------------------------------------


class CoverLoader:
    """Cover textures off the main loop: the offline copy of a downloaded
    book, then the disk cache, then the server (origin-keyed bearer).
    Decoded once; failures are remembered so rows never retry in a loop."""

    LIMIT = 300

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._textures: dict = {}
        self._waiting: dict = {}

    def cached(self, item_id: str, size: str):
        return self._textures.get((item_id, size))

    def request(self, item_id: str, size: str, deliver) -> None:
        key = (item_id, size)
        if key in self._textures:
            if self._textures[key] is not None:
                deliver(self._textures[key])
            return
        if key in self._waiting:
            self._waiting[key].append(deliver)
            return
        self._waiting[key] = [deliver]
        self._ctx.run(lambda: self._load(item_id, size),
                      lambda texture, _err: self._loaded(key, texture))

    def _load(self, item_id: str, size: str):
        safe = hashlib.sha1(item_id.encode("utf-8")).hexdigest()[:20]
        offline = download_module.BookFiles().cover_file(item_id)
        cached = paths_module.cache_dir() / "covers" / f"{safe}-{size}"
        for path in (offline, cached):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            texture = _texture(data)
            if texture is not None:
                return texture
        try:
            data = self._ctx.api.cover_bytes(item_id, size)
        except api_module.ApiError:
            return None
        texture = _texture(data)
        if texture is not None:
            try:
                paths_module.ensure_parent_for(cached).write_bytes(data)
            except OSError:
                pass
        return texture

    def _loaded(self, key, texture) -> None:
        if len(self._textures) > self.LIMIT:
            self._textures.clear()
        self._textures[key] = texture
        for deliver in self._waiting.pop(key, []):
            if texture is not None:
                deliver(texture)


def _texture(data: bytes):
    if not data:
        return None
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except Exception:
        return None
