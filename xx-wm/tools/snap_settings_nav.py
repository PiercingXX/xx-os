"""In-process shot driver for snap_settings.sh (runs as a snap.py --script).

One shell launch renders every Settings shot: the category menu, each
category subpage at its top and at every further viewport, and the
sheets and menus that rows open. Shots go through snap.py's own
renderer (SNAP_MULTI=1: ``snap_render`` per shot, the same 2x render and
the same `snap: WARNING` line when the window is not 360x720) into
$SNAP_SETTINGS_OUT/<theme>-<name>.png. Sheets draw inside the shell
window, so the render shows them as they are.

Read-only: opening rows only shows sheets and menus; nothing is
committed, and no helper (privileged) call is made.
"""
import os
import sys

from gi.repository import GLib, Graphene, Gtk  # noqa: F401

OUT = os.environ['SNAP_SETTINGS_OUT']
THEME = os.environ.get('SNAP_SETTINGS_THEME', 'theme')
ONLY = set(filter(None, os.environ.get('SNAP_SETTINGS_ONLY', '').split()))
MAX_VIEWPORTS = 7
SETTLE_MS = 900

import settings_page as sp  # noqa: E402

# Transitions and switch slides finish instantly: a headless Broadway
# display has no client pacing its frame clock.
Gtk.Settings.get_default().set_property('gtk-enable-animations', False)

# ---- sheets ---------------------------------------------------------------
# Menus and prompts are in-window sheets (action_sheet.py), so the window
# render shows them exactly as the phone does; nothing is patched.


def _clear():
    win._sheet_host.close_all()  # noqa: F821


# ---- capture ------------------------------------------------------------


def _capture(name: str, then, tries: int = 12) -> None:
    """Render one shot, then call ``then``. A headless frame can come
    back empty while GTK is mid-layout; retry it a few times."""
    try:
        snap_render(os.path.join(OUT, '%s-%s.png' % (THEME, name)))  # noqa: F821
    except Exception as err:
        if tries > 1:
            win.queue_draw()  # noqa: F821
            GLib.timeout_add(250, lambda: _capture(name, then, tries - 1))
            return
        print('snap: WARNING capture failed (%s): %s' % (name, err),
              file=sys.stderr, flush=True)
    then()


# ---- shots ----------------------------------------------------------------

def _scroll():
    return win._settings_scroll  # noqa: F821


def _row_titled(title: str):
    """First settings row (or card child) whose title reads ``title``."""
    stack = win._settings_stack  # noqa: F821
    todo = [stack.get_visible_child()]
    while todo:
        widget = todo.pop(0)
        label = getattr(widget, 'title_label', None)
        if label is not None and label.get_text() == title:
            return widget
        child = widget.get_first_child()
        while child is not None:
            todo.append(child)
            child = child.get_next_sibling()
    return None


def _activate(title: str):
    row = _row_titled(title)
    if row is None:
        print('snap: no row titled %r' % title, file=sys.stderr, flush=True)
        return
    if isinstance(row, Gtk.Button):
        row.emit('clicked')
        return
    press = row.get_first_child()
    if isinstance(press, Gtk.Button):
        press.emit('clicked')


def _open(category):
    """Open a page the way a user does: Settings, then a menu row."""
    _clear()
    win.open_settings(None)  # noqa: F821
    if category is not None:
        win._settings_page.show_category(category)  # noqa: F821


def _viewport(index: int):
    def _go():
        adj = _scroll().get_vadjustment()
        step = adj.get_page_size() - 96
        adj.set_value(min(index * step,
                          adj.get_upper() - adj.get_page_size()))
    return _go


def _at_end(index: int) -> bool:
    adj = _scroll().get_vadjustment()
    step = adj.get_page_size() - 96
    # Skip a viewport that would add under 120 px of unseen content.
    return (index - 1) * step >= adj.get_upper() - adj.get_page_size() - 120


def _section(key):
    return win._settings_page.section(key)  # noqa: F821


def _charge(custom: bool):
    """The charge-limit menu and custom sheet, shown as on a phone
    whose battery driver has the knob (this host may lack one)."""
    section = _section('charging')
    section._supported = True
    section._render_options(win)  # noqa: F821
    if custom:
        section._open_custom(win, section._row)  # noqa: F821
    else:
        section._pick(win, section._row)  # noqa: F821


