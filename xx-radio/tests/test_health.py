"""Health monitor: three counted failures raise the banner, one success clears it."""

from xx_music import health
from xx_music.engine import ErrorKind
from xx_music.health import BannerKind, HealthMonitor


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _monitor(clock=None, **kwargs):
    return HealthMonitor("Demo", clock or Clock(), **kwargs)


def test_quiet_until_three_source_changed_then_banner():
    clock = Clock()
    monitor = _monitor(clock)
    assert monitor.banner is None
    monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    assert monitor.banner is None
    monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    banner = monitor.banner
    assert banner is not None
    assert banner.kind == BannerKind.SOURCE_CHANGED
    assert banner.text == ("Demo changed something "
                           "\u2014 update the playback engine")
    assert banner.action == "update-engine"


def test_success_clears_the_banner():
    clock = Clock()
    monitor = _monitor(clock)
    for _ in range(3):
        monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    assert monitor.banner is not None
    monitor.note_success("search")
    assert monitor.banner is None


def test_helper_missing_trips_and_names_its_action():
    clock = Clock()
    monitor = _monitor(clock)
    for _ in range(3):
        monitor.note_error("stream", ErrorKind.HELPER_MISSING)
    banner = monitor.banner
    assert banner is not None
    assert banner.kind == BannerKind.HELPER_MISSING
    assert banner.text == "Playback helper missing"
    assert banner.action == "setup-helper"


def test_mixed_counted_failures_trip_together():
    clock = Clock()
    monitor = _monitor(clock)
    monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    monitor.note_error("search", ErrorKind.OTHER)
    assert monitor.banner is None
    monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    assert monitor.banner is None
    monitor.note_error("search", ErrorKind.HELPER_MISSING)
    assert monitor.banner is not None


def test_window_expires_old_failures():
    clock = Clock()
    monitor = _monitor(clock)
    monitor.note_error("home", ErrorKind.SOURCE_CHANGED)
    monitor.note_error("home", ErrorKind.SOURCE_CHANGED)
    clock.advance(11 * 60)
    monitor.note_error("home", ErrorKind.SOURCE_CHANGED)
    assert monitor.banner is None


def test_capabilities_are_independent():
    clock = Clock()
    monitor = _monitor(clock)
    for _ in range(3):
        monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    assert monitor.banner is not None
    monitor.note_success("home")
    assert monitor.banner is not None
    monitor.note_success("search")
    assert monitor.banner is None


def test_offline_and_signed_out_banners():
    clock = Clock()
    monitor = _monitor(clock)
    monitor.note_error("search", ErrorKind.OFFLINE)
    banner = monitor.banner
    assert banner is not None
    assert banner.kind == BannerKind.OFFLINE
    assert banner.text == "Offline"
    assert banner.action == "retry"
    monitor.note_success("search")
    monitor.note_error("library", ErrorKind.AUTH_EXPIRED)
    banner = monitor.banner
    assert banner is not None
    assert banner.kind == BannerKind.SIGNED_OUT
    assert banner.text == "Signed out"
    assert banner.action == "sign-in"
    monitor.note_success()
    assert monitor.banner is None


def test_offline_outranks_the_update_banner():
    clock = Clock()
    monitor = _monitor(clock)
    for _ in range(3):
        monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    monitor.note_error("search", ErrorKind.OFFLINE)
    assert monitor.banner is not None
    assert monitor.banner.kind == BannerKind.OFFLINE


def test_every_banner_has_one_action():
    for kind, action in health.BANNER_ACTIONS.items():
        assert isinstance(kind, BannerKind)
        assert action and " " not in action


def test_recent_errors_feed_diagnostics():
    clock = Clock()
    monitor = _monitor(clock)
    monitor.note_error("search", ErrorKind.SOURCE_CHANGED)
    monitor.note_error("home", ErrorKind.OFFLINE)
    recent = monitor.recent_errors()
    assert [(event.capability, event.kind) for event in recent] == [
        ("search", "source-changed"), ("home", "offline")]
    assert all(event.when for event in recent)
    assert monitor.recent_errors(limit=1)[0].capability == "home"
    assert monitor.recent_errors(limit=0) == ()
