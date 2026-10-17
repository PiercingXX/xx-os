"""Downloads: pins, queue gates, prefetch, offline mix and proxy fills."""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from xx_music import downloads
from xx_music.cache import CacheEntry, CacheIndex, CacheManager
from xx_music.downloads import (
    DailyCap,
    DownloadGate,
    DownloadQueue,
    FakeClock,
    OfflineState,
    OfflineTracker,
    PinBoard,
    RefusalTracker,
    build_offline_mix,
    classify_failure,
    fill_via_proxy,
    plan_prefetch,
)
from xx_music.engine import EngineError, ErrorKind, Track
from xx_music.streamproxy import StreamProxy

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    def guard(sock, *args):
        target = args[0] if args else None
        host = target[0] if isinstance(target, tuple) else ""
        if host in ("127.0.0.1", "::1", "localhost"):
            return _REAL_CONNECT(sock, *args)
        raise AssertionError("tests must not use the network")

    def guard_ex(sock, *args):
        target = args[0] if args else None
        host = target[0] if isinstance(target, tuple) else ""
        if host in ("127.0.0.1", "::1", "localhost"):
            return _REAL_CONNECT_EX(sock, *args)
        raise AssertionError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_ex)


DATA = bytes(i % 251 for i in range(100))


class FakeUpstream:
    """Loopback origin serving DATA to ranged fetches, with one drop."""

    def __init__(self, chunk_limit=64, drop_first=False):
        self.chunk_limit = chunk_limit
        self.drop_first = drop_first
        self.dropped = False
        self.ranges = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                if outer.drop_first and not outer.dropped:
                    outer.dropped = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    return
                raw = self.headers.get("Range", "")
                try:
                    first_text, _, last_text = raw[6:].partition("-")
                    first = int(first_text)
                    last = int(last_text) if last_text else len(DATA) - 1
                except ValueError:
                    self._fail(400)
                    return
                last = min(last, len(DATA) - 1)
                if last < first or first >= len(DATA):
                    self._fail(416)
                    return
                if last - first + 1 > outer.chunk_limit:
                    self._fail(403)
                    return
                outer.ranges.append((first, last))
                body = DATA[first:last + 1]
                self.send_response(206)
                self.send_header("Content-Type", "audio/webm")
                self.send_header("Content-Range",
                                 f"bytes {first}-{last}/{len(DATA)}")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _fail(self, code):
                body = b"no"
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def url(self):
        return f"http://127.0.0.1:{self.port}/audio"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture()
def upstream():
    server = FakeUpstream()
    yield server
    server.close()


@pytest.fixture()
def proxy(tmp_path):
    proxy = StreamProxy(tmp_path / "cache", chunk_size=16)
    proxy.start()
    yield proxy
    proxy.stop()


def _source(upstream):
    from xx_music.engine import StreamSource
    return StreamSource(uri=upstream.url(), headers={}, mime="audio/webm",
                        expires_at=time.time() + 3600,
                        size_bytes=len(DATA))


def _track(track_id, artist="Lumen Drive"):
    from xx_music.engine import ArtistRef
    return Track(id=track_id, title=f"Song {track_id}",
                 artists=(ArtistRef(artist, "ar-1"),))


# -- failure classes ----------------------------------------------------------

def test_classify_failure():
    assert classify_failure(ErrorKind.NOT_FOUND) == "gone"
    assert classify_failure(ErrorKind.NOT_AVAILABLE) == "gone"
    assert classify_failure(ErrorKind.RATE_LIMITED) == "refusal"
    assert classify_failure(ErrorKind.OFFLINE) == "network"
    assert classify_failure(ErrorKind.TIMEOUT) == "network"
    assert classify_failure(ErrorKind.HELPER_MISSING) == "pause"
    assert classify_failure(ErrorKind.SOURCE_CHANGED) == "pause"
    assert classify_failure(ErrorKind.AUTH_EXPIRED) == "pause"
    assert classify_failure(ErrorKind.OTHER) == "transient"


# -- prefetch planner ---------------------------------------------------------

