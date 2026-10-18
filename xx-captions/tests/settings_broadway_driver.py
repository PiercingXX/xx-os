"""Broadway driver: builds the REAL settings window and pumps the loop.

Runs under GDK_BACKEND=broadway with HOME pointed at a seeded temp
dir: main page sections, a live switch toggle, engine switching,
transcript detail, delete, and the empty state on a bare HOME. Any
exception fails the run; success prints SMOKE_OK.
"""
from __future__ import annotations

import os
import sys
import traceback

SRC_DIR = sys.argv[1]
HOME = sys.argv[2]
SEED = sys.argv[3] if len(sys.argv) > 3 else ""

sys.path.insert(0, SRC_DIR)
if "COMMON_DIR" in os.environ:
    sys.path.insert(0, os.environ["COMMON_DIR"])
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, ".config")

errors: list[str] = []


def _hook(exc_type, exc, tb):
    errors.append("".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _hook

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")  # noqa: E402

from gi.repository import GLib, Gtk  # noqa: E402

from xx_captions import config  # noqa: E402
from xx_captions.settings import SettingsWindow  # noqa: E402


def check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _labels(window) -> list[str]:
    return [w.get_text() for w in _walk(window)
            if isinstance(w, Gtk.Label) and w.get_text()]


def _switches(window) -> list:
    return [w for w in _walk(window) if isinstance(w, Gtk.Switch)]


def main() -> int:
    if SEED:
        sys.path.insert(0, os.path.dirname(SEED))
        import demo_seed

        demo_seed.seed(HOME)
    home = __import__("pathlib").Path(HOME)
    config.ensure_dirs(home)

    window = SettingsWindow(home=home)
    window.present()
    check(len(window.history_ids) == 5,
          f"seeded history lists 5 calls, got {len(window.history_ids)}")
    texts = _labels(window)
    check(any("Mara Voss" in text for text in texts), "peer row listed")
    check(any("SHERPA" in text for text in texts), "engine segmented shown")
    check(any("Transcription" in text or "transcription" in text.lower()
              for text in texts), "opt-in row shown")

    switches = _switches(window)
    check(switches, "opt-in switch exists")
    switches[0].set_active(True)
    check(config.load(home)["call_transcription_enabled"] is True,
          "switch writes the opt-in through the real config")

    window._on_engine("vosk")
    check(config.load(home)["engine"] == "vosk", "engine switch persists")
    check(config.load(home)["model"] == config.VOSK_ID,
          "engine switch moves to that engine's model")
    window._on_language("de")
    check(config.load(home)["language"] == "de", "language persists")

    first = window.history_ids[0]
    window.show_detail(first)
    check(window._stack.get_visible_child_name() == "detail",
          "detail page opens")
    check("MARA VOSS" in window._detail_title.get_text(),
          "detail titles the peer in caps")
    check(window._detail_sub.get_visible()
          and window._detail_sub.get_text(),
          "detail shows when the call was and how long")
    detail_texts = _labels(window._detail_page)
    check(any("broken sign" in text for text in detail_texts),
          "detail shows transcript lines")
    window.show_main()
    check(window._stack.get_visible_child_name() == "main",
          "back returns to settings")

    doomed = window.history_ids[-1]
    window.show_detail(doomed)
    window._on_delete(doomed)
    check(len(window.history_ids) == 4, "delete removes the transcript")
    check(window._stack.get_visible_child_name() == "main",
          "delete returns to settings")

    bare_home = home.parent / "bare"
    bare = SettingsWindow(home=bare_home)
    bare.present()
    bare_texts = _labels(bare)
    check(not any("transcripts yet" in text.lower() for text in bare_texts),
          "an empty history shows no empty-state line")
    check(not bare._history_label.get_visible(),
          "an empty history hides its HISTORY heading")
    check(window._history_label.get_visible(),
          "a filled history keeps its heading")
    check(not any(text == "Close" for text in bare_texts),
          "no Close action: the shell closes windows")
    check(any("Download" in text for text in bare_texts),
          "bare HOME offers the model download")

    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(2, loop.quit)
    loop.run()

    for seen, name in ((window, "settings"), (bare, "bare settings")):
        width = seen.get_width() if hasattr(seen, "get_width") else 0
        check(width <= 360, f"{name} fits 360px phone width, got {width}")
    window.close()
    bare.close()
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
