"""Wizard PIN minimum: 6 digits enforced at both gates — the typing-hint
clear threshold (_pin_add) and the Next-button validation (_on_pin_next).
Headless: the methods run against a namespace self + widget fakes, so no
GObject is ever constructed (PyGObject forbids object.__new__ on widgets)."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _FakeLabel:
    def __init__(self):
        self.text = ''
        self.css: list[str] = []

    def set_text(self, text):
        self.text = text

    def set_visible(self, visible):
        self.visible = visible

    def add_css_class(self, name):
        if name not in self.css:
            self.css.append(name)

    def remove_css_class(self, name):
        if name in self.css:
            self.css.remove(name)


class _FakeStack:
    def __init__(self):
        self.visible_child_name = None

    def set_visible_child_name(self, name):
        self.visible_child_name = name


def _first_boot():
    # Imported lazily: sibling test modules stub sys.modules['gi'] during
    # collection; conftest restores the real bindings before tests run.
    import first_boot
    return first_boot


def _wizard():
    return types.SimpleNamespace(
        _pin_buf='',
        _pin_entered='',
        _pin_dots=_FakeLabel(),
        _pin_hint=_FakeLabel(),
        _confirm_buf='',
        _confirm_dots=_FakeLabel(),
        _pin_error=_FakeLabel(),
        _stack=_FakeStack(),
    )


class TestPinNextValidation:
    @pytest.mark.parametrize('short_pin', ['1234', '12345'])
    def test_rejects_fewer_than_six_digits(self, short_pin):
        wizard = _wizard()
        wizard._pin_buf = short_pin
        _first_boot().FirstBootWizard._on_pin_next(wizard, None)
        assert wizard._pin_hint.text == 'PIN must be at least 6 digits'
        assert 'error' in wizard._pin_dots.css
        assert wizard._stack.visible_child_name is None
        assert wizard._pin_entered == ''

    def test_accepts_exactly_six_digits(self):
        wizard = _wizard()
        wizard._pin_buf = '123456'
        _first_boot().FirstBootWizard._on_pin_next(wizard, None)
        assert wizard._stack.visible_child_name == 'pin_confirm'
        assert wizard._pin_entered == '123456'
        assert 'error' not in wizard._pin_dots.css


class TestTypingHintThreshold:
    def test_hint_persists_below_six_digits(self):
        wizard = _wizard()
        wizard._pin_hint.set_text('PIN must be at least 6 digits')
        for digit in '12345':
            _first_boot().FirstBootWizard._pin_add(wizard, digit)
        assert wizard._pin_hint.text != ''

    def test_hint_clears_on_sixth_digit(self):
        wizard = _wizard()
        wizard._pin_hint.set_text('PIN must be at least 6 digits')
        for digit in '123456':
            _first_boot().FirstBootWizard._pin_add(wizard, digit)
        assert wizard._pin_hint.text == ''
        # Hidden, not an empty row: the page must fit a 320x640 screen.
        assert wizard._pin_hint.visible is False


class TestPinLengthCap:
    """The wizard must share the lock screen's entry cap (_MAX_PIN): a longer
    PIN could be set here but never typed back = permanent lockout."""

    def _max_pin(self):
        from lock_screen import _MAX_PIN
        return _MAX_PIN

    def test_wizard_uses_lock_screen_cap_constant(self):
        first_boot = _first_boot()
        from lock_screen import _MAX_PIN
        assert first_boot._MAX_PIN is _MAX_PIN

    def test_pin_buf_refuses_to_grow_past_cap(self):
        wizard = _wizard()
        fb = _first_boot()
        cap = self._max_pin()
        for digit in '1234567890' * 12:
            fb.FirstBootWizard._pin_add(wizard, digit)
        assert len(wizard._pin_buf) == cap

    def test_confirm_buf_refuses_to_grow_past_cap(self):
        wizard = _wizard()
        fb = _first_boot()
        cap = self._max_pin()
        for digit in '1234567890' * 12:
            fb.FirstBootWizard._confirm_add(wizard, digit)
        assert len(wizard._confirm_buf) == cap

    def test_delete_still_works_at_cap(self):
        wizard = _wizard()
        fb = _first_boot()
        cap = self._max_pin()
        wizard._pin_buf = '9' * cap
        fb.FirstBootWizard._pin_del(wizard)
        assert len(wizard._pin_buf) == cap - 1
        fb.FirstBootWizard._pin_add(wizard, '7')
        assert len(wizard._pin_buf) == cap
