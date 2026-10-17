"""SNAP_MULTI capture for non-main surfaces (ring card, editor dialog).

snap.py's snap_render only paints the main window; the ring card and the
alarm editor are separate surfaces. This script paints $SHOT_TARGET
("ring" or "dialog") to $SHOT_OUT at 2x and exits. It runs under
SNAP_MULTI=1 (see tools/snap_all.sh).
"""
import os

from gi.repository import Graphene

target = os.environ.get("SHOT_TARGET", "")
out = os.environ["SHOT_OUT"]
cw = app._clock_window  # noqa: F821 -- provided by scripts/snap.py


def _widget():
    if target == "dialog":
        editor = cw._editor
        return editor.dialog if editor is not None else None
    ring = cw._ring
    return ring.win if ring is not None else None


attempts = [0]


def _retry():
    if attempts[0] >= 10:
        os._exit(3)
    GLib.timeout_add(500, _shot)  # noqa: F821 -- provided by snap.py
    return False


def _shot():
    attempts[0] += 1
    widget = _widget()
    if widget is None:
        return _retry()
    try:
        width, height = widget.get_width(), widget.get_height()
    except Exception:
        return _retry()
    if width < 10 or height < 10:
        return _retry()
    native = widget.get_native()
    renderer = native.get_renderer() if native is not None else None
    if renderer is None:
        return _retry()
    snap = Gtk.Snapshot()  # noqa: F821 -- provided by snap.py
    snap.scale(2, 2)
    Gtk.WidgetPaintable.new(widget).snapshot(snap, width, height)  # noqa: F821
    node = snap.to_node()
    rect = Graphene.Rect().init(0, 0, width * 2, height * 2)
    renderer.render_texture(node, rect).save_to_png(out)
    print("snap: wrote", out, flush=True)
    os._exit(0)


if target == "dialog":
    cw.show_tab("alarms")
    cw.open_editor(None)
GLib.timeout_add(1000, _shot)  # noqa: F821
