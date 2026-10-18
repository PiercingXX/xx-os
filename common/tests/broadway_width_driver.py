"""Broadway width driver: every widget helper with very long strings.

Run as a subprocess by tests/test_width.py with GDK_BACKEND=broadway.
Builds each helper with ~280-char strings inside a 360 px-wide window
and fails (exit != 0) when the window allocation grows past 360 wide,
on any exception, or on any GLib/Gtk/Gdk CRITICAL.
"""

import sys
import traceback

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib, Gtk

import xx_ui
from xx_ui import widgets

criticals = []
LONG = ('Lorem ipsum dolor sit amet consectetur adipiscing elit sed '
        'do eiusmod tempor incididunt ut labore et dolore magna ' * 3).strip()


def _log_handler(domain, level, message, _data):
    if level & GLib.LogLevelFlags.LEVEL_CRITICAL:
        criticals.append(f'{domain}: {message}')
    return True


GLib.log_set_handler('Gtk', GLib.LogLevelFlags.LEVEL_MASK, _log_handler, None)
GLib.log_set_handler('Gdk', GLib.LogLevelFlags.LEVEL_MASK, _log_handler, None)
GLib.log_set_handler('GLib', GLib.LogLevelFlags.LEVEL_MASK, _log_handler, None)


def _fail(text):
    sys.stderr.write(text + '\n')
    raise SystemExit(1)


def main() -> None:
    xx_ui.apply()

    rows = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    rows.append(widgets.page_header(LONG, LONG, actions=[('OK', None)],
                                    overflow_cb=lambda: None,
                                    icon_name='list-add-symbolic'))
    rows.append(widgets.compact_header(None, LONG, LONG,
                                       'call-start-symbolic', None))
    rows.append(widgets.list_row(
        LONG, LONG, '21:44', leading=widgets.avatar('AL', 40),
        trailing=widgets.make_switch(True)))
    rows.append(widgets.list_row(
        LONG, LONG, '21:44', trailing=widgets.text_button('Edit')))
    rows.append(widgets.list_row('Ada', 'Overdue', LONG, last=True))
    rows.append(widgets.chip_bar([(LONG, True, None), (LONG, False, None)]))
    rows.append(widgets.segmented([(LONG, True, None), (LONG, False, None)]))
    rows.append(widgets.bottom_tabs([('', LONG, True, None),
                                     ('', LONG, False, None),
                                     ('', LONG, False, None),
                                     ('', LONG, False, None)]))
    rows.append(widgets.search_bar(LONG))
    rows.append(widgets.search_bar(LONG, boxed=True))
    rows.append(widgets.outline_button(LONG))
    rows.append(widgets.primary_button(LONG))
    rows.append(widgets.danger_button(LONG))
    rows.append(widgets.text_button(LONG))
    rows.append(widgets.caps_button(LONG))
    rows.append(widgets.icon_button('edit-clear-symbolic'))
    rows.append(widgets.call_button(LONG))
    rows.append(widgets.fab())
    rows.append(widgets.field(LONG, LONG))
    rows.append(widgets.field(LONG, LONG, placeholder=True))
    rows.append(widgets.section_label(LONG))
    card = widgets.card()
    card.append(widgets.label(LONG, 'xx-title'))
    card.append(widgets.label(LONG, 'xx-secondary'))
    card.append(widgets.segmented([(LONG, True, None), (LONG, False, None)]))
    rows.append(card)
    rows.append(widgets.setting_row(LONG, LONG, widgets.make_switch(True)))
    rows.append(widgets.setting_row(LONG, LONG, widgets.text_button(LONG)))
    rows.append(widgets.setting_row(LONG, LONG, wrap=False))
    rows.append(widgets.list_row(LONG, LONG, LONG, wrap=True,
                                 trailing=widgets.make_switch(True)))
    rows.append(widgets.compact_header(
        None, LONG, LONG, 'call-start-symbolic', None,
        second_icon_name='system-search-symbolic'))
    rows.append(widgets.button_pair(widgets.primary_button(LONG),
                                    widgets.outline_button(LONG)))
    rows.append(widgets.glyph_tile('xx-none-symbolic', 'AIRPLANE', LONG))
    rows.append(widgets.shrink_to_fit(widgets.label('Contacts',
                                                    'xx-display')))
    rows.append(widgets.setting_row(LONG, LONG, widgets.make_switch(False),
                                    last=True))
    rows.append(widgets.list_row(LONG, LONG, trailing=widgets.chevron(),
                                 last=True))
    rows.append(widgets.empty_state(LONG, LONG))
    keys = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    for digit, sub in (('1', 'VM'), ('2', 'ABCDEF')):
        keys.append(widgets.keypad_key(digit, sub))
    rows.append(keys)
    rows.append(widgets.call_action(LONG, active=True))
    rows.append(widgets.status_tile(LONG, LONG, active=True))
    rows.append(widgets.glyph_tile('xx-none-symbolic', LONG, LONG, active=True))
    rows.append(widgets.end_call_button())
    rows.append(widgets.bubble(LONG, outgoing=True))
    rows.append(widgets.bubble(LONG, outgoing=False))
    rows.append(widgets.day_separator(LONG))
    dialog = widgets.dialog_box(LONG)
    dialog.append(widgets.menu_row(LONG))
    dialog.append(widgets.text_entry(LONG))
    dialog.append(widgets.deferred_entry(LONG))
    dialog.append(widgets.wrap_label(LONG, 'xx-body'))
    dialog.append(widgets.wrap_label(LONG))
    dialog.append(widgets.fit_text(Gtk.Label(), LONG, 12))
    rows.append(dialog)
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    body.append(widgets.label(LONG, 'xx-title'))
    body.append(widgets.label(LONG, 'xx-body'))
    rows.append(widgets.page(
        widgets.page_header(LONG, LONG),
        body,
        widgets.bottom_tabs([('', LONG, True, None),
                             ('', LONG, False, None),
                             ('', LONG, False, None),
                             ('', LONG, False, None)]),
        widgets.fab()))

    window = Gtk.Window()
    window.set_default_size(360, 720)
    window.set_child(rows)
    window.present()

    loop = GLib.MainLoop()

    def _check():
        if criticals:
            _fail('CRITICALS:\n' + '\n'.join(criticals))
        width = window.get_width()
        if width != 360:
            _fail(f'window width is {width}, not 360')
        loop.quit()
        return False

    GLib.timeout_add(1500, _check)
    GLib.timeout_add(15000, lambda: (_fail('loop stall'), False)[1])
    loop.run()
    print('width ok 360')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
