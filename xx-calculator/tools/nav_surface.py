"""snap.py --script for the overflow menu and the licenses pages.

CALC_SURFACE=menu opens the overflow menu and paints the popover (its
own surface) straight to SURFACE_OUT; snap.py still writes the window to
--out. CALC_SURFACE=licenses opens the licenses page and license the
first license text; both live in the window, so snap.py paints them.
"""

import os

SURFACE = os.environ.get("CALC_SURFACE", "")


def snap_widget(widget, path: str) -> bool:
    import gi

    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene, Gtk

    width, height = widget.get_width(), widget.get_height()
    if width <= 0 or height <= 0:
        return True  # not allocated yet; retry on the next tick
    snap = Gtk.Snapshot()
    snap.scale(2, 2)
    Gtk.WidgetPaintable.new(widget).snapshot(snap, width, height)
    node = snap.to_node()
    if node is None:
        return True
    rect = Graphene.Rect().init(0, 0, width * 2, height * 2)
    native = widget.get_native()
    if native is None:
        return True
    texture = native.get_renderer().render_texture(node, rect)
    if texture is None:
        return True
    texture.save_to_png(path)
    print("snap: wrote", path, flush=True)
    return False


calc = app._calc_window  # noqa: F821

if SURFACE == "menu":
    calc._on_menu(calc.menu_btn)
    out = os.environ["SURFACE_OUT"]
    GLib.timeout_add(200, lambda: snap_widget(calc._menu_popover, out))  # noqa: F821
elif SURFACE == "licenses":
    calc._on_licenses()
elif SURFACE == "license":
    from xx_calculator.main import license_entries

    calc._on_licenses()
    calc._open_license(license_entries()[0])
