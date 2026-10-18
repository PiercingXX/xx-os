"""RulesController tests: every Rules-screen write path, headless."""
import pytest

from xx_dialer import config as app_config
from xx_dialer.store import Store
from xx_dialer.ui_rules import RulesController, RulesDeps

NOW = 1_700_000_000_000


def _controller(tmp_path):
    return RulesController(RulesDeps(db_file=tmp_path / "calls.db",
                                     home=tmp_path, notice=lambda _t: None))


def test_load_defaults(tmp_path):
    snapshot = _controller(tmp_path).load()
    assert snapshot.enforcing is False
    assert snapshot.offer is False
    assert snapshot.bypass_until_ms is None
    assert snapshot.hidden_policy == "unknown"
    assert snapshot.repeat is True
    assert snapshot.patterns == []
    assert snapshot.blocked == []


def test_enforce_and_bypass_round_trip(tmp_path):
    controller = _controller(tmp_path)
    snapshot = controller.set_enforcing(True)
    assert snapshot.enforcing is True
    assert app_config.load(tmp_path)["enforcement_mode"] == "enforcing"
    snapshot = controller.arm_bypass(30)
    assert snapshot.bypass_until_ms is not None
    snapshot = controller.stop_bypass()
    assert snapshot.bypass_until_ms is None
    snapshot = controller.set_bypass_minutes(480)
    assert snapshot.bypass_minutes == 480


def test_save_window_and_policies(tmp_path):
    controller = _controller(tmp_path)
    snapshot = controller.save_window("unknown_window", 600, 900, 31)
    assert (snapshot.unknown.start_minute, snapshot.unknown.end_minute,
            snapshot.unknown.days_mask) == (600, 900, 31)
    with pytest.raises(ValueError):
        controller.save_window("unknown_window", 0, 0, 0)
    snapshot = controller.set_hidden_policy("block")
    assert snapshot.hidden_policy == "block"
    with pytest.raises(ValueError):
        controller.set_hidden_policy("lobotomize")
    snapshot = controller.set_repeat(False)
    assert snapshot.repeat is False
    snapshot = controller.set_notif_policy("daily")
    assert snapshot.notif_policy == "daily"
    with pytest.raises(ValueError):
        controller.set_notif_policy("someday")


def test_pattern_add_delete_and_neighbor_preset(tmp_path):
    controller = _controller(tmp_path)
    snapshot = controller.add_pattern("1425555", 4, "block")
    assert len(snapshot.patterns) == 1
    with pytest.raises(ValueError):
        controller.add_pattern("abc", 4, "block")
    with pytest.raises(ValueError):
        controller.add_pattern("1425555", 4, "quarantine")
    snapshot = controller.set_neighbor_preset("+14155550100")
    assert any(row.preset == "neighbor_spoof"
               for row in snapshot.patterns)
    snapshot = controller.set_neighbor_preset(None)
    assert not any(row.preset == "neighbor_spoof"
                   for row in snapshot.patterns)
    with pytest.raises(ValueError):
        controller.set_neighbor_preset(None)
    row_id = snapshot.patterns[0].row_id
    snapshot = controller.delete_pattern(row_id)
    assert snapshot.patterns == []


def test_blocklist_import_unblock_and_clear_log(tmp_path):
    controller = _controller(tmp_path)
    snapshot = controller.import_blocklist_text("+14155550200\njunk\n")
    assert snapshot.blocked == ["+14155550200"]
    snapshot = controller.unblock("+14155550200")
    assert snapshot.blocked == []
    from xx_dialer.service import PolicyService

    service = PolicyService(db_file=tmp_path / "calls.db",
                            home=tmp_path)
    service.check_number("+14155550200")
    with Store(tmp_path / "calls.db") as store:
        assert len(store.last_screened(10)) >= 1
    snapshot = controller.clear_log()
    assert snapshot.log == []


def test_unblock_clears_blocked_tier_and_pushes_delta(tmp_path):
    from xx_dialer.store import BLOCKED_GROUP, MirrorRow

    pushed = []

    class _Client:
        def set_blocked(self, uid, blocked):
            pushed.append((uid, blocked))
            with Store(tmp_path / "calls.db") as store:
                if blocked:
                    store.upsert_tier(uid, BLOCKED_GROUP)
                    for number in store.mirror_numbers_for_key(uid):
                        store.add_blocked(number)
                else:
                    store.remove_tier(uid, BLOCKED_GROUP)
                    for number in store.mirror_numbers_for_key(uid):
                        store.remove_blocked(number)
            return []

    with Store(tmp_path / "calls.db") as store:
        store.upsert_mirror([MirrorRow("uid-spam", "+19999999999",
                                       "Spammer", False, None, False,
                                       NOW)])
        store.upsert_tier("uid-spam", BLOCKED_GROUP)
        store.add_blocked("+19999999999")
    controller = RulesController(RulesDeps(
        db_file=tmp_path / "calls.db", home=tmp_path,
        notice=lambda _t: None, contacts=_Client()))
    snapshot = controller.unblock("+1 (999) 999-9999")
    assert snapshot.blocked == []
    assert pushed == [("uid-spam", False)]
    with Store(tmp_path / "calls.db") as store:
        assert store.all_tiers() == []
        assert not store.is_blocked("+19999999999")


def test_unblock_without_client_clears_tier_locally(tmp_path):
    from xx_dialer.store import BLOCKED_GROUP, MirrorRow

    with Store(tmp_path / "calls.db") as store:
        store.upsert_mirror([MirrorRow("uid-spam", "+19999999999",
                                       "Spammer", False, None, False,
                                       NOW)])
        store.upsert_tier("uid-spam", BLOCKED_GROUP)
        store.add_blocked("+19999999999")
    snapshot = _controller(tmp_path).unblock("+19999999999")
    assert snapshot.blocked == []
    with Store(tmp_path / "calls.db") as store:
        assert store.all_tiers() == []
        assert not store.is_blocked("+19999999999")


def test_test_number_validates_and_runs_engine(tmp_path):
    controller = _controller(tmp_path)
    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_blocked("+14155550200")
    assert controller.test_number("+14155550200") == ("block",
                                                      "blocked")
    with pytest.raises(ValueError):
        controller.test_number("   ")


def test_direct_rule_changes_pop_no_toast(tmp_path):
    # The screen shows each change (segment, card, list): no HUD on top.
    notes: list[str] = []
    controller = RulesController(RulesDeps(
        db_file=tmp_path / "calls.db", home=tmp_path, notice=notes.append))
    controller.set_enforcing(True)
    controller.set_enforcing(False)
    controller.arm_bypass(30)
    controller.stop_bypass()
    controller.clear_log()
    assert notes == []
