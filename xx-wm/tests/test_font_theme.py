"""font_theme hardening: the custom font family (settable via a restored
backup) must never break CSS parsing or kill startup. Covers the sanitizer
and the guarded provider load. Headless: apply_global_font runs against a
fake gi stack (repo idiom: GTK objects are never constructed under pytest)."""
import logging
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


@pytest.fixture
def font_theme():
    import font_theme
    return font_theme


class TestSanitizeFontFamily:
    @pytest.mark.parametrize('hostile', [
        "Evil'; } window { background: red } '",
        'Back\\slash',
        'Tick`Name',
        'Semi;colon',
        'Quote"Name',
    ])
    def test_neutralizes_css_breakers(self, font_theme, hostile):
        cleaned = font_theme.sanitize_font_family(hostile)
        assert cleaned
        for ch in '\'"`\\;{}':
            assert ch not in cleaned

    @pytest.mark.parametrize('clean', [
        'JetBrains Mono',
        'Space Mono, Monospace',
        'JetBrainsMono Nerd Font',
        '  Padded Name  ',
    ])
    def test_clean_names_untouched(self, font_theme, clean):
        assert font_theme.sanitize_font_family(clean) == clean.strip()

    @pytest.mark.parametrize('garbage', ['', '   ', '\';}{', None])
    def test_empty_or_garbage_falls_back_to_default(self, font_theme, garbage):
        from config import DEFAULT_CONFIG, FONT_FAMILIES
        default = FONT_FAMILIES[DEFAULT_CONFIG['font']]
        assert font_theme.sanitize_font_family(garbage) == default

    def test_sanitized_family_is_idempotent(self, font_theme):
        once = font_theme.sanitize_font_family("A'B;C}")
        assert font_theme.sanitize_font_family(once) == once


class _FakeProvider:
    def __init__(self):
        self.loads: list[bytes] = []
        self.failures = 0

    def load_from_data(self, data):
        if self.failures > 0:
            self.failures -= 1
            raise ValueError('simulated CSS parse failure')
        self.loads.append(data)


@pytest.fixture
def fake_gi(monkeypatch):
    """Minimal gi.repository stand-in: Gdk.Display.get_default() non-None,
    Gtk.CssProvider returning one shared recording provider."""
    provider = _FakeProvider()

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: object())

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.CssProvider = lambda: provider
    gtk.StyleContext = types.SimpleNamespace(
        add_provider_for_display=lambda *a: None)
    gtk.STYLE_PROVIDER_PRIORITY_APPLICATION = 800

    repo = types.ModuleType('gi.repository')
    repo.Gdk = gdk
    repo.Gtk = gtk

    gi = types.ModuleType('gi')
    gi.require_version = lambda *_a, **_k: None
    gi.repository = repo

    monkeypatch.setitem(sys.modules, 'gi', gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', repo)
    return provider


@pytest.fixture(autouse=True)
def _fresh_provider(font_theme, monkeypatch):
    monkeypatch.setattr(font_theme, '_provider', None)


class TestApplyGlobalFont:
    def test_clean_family_reaches_provider(self, font_theme, fake_gi):
        font_theme.apply_global_font('My Font')
        assert len(fake_gi.loads) == 1
        assert b"font-family: 'My Font';" in fake_gi.loads[0]

    def test_buttons_are_regular_weight(self, font_theme, fake_gi):
        """libadwaita bolds button labels; the shell's type is regular."""
        font_theme.apply_global_font('My Font')
        assert b'button { font-weight: 400; }' in fake_gi.loads[0]

    def test_hostile_family_is_sanitized_before_load(self, font_theme, fake_gi):
        font_theme.apply_global_font("Evil'; } body {")
        assert len(fake_gi.loads) == 1
        css = fake_gi.loads[0]
        for ch in '\'"`\\;{}':
            found = ch in css.decode().split("font-family: '")[1].split("'")[0]
            assert not found

    def test_load_failure_falls_back_to_default(self, font_theme, fake_gi, caplog):
        from config import DEFAULT_CONFIG, FONT_FAMILIES
        default = FONT_FAMILIES[DEFAULT_CONFIG['font']]
        fake_gi.failures = 1
        with caplog.at_level(logging.WARNING, logger='piercing.font_theme'):
            font_theme.apply_global_font('Whatever Font')
        assert len(fake_gi.loads) == 1
        assert f"font-family: '{default}';".encode() in fake_gi.loads[0]
        assert any('falling back to default' in r.getMessage() for r in caplog.records)

    def test_total_load_failure_never_escapes(self, font_theme, fake_gi, caplog):
        fake_gi.failures = 99
        with caplog.at_level(logging.WARNING, logger='piercing.font_theme'):
            font_theme.apply_global_font('Whatever Font')
        assert fake_gi.loads == []
        assert any('reload failed' in r.getMessage() for r in caplog.records)

    def test_provider_reused_across_calls(self, font_theme, fake_gi):
        font_theme.apply_global_font('First')
        font_theme.apply_global_font('Second')
        assert len(fake_gi.loads) == 2
