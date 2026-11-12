"""D-Bus parameter shapes under the real GLib (w5_fixup_7 item 1).

Test fakes accept any Variant nesting, but real PyGObject raises
``KeyError: 0`` when a prebuilt container Variant is nested inside a
tuple — which turned every call and SMS into a logged "failed" on the
device. These tests build the exact params of every D-Bus call that
passes ``a{sv}`` through the production builders, under the real GLib,
so a construction-time failure breaks here instead of on the phone.

Production modules stay bound to whatever gi they imported with; the
builders take an explicit ``glib`` so the tests can hand them the real
one. Nothing here may install fake gi: ``conftest`` restores the real
modules before tests run, and imports stay lazy (function-local) so
collection order cannot break them.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _real_glib():
    pytest.importorskip('gi')
    from gi.repository import GLib as RealGLib
    return RealGLib


class TestCreateCallParams:
    def test_voice_params_build_under_real_glib(self):
        import modem_monitor
        params = modem_monitor.voice_call_params(
            '+15550001111', glib=_real_glib())
        assert params.get_type_string() == '(a{sv})'
        (props,) = params.unpack()
        assert dict(props) == {'number': '+15550001111'}

    def test_voice_params_multiline_safe(self):
        import modem_monitor
        params = modem_monitor.voice_call_params(
            '+15550001111', glib=_real_glib())
        (props,) = params.unpack()
        assert props['number'] == '+15550001111'

class TestOtherASVCallSites:
    def test_openuri_portal_params(self, monkeypatch, tmp_path):
        import capture
        glib = _real_glib()
        monkeypatch.setattr(capture, 'GLib', glib)
        seen = []

        class _Bus:
            def call(self, *args):
                seen.append(args)

        target = tmp_path / 'shot.png'
        target.touch()
        assert capture.share_file(str(target), bus=_Bus()) is True
        (_bus, _path, _iface, method, params, _rt, _fl,
         _to, _cancel, _cb) = seen[0]
        assert method == 'OpenURI'
        assert params.get_type_string() == '(ssa{sv})'
        parent, uri, options = params.unpack()
        assert parent == ''
        assert uri == target.absolute().as_uri()
        assert dict(options) == {'ask': True}

    def test_trigger_feedback_params(self):
        import call_ui
        params = call_ui.trigger_feedback_params(glib=_real_glib())
        assert params.get_type_string() == '(ssa{sv}i)'
        app_id, event, options, timeout = params.unpack()
        assert (app_id, event) == (call_ui._RINGER_APP_ID,
                                   call_ui._RINGER_EVENT)
        assert dict(options) == {}
        assert timeout == 0

    def test_max_accuracy_changed_params(self):
        import quick_actions
        params = quick_actions.max_accuracy_changed_params(
            quick_actions._ACC_EXACT, glib=_real_glib())
        assert params.get_type_string() == '(sa{sv}as)'
        iface, changed, invalidated = params.unpack()
        assert iface == quick_actions._AGENT_IFACE
        assert dict(changed) == {'MaxAccuracyLevel':
                                 quick_actions._ACC_EXACT}
        assert list(invalidated) == []
