"""Broadway driver for the L3 round-3 focus tests.

Runs one focus scenario against a real GTK display (the parent pytest
hands it ``GDK_BACKEND=broadway`` plus ``BROADWAY_DISPLAY``) and prints
``KEY: value`` markers on stdout. Any unexpected exception exits
nonzero so the parent test fails instead of passing on empty output.
Not collected by pytest: it is executed as a subprocess by
``tests/test_l3fix3.py``.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "common"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from xx_contacts.list_ui import install_tap_to_focus  # noqa: E402
from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402


def _subtree_has_focus(widget: Gtk.Widget) -> bool:
    if widget.has_focus():
        return True
    child = widget.get_first_child()
    while child is not None:
        if _subtree_has_focus(child):
            return True
        child = child.get_next_sibling()
    return False


def _focus_chain(window: Gtk.Window) -> str:
    names = []
    widget = window.get_focus()
    while widget is not None:
        names.append(type(widget).__name__)
        widget = widget.get_parent()
    return ">".join(names)


def _tap(entry: Gtk.Widget) -> None:
    model = entry.observe_controllers()
    for index in range(model.get_n_items()):
        controller = model.get_item(index)
        if isinstance(controller, Gtk.GestureClick):
            assert (controller.get_propagation_phase()
                    == Gtk.PropagationPhase.CAPTURE)
            controller.emit("pressed", 1, 5.0, 5.0)
            return
    raise AssertionError("no GestureClick on entry")


def _scenario_startup() -> None:
    window = Gtk.Window()
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    print("STARTUP_CAN_FOCUS:", entry.get_can_focus())
    window.set_child(entry)
    window.present()
    _wait_until(window.get_mapped)
    print("STARTUP_INSIDE:", _subtree_has_focus(entry))
    window.destroy()


def _scenario_tap() -> None:
    window = Gtk.Window()
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    window.set_child(entry)
    window.present()
    _wait_until(window.get_mapped)
    print("TAP_BEFORE:", _subtree_has_focus(entry))
    _tap(entry)
    _wait_until(lambda: _subtree_has_focus(entry))
    print("TAP_CAN_FOCUS:", entry.get_can_focus())
    print("TAP_INSIDE:", _subtree_has_focus(entry))
    print("TAP_CHAIN:", _focus_chain(window))
    window.destroy()


def _scenario_relock() -> None:
    window = Gtk.Window()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    other = Gtk.Button(label="Other")
    box.append(entry)
    box.append(other)
    window.set_child(box)
    window.present()
    _wait_until(window.get_mapped)
    _tap(entry)
    _wait_until(lambda: _subtree_has_focus(entry))
    print("RELOCK_TAPPED_INSIDE:", _subtree_has_focus(entry))
    other.grab_focus()
    _wait_until(lambda: not _subtree_has_focus(entry))
    print("RELOCK_INSIDE:", _subtree_has_focus(entry))
    print("RELOCK_CAN_FOCUS:", entry.get_can_focus())
    window.destroy()


def _scenario_navpop() -> None:
    window = Gtk.Window()
    navigation = Adw.NavigationView()
    toolbar = Adw.ToolbarView()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    box.append(entry)
    toolbar.set_content(box)
    list_page = Adw.NavigationPage(child=toolbar, title="List")
    navigation.push(list_page)
    window.set_child(navigation)
    window.present()
    _wait_until(window.get_mapped)
    print("NAVPOP_STARTUP_INSIDE:", _subtree_has_focus(entry))
    _tap(entry)
    _wait_until(lambda: _subtree_has_focus(entry))
    print("NAVPOP_TAPPED_INSIDE:", _subtree_has_focus(entry))
    detail = Adw.NavigationPage(child=Gtk.Label(label="Detail"),
                                title="Detail")
    navigation.push(detail)
    _wait_until(lambda: navigation.get_visible_page() is detail)
    navigation.pop()
    _wait_until(lambda: navigation.get_visible_page() is list_page)
    _wait_until(lambda: not _subtree_has_focus(entry))
    print("NAVPOP_AFTER_POP_INSIDE:", _subtree_has_focus(entry))
    window.destroy()


_SCENARIOS = {
    "startup": _scenario_startup,
    "tap": _scenario_tap,
    "relock": _scenario_relock,
    "navpop": _scenario_navpop,
}


def main(argv: list) -> None:
    Gtk.init()
    scenario = _SCENARIOS[argv[1]]
    scenario()


main(sys.argv)
