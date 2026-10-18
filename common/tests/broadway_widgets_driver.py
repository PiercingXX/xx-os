"""Broadway driver: apply() + build every widget helper, pump the loop.

Run as a subprocess by tests/test_broadway.py with GDK_BACKEND=broadway.
Fails (exit != 0) on any exception or any GLib/Gtk/Gdk CRITICAL.
"""

import sys
import traceback

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')

from gi.repository import GLib, Graphene, Gtk, Pango

import xx_ui
from xx_ui import widgets

criticals = []


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


def _walk_buttons(widget):
    if isinstance(widget, Gtk.Button):
        yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk_buttons(child)
        child = child.get_next_sibling()


def _walk_images(widget):
    if isinstance(widget, Gtk.Image):
        yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk_images(child)
        child = child.get_next_sibling()


def main() -> None:
    switches = []
    seen = []
    xx_ui.on_change(seen.append)
    first = xx_ui.apply()
    second = xx_ui.apply()
    assert first == second, 'apply() is not idempotent'
    assert xx_ui.reload() == first, 'reload() diverged'
    assert seen and seen[-1] == first, 'on_change missed reload'

    rows = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    rows.append(widgets.page_header('Inbox', '9 contacts',
                                    [('New', None)], overflow_cb=None,
                                    icon_name='list-add-symbolic'))
    rows.append(widgets.page_header('Calls', '2 missed',
                                    [('Edit', None)],
                                    overflow_cb=lambda: None))
    rows.append(widgets.compact_header(None, 'Mara Voss', 'Mobile',
                                       'call-start-symbolic', None))
    two_icon = widgets.compact_header(
        None, 'Thread', 'sub', 'call-start-symbolic', None,
        second_icon_name='system-search-symbolic')
    assert len([b for b in _walk_buttons(two_icon)]) == 3, \
        'compact header needs back + two icons'
    badge = widgets.count_badge('3')
    assert badge.get_text() == '3'
    assert 'xx-count' in badge.get_css_classes()
    rows.append(badge)
    for index, name in enumerate(('Ada Lovelace', 'Grace Hopper')):
        rows.append(widgets.list_row(
            name, '+1 555-010-0142', '21:44',
            leading=widgets.avatar('AL', 40),
            trailing=widgets.text_button('Edit'),
            last=index == 1))
    rows.append(widgets.chip_bar([('All', True, None),
                                  ('Missed', False, None)]))
    rows.append(widgets.segmented([('Enforce', True, None),
                                   ('Observe', False, None)]))
    rows.append(widgets.bottom_tabs([('I', 'Inbox', True, None),
                                     ('K', 'Keypad', False, None),
                                     ('P', 'People', False, None),
                                     ('R', 'Rules', False, None)]))
    rows.append(widgets.search_bar())
    rows.append(widgets.search_bar(boxed=True))
    buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    buttons.append(widgets.outline_button('Call'))
    buttons.append(widgets.primary_button('Save'))
    buttons.append(widgets.danger_button('End call'))
    buttons.append(widgets.text_button('New'))
    buttons.append(widgets.caps_button('Save'))
    buttons.append(widgets.icon_button('edit-clear-symbolic'))
    buttons.append(widgets.call_button('Call'))
    rows.append(buttons)
    rows.append(widgets.fab())
    rows.append(widgets.field('Phone', '+1 555-010-0142'))
    rows.append(widgets.field('Note', 'Add a note', placeholder=True))
    rows.append(widgets.section_label('Who gets through'))
    card = widgets.card()
    card.append(widgets.label('Expecting a call', 'xx-title'))
    rows.append(card)
    rows.append(widgets.setting_row('Repeat caller rings',
                                    'Second call gets through',
                                    widgets.make_switch(True), last=True))
    rows.append(widgets.list_row('Ada Lovelace', '+1 555-010-0142',
                                 trailing=widgets.chevron(), last=True))
    rows.append(widgets.empty_state('Nothing scheduled.'))
    keys = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    for digit, sub in (('1', ''), ('2', 'ABC')):
        keys.append(widgets.keypad_key(digit, sub))
    rows.append(keys)
    rows.append(widgets.call_action('Speaker', active=True))
    rows.append(widgets.status_tile('WiFi', 'HomeNet', active=True,
                                    on_toggle=lambda _b: None))
    rows.append(widgets.status_tile('Mute'))
    tile = widgets.glyph_tile('xx-none-symbolic', 'Tailnet', 'Sign in',
                              on_toggle=lambda _b: None)
    tile.add_css_class('attention')
    assert widgets.glyph_tile_state(tile).get_text() == 'Sign in'
    rows.append(tile)
    airplane = widgets.glyph_tile('airplane-mode-symbolic', 'Airplane')
    holder = airplane.get_child().get_last_child().get_first_child()
    name = holder.get_first_child()
    assert isinstance(name, Gtk.Label) and name.get_text() == 'AIRPLANE'
    assert name.has_css_class('long'), 'an 8-char tile label tracks tighter'
    assert name.get_ellipsize() == Pango.EllipsizeMode.NONE, \
        'a tile word scales down instead of ellipsizing'
    rows.append(airplane)
    copy = widgets.primary_button('Copy code')
    nxt = widgets.outline_button('Next code')
    pair = widgets.button_pair(copy, nxt)
    assert isinstance(pair, Gtk.FlowBox)
    assert pair.get_max_children_per_line() == 2
    widgets.set_slot_visible(nxt, False)
    assert not nxt.get_parent().get_visible()
    assert pair.get_max_children_per_line() == 1, \
        'a hidden pair member left its column behind'
    widgets.set_slot_visible(nxt, True)
    assert pair.get_max_children_per_line() == 2
    rows.append(pair)
    setting = widgets.setting_row('Open-source licenses', '9 components')
    for line in (setting.get_first_child().get_first_child(),
                 setting.get_first_child().get_last_child()):
        assert line.get_wrap(), 'setting rows wrap by default'
        assert line.get_ellipsize() == Pango.EllipsizeMode.NONE
    rows.append(setting)
    target = widgets.make_switch(True)
    assert isinstance(target, Gtk.Switch)
    rows.append(widgets.setting_row('Gradual volume', 'Fades in', target))
    switches.append(target)
    rows.append(widgets.end_call_button())
    rows.append(widgets.bubble('yeah. 9 at the laundromat', outgoing=True))
    rows.append(widgets.bubble('still on for tonight?', outgoing=False))
    rows.append(widgets.day_separator('Today'))
    dialog = widgets.dialog_box('Delete thread?')
    dialog.append(widgets.menu_row('Delete'))
    dialog.append(widgets.text_entry('Name'))
    rows.append(dialog)
    rows.append(widgets.page(
        widgets.page_header('Preview', '1 thread'),
        widgets.label('Body copy', 'xx-body'),
        widgets.bottom_tabs([('', 'Inbox', True, None),
                             ('', 'Keypad', False, None),
                             ('', 'People', False, None),
                             ('', 'Rules', False, None)]),
        widgets.fab()))

    # The About block's licenses row carries no decorative chevron.
    about = widgets.about_content(
        'TxxT', '1.0', widgets.standard_license_entries('TxxT'),
        lambda: None)
    rows.append(about)
    licenses_rows = [b for b in _walk_buttons(about)
                     if b.has_css_class('xx-row')]
    assert len(licenses_rows) == 1, 'About lost its licenses row'
    assert not list(_walk_images(licenses_rows[0])), \
        'About licenses row shows an arrow'

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_child(rows)
    window = Gtk.Window()
    window.set_default_size(360, 720)
    window.set_child(scrolled)
    window.present()

    loop = GLib.MainLoop()

    def _check():
        for switch in switches:
            ok, box = switch.compute_bounds(switch)
            assert ok
            # The target is 48 x 48 around the pill (46 x 26 with the
            # theme's padding): 10 px above it still hits.
            width, height = box.get_width(), box.get_height()
            middle_x = box.get_x() + width / 2
            middle_y = box.get_y() + height / 2
            assert switch.contains(middle_x - 23.5, middle_y), \
                'the switch target is not 48 px wide'
            assert switch.contains(middle_x, middle_y - 23.5), \
                'the switch target is not 48 px tall'
            assert switch.contains(middle_x, box.get_y() - 10)
            assert not switch.contains(middle_x, middle_y - 30), \
                'the switch target grew past 48 px'
            # GTK's hit test agrees: a tap 10 px above the pill, inside
            # the setting row, lands on the switch.
            row = switch.get_parent()
            point = Graphene.Point()
            point.init(middle_x, box.get_y() - 10)
            ok, in_row = switch.compute_point(row, point)
            assert ok and row.pick(in_row.x, in_row.y,
                                   Gtk.PickFlags.DEFAULT) is switch, \
                'a tap beside the pill missed the switch'
        if criticals:
            _fail('CRITICALS:\n' + '\n'.join(criticals))
        loop.quit()
        return False

    GLib.timeout_add(1000, _check)
    GLib.timeout_add(10000, lambda: (_fail('loop stall'), False)[1])
    loop.run()
    print('broadway widgets ok')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