SHEETS = [
    # (shot name, category, row title to tap, or a callable that opens it)
    ('sheet-apn', 'network', 'Access point (APN)'),
    ('menu-slots', 'home', 'Home Slot Count'),
    ('menu-alignment', 'home', 'Text Alignment'),
    ('menu-datetime', 'home', 'Date/Time Mode'),
    ('sheet-widgets', 'home', 'Widgets'),
    ('sheet-location', 'home', 'Manual Location'),
    ('menu-sort', 'home', 'Sort Mode'),
    ('sheet-help', 'home', 'Gestures'),
    ('menu-swipe-app', 'home', 'Swipe Left App'),
    ('menu-swipe-down', 'home', 'Swipe Down Action'),
    ('menu-swipe', 'system', 'Swipe up (short)'),
    ('menu-charge', 'battery', lambda: _charge(False)),
    ('sheet-charge', 'battery', lambda: _charge(True)),
    ('sheet-pin', 'privacy', 'Change PIN'),
    ('sheet-scan', 'privacy', 'Scan a file…'),
    ('privacy-apps', 'privacy', 'Choose an app'),
    ('sheet-medical', 'privacy', 'Medical ID'),
    ('sheet-contact', 'privacy', 'Add emergency contact…'),
    ('menu-keyboard', 'system', 'Keyboard layout'),
    ('sheet-keyboard', 'system', lambda: _section('keyboard')._open_custom(
        win, _section('keyboard')._row)),  # noqa: F821
]

steps: list = []  # (callable, shot name or None)


def _plan():
    steps.append((lambda: _open(None), 'menu'))
    for index in range(1, MAX_VIEWPORTS):
        steps.append(('viewport', 'menu', index))
    for key, _title, _sections in sp.SETTINGS_CATEGORIES:
        if ONLY and key not in ONLY:
            continue
        steps.append((lambda k=key: _open(k), '%s-0' % key))
        for index in range(1, MAX_VIEWPORTS):
            steps.append(('viewport', key, index))
    for name, category, title in SHEETS:
        if ONLY and category not in ONLY and name not in ONLY:
            continue
        steps.append((lambda c=category: _open(c), None))
        if callable(title):
            steps.append((title, name))
        else:
            steps.append((lambda t=title: _find_and_tap(t), name))


def _find_and_tap(title: str):
    row = _row_titled(title)
    if row is not None:
        adj = _scroll().get_vadjustment()
        ok, point = row.compute_point(
            _scroll().get_child(), Graphene.Point().init(0, 0))
        if ok:
            adj.set_value(max(0.0, point.y - 160))
    _activate(title)


def _finish() -> None:
    try:
        snap_done()  # noqa: F821 - renders --out and exits
    except Exception:
        os._exit(0)


def _run(index: int = 0) -> bool:
    try:
        return _step(index)
    except Exception:
        import traceback
        traceback.print_exc()
        print('snap: WARNING step %d failed' % index, file=sys.stderr,
              flush=True)
        GLib.idle_add(_run, index + 1)
        return False


def _step(index: int) -> bool:
    if index >= len(steps):
        _clear()
        _open(None)
        GLib.timeout_add(SETTLE_MS, lambda: _capture('end', _finish))
        return False
    step = steps[index]
    if step[0] == 'viewport':
        _key, number = step[1], step[2]
        if _at_end(number):
            # Past the end of this page: skip its remaining viewports.
            nxt = index + 1
            while nxt < len(steps) and steps[nxt][0] == 'viewport' \
                    and steps[nxt][1] == _key:
                nxt += 1
            GLib.idle_add(_run, nxt)
            return False
        _viewport(number)()
        name = '%s-%d' % (_key, number)
    else:
        action, name = step
        action()
    if name is None:
        GLib.timeout_add(SETTLE_MS, _run, index + 1)
        return False

    def _shoot():
        _capture(name, lambda: GLib.idle_add(_run, index + 1))
        return False

    # A freshly opened page waits longer: its refresh workers report back.
    GLib.timeout_add(2200 if name.endswith('-0') else SETTLE_MS, _shoot)
    return False


_plan()
GLib.timeout_add(SETTLE_MS, _run, 0)
