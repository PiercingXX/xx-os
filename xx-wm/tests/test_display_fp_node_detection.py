"""_detect_fp_node: sysfs scan, FP_INPUT_DEV override, no guessed fallback."""
import sys
import types

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_glib() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_glib()

import display_manager  # noqa: E402  (needs fake GLib installed first)


def _make_sysfs(tmp_path: Path, inputs: dict[str, tuple[str, str | None]]) -> Path:
    """Fake sysfs tree: {inputX: (device name, event child dir or None)}."""
    base = tmp_path / 'class' / 'input'
    for input_name, (dev_name, event_child) in inputs.items():
        d = base / input_name
        d.mkdir(parents=True)
        (d / 'name').write_text(dev_name)
        if event_child:
            (d / event_child).mkdir()
    return base


class TestDetectFpNode:
    def test_matches_focaltech_case_insensitive(self, tmp_path):
        base = _make_sysfs(tmp_path, {
            'input0': ('AT Translated Set 2 keyboard', 'event0'),
            'input5': ('FocalTech Fingerprint', 'event7'),
        })
        assert display_manager._detect_fp_node(str(base)) == '/dev/input/event7'

    def test_matches_goodix_vendor_name(self, tmp_path):
        base = _make_sysfs(tmp_path, {
            'input2': ('goodix-ts', 'event4'),
        })
        assert display_manager._detect_fp_node(str(base)) == '/dev/input/event4'

    def test_no_match_means_no_fingerprint_node(self, tmp_path):
        # FLX1: event3 is the fts_ts touchscreen; a guessed event3 turned
        # every touch into a fingerprint key event.
        base = _make_sysfs(tmp_path, {
            'input0': ('fts_ts', 'event3'),
        })
        assert display_manager._detect_fp_node(str(base)) == ''

    def test_empty_sysfs_means_no_fingerprint_node(self, tmp_path):
        base = tmp_path / 'class' / 'input'
        base.mkdir(parents=True)
        assert display_manager._detect_fp_node(str(base)) == ''

    def test_touchscreen_with_reader_vendor_name_is_skipped(self, tmp_path):
        base = _make_sysfs(tmp_path, {
            'input1': ('Goodix Capacitive TouchScreen', 'event1'),
            'input4': ('goodix_fp', 'event4'),
        })
        (base / 'input1' / 'properties').write_text('2\n')
        assert display_manager._detect_fp_node(str(base)) == '/dev/input/event4'

    def test_touch_key_on_event3_is_not_a_fingerprint(self):
        import struct
        btn_touch = struct.pack(display_manager._EV_FMT, 0, 0,
                                display_manager.EV_KEY, 330, 1)
        wanted, _ = display_manager.filter_evdev_data(
            btn_touch, '/dev/input/event3', '', 1000.0)
        assert wanted == []

    def test_env_override_wins_over_detection(self, tmp_path, monkeypatch):
        base = _make_sysfs(tmp_path, {
            'input5': ('FocalTech Fingerprint', 'event7'),
        })
        monkeypatch.setenv('FP_INPUT_DEV', '/dev/input/event42')
        assert display_manager._detect_fp_node(str(base)) == '/dev/input/event42'
