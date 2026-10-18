"""Broadway driver: builds the REAL overlay window and pumps the loop.

Runs under GDK_BACKEND=broadway with HOME pointed at a temp dir:
first with no model (first-run download screen), then with a seeded
fake model (caption lines, partial->final, live theme reload, drag
margin). Any exception fails the run; success prints SMOKE_OK.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

SRC_DIR = sys.argv[1]
HOME = sys.argv[2]

sys.path.insert(0, SRC_DIR)
for candidate in ("COMMON_DIR",):
    if candidate in os.environ:
        sys.path.insert(0, os.environ[candidate])
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, ".config")

errors: list[str] = []


def _hook(exc_type, exc, tb):
    errors.append("".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _hook

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")  # noqa: E402

from gi.repository import GLib, Gtk  # noqa: E402

import xx_ui  # noqa: E402
from xx_captions import config  # noqa: E402
from xx_captions.overlay import CaptionWindow, layer_available  # noqa: E402
from xx_captions.overlay import load_ui_theme  # noqa: E402


def check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def main() -> int:
    check(not layer_available(),
          "layer-shell must fall back under Broadway (no Wayland)")
    home = __import__("pathlib").Path(HOME)
    config.ensure_dirs(home)

    bare = CaptionWindow(home=home)
    bare.present()
    check(bare._model_box.get_visible(), "first run must show model screen")
    check(not bare._caption_box.get_visible(), "captions hidden pre-model")
    check("MB" in bare._model_size.get_text(),
          f"size shown before download, got {bare._model_size.get_text()!r}")
    check(not bare._toggle.get_visible(),
          "no Start before a model is installed")
    bare._on_download_finished("")
    check(not bare._model_error.get_visible(),
          "a cancelled download shows no message")
    bare._on_download_finished("Download failed")
    check(bare._model_error.get_visible(), "a failed download says so")

    model_id = config.DEFAULT_CONFIG["model"]
    entry = config.MODEL_CATALOG[model_id]
    for name in entry["files"]:
        target = config.models_dir(home) / model_id / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-model-bytes")

    window = CaptionWindow(home=home)
    window.present()
    check(window._caption_box.get_visible(), "caption view with model")
    check(window._toggle.get_visible(), "Start shown once a model exists")
    check(not window._model_box.get_visible(), "model screen hidden")

    window.on_text("hello", False)
    check(window._lines[2].get_text() == "hello", "partial on last line")
    check("caption-line-partial" in
          window._lines[2].get_css_classes(), "partial styled muted")
    window.on_text("hello world", True)
    check(window._lines[2].get_text() == "hello world", "final commits")
    check("caption-line-partial" not in
          window._lines[2].get_css_classes(), "final styled solid")
    window.on_text("second line", True)
    window.on_text("third", False)
    texts = [line.get_text() for line in window._lines if line.get_visible()]
    check(texts == ["hello world", "second line", "third"],
          f"older lines scroll up, got {texts!r}")

    window.set_starting()
    check(not window._toggle.get_sensitive(), "no double Start while starting")
    window.set_active(True, "playback")
    check(window._toggle.get_sensitive(), "Stop is live once on")
    check("on" in window._status.get_text().lower(), "privacy row shows on")
    check("CAPTIONS" in window._status.get_text(), "status is a caps label")
    check(window._toggle.get_label() == "STOP", "toggle offers Stop")
    check(window._menu is not None, "overflow menu exists")
    menu_texts = [w.get_text() for w in _walk(window._menu.get_child())
                  if isinstance(w, Gtk.Label) and w.get_text()]
    check(menu_texts == ["Settings", "Close"],
          f"one way to each place, got {menu_texts!r}")
    window.set_active(False, "")
    check(window._toggle.get_label() == "START", "toggle offers Start")
    check("\u25cf" not in window._status.get_text(), "no decorative dot")
    window.set_error("service unavailable")
    check("service unavailable" in window._status.get_text().lower(),
          "error is user-facing")
    check(window._status.get_lines() == 2 and window._status.get_wrap(),
          "a long status wraps instead of widening the overlay")

    shell_conf = home / ".config" / "xx-wm" / "config.json"
    shell_conf.parent.mkdir(parents=True, exist_ok=True)
    shell_conf.write_text(json.dumps({"theme": "ocean"}), encoding="utf-8")
    reloaded = xx_ui.reload()
    check(reloaded.key == "ocean", f"live switch to ocean, got {reloaded.key}")
    check(load_ui_theme(home).key == "ocean", "overlay resolves ocean")
    window.apply_theme()
    shell_conf.write_text(json.dumps({"theme": "paper"}), encoding="utf-8")
    xx_ui.reload()
    window.apply_margin(120)
    window._on_drag_begin(None, 0.0, 0.0)

    class _Drag:
        def get_offset(self):
            return (True, 0.0, -40.0)

    window._on_drag_update(_Drag(), 0.0, 0.0)
    check(window._margin == 160,
          f"drag up grows the bottom margin, got {window._margin}")
    window._on_drag_end(None, 0.0, 0.0)
    check(config.load(home)["overlay_margin_bottom"] == 160,
          "dragged position persists")

    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(2, loop.quit)
    loop.run()

    width = window.get_width() if hasattr(window, "get_width") else 0
    check(width <= 360, f"overlay fits 360px phone width, got {width}")
    bare.close()
    window.close()
    if errors:
        sys.stderr.write("".join(errors))
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
