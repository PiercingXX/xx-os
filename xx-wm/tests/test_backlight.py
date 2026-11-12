"""backlight.find: the screen node, never the FLX1's vibrator LED or a
workstation's keyboard LEDs."""
from pathlib import Path

import pytest

import backlight


def _node(root: Path, subsystem: str, name: str, value: int = 100,
          maximum: int = 200) -> None:
    node = root / subsystem / name
    node.mkdir(parents=True)
    (node / 'brightness').write_text(str(value))
    (node / 'max_brightness').write_text(str(maximum))


def test_backlight_class_wins(tmp_path):
    _node(tmp_path, 'backlight', 'intel_backlight')
    _node(tmp_path, 'leds', 'lcd-backlight')
    found = backlight.find(tmp_path)
    assert (found.name, found.subsystem) == ('intel_backlight', 'backlight')


def test_halium_led_backlight_not_vibrator(tmp_path):
    # FLX1: leds/vibrator sorts first and is what bare brightnessctl hit.
    _node(tmp_path, 'leds', 'vibrator')
    _node(tmp_path, 'leds', 'lcd-backlight', 1023, 2047)
    found = backlight.find(tmp_path)
    assert (found.name, found.subsystem) == ('lcd-backlight', 'leds')
    assert backlight.brightnessctl_args(found) == [
        'brightnessctl', '--device', 'lcd-backlight']
    assert backlight.read_pct(found) == 49


def test_nothing_found(tmp_path):
    _node(tmp_path, 'leds', 'vibrator')
    assert backlight.find(tmp_path) is None


def test_no_command_without_backlight(tmp_path, monkeypatch):
    # Bare brightnessctl sets the first LED it finds: never run it.
    monkeypatch.setattr(backlight, 'find', lambda root=None: None)
    assert backlight.brightnessctl_args() is None


def _workstation_leds(root: Path) -> None:
    # The x86 workstation the snapshot rig rendered on: no backlight
    # class, only keyboard and Wi-Fi LEDs.
    (root / 'backlight').mkdir(parents=True)
    _node(root, 'leds', 'input68::scrolllock', 0, 1)
    _node(root, 'leds', 'phy0-led', 0, 1)


def test_keyboard_and_radio_leds_are_not_a_panel(tmp_path):
    _workstation_leds(tmp_path)
    assert backlight.find(tmp_path) is None


@pytest.mark.parametrize('name', [
    'input68::scrolllock', 'input3::capslock', 'input3::numlock',
    'input5::kana', 'tpacpi::kbd_backlight', 'asus::kbd_backlight',
    'dell::kbd_backlight', 'platform::mute', 'vibrator', 'torch',
    'flashlight', 'white:flash', 'led:flash_0', 'led:torch_0',
    'mmc0::', 'phy0-led', 'phy0radio', 'charging', 'bq27441-0-charging',
    'red', 'green', 'blue', 'white:status', 'lcd-backlight::',
    'button-backlight', 'kbd_backlight', 'keyboard-backlight',
])
def test_not_panel_leds(tmp_path, name):
    assert not backlight.is_panel_led(name)
    _node(tmp_path, 'leds', name)
    assert backlight.find(tmp_path) is None


@pytest.mark.parametrize('name', [
    'lcd-backlight', 'lcd_backlight', 'panel-backlight', 'backlight',
    'backlight0', 'LCD-Backlight',
])
def test_panel_leds(tmp_path, name):
    assert backlight.is_panel_led(name)
    _node(tmp_path, 'leds', name)
    assert backlight.find(tmp_path).name == name


def test_env_root_points_find_at_a_fake_tree(tmp_path, monkeypatch):
    _node(tmp_path, 'leds', 'lcd-backlight')
    monkeypatch.setenv(backlight.SYSFS_ENV, str(tmp_path))
    assert backlight.sysfs_class() == tmp_path
    assert backlight.find().name == 'lcd-backlight'
    monkeypatch.delenv(backlight.SYSFS_ENV)
    assert backlight.sysfs_class() == Path('/sys/class')


def test_tests_never_see_the_host_sysfs():
    assert backlight.sysfs_class() != Path('/sys/class')
    assert backlight.find() is None


def test_workstation_leds_get_no_write(tmp_path, monkeypatch):
    """Every brightness path on a machine with only keyboard/Wi-Fi LEDs:
    no process runs, no logind call, no sysfs write."""
    import subprocess

    import als_brightness
    import display_manager
    import quick_actions
    _workstation_leds(tmp_path)
    monkeypatch.setenv(backlight.SYSFS_ENV, str(tmp_path))
    ran: list = []

    def _record(cmd, *a, **k):
        ran.append(cmd)
        raise AssertionError(f'ran {cmd}')

    for name in ('Popen', 'run', 'check_output', 'call'):
        monkeypatch.setattr(subprocess, name, _record)
    monkeypatch.setattr(als_brightness, 'login1_set_brightness',
                        lambda *a, **k: ran.append(('login1', a)) or True)
    monkeypatch.setattr(display_manager, 'login1_set_brightness',
                        lambda *a, **k: ran.append(('login1', a)) or True)

    quick_actions._set_brightness_pct(72)
    assert als_brightness.apply_backlight_pct_sync(72) is False
    assert als_brightness.apply_backlight(500) is False
    assert display_manager._brightnessctl('set', '0') is None
    assert display_manager._brightnessctl('get') is None
    assert quick_actions._has_backlight() is False
    assert ran == []
    for led in ('input68::scrolllock', 'phy0-led'):
        assert (tmp_path / 'leds' / led / 'brightness').read_text() == '0'


def test_torch_reads_the_same_root(tmp_path, monkeypatch):
    import quick_actions
    _workstation_leds(tmp_path)
    monkeypatch.setenv(backlight.SYSFS_ENV, str(tmp_path))
    assert quick_actions._torch_leds() == []
    assert quick_actions._get_torch_state() is None
    _node(tmp_path, 'leds', 'torch', 0, 1)
    quick_actions._toggle_torch(True)
    assert (tmp_path / 'leds' / 'torch' / 'brightness').read_text() == '1'
    assert quick_actions._get_torch_state() is True


def test_shade_slider_shows_for_led_class_backlight(monkeypatch, tmp_path):
    # FLX1: no /sys/class/backlight, panel is leds/lcd-backlight; the
    # shade hid its brightness slider.
    import quick_actions
    found = backlight.Backlight('lcd-backlight', 'leds', tmp_path / 'b')
    monkeypatch.setattr(backlight, 'find', lambda root=None: found)
    assert quick_actions._has_backlight() is True
    monkeypatch.setattr(backlight, 'find', lambda root=None: None)
    assert quick_actions._has_backlight() is False
