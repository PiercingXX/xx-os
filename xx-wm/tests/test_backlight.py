"""backlight.find: the screen node, never the FLX1's vibrator LED."""
from pathlib import Path

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


def test_bare_command_without_backlight(tmp_path, monkeypatch):
    monkeypatch.setattr(backlight, 'find', lambda root=None: None)
    assert backlight.brightnessctl_args() == ['brightnessctl']


def test_shade_slider_shows_for_led_class_backlight(monkeypatch, tmp_path):
    # FLX1: no /sys/class/backlight, panel is leds/lcd-backlight; the
    # shade hid its brightness slider.
    import quick_actions
    found = backlight.Backlight('lcd-backlight', 'leds', tmp_path / 'b')
    monkeypatch.setattr(backlight, 'find', lambda root=None: found)
    assert quick_actions._has_backlight() is True
    monkeypatch.setattr(backlight, 'find', lambda root=None: None)
    assert quick_actions._has_backlight() is False
