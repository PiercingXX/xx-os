"""Phone-fit pins: a code is never cut, rows never squeeze a button."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from xx_auth.main import CODE_MAX_PX, app_css

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_auth"


def _theme(scale: float):  # type: ignore[no-untyped-def]
    return SimpleNamespace(scale=scale, foreground="#ffffff", muted="#888888")


def _size(css: str, selector: str) -> int:
    match = re.search(re.escape(selector) + r"\s*\{\s*font-size:\s*(\d+)px", css)
    assert match, selector
    return int(match.group(1))


def test_code_sizes_at_normal_text_are_the_android_ones():
    css = app_css(_theme(1.0))
    assert _size(css, ".xx-auth-code ") == 32
    assert _size(css, ".xx-auth-code.hero ") == 40


def test_large_text_stops_growing_the_code_at_the_fit_cap():
    for scale in (1.3, 1.5, 2.0):
        css = app_css(_theme(scale))
        assert _size(css, ".xx-auth-code ") <= CODE_MAX_PX
        assert _size(css, ".xx-auth-code.hero ") <= CODE_MAX_PX


def test_an_eight_digit_code_fits_a_320px_screen_at_the_cap():
    # "1234 5678" is 9 Space Mono cells: 0.612 em advance + 0.04 em
    # tracking each, inside 320 px minus two 20 px gutters.
    assert 9 * (0.612 + 0.04) * CODE_MAX_PX <= 320 - 2 * 20


def test_code_labels_never_ellipsize():
    text = (SRC / "ui_list.py").read_text(encoding="utf-8")
    body = text.split("def code_label", 1)[1].split("\nclass ", 1)[0]
    assert "EllipsizeMode.NONE" in body
    assert "EllipsizeMode.END" not in body


def test_add_and_import_pages_use_full_width_buttons():
    # A setting row with a trailing button ellipsized both the row text
    # and the button label at large text ("Choose f...", "PNG or JP...").
    for name in ("ui_add.py", "ui_io.py"):
        assert "setting_row(" not in (SRC / name).read_text(encoding="utf-8"), name
