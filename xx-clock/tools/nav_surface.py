"""SNAP_MULTI capture for the editor dialog and the ring card.

$SHOT_TARGET picks the surface, painted to $SHOT_OUT at 2x; the script
runs under SNAP_MULTI=1 (see tools/snap_all.sh).

- "dialog": the alarm editor is an Adw.Dialog. Headless Broadway never
  ticks the frame clock, so the sheet cannot animate open (the window
  paints empty); the editor's content is docked where the bottom sheet
  sits instead, over a scrim, and the main window is painted.
- "edit": the same dock, for an existing alarm with a picked sound
  (title "Edit alarm", the "Use default sound" action).
- "analog": the Clock tab in the analog style.
- "timer-error": the Timers tab after Start with minutes out of range.
- "ring": the ring card is its own window (a full-output layer surface
  on the phone). It gets the snap canvas size, and a WARNING (which
  snap_all.sh treats as a phone-fit failure) when its content refuses
  to fit that canvas.
"""
import os
import sys

from gi.repository import Adw, Graphene

target = os.environ.get("SHOT_TARGET", "")
out = os.environ["SHOT_OUT"]
canvas = (int(os.environ.get("SNAP_W", "360")),
          int(os.environ.get("SNAP_H", "720")))
cw = app._clock_window  # noqa: F821 -- provided by scripts/snap.py

attempts = [0]


def _retry():
    if attempts[0] >= 40:
        os._exit(3)
    # Off the 500 ms / 1 s tick phase: a retry that lands right after a
    # clock or timer tick finds the render node invalidated every time.
    GLib.timeout_add(130, _shot)  # noqa: F821 -- provided by snap.py
    return False


def _shot():
    attempts[0] += 1
    if target in ("analog", "timer-error"):
        try:
            snap_render(out)  # noqa: F821 -- provided by snap.py
        except TypeError:
            return _retry()
        os._exit(0)
    if target in ("dialog", "edit"):
        if cw._editor is None:
            return _retry()
        try:
            snap_render(out)  # noqa: F821 -- provided by snap.py
        except TypeError:
            # Empty first frame under headless Broadway (no render node):
            # paint again on a later main-loop turn.
            return _retry()
        os._exit(0)
    ring = cw._ring
    widget = ring.win if ring is not None else None
    if widget is None:
        return _retry()
    width, height = widget.get_width(), widget.get_height()
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
    if node is None:
        # Headless Broadway sometimes paints an empty first frame: retry
        # instead of dying in render_texture (which would leave the shot
        # hanging until snap.py's multi-shot timeout).
        return _retry()
    if (width, height) != canvas:
        print(f"snap: WARNING ring card is {width}x{height}, not "
              f"{canvas[0]}x{canvas[1]}: content does not fit a phone screen",
              file=sys.stderr, flush=True)
    rect = Graphene.Rect().init(0, 0, width * 2, height * 2)
    renderer.render_texture(node, rect).save_to_png(out)
    print("snap: wrote", out, flush=True)
    os._exit(0)


if target in ("dialog", "edit"):
    # Headless frame clocks barely tick: with animations on, the dialog's
    # open transition never leaves its first (invisible) frame.
    Gtk.Settings.get_default().set_property(  # noqa: F821
        "gtk-enable-animations", False)
    content = cw.win.get_content()
    cw.win.set_content(None)
    overlay = Gtk.Overlay()  # noqa: F821
    overlay.set_child(content)
    cw.win.set_content(overlay)
    css = Gtk.CssProvider()  # noqa: F821
    css.load_from_data(b"""
.snap-scrim { background-color: alpha(black, 0.55); }
.snap-dock { background-color: @dialog_bg_color; border-radius: 12px 12px 0 0; }
""")
    Gtk.StyleContext.add_provider_for_display(  # noqa: F821
        cw.win.get_display(), css, 900)

    def _dock(dialog, _parent=None):
        child = dialog.get_child()
        dialog.set_child(None)
        scrim = Gtk.Box()  # noqa: F821
        scrim.add_css_class("snap-scrim")
        child.add_css_class("snap-dock")
        child.set_valign(Gtk.Align.END)  # noqa: F821
        overlay.add_overlay(scrim)
        overlay.add_overlay(child)

    Adw.Dialog.present = _dock
    cw.show_tab("alarms")
    if target == "edit":
        cw.open_editor({"id": 1, "hour": 7, "minute": 0, "days_mask": 31,
                        "label": "Work", "enabled": True, "vibrate": True,
                        "sound_uri": "file:///home/me/Music/Morning%20Bell.ogg"})
    else:
        cw.open_editor(None)
elif target == "analog":
    cw.toggle_clock_style()
    cw.show_tab("clock")
elif target == "timer-error":
    cw.show_tab("timers")
    cw.timers._custom.set_text("0")
    cw.timers._start_custom()
elif cw._ring is not None:
    # On the phone the layer surface fills the output; give the headless
    # window the snap canvas so the shot shows that layout.
    cw._ring.win.unfullscreen()
    cw._ring.win.set_default_size(*canvas)
    cw._ring.win.set_size_request(*canvas)
GLib.timeout_add(1000, _shot)  # noqa: F821