def test_prefetch_window_unmetered_eight_metered_two():
    queue = [f"t{n}" for n in range(12)]
    assert plan_prefetch(queue, 0, metered=False) == queue[1:9]
    assert plan_prefetch(queue, 0, metered=True) == queue[1:3]


def test_prefetch_skips_playing_cached_and_excluded():
    queue = ["now", "a", "b", "c", "d"]
    assert plan_prefetch(queue, 0, cached={"a"},
                         exclude={"b"}) == ["c", "d"]
    assert plan_prefetch(queue, 3) == ["d"]
    assert plan_prefetch(queue, 4) == []


# -- offline mix -----------------------------------------------------------------

def _mix_entry(track_id, artist, played):
    return CacheEntry(track_id=track_id, artist=artist, title=track_id,
                      last_played=played)


def test_offline_mix_skips_recently_heard():
    now = 5_000_000.0
    entries = [_mix_entry("old", "A", 1.0),
               _mix_entry("new", "B", now - 100.0)]
    heard = {"old": 1.0, "new": now - 100.0}
    mix = build_offline_mix(entries, heard, limit=24, now=now)
    assert [entry.track_id for entry in mix] == ["old"]


def test_offline_mix_spaces_artists_and_limits_seed():
    entries = [_mix_entry(f"s{n}", "Seed", 100.0 + n) for n in range(6)]
    entries += [_mix_entry(f"o{n}", f"Other{n}", 200.0 + n)
                for n in range(12)]
    mix = build_offline_mix(entries, {}, seed_artist="Seed", limit=24,
                            now=9_000_000.0)
    artists = [entry.artist for entry in mix]
    for pos in range(len(artists)):
        window = artists[pos:pos + 4]
        assert window.count("Seed") <= 1, window
        for artist in set(window):
            if artist:
                assert window.count(artist) <= 1 or len(window) < 4, window
    assert len(mix) <= 24 and mix


def test_offline_mix_limit_zero():
    assert build_offline_mix([_mix_entry("a", "A", 1.0)], {}, limit=0) == []


# -- refusal cool-down and daily cap (fake clock) ----------------------------------

def test_three_refusals_cool_down_for_an_hour():
    clock = FakeClock()
    tracker = RefusalTracker(clock)
    tracker.record_refusal()
    clock.advance(60)
    tracker.record_refusal()
    assert not tracker.in_cooldown()
    clock.advance(60)
    tracker.record_refusal()
    assert tracker.in_cooldown()
    clock.advance(3599)
    assert tracker.in_cooldown()
    clock.advance(2)
    assert not tracker.in_cooldown()


def test_old_refusals_fall_out_of_the_window():
    clock = FakeClock()
    tracker = RefusalTracker(clock)
    tracker.record_refusal()
    tracker.record_refusal()
    clock.advance(16 * 60)
    tracker.record_refusal()
    tracker.record_refusal()
    assert not tracker.in_cooldown()


def test_daily_cap_200_then_tomorrow():
    clock = FakeClock()
    cap = DailyCap(clock=clock)
    for _ in range(200):
        assert cap.allowed()
        cap.record_done()
    assert not cap.allowed()
    clock.advance(24 * 60 * 60 + 1)
    assert cap.allowed()


# -- playback gate (fake clock) ------------------------------------------------------

def test_gate_idle_is_full_speed():
    clock = FakeClock()
    gate = DownloadGate(clock)
    assert gate.can_start()


def test_gate_quiet_after_taps_and_song_starts():
    clock = FakeClock()
    gate = DownloadGate(clock)
    gate.notify_interaction()
    assert gate.block_reason() == "recent-tap"
    clock.advance(30)
    assert gate.can_start()
    gate.notify_song_started()
    assert gate.block_reason() == "recent-tap"
    clock.advance(31)
    assert gate.can_start()


def test_gate_buffering_pauses_everything():
    clock = FakeClock()
    gate = DownloadGate(clock)
    gate.notify_playback(playing=False, buffering=True)
    assert gate.block_reason() == "buffering"
    gate.notify_playback(playing=False, buffering=False)
    assert gate.can_start()


