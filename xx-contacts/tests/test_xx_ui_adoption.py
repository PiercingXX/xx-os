"""xx_ui adoption: the app themes through the shared design system.

The app keeps no theme reader or CSS of its own (shell_theme.py is
gone): ``main`` calls ``xx_ui.apply`` once, every preset renders
listview/danger rules from its tokens, live reload notifies
subscribers, and the list filter covers the Starred chip.
"""

import pytest

import xx_ui
from xx_ui import css as xx_css
from xx_ui import theme as xx_theme
from xx_contacts.list_model import ListFilter, ListRow, apply_filters


def _write_theme(monkeypatch, tmp_path, payload: dict) -> None:
    config_dir = tmp_path / ".config" / "xx-wm"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        __import__("json").dumps(payload), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))


@pytest.mark.parametrize("key", xx_theme.preset_keys())
def test_every_preset_renders_listview_and_danger(
        monkeypatch, tmp_path, key):
    _write_theme(monkeypatch, tmp_path, {"theme": key})
    active = xx_theme.load()
    assert active.key == key
    out = xx_css.render(active)
    assert "listview" in out
    assert active.background in out
    assert active.foreground in out
    # Light presets take the darker, readable danger red.
    assert active.danger in out
    assert "white" not in out.lower()


def test_missing_config_falls_back_to_amoled(monkeypatch, tmp_path):
    _write_theme(monkeypatch, tmp_path, {})
    assert xx_theme.load().key == "amoled"


def test_unknown_preset_falls_back(monkeypatch, tmp_path):
    _write_theme(monkeypatch, tmp_path, {"theme": "neon"})
    assert xx_theme.load().key == "amoled"


def test_apply_is_safe_headless(monkeypatch, tmp_path):
    _write_theme(monkeypatch, tmp_path, {"theme": "forest"})
    active = xx_ui.apply(None)
    assert active.key == "forest"


def test_reload_notifies_subscribers(monkeypatch, tmp_path):
    _write_theme(monkeypatch, tmp_path, {"theme": "amoled"})
    seen: list[str] = []
    drop = xx_ui.on_change(lambda active: seen.append(active.key))
    try:
        xx_css.reload()
        assert seen and seen[-1] == "amoled"
    finally:
        drop()


def test_main_applies_design_system_once():
    from pathlib import Path

    src = (Path(__file__).parent.parent / "src" / "xx_contacts"
           / "main.py").read_text(encoding="utf-8")
    assert "import xx_ui" in src
    assert src.count("xx_ui.apply(") == 1
    assert "shell_theme" not in src


def test_starred_filter_defaults_off_and_filters():
    assert ListFilter().starred_only is False
    rows = [ListRow(uid="a", display_name="A", starred=True),
            ListRow(uid="b", display_name="B")]
    kept = apply_filters(rows, "", ListFilter(starred_only=True))
    assert [row.uid for row in kept] == ["a"]
    assert len(apply_filters(rows, "", ListFilter())) == 2


def _read_src(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).parent.parent / "src" / "xx_contacts"
            / name).read_text(encoding="utf-8")


def test_row_menu_is_chromeless_and_cancellable():
    src = _read_src("list_ui.py")
    menu = src[src.index("def _show_row_menu"):]
    menu = menu[:menu.index("\n    def ")]
    assert "set_decorated(False)" in menu
    assert '"Cancel"' in menu


def test_detail_field_links_skip_call_and_message():
    src = _read_src("detail_ui.py")
    assert 'action.kind not in ("call", "message")' in src
