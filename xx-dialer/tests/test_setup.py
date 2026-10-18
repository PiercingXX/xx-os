"""Setup probe tests: real-state mapping, observe flow, test ring."""
import time

from xx_dialer import config as app_config
from xx_dialer import setup as setup_probes
from xx_dialer.service import BUS_NAME as POLICY_BUS_NAME
from xx_dialer.store import ScreenRow, Store
from xx_dialer.ui_setup import SetupController, SetupDeps

CONTACTS = setup_probes.CONTACTS_BUS_NAME
NOTIFY = setup_probes.NOTIFY_BUS_NAME


def _controller(tmp_path, names=([], []), objects=None, notices=None):
    notes = notices if notices is not None else []
    return SetupController(SetupDeps(
        db_file=tmp_path / "calls.db", home=tmp_path,
        notice=notes.append,
        fetch_names=lambda: names, fetch_modems=lambda: objects))


def test_all_present_probes_ok(tmp_path):
    controller = _controller(
        tmp_path,
        names=([CONTACTS, POLICY_BUS_NAME, NOTIFY],
               [CONTACTS, POLICY_BUS_NAME]),
        objects={"/modem/0": {
            "org.freedesktop.ModemManager1.Modem": {},
            "org.freedesktop.ModemManager1.Modem.Voice": {}}})
    snapshot = controller.probe()
    assert snapshot.contacts.ok
    assert snapshot.policy.ok
    assert snapshot.modem.ok
    assert snapshot.notify.ok
    assert snapshot.consult.ok is False
    assert "No screenings" in snapshot.consult.detail


def test_absent_contacts_and_policy_report_honestly(tmp_path):
    snapshot = _controller(tmp_path, names=([], [])).probe()
    assert snapshot.contacts.ok is False
    assert "not installed" in snapshot.contacts.detail
    assert snapshot.policy.ok is False
    assert snapshot.modem.ok is False
    assert "not reachable" in snapshot.modem.detail
    assert snapshot.notify.ok is False


def test_activatable_but_idle_reads_as_installed(tmp_path):
    snapshot = _controller(
        tmp_path, names=([], [CONTACTS, POLICY_BUS_NAME])).probe()
    assert snapshot.contacts.ok
    assert "starts on demand" in snapshot.contacts.detail
    assert snapshot.policy.ok
    assert "first call starts it" in snapshot.policy.detail


def test_modem_without_voice_is_not_enough(tmp_path):
    snapshot = _controller(
        tmp_path,
        objects={"/modem/0": {
            "org.freedesktop.ModemManager1.Modem": {}}}).probe()
    assert snapshot.modem.ok is False
    assert "voice" in snapshot.modem.detail.lower()


def test_consult_probe_reports_recorded_screenings(tmp_path):
    now = int(time.time() * 1000)
    with Store(tmp_path / "calls.db") as store:
        store.log_screen(ScreenRow(at=now - 60_000, verdict="Silence",
                                   reason="unknown caller",
                                   e164="+14155550100"))
    snapshot = _controller(tmp_path, names=([], [])).probe()
    assert snapshot.consult.ok
    assert "unknown caller" in snapshot.consult.detail


def test_finish_seeds_observe_week_once(tmp_path):
    notices: list[str] = []
    controller = _controller(tmp_path, notices=notices)
    before = int(time.time() * 1000)
    controller.finish()
    config = app_config.load(tmp_path)
    first = config["observe_week_end"]
    assert before < first <= before + 7 * 24 * 60 * 60 * 1000 + 5000
    controller.finish()
    assert app_config.load(tmp_path)["observe_week_end"] == first
    assert notices == [], "Finish pops no toast; the page shows the state"


def test_offer_then_enforce_flow(tmp_path):
    controller = _controller(tmp_path)
    config = app_config.load(tmp_path)
    config["observe_week_end"] = float(int(time.time() * 1000) - 1000)
    app_config.save(config, tmp_path)
    assert controller.probe().offer is True
    notices: list[str] = []
    controller._deps.notice = notices.append
    controller.start_screening()
    assert app_config.load(tmp_path)["enforcement_mode"] == "enforcing"
    assert controller.probe().offer is False
    assert notices == []


def test_probe_fetch_failure_degrades_to_absent(tmp_path):
    def _boom():
        raise OSError("no bus")

    controller = SetupController(SetupDeps(
        db_file=tmp_path / "calls.db", home=tmp_path,
        fetch_names=_boom, fetch_modems=_boom))
    snapshot = controller.probe()
    assert snapshot.contacts.ok is False
    assert snapshot.modem.ok is False


def test_send_test_ring_success_and_failure_shapes():
    sent: list[tuple] = []

    class FakeVariant:
        def __init__(self, fmt, value):
            self.fmt = fmt
            self.value = value

        def unpack(self):
            return self.value

    class FakeBus:
        def call(self, bus_name, path, iface, method, params=None,
                 reply_type=None, flags=None, timeout=-1,
                 cancellable=None, callback=None):
            assert reply_type is None
            assert callback is not None
            sent.append((bus_name, method))
            callback(self, object())

        def call_finish(self, _result):
            return FakeVariant("(u)", (7,))

    class FakeGio:
        BusType = type("BusType", (), {"SESSION": "session"})
        DBusCallFlags = type("Flags", (), {"NONE": 0})

        def bus_get_sync(self, *_args):
            return FakeBus()

    outcomes: list[tuple] = []

    def _on_done(ok: bool, detail: str) -> None:
        outcomes.append((ok, detail))

    setup_probes.send_test_ring(_on_done, bus=FakeBus(),
                                gio=FakeGio(), glib=FakeGlib())
    assert outcomes == [(True, "")]
    assert sent == [(NOTIFY, "Notify")]


class FakeGlib:
    Error = type("Error", (Exception,), {})

    def Variant(self, fmt, value):
        return (fmt, value)