def test_gate_playing_spaces_downloads_20s():
    clock = FakeClock()
    gate = DownloadGate(clock)
    gate.notify_playback(playing=True, buffering=False)
    assert gate.can_start()
    gate.notify_finished()
    assert gate.block_reason() == "gap"
    clock.advance(19)
    assert gate.block_reason() == "gap"
    clock.advance(2)
    assert gate.can_start()


# -- pins ----------------------------------------------------------------------------

def test_pin_unpin_track_album_playlist():
    pins = PinBoard()
    pins.pin_track("t1")
    pins.pin_album("al-1", ["t1", "t2"])
    pins.pin_playlist("pl-1", ["t2", "t3"])
    assert pins.pinned_ids() == {"t1", "t2", "t3"}
    pins.unpin_album("al-1")
    assert pins.is_pinned("t1") and pins.is_pinned("t2")
    assert pins.owners_of("t2") == {"playlist:pl-1"}
    pins.unpin_track("t1")
    assert not pins.is_pinned("t1")
    pins.unpin_playlist("pl-1")
    assert pins.pinned_ids() == set()


# -- the queue --------------------------------------------------------------------------

def test_metered_blocks_downloads_by_default():
    clock = FakeClock()
    queue = DownloadQueue(clock=clock)
    queue.enqueue(["t1"])
    _id, reason = queue.next_ready(online=True, metered=True)
    assert _id is None and reason == "metered"
    queue.set_wifi_only(False)
    _id, reason = queue.next_ready(online=True, metered=True)
    assert _id == "t1" and reason == "ready"


def test_offline_mode_switch_after_3_failures():
    clock = FakeClock()
    queue = DownloadQueue(clock=clock)
    queue.enqueue([_track("t1"), _track("t2")])
    for _ in range(3):
        _id, _ = queue.next_ready(online=True, metered=False)
        assert _id is not None
        queue.mark_failed(_id, ErrorKind.OFFLINE)
    assert queue.offline.offline
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id is None and reason == "offline"
    queue.offline.record_success()
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id is not None and reason == "ready"


def test_gone_tracks_never_retry_but_requeue_revives():
    clock = FakeClock()
    queue = DownloadQueue(clock=clock)
    queue.enqueue(["t1"])
    _id, _ = queue.next_ready(online=True, metered=False)
    assert queue.mark_failed(_id, ErrorKind.NOT_FOUND) == "gone"
    assert queue.pending() == []
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id is None and reason == "empty"
    queue.enqueue(["t1"])
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id == "t1"


def test_refused_track_waits_24h():
    clock = FakeClock()
    queue = DownloadQueue(clock=clock)
    queue.enqueue(["t1", "t2"])
    _id, _ = queue.next_ready(online=True, metered=False)
    assert queue.mark_failed(_id, ErrorKind.RATE_LIMITED) == "refusal"
    second, _ = queue.next_ready(online=True, metered=False)
    assert second == "t2"
    queue.mark_done(second)
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id is None and reason == "empty"
    clock.advance(25 * 60 * 60)
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id == "t1"


def test_refusal_cooldown_blocks_the_queue():
    clock = FakeClock()
    queue = DownloadQueue(clock=clock)
    queue.enqueue(["t1", "t2", "t3"])
    for _ in range(3):
        _id, _ = queue.next_ready(online=True, metered=False)
        queue.mark_failed(_id, ErrorKind.RATE_LIMITED)
        clock.advance(60)
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id is None and reason == "cooldown"


def test_daily_cap_blocks_the_queue():
    clock = FakeClock()
    queue = DownloadQueue(clock=clock, wifi_only=False)
    queue.daily = DailyCap(limit=1, clock=clock)
    queue.enqueue(["t1", "t2"])
    _id, _ = queue.next_ready(online=True, metered=False)
    queue.mark_done(_id)
    _id, reason = queue.next_ready(online=True, metered=False)
    assert _id is None and reason == "daily-cap"


