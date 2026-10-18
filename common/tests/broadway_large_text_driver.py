"""Broadway driver: at text scale 1.5, long tab labels, a shrinking page
title, wrapping rows and the button pair keep a 320 px window 320 wide.

Run as a subprocess by tests/test_width.py with GDK_BACKEND=broadway and
a config whose text_size_scale is 1.5.
"""

import sys
import traceback

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib, Gtk, Pango

import xx_ui
from xx_ui import widgets

WIDTH = 320


def _fail(text):
    sys.stderr.write(text + '\n')
    raise SystemExit(1)


def _labels(widget):
    if isinstance(widget, Gtk.Label):
        yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _labels(child)
        child = child.get_next_sibling()


def main() -> None:
    active = xx_ui.apply()
    if active.scale != 1.5:
        _fail(f'config scale is {active.scale}, not 1.5')
    rows = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    header = widgets.page_header('Schedule', 'October 2026',
                                 actions=[('Today', None)],
                                 icon_name='system-search-symbolic',
                                 shrink_title=True)
    rows.append(header)
    rows.append(widgets.compact_header(None, 'Network & internet'))
    rows.append(widgets.list_row('Body measurements',
                                 'Weight, height and body fat',
                                 trailing=widgets.chevron()))
    rows.append(widgets.setting_row('Open-source licenses', '9 components',
                                    widgets.make_switch(True)))
    rows.append(widgets.button_pair(widgets.primary_button('Copy code'),
                                    widgets.outline_button('Next code')))
    grid = Gtk.Grid(column_spacing=8, column_homogeneous=True)
    for column, text in enumerate(('Airplane', 'Location', 'Captions',
                                   'Hotspot')):
        tile = widgets.glyph_tile('xx-none-symbolic', text, 'Off')
        tile.set_hexpand(True)
        grid.attach(tile, column, 0, 1, 1)
    rows.append(grid)
    body = Gtk.ScrolledWindow()
    body.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    body.set_child(rows)
    body.set_vexpand(True)
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.append(body)
    tabs = widgets.bottom_tabs([('', 'Schedule', True, None),
                                ('', 'Calendar', False, None),
                                ('', 'Accounts', False, None),
                                ('', 'Settings', False, None)])
    root.append(tabs)

    window = Gtk.Window()
    window.set_default_size(WIDTH, 640)
    window.set_size_request(WIDTH, 640)
    window.set_child(root)
    window.present()
    loop = GLib.MainLoop()

    def _check():
        width = window.get_width()
        if width != WIDTH:
            _fail(f'window width is {width}, not {WIDTH}')
        for label in _labels(root):
            if label.get_ellipsize() != Pango.EllipsizeMode.NONE and \
                    label.get_layout().is_ellipsized():
                _fail(f'"{label.get_text()}" is cut at 1.5x / {WIDTH} px')
        loop.quit()
        return False

    GLib.timeout_add(1500, _check)
    GLib.timeout_add(15000, lambda: (_fail('loop stall'), False)[1])
    loop.run()
    print(f'large text ok {WIDTH}')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
