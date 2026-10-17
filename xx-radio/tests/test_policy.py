"""Policy units with a fake clock: skip, sleep, errors, radio, offline mix."""

import pytest

from xx_music import engine as e
from xx_music import policy as P
from xx_music.policy import ErrorPolicy, SleepTimer


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _track(track_id: str, artist: str = "Singer",
           artist_id: str = "ar-1") -> e.Track:
    return e.Track(id=track_id, title=track_id,
                   artists=(e.ArtistRef(artist, artist_id),))


def _err(kind: e.ErrorKind, retry_after=None) -> e.EngineError:
    return e.EngineError(kind, "detail", retry_after)


# -- skip ----------------------------------------------------------------------


def test_skip_helpers():
    assert P.should_restart_current(3.5, 2)
    assert P.should_restart_current(0.0, 0)
    assert not P.should_restart_current(1.0, 2)
    assert P.should_extend_radio(6)
    assert P.should_extend_radio(0)
    assert not P.should_extend_radio(7)


# -- sleep ------------------------------------------------------------------------


def test_sleep_deadline_and_choices():
    clock = FakeClock()
    timer = SleepTimer(clock)
    assert not timer.armed
    timer.arm(900)
    assert timer.remaining() == pytest.approx(900.0)
    clock.advance(901)
    assert timer.expired()
    assert set(P.SLEEP_CHOICES_S) == {900, 1800, 2700, 3600}
    with pytest.raises(ValueError):
        timer.arm(-5)


def test_sleep_fade_last_ten_seconds():
    clock = FakeClock()
    timer = SleepTimer(clock)
    timer.arm(60)
    assert timer.fade_scale() == pytest.approx(1.0)
    clock.advance(55)
    assert timer.fade_scale() == pytest.approx(0.5)
    clock.advance(5)
    assert timer.fade_scale() == pytest.approx(0.0)
    assert timer.expired()


def test_sleep_end_of_song():
    clock = FakeClock()
    timer = SleepTimer(clock)
    timer.arm(P.SLEEP_END_OF_SONG)
    assert timer.fade_scale() == pytest.approx(1.0)
    assert not timer.expired()
    assert timer.end_of_song_pause(118.6, 120.0)
    assert not timer.end_of_song_pause(100.0, 120.0)
    assert not timer.end_of_song_pause(None, 120.0)


# -- error policy -------------------------------------------------------------------


def test_gone_songs_drop_and_decode_drops():
    policy = ErrorPolicy(FakeClock())
    for kind in (e.ErrorKind.NOT_FOUND, e.ErrorKind.NOT_AVAILABLE):
        assert policy.decide(_err(kind), "tr-1").action == "drop-next"
    assert policy.decode_error("tr-1").action == "drop-next"


def test_network_errors_retry_twice_then_skip():
    policy = ErrorPolicy(FakeClock())
    first = policy.decide(_err(e.ErrorKind.TIMEOUT), "tr-1")
    second = policy.decide(_err(e.ErrorKind.OFFLINE), "tr-1")
    assert (first.action, first.delay_s) == ("retry", 0.5)
    assert (second.action, second.delay_s) == ("retry", 1.0)
    assert policy.decide(_err(e.ErrorKind.TIMEOUT), "tr-1").action == "skip"


def test_rate_limit_clamped():
    policy = ErrorPolicy(FakeClock())
    assert policy.decide(
        _err(e.ErrorKind.RATE_LIMITED, 1), "a").delay_s == 15.0
    assert policy.decide(
        _err(e.ErrorKind.RATE_LIMITED, 3600), "b").delay_s == 600.0
    assert policy.decide(
        _err(e.ErrorKind.RATE_LIMITED, 45), "c").delay_s == 45.0


def test_pause_kinds_never_walk_the_queue():
    policy = ErrorPolicy(FakeClock())
    for kind in (e.ErrorKind.HELPER_MISSING, e.ErrorKind.SOURCE_CHANGED,
                 e.ErrorKind.AUTH_EXPIRED, e.ErrorKind.AUTH_REQUIRED):
        assert policy.decide(_err(kind), "tr-1").action == "pause"


def test_expired_url_reresolves_once():
    policy = ErrorPolicy(FakeClock())
    assert policy.expired_url(False).action == "reresolve"
    assert policy.expired_url(True).action == "skip"


def test_three_distinct_failures_trigger_offline_mix():
    policy = ErrorPolicy(FakeClock())
    assert policy.decide(_err(e.ErrorKind.TIMEOUT), "a").action == "retry"
    assert policy.decide(_err(e.ErrorKind.TIMEOUT), "b").action == "retry"
    assert policy.decide(
        _err(e.ErrorKind.TIMEOUT), "c").action == "offline-mix"
    policy.note_offline_mix()
    assert policy.decide(_err(e.ErrorKind.TIMEOUT), "d").action == "retry"
    assert policy.decide(_err(e.ErrorKind.TIMEOUT), "e").action == "retry"
    assert policy.decide(
        _err(e.ErrorKind.TIMEOUT), "f").action == "offline-mix"


def test_success_resets_counters():
    policy = ErrorPolicy(FakeClock())
    policy.decide(_err(e.ErrorKind.TIMEOUT), "a")
    policy.decide(_err(e.ErrorKind.TIMEOUT), "b")
    policy.note_playing()
    assert policy.decide(_err(e.ErrorKind.TIMEOUT), "c").action == "retry"
    assert policy.decide(_err(e.ErrorKind.TIMEOUT), "d").action == "retry"


def test_lone_song_pauses_instead_of_skipping():
    policy = ErrorPolicy(FakeClock())
    assert policy.skip_or_pause(1) == "pause"
    assert policy.skip_or_pause(3) == "skip"


# -- radio and offline mix -----------------------------------------------------------------


def test_radio_filter():
    tracks = [_track("a"), _track("b"), _track("c"), _track("d")]
    kept = P.filter_radio(tracks, {"a"}, {"b"}, {"c"})
    assert [t.id for t in kept] == ["d"]


def test_offline_mix_constraints():
    seed = _track("seed", "Seed Band", "ar-seed")
    tracks = ([_track(f"s-{i}", "Seed Band", "ar-seed") for i in range(8)]
              + [_track(f"o-{i}", f"Band {i % 3}", f"ar-{i % 3}")
                 for i in range(30)])
    mix = P.build_offline_mix(tracks, seed, heard_ids={"o-1"}, limit=24)
    assert len(mix) <= 24
    assert "o-1" not in {t.id for t in mix}
    seed_count = sum(1 for t in mix if P.artist_key(t) == "ar-seed")
    assert seed_count <= 6
    keys = [P.artist_key(t) for t in mix]
    for i, key in enumerate(keys):
        assert key not in keys[max(0, i - 3):i]


# -- idle ------------------------------------------------------------------------


def test_idle_exit_matrix():
    assert P.idle_action("Playing", 5, 9999, False) is None
    assert P.idle_action("Paused", 5, 10, True) is None
    assert P.idle_action("Stopped", 0, 59, False) is None
    assert P.idle_action("Stopped", 0, 60, False) == "exit"
    assert P.idle_action("Stopped", 4, 61, False) == "exit"
    assert P.idle_action("Paused", 4, 60, False) is None
    assert P.idle_action("Paused", 4, 30 * 60, False) == "save-exit"
    assert P.idle_action("Paused", 0, 9999, False) == "exit"
