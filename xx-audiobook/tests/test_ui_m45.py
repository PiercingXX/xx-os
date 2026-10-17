"""M4/M5 screens: the wiring under Downloads, Setup, Sign in, No access,
Zones, Stats and the reviews block, against the loopback fake server
(no display needed: pure helpers + AppContext with a pumped main loop)."""

import datetime as dt
import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import GLib  # noqa: E402

from xx_audiobook import api as api_module  # noqa: E402
from xx_audiobook import applog as applog_module  # noqa: E402
from xx_audiobook import auth as auth_module  # noqa: E402
from xx_audiobook import download as download_module  # noqa: E402
from xx_audiobook import fmt  # noqa: E402
from xx_audiobook import prefs as prefs_module  # noqa: E402
from xx_audiobook import reviews as reviews_module  # noqa: E402
from xx_audiobook import store as store_module  # noqa: E402
from xx_audiobook.ui import context as context_module  # noqa: E402

PASSWORD = "pw-Sekrit-9"


def pump(until, timeout_ms: int = 5000) -> bool:
    loop = GLib.MainLoop()
    state = {"left": timeout_ms // 20}

    def _check() -> bool:
        state["left"] -= 1
        if until() or state["left"] <= 0:
            loop.quit()
            return False
        return True

    GLib.timeout_add(20, _check)
    loop.run()
    return bool(until())


def _session(server=None):
    coordinator = auth_module.AuthCoordinator(
        store=auth_module.InMemoryCredentialStore(),
        probe=lambda: None)
    return auth_module.SessionStore(coordinator,
                                    debug_override=lambda: None)


def _ctx(server, session=None, scheduler=None):
    session = session or _session()
    api = api_module.AudiobookApi(base_url=session.live_api_base,
                                  tokens=session)
    ctx = context_module.AppContext(
        api, session=session, db_path=":memory:",
        scheduler=scheduler or download_module.RecordingScheduler())
    ctx._open()
    ctx.started = True
    return ctx


def _signed_in(server, ctx):
    lines = []
    ctx.sign_in(server.origin, "alice", PASSWORD, lines.append)
    assert pump(lambda: lines)
    assert lines == [None]
    return ctx


# -- pure helpers --------------------------------------------------------------


def test_size_and_time_formatting():
    assert fmt.size_text(0) == "0 B"
    assert fmt.size_text(940_000) == "940 KB"
    assert fmt.size_text(466_560_000) == "466 MB"
    assert fmt.size_text(1_240_000_000) == "1.2 GB"
    assert fmt.short_hours(3600 * 43.2) == "43 h"
    assert fmt.short_hours(5400) == "1.5 h"
    assert fmt.short_hours(40 * 60) == "40 m"
    assert fmt.stars_text(4.4) == "4 of 5 stars"
    today = dt.date(2026, 10, 17)
    noon = dt.datetime(2026, 10, 16, 12).timestamp()
    assert fmt.day_text(noon, today) == "Yesterday"
    assert fmt.day_text(dt.datetime(2025, 3, 3, 9).timestamp(),
                        today) == "Mar 3, 2025"


def test_downloads_rows_group_and_read_out():
    from xx_audiobook.ui import downloads as ui

    rows = [store_module.Download("a", "A", "Running", 42, 100),
            store_module.Download("b", "B", "Done", 100, 100),
            store_module.Download("c", "C", "Failed", 1, 100,
                                  "Checksum mismatch on file 2."),
            store_module.Download("d", "D", "Queued", 0, 2_000_000),
            store_module.Download("e", "E", "Cancelled", 5, 100)]
    active, stopped, done = ui.group(rows)
    assert [r.item_id for r in active] == ["a", "d"]
    assert [r.item_id for r in stopped] == ["c", "e"]
    assert [r.item_id for r in done] == ["b"]
    assert ui.status_line(rows[0], None).startswith("42%")
    assert ui.status_line(rows[3], "Waiting for Wi-Fi.") \
        == "Waiting for Wi-Fi."
    assert ui.status_line(rows[2], None) == "Checksum mismatch on file 2."
    view = ui.DownloadsView(used_bytes=466_000_000, free_bytes=None)
    assert ui.storage_line(view) == "Storage used: 466 MB"


def test_stats_bars_are_proportional_with_a_visible_floor():
    from xx_audiobook.ui import stats as ui

    assert ui.bar_heights([0, 60, 120], 100) == [0.0, 50.0, 100.0]
    assert ui.bar_heights([1, 3600], 100)[0] == 2.0
    assert ui.bar_heights([0, 0], 100) == [0.0, 0.0]


def test_zone_rows_read_state_and_volume():
    from xx_audiobook.ui import zones as ui

    target = api_module.CastTarget("media_player.k", "Kitchen", "playing",
                                   0.35, "Book", 12.0)
    assert ui.target_line(target) == "Playing  ·  35%  ·  Book"
    assert ui.state_word("standby") == "Standby"


def test_share_tail_is_capped_and_redacted(tmp_path):
    applog_module.reset()
    log = applog_module.get_logger()
    for index in range(4000):
        log.info("line %d Bearer abc.def.ghi user@example.com", index)
    text = applog_module.share_tail(10 * 1024)
    assert len(text.encode()) <= 10 * 1024
    assert "abc.def.ghi" not in text and "user@example.com" not in text
    assert "line 3999" in text
    applog_module.reset()


# -- context wiring against the fake server -----------------------------------


def test_sign_in_never_keeps_the_password(fake_server):
    applog_module.reset()
    session = _session()
    ctx = _signed_in(fake_server, _ctx(fake_server, session))
    assert ctx.signed_in and session.session is not None
    store = session._coordinator._store
    saved = store.load()
    assert PASSWORD not in json.dumps(vars(saved))
    assert PASSWORD not in repr(session.session)
    assert all(PASSWORD not in line for line in applog_module.dump()
               .splitlines())
    login = fake_server.last_call("POST", "/auth/login")
    assert json.loads(login[3])["password"] == PASSWORD


def test_bad_origin_explains_and_stays_signed_out(fake_server):
    ctx = _ctx(fake_server)
    lines = []
    ctx.sign_in("ftp://nope", "alice", PASSWORD, lines.append)
    assert pump(lambda: lines)
    assert lines == ["Use an http(s) address."]
    assert not ctx.signed_in


def test_not_granted_shows_no_access_and_retry_clears_it(fake_server):
    ctx = _signed_in(fake_server, _ctx(fake_server))
    events = []
    ctx.connect("access-changed", lambda: events.append(ctx.access_denied))
    fake_server.queue("GET", "/audiobook/api/me", 403,
                      {"detail": {"reason": "not_granted"}})
    done = []
    ctx.recheck_access(done.append)
    assert pump(lambda: done)
    assert ctx.access_denied and events[-1] is True
    assert ctx.signed_in  # never signs out by itself
    done.clear()
    ctx.recheck_access(done.append)
    assert pump(lambda: done)
    assert done == [None] and not ctx.access_denied
    assert ctx.me is not None and ctx.me.user_id == "u-1"


def test_book_download_queues_through_the_context(fake_server):
    scheduler = download_module.RecordingScheduler()
    ctx = _signed_in(fake_server, _ctx(fake_server, scheduler=scheduler))
    changed, lines = [], []
    ctx.connect("downloads-changed", lambda: changed.append(1))
    ctx.request_download("it-1", "The Long Way", lines.append)
    assert pump(lambda: lines)
    assert lines == [context_module.QUEUED_LINE]
    assert changed and scheduler.scheduled == [("it-1", True)]
    assert ctx.db.downloads().get("it-1").state == "Queued"
    ctx.downloads.network.on_facts(download_module.Facts(
        internet=True, validated=True, not_metered=False))
    lines.clear()
    ctx.request_download("it-1", "The Long Way", lines.append)
    assert pump(lambda: lines)
    assert lines == ["Queued. Waiting for Wi-Fi."]


def test_cancel_retry_delete_and_streaming_after_delete(fake_server):
    ctx = _signed_in(fake_server, _ctx(fake_server))
    ctx.db.downloads().upsert(store_module.Download(
        "it-1", "The Long Way", "Done", 10, 10))
    ctx.player.item_id, ctx.player.status = "it-1", "Playing"
    reopened = []
    ctx.player.reopen_streaming = lambda: reopened.append(True)
    done = []
    ctx.download_action("delete", "it-1", done.append)
    assert pump(lambda: done)
    assert ctx.db.downloads().get("it-1") is None
    assert reopened == [True] and "it-1" not in ctx.downloaded
    ctx.db.downloads().upsert(store_module.Download(
        "it-2", "B", "Running", 1, 10))
    done.clear()
    ctx.download_action("cancel", "it-2", done.append)
    assert pump(lambda: done)
    assert ctx.db.downloads().get("it-2").state == "Cancelled"
    done.clear()
    ctx.download_action("retry", "it-2", done.append)
    assert pump(lambda: done)
    assert ctx.db.downloads().get("it-2").state == "Queued"


def test_setup_prefs_save_here_then_sync(fake_server):
    ctx = _signed_in(fake_server, _ctx(fake_server))
    events, done = [], []
    ctx.connect("settings-changed", lambda: events.append(1))
    ctx.save_settings(done.append, skip_back_seconds=15, default_speed=1.25)
    assert pump(lambda: done)
    assert done == [True] and events
    prefs = prefs_module.load()
    assert (prefs.skip_back_seconds, prefs.default_speed) == (15, 1.25)
    sent = json.loads(fake_server.last_call(
        "PUT", "/audiobook/api/me/settings")[3])
    assert sent["skip_back_s"] == 15 and sent["default_speed"] == 1.25


def test_sign_out_keeps_downloads_and_forgets_the_token(fake_server):
    session = _session()
    ctx = _signed_in(fake_server, _ctx(fake_server, session))
    ctx.db.downloads().upsert(store_module.Download("it-1", "T", "Done",
                                                    1, 1))
    done = []
    ctx.sign_out(lambda: done.append(1))
    assert pump(lambda: done)
    assert not ctx.signed_in and session.session is None
    assert session._coordinator._store.load() is None
    assert ctx.db.downloads().get("it-1") is not None


def test_cast_then_bring_back_resumes_only_after_the_stop(fake_server):
    ctx = _signed_in(fake_server, _ctx(fake_server))
    zones = ctx.zones
    rows, error = zones.refresh()
    assert error is None and rows
    snap = zones.snapshot()
    target = snap.targets[snap.selected][0]
    zone, error = zones.play_on(target, "it-1", 30.0)
    assert error is None and zone.item_id == "it-1"
    fake_server.queue("POST", "/audiobook/api/cast/transport", 502,
                      {"detail": "Home Assistant answered 502."})
    item, error = zones.bring_back_to_phone()
    assert item is None and error is not None
    assert zones.repo.output.__class__.__name__ == "ZoneOutput"
    item, error = zones.bring_back_to_phone()
    assert (item, error) == ("it-1", None)


# -- reviews: optimistic with rollback ---------------------------------------


class _ReviewApi:
    def __init__(self, fail_put=None) -> None:
        self.fail_put = fail_put
        self.rows = [api_module.Review(
            "r1", "b", api_module.ReviewUser("u-2", "Listener A"), 4,
            "[Review text]", 1.0, 1.0)]

    def reviews(self, item_id, page=1):
        return api_module.ReviewsPage(4.0, len(self.rows), page, 20,
                                      list(self.rows))

    def put_review(self, item_id, body):
        if self.fail_put is not None:
            raise self.fail_put
        mine = api_module.Review("r-me", item_id,
                                 api_module.ReviewUser("me", "You"),
                                 body.stars, body.body, 2.0, 2.0)
        self.rows.insert(0, mine)
        return mine


def _state(api):
    seen = []
    repo = reviews_module.ReviewRepository(api)
    state = reviews_module.ReviewsState("b", repo, me_id="me",
                                        listener=lambda: seen.append(
                                            state.ui()))
    state.set_eligibility(True)
    return state, seen


def test_review_post_paints_before_the_server_answers():
    state, seen = _state(_ReviewApi())
    state.start_with_stars(5)
    seen.clear()
    state.save()
    first = seen[0]
    assert first.saving and first.mine is not None and first.mine.stars == 5
    assert state.ui().mine.id == "r-me" and not state.ui().saving


def test_review_post_rolls_back_on_failure():
    state, _seen = _state(_ReviewApi(api_module.Http(500, None, "Boom.")))
    state.start_with_stars(3)
    state.save()
    ui = state.ui()
    assert ui.mine is None and ui.editing and ui.error == "Boom."


def test_review_not_started_gate_shows_before_writing():
    state, _seen = _state(_ReviewApi())
    state.set_eligibility(False)
    assert state.ui().gated
    state.start_editing()
    assert not state.ui().editing and state.ui().not_started