def test_pause_states_leave_the_queue_untouched():
    clock = FakeClock()
    queue = DownloadQueue(clock=clock)
    queue.enqueue(["t1"])
    _id, _ = queue.next_ready(online=True, metered=False)
    assert queue.mark_failed(_id, ErrorKind.HELPER_MISSING) == "pause"
    assert [job.track_id for job in queue.pending()] == ["t1"]


def test_offline_tracker_resets_on_success():
    tracker = OfflineTracker()
    tracker.record_network_failure()
    tracker.record_network_failure()
    assert not tracker.offline
    tracker.record_network_failure()
    assert tracker.offline
    tracker.record_success()
    assert not tracker.offline and tracker.consecutive_failures == 0


# -- OfflineState --------------------------------------------------------------------------

def test_offline_state_snapshot_texts():
    clock = FakeClock()
    online_queue = DownloadQueue(clock=clock)
    online_queue.enqueue(["t1"])
    state = OfflineState(online_queue)
    snap = state.snapshot()
    assert not snap.offline and snap.pending == 1
    assert snap.can_download and snap.prefetch_depth == 8

    metered = OfflineState(online_queue, metered_fn=lambda: True)
    snap = metered.snapshot()
    assert not snap.can_download
    assert snap.can_download_reason == "metered"
    assert snap.prefetch_depth == 2
    assert "Wi-Fi" in snap.text

    down = OfflineState(online_queue, online_fn=lambda: False)
    snap = down.snapshot()
    assert snap.offline and "offline" in snap.text.lower()


# -- fills and resume --------------------------------------------------------------------------

def test_fill_downloads_into_the_proxy_cache(proxy, upstream, tmp_path):
    index = CacheIndex(tmp_path / "music.db")
    try:
        manager = CacheManager(proxy._cache_dir, index,
                               free_bytes_fn=lambda: 64 * 2**30)
        assert fill_via_proxy(proxy, "tr-001",
                              lambda: _source(upstream)) == len(DATA)
        assert proxy.is_cached("tr-001")
        manager.register(CacheEntry(track_id="tr-001", title="Glass"))
        assert manager.is_cached("tr-001")
        assert index.get("tr-001") is not None
    finally:
        index.close()


def test_fill_resumes_a_part_left_by_a_drop(proxy, upstream):
    part = proxy._part_path("tr-009")
    part.write_bytes(DATA[:50])
    ranges_before = len(upstream.ranges)
    assert fill_via_proxy(proxy, "tr-009",
                          lambda: _source(upstream)) == len(DATA)
    assert (proxy._complete_path("tr-009")).read_bytes() == DATA
    assert not part.exists()
    resumed = [first for first, _ in upstream.ranges[ranges_before:]]
    assert resumed and resumed[0] >= 50


def test_drop_keeps_the_part_and_retry_finishes(proxy, tmp_path):
    flaky = FakeUpstream(drop_first=True)
    try:
        part = proxy._part_path("tr-010")
        part.write_bytes(DATA[:50])
        with pytest.raises(EngineError):
            fill_via_proxy(proxy, "tr-010", lambda: _source(flaky))
        assert part.is_file() and part.read_bytes() == DATA[:50]
        healthy = FakeUpstream()
        try:
            assert fill_via_proxy(proxy, "tr-010",
                                  lambda: _source(healthy)) == len(DATA)
        finally:
            healthy.close()
        assert (proxy._complete_path("tr-010")).read_bytes() == DATA
    finally:
        flaky.close()


def test_fill_error_hides_addresses():
    def resolve():
        raise AssertionError("must not be called")

    class ClosedProxy:
        def url_for(self, track_id, _resolve):
            raise OSError("proxy is down")

    with pytest.raises(EngineError) as caught:
        fill_via_proxy(ClosedProxy(), "tr-011", resolve)
    assert "127.0.0.1" not in str(caught.value)
    assert "tr-011" in str(caught.value)


def test_public_names_are_stable():
    for name in ("plan_prefetch", "build_offline_mix", "FakeClock",
                 "RefusalTracker", "DailyCap", "DownloadGate", "PinBoard",
                 "OfflineTracker", "DownloadQueue", "OfflineState",
                 "fill_via_proxy"):
        assert hasattr(downloads, name), name
