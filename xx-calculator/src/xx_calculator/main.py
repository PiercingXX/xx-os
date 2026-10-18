"""xx-calculator main window: display, keypad, science panel, history.

Headless-safe import: Gtk/Adw load lazily so pure tests never need a
display. The shared xx_ui design system owns every color, so all suite
themes repaint live with no per-preset code.
"""
from __future__ import annotations

import os
import time

from .builder import ExpressionBuilder
from .engine import DIVIDE, MINUS, TIMES, AngleMode, Calculator, EvalResult
from .engine import format_grouped as format_number
from .history import HistoryStore

__all__ = ["CalcApp", "CalcWindow", "format_number", "main"]

# Font sizes in px at text-size-scale 1.0; app_css() scales them with the
# suite's text size so large text grows the display and keys too.
_APP_CSS_TEMPLATE = """
.xx-calc-expr {{ font-size: {expr}px; font-weight: 400; }}
.xx-calc-expr-s1 {{ font-size: {s1}px; }}
.xx-calc-expr-s2 {{ font-size: {s2}px; }}
.xx-calc-expr-s3 {{ font-size: {s3}px; }}
.xx-calc-expr-s4 {{ font-size: {s4}px; }}
.xx-calc-expr-s5 {{ font-size: {s5}px; }}
.xx-calc-result {{ font-size: {result}px; font-weight: 400; }}
.xx-calc-preview {{ opacity: 0.55; }}
.xx-calc-error {{ color: @error_color; }}
.xx-calc-error-light {{ color: @theme_fg_color; }}
.xx-calc-hist-title {{ font-size: {hist_title}px; font-weight: 400; }}
.xx-calc-hist-expr {{ font-size: {hist_expr}px; }}
.xx-calc-hist-result {{ font-size: {hist_result}px; font-weight: 400; }}
list.xx-calc-hist, list.xx-calc-hist row {{
  background: none; padding: 0; border-radius: 0;
}}
list.xx-calc-hist row {{ border-bottom: 1px solid @borders; }}
list.xx-calc-hist row:last-child {{ border-bottom: none; }}
button.xx-calc-key {{
  min-height: 0; min-width: 0; padding: 0;
  border-radius: 9999px; font-size: {key}px; font-weight: 400;
  background-color: @card_bg_color; color: @theme_fg_color;
  border: none; box-shadow: none;
}}
button.xx-calc-key:active {{ background-color: @theme_selected_bg_color;
  color: @theme_selected_fg_color; }}
button.xx-calc-key.xx-calc-equals {{ font-size: {equals}px; }}
button.xx-calc-chip {{
  min-height: 48px; min-width: 0; padding: 0 18px;
  border-radius: 9999px; font-size: {chip}px; font-weight: 400;
  background-color: @card_bg_color; color: @theme_fg_color;
  border: none; box-shadow: none;
}}
button.xx-calc-chip.xx-calc-inv-on {{
  background-color: @theme_fg_color; color: @theme_bg_color;
}}
button.xx-calc-bar {{
  min-width: 48px; min-height: 48px; padding: 0;
  border-radius: 9999px; color: @theme_fg_color;
  background: none; border: none; box-shadow: none;
}}
.xx-calc-landscape .xx-calc-expr {{ font-size: {land_expr}px; }}
.xx-calc-landscape .xx-calc-result {{ font-size: {land_result}px; }}
.xx-calc-landscape button.xx-calc-key {{ font-size: {land_key}px; }}
"""

_APP_CSS_PX = {
    "expr": 52, "s1": 44, "s2": 38, "s3": 32, "s4": 27, "s5": 22,
    "result": 24, "hist_title": 20, "hist_expr": 14, "hist_result": 22,
    "key": 28, "equals": 30, "chip": 16,
    "land_expr": 34, "land_result": 18, "land_key": 20,
}


def app_css(scale: float = 1.0) -> str:
    """The app stylesheet with every font size scaled (pure)."""
    return _APP_CSS_TEMPLATE.format(
        **{key: round(px * scale) for key, px in _APP_CSS_PX.items()})


APP_CSS = app_css()

# Keypad geometry (logical px). A key is a circle KEY_RATIO of its column,
# as on Android (72dp keys in ~99dp columns); a short window turns the
# keys into pills so all five rows still fit.
KEY_RATIO = 0.8
KEY_MAX = 104
KEY_MIN = 36
KEY_ROW_GAP = 10


def key_geometry(width: float, height: float | None,
                 cols: int = 4, rows: int = 5) -> tuple[int, int]:
    """(key width, key height) for a keypad allocation (pure).

    ``height`` None asks for the natural size: round keys from width.
    """
    cell = max(width, 0) / cols
    diameter = max(int(min(cell * KEY_RATIO, KEY_MAX)), KEY_MIN)
    if height is None:
        return diameter, diameter
    fit = height / rows - KEY_ROW_GAP
    if fit >= diameter - 1:
        return diameter, diameter
    key_h = max(int(fit), KEY_MIN // 2)
    key_w = int(min(cell - 8, max(diameter, key_h * 1.8)))
    return max(key_w, key_h), key_h


_KEYPAD_CLASS = None


def _keypad_class():
    """Gtk.Widget subclass laying out the keypad (built after gi loads)."""
    global _KEYPAD_CLASS
    if _KEYPAD_CLASS is not None:
        return _KEYPAD_CLASS
    from gi.repository import Gdk, Gtk

    class KeyPad(Gtk.Widget):
        """4x5 grid of round keys sized from the width (height for width)."""

        def __init__(self, cols: int = 4, rows: int = 5) -> None:
            super().__init__()
            self.cols, self.rows = cols, rows
            self._cells: list = []

        def attach(self, child, col: int, row: int, _w: int = 1,
                   _h: int = 1) -> None:
            child.set_parent(self)
            self._cells.append((child, col, row))

        def get_child_at(self, col: int, row: int):
            for child, c, r in self._cells:
                if (c, r) == (col, row):
                    return child
            return None

        def do_get_request_mode(self):
            return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

        def do_measure(self, orientation, for_size):
            if orientation == Gtk.Orientation.HORIZONTAL:
                return (self.cols * (KEY_MIN + 8),
                        self.cols * (KEY_MIN + 8), -1, -1)
            minimum = self.rows * (KEY_MIN // 2 + KEY_ROW_GAP)
            width = for_size if for_size > 0 else 344
            _w, key_h = key_geometry(width, None, self.cols, self.rows)
            natural = self.rows * (key_h + KEY_ROW_GAP)
            return minimum, max(minimum, natural), -1, -1

        def do_size_allocate(self, width, height, _baseline):
            key_w, key_h = key_geometry(width, height, self.cols, self.rows)
            cell_w = width / self.cols
            row_h = key_h + KEY_ROW_GAP
            top = max(0.0, height - self.rows * row_h)
            for child, col, row in self._cells:
                rect = Gdk.Rectangle()
                rect.x = int(col * cell_w + (cell_w - key_w) / 2)
                rect.y = int(top + row * row_h + KEY_ROW_GAP / 2)
                rect.width, rect.height = key_w, key_h
                child.size_allocate(rect, -1)

        def do_dispose(self):
            for child, _c, _r in self._cells:
                child.unparent()
            self._cells = []

    _KEYPAD_CLASS = KeyPad
    return KeyPad

APP_NAME = "xx-calculator"
# Sub-pages and where Back (the phone's back gesture, Escape) returns.
BACK_TARGETS = {"history": "calc", "licenses": "calc", "license": "licenses"}


def license_entries() -> list:
    """The suite's shared licenses table (the app's own GPL-3.0-only line
    first), minus what this app does not ship (phone numbers)."""
    import xx_ui

    return [entry for entry in xx_ui.widgets.standard_license_entries(APP_NAME)
            if entry.name != "phonenumbers"]


RAW_INSERT_TAIL_CHARS = frozenset("+{m}{t}*/{d}^(.".format(m=MINUS, t=TIMES, d=DIVIDE))

_EXPR_SIZE_CLASSES = (
    (9, None),
    (12, "xx-calc-expr-s1"),
    (15, "xx-calc-expr-s2"),
    (19, "xx-calc-expr-s3"),
    (24, "xx-calc-expr-s4"),
    (10**9, "xx-calc-expr-s5"),
)


# Expression font steps, largest first: (css class, key in _APP_CSS_PX).
_EXPR_STEPS = (
    (None, "expr"), ("xx-calc-expr-s1", "s1"), ("xx-calc-expr-s2", "s2"),
    ("xx-calc-expr-s3", "s3"), ("xx-calc-expr-s4", "s4"),
    ("xx-calc-expr-s5", "s5"),
)
# A shown number shrinks this far (px at scale 1.0) before it may scroll.
FIT_FLOOR_PX = 12


def pick_fit(widths: list[int], available: int) -> int:
    """Index of the first (largest) font step whose text width fits
    ``available`` px; the last step when none does (Android's
    autoShrinkExpression). Pure."""
    for index, width in enumerate(widths):
        if width <= available:
            return index
    return max(len(widths) - 1, 0)


def shrink_to_fit(px: float, width: float, available: float,
                  floor: float) -> float | None:
    """Font px that makes a ``width``-px text at ``px`` fit ``available``,
    never under ``floor``; None when it already fits. Pure.

    A number on screen is never cut short: "1.2676…" would read as a
    different value than 1.26765060022823E30."""
    if available <= 0 or width <= available:
        return None
    return max(floor, int(px * available / width))


def _require_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: F401

    return Adw, Gdk, Gio, GLib, Gtk, Pango


def error_text(kind) -> str:
    """User-visible error string per SPEC D5 (pure, headless-testable)."""
    from .engine import ErrorKind

    if kind is ErrorKind.DIVIDE_BY_ZERO:
        return "Can't divide by 0"
    if kind is ErrorKind.SYNTAX:
        return "Invalid expression"
    return "Invalid input"


def _mix(foreground: str, background: str, amount: float) -> str:
    """Mix fg into bg by amount (0..1); ground-derived key fills (pure)."""
    import re

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", foreground or ""):
        return background
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", background or ""):
        return background
    mixed = []
    for index in (1, 3, 5):
        low = int(background[index:index + 2], 16)
        high = int(foreground[index:index + 2], 16)
        mixed.append(round(low + (high - low) * amount))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


_PASTE_LETTERS = frozenset("sincotalg")


def sanitize_paste(text: str) -> str:
    """Keep only display-form characters from clipboard text (pure)."""
    mapped = text.replace("*", TIMES).replace("/", DIVIDE)
    mapped = mapped.replace("-", MINUS).replace("x", TIMES)
    out = []
    for char in mapped:
        if char.isdigit() or char in ".()%!^πeE√×−÷+-()":
            out.append(char)
        elif char in _PASTE_LETTERS:
            out.append(char)
    return "".join(out)


def _copy_text(text: str) -> bool:
    try:
        from gi.repository import Gdk
    except Exception:
        return False
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return False
        display.get_clipboard().set_text(text)
        return True
    except Exception:
        return False


class CalcWindow:
    """Main window; built inside an Adw.ApplicationWindow."""

    def __init__(self, app, store: HistoryStore | None = None) -> None:
        Adw, Gdk, Gio, GLib, Gtk, Pango = _require_gtk()
        self._Adw, self._Gdk, self._Gio = Adw, Gdk, Gio
        self._GLib, self._Gtk, self._Pango = GLib, Gtk, Pango
        self.app = app
        self.store = store if store is not None else HistoryStore()
        self.builder = ExpressionBuilder()
        self._bright = False
        self._bright_text = ""
        self._error_shown = False
        self._rad_mode = False
        self._inv_active = False
        self._science_expanded = False
        self._science_known = False
        self._landscape = False
        self._lp_clear = False

        import xx_ui

        self._xx_ui = xx_ui
        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-calculator")
        self.win.set_default_size(360, 720)
        # Adw windows floor at 360 px wide by default: a 320 px
        # screen would cut the app off.
        self.win.set_size_request(320, 200)
        xx_ui.apply(self.win)
        self._load_app_css()
        self._key_provider = None
        self._install_key_colors(None)
        try:
            self._theme_unsub = xx_ui.on_change(self._on_suite_theme)
        except Exception:
            self._theme_unsub = None

        self._build()
        self._on_suite_theme(None)
        self._render_expression()
        self._refresh_preview()

    # -- theme ------------------------------------------------------
    def _load_app_css(self, active=None) -> None:
        try:
            from xx_ui import theme as theme_mod

            theme = active if active is not None else theme_mod.load()
            scale = float(getattr(theme, "scale", 1.0) or 1.0)
        except Exception:
            scale = 1.0
        self._scale = scale
        try:
            Gtk, Gdk = self._Gtk, self._Gdk
            if getattr(self, "_app_provider", None) is None:
                self._app_provider = Gtk.CssProvider()
                display = Gdk.Display.get_default()
                if display is not None:
                    Gtk.StyleContext.add_provider_for_display(
                        display, self._app_provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._app_provider.load_from_data(app_css(scale).encode("utf-8"))
        except Exception:
            pass

    def _install_key_colors(self, active) -> None:
        try:
            from xx_ui import theme as theme_mod

            theme = active if active is not None else theme_mod.load()
            digit = _mix(theme.foreground, theme.background, 0.16)
            misc = _mix(theme.foreground, theme.background, 0.12)
            css = ("button.xx-calc-key{background-color:%s;}"
                   "button.xx-calc-digit{background-color:%s;}"
                   "button.xx-calc-key.xx-calc-equals{background-color:%s;"
                   "color:%s;}"
                   "button.xx-calc-chip{background-color:%s;}"
                   "button.xx-calc-chip.xx-calc-inv-on{background-color:%s;"
                   "color:%s;}"
                   % (misc, digit, theme.foreground, theme.background,
                      misc, theme.foreground, theme.background))
            Gtk, Gdk = self._Gtk, self._Gdk
            if self._key_provider is None:
                self._key_provider = Gtk.CssProvider()
                display = Gdk.Display.get_default()
                if display is not None:
                    Gtk.StyleContext.add_provider_for_display(
                        display, self._key_provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._key_provider.load_from_data(css.encode("utf-8"))
        except Exception:
            pass

    def _on_suite_theme(self, _theme) -> None:
        self._load_app_css(_theme)
        self._install_key_colors(_theme)
        if getattr(self, "expr_label", None) is not None:
            self._queue_fit()
        try:
            from xx_ui import theme as theme_mod

            dark = theme_mod.load().dark if _theme is None else _theme.dark
        except Exception:
            dark = True
        try:
            if self._error_shown:
                self.result_label.remove_css_class("xx-calc-error")
                self.result_label.remove_css_class("xx-calc-error-light")
                self.result_label.add_css_class(
                    "xx-calc-error" if dark else "xx-calc-error-light")
        except Exception:
            pass

    # -- layout -----------------------------------------------------
    def _build(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        # Android order, top to bottom: the display takes the free space
        # (text sits at its bottom edge), then one bar (history, science
        # expander, overflow), the science chips, and the keypad, which
        # fills the width with round keys.
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._root = root
        self.land_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.land_left.set_vexpand(True)
        self.land_left.set_margin_end(8)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        root.append(self.stack)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.set_vexpand(True)
        self.stack.add_named(self.content, "calc")

        self.display = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.display.set_vexpand(True)
        self.display.set_valign(Gtk.Align.FILL)
        self.display.set_margin_start(24)
        self.display.set_margin_end(24)
        self.display.set_margin_top(24)
        self.display.set_margin_bottom(8)
        filler = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        filler.set_vexpand(True)
        self.display.append(filler)
        self.expr_scroll = Gtk.ScrolledWindow()
        # Android's HorizontalScrollView: a long expression scrolls
        # (no scrollbar) and stays scrolled to its end, where you type,
        # instead of ellipsizing the digits just entered.
        self.expr_scroll.set_policy(Gtk.PolicyType.EXTERNAL,
                                   Gtk.PolicyType.NEVER)
        self.expr_scroll.set_propagate_natural_height(True)
        self.expr_label = Gtk.Label(label="")
        self.expr_label.add_css_class("xx-calc-expr")
        self.expr_label.set_xalign(1.0)
        self.expr_label.set_hexpand(True)
        self.expr_scroll.set_child(self.expr_label)
        self.expr_scroll.get_hadjustment().connect(
            "changed", lambda *_: self._queue_fit())
        self.display.append(self.expr_scroll)
        self.result_label = Gtk.Label(label="")
        self.result_label.add_css_class("xx-calc-result")
        self.result_label.set_xalign(1.0)
        # Only a floor for the window width: _fit_result_line shrinks the
        # text to fit, so the ellipsis never shows on a number.
        self.result_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.result_label.set_margin_top(2)
        self.display.append(self.result_label)
        try:
            swipe = Gtk.GestureSwipe()
            swipe.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            swipe.connect("swipe", self._on_display_swipe)
            self.display.add_controller(swipe)
        except Exception:
            pass
        self.content.append(self.display)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._sci_row = bar
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        self.hist_btn = self._bar_button(
            self._icon("document-open-recent-symbolic", 24), "History")
        self.hist_btn.connect("clicked", lambda *_: self.show("history"))
        bar.append(self.hist_btn)
        self._sci_icon = self._expander_icon()
        self.sci_toggle = self._bar_button(
            self._sci_icon, "Show or hide scientific keys")
        self.sci_toggle.connect("clicked", self._on_science_toggle)
        bar.append(self.sci_toggle)
        spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        spacer.set_hexpand(True)
        bar.append(spacer)
        self.menu_btn = self._bar_button(
            self._icon("view-more-symbolic", 24), "More options")
        self.menu_btn.connect("clicked", self._on_menu)
        bar.append(self.menu_btn)
        self.content.append(bar)

        self.sci_reveal = Gtk.Revealer()
        self.sci_reveal.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.sci_reveal.set_reveal_child(False)
        # Full-bleed scroller: chips run off the screen edge like Android,
        # not into a clipped margin.
        sci_scroll = Gtk.ScrolledWindow()
        sci_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.sci_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
        self.sci_box.set_margin_start(16)
        self.sci_box.set_margin_end(16)
        self.sci_box.set_margin_top(6)
        self.sci_box.set_margin_bottom(6)
        sci_scroll.set_child(self.sci_box)
        self.sci_reveal.set_child(sci_scroll)
        self.content.append(self.sci_reveal)
        self._build_science_chips()

        self.pad = _keypad_class()()
        self.pad.set_margin_start(8)
        self.pad.set_margin_end(8)
        self.pad.set_margin_top(4)
        self.pad.set_margin_bottom(16)
        self.pad.set_valign(Gtk.Align.END)
        self._build_pad()
        self.content.append(self.pad)

        self.side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.side.set_vexpand(True)
        self._build_history_page()
        self._licenses_built = False

        self.win.set_content(root)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.win.add_controller(key)

    def _icon(self, name: str, px: int):
        Gtk = self._Gtk
        img = Gtk.Image.new_from_icon_name(name)
        img.set_pixel_size(px)
        return img

    def _bar_button(self, child, tooltip: str):
        Gtk = self._Gtk
        btn = Gtk.Button()
        btn.add_css_class("xx-calc-bar")
        btn.set_tooltip_text(tooltip)
        btn.set_child(child)
        btn.set_valign(Gtk.Align.CENTER)
        return btn

    def _expander_icon(self):
        """Android's science expander: two outward chevrons, inward when
        open. Drawn in the button's own color so every theme follows."""
        Gtk = self._Gtk
        area = Gtk.DrawingArea()
        area.set_content_width(24)
        area.set_content_height(24)
        area.set_halign(Gtk.Align.CENTER)
        area.set_valign(Gtk.Align.CENTER)

        def draw(widget, cr, width, height):
            try:
                color = widget.get_color()
                cr.set_source_rgba(color.red, color.green, color.blue,
                                   color.alpha)
            except Exception:
                cr.set_source_rgba(0.5, 0.5, 0.5, 1.0)
            cr.translate((width - 24) / 2, (height - 24) / 2)
            cr.set_line_width(2)
            cr.set_line_cap(1)   # ROUND
            cr.set_line_join(1)  # ROUND
            if self._science_expanded:
                strokes = ((5, 7.5, 9.5, 12, 5, 16.5),
                           (19, 7.5, 14.5, 12, 19, 16.5))
            else:
                strokes = ((9.5, 7.5, 5, 12, 9.5, 16.5),
                           (14.5, 7.5, 19, 12, 14.5, 16.5))
            for x0, y0, x1, y1, x2, y2 in strokes:
                cr.move_to(x0, y0)
                cr.line_to(x1, y1)
                cr.line_to(x2, y2)
                cr.stroke()

        area.set_draw_func(draw)
        return area

    def _key_button(self, text: str, callback, digit=False):
        Gtk = self._Gtk
        btn = Gtk.Button(label=text)
        btn.add_css_class("xx-calc-key")
        if digit:
            btn.add_css_class("xx-calc-digit")
        btn.connect("clicked", callback)
        return btn

    def _chip(self, label: str, callback, group_end: bool = False):
        Gtk = self._Gtk
        btn = Gtk.Button(label=label)
        btn.add_css_class("xx-calc-chip")
        btn.set_valign(Gtk.Align.CENTER)
        if group_end:
            # Android spaces the groups (mode / trig / logs / ops / consts)
            # 16dp apart, chips inside a group 8dp.
            btn.set_margin_end(8)
        btn.connect("clicked", callback)
        self.sci_box.append(btn)
        return btn

    def _build_science_chips(self) -> None:
        self.inv_btn = self._chip("INV", lambda *_: self._on_inv())
        self.deg_btn = self._chip("DEG", lambda *_: self._on_deg(), True)
        self.sin_btn = self._chip("sin", lambda *_: self._insert_fn("sin"))
        self.cos_btn = self._chip("cos", lambda *_: self._insert_fn("cos"))
        self.tan_btn = self._chip("tan", lambda *_: self._insert_fn("tan"),
                                  True)
        self.ln_btn = self._chip("ln", lambda *_: self._insert_fn("ln"))
        self.log_btn = self._chip("log", lambda *_: self._insert_fn("log"),
                                  True)
        self._chip("√", lambda *_: self._fresh_fn("√("))
        self._chip("π", lambda *_: self._fresh_const("π"))
        self._chip("^", lambda *_: self._press_raw("^"))
        self._chip("!", lambda *_: self._press_raw("!"), True)
        self._chip("e", lambda *_: self._fresh_const("e"))
        self._chip("(", lambda *_: self._fresh_paren("open"))
        self._chip(")", lambda *_: self._fresh_paren("close"))
        self._refresh_science_labels()

    def _build_pad(self) -> None:
        Gtk = self._Gtk
        rows = (
            (("AC", self._on_ac, False), ("()", self._on_parens, False),
             ("%", self._on_percent, False), (DIVIDE, self._on_divide, False)),
            (("7", self._digit("7"), True), ("8", self._digit("8"), True),
             ("9", self._digit("9"), True), (TIMES, self._on_times, False)),
            (("4", self._digit("4"), True), ("5", self._digit("5"), True),
             ("6", self._digit("6"), True), (MINUS, self._on_minus, False)),
            (("1", self._digit("1"), True), ("2", self._digit("2"), True),
             ("3", self._digit("3"), True), ("+", self._on_plus, False)),
            (("0", self._digit("0"), True), (".", self._on_dot, True),
             (None, None, False), ("=", self._on_equals, False)),
        )
        for row, keys in enumerate(rows):
            for col, (label, callback, digit) in enumerate(keys):
                if label is None:
                    back = Gtk.Button()
                    back.add_css_class("xx-calc-key")
                    back.set_tooltip_text("Delete")
                    back.set_child(self._icon("edit-clear-symbolic", 24))
                    back.connect("clicked", self._on_backspace_clicked)
                    try:
                        hold = Gtk.GestureLongPress()
                        hold.set_propagation_phase(
                            Gtk.PropagationPhase.CAPTURE)
                        hold.connect("pressed", self._on_backspace_hold)
                        back.add_controller(hold)
                    except Exception:
                        pass
                    self.pad.attach(back, col, row, 1, 1)
                    continue
                btn = self._key_button(label, callback, digit=digit)
                if label == "=":
                    btn.add_css_class("xx-calc-equals")
                self.pad.attach(btn, col, row, 1, 1)

    def _sub_header(self, title: str, back_to: str):
        """Android's sheet title: back, then a Space Mono title."""
        Gtk = self._Gtk
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_margin_start(8)
        bar.set_margin_end(24)
        bar.set_margin_top(8)
        back = self._bar_button(self._icon("go-previous-symbolic", 24),
                                "Back")
        back.connect("clicked", lambda *_: self.show(back_to))
        bar.append(back)
        label = Gtk.Label(label=title)
        label.add_css_class("xx-calc-hist-title")
        label.add_css_class("xx-display-face")
        label.set_hexpand(True)
        label.set_xalign(0.0)
        label.set_valign(Gtk.Align.CENTER)
        # Large text on a narrow phone wraps the title, never cuts it.
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        bar.append(label)
        return bar, label

    def _sub_scroll(self, child):
        Gtk = self._Gtk
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_margin_start(24)
        scroll.set_margin_end(24)
        scroll.set_margin_top(8)
        scroll.set_child(child)
        return scroll

    def _build_history_page(self) -> None:
        # Android's history sheet: a Space Mono "History" title over the
        # entries, hairlines between them. Clearing lives in the overflow
        # menu (one entry point); an empty history is just empty.
        Gtk = self._Gtk
        self.side.append(self._sub_header("History", "calc")[0])
        self.hist_list = Gtk.ListBox()
        self.hist_list.add_css_class("xx-calc-hist")
        self.hist_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.hist_list.set_valign(Gtk.Align.START)
        self.hist_list.connect("row-activated", self._on_history_row)
        self.side.append(self._sub_scroll(self.hist_list))
        self.stack.add_named(self.side, "history")

    def _build_licenses_pages(self) -> None:
        # The suite's licenses page, in the History page's dress: one
        # row per component (name over its SPDX id); a tap shows the
        # license text. No dialog, no explanatory paragraph.
        Gtk = self._Gtk
        import xx_ui

        widgets = xx_ui.widgets
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self._sub_header("Open-source licenses", "calc")[0])
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        entries = license_entries()
        for index, entry in enumerate(entries):
            body.append(widgets.list_row(
                entry.name, entry.spdx, last=index == len(entries) - 1,
                on_activate=lambda item=entry: self._open_license(item)))
        page.append(self._sub_scroll(body))
        self.stack.add_named(page, "licenses")

        text_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar, self._license_title = self._sub_header("", "licenses")
        text_page.append(bar)
        self._license_text = Gtk.Label(label="")
        self._license_text.add_css_class("xx-secondary")
        self._license_text.set_xalign(0.0)
        self._license_text.set_yalign(0.0)
        self._license_text.set_wrap(True)
        self._license_text.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        self._license_text.set_selectable(True)
        self._license_text.set_margin_bottom(24)
        text_page.append(self._sub_scroll(self._license_text))
        self.stack.add_named(text_page, "license")

    def _open_license(self, entry) -> None:
        import xx_ui

        self._license_title.set_text(entry.name)
        self._license_text.set_text(xx_ui.widgets.license_text(entry))
        self.show("license")

    # -- keys -------------------------------------------------------
    def _digit(self, char: str):
        def on_press(*_args) -> None:
            self._fresh_if_post_equals()
            self.builder.digit(char)
            self._on_edited()
        return on_press

    def _on_dot(self, *_args) -> None:
        self._fresh_if_post_equals()
        self.builder.decimal()
        self._on_edited()

    def _on_plus(self, *_args) -> None:
        self.builder.operator("+")
        self._on_edited()

    def _on_minus(self, *_args) -> None:
        self.builder.operator("-")
        self._on_edited()

    def _on_times(self, *_args) -> None:
        self.builder.operator("*")
        self._on_edited()

    def _on_divide(self, *_args) -> None:
        self.builder.operator("/")
        self._on_edited()

    def _on_percent(self, *_args) -> None:
        self.builder.percent()
        self._on_edited()

    def _on_ac(self, *_args) -> None:
        self.builder.clear()
        self._on_edited()

    def _on_parens(self, *_args) -> None:
        self._fresh_if_post_equals()
        self.builder.paren()
        self._on_edited()

    def _fresh_fn(self, token: str) -> None:
        self._fresh_if_post_equals()
        self.builder.function(token)
        self._on_edited()

    def _fresh_const(self, char: str) -> None:
        self._fresh_if_post_equals()
        self.builder.constant(char)
        self._on_edited()

    def _fresh_paren(self, which: str) -> None:
        self._fresh_if_post_equals()
        if which == "open":
            self.builder.open_paren()
        else:
            self.builder.close_paren()
        self._on_edited()

    def _press_raw(self, token: str) -> None:
        self.builder.raw(token)
        self._on_edited()

    def _insert_fn(self, name: str) -> None:
        self._fresh_if_post_equals()
        if name == "sin":
            token = "asin(" if self._inv_active else "sin("
        elif name == "cos":
            token = "acos(" if self._inv_active else "cos("
        elif name == "tan":
            token = "atan(" if self._inv_active else "tan("
        elif name == "ln":
            token = "e^(" if self._inv_active else "ln("
        else:
            token = "10^(" if self._inv_active else "log("
        self.builder.function(token)
        self._on_edited()

    def _on_inv(self, *_args) -> None:
        self._inv_active = not self._inv_active
        self._refresh_science_labels()

    def _on_deg(self, *_args) -> None:
        self._rad_mode = not self._rad_mode
        self._refresh_science_labels()
        self._refresh_preview()

    def _on_backspace_clicked(self, *_args) -> None:
        if self._lp_clear:
            self._lp_clear = False
            return
        self.builder.backspace()
        self._on_edited()

    def _on_backspace_hold(self, *_args) -> None:
        self._lp_clear = True
        self.builder.clear()
        self._on_edited()

    def _on_science_toggle(self, *_args) -> None:
        self._apply_science_expanded(not self._science_expanded)

    def _on_display_swipe(self, _gesture, vel_x: float, vel_y: float) -> None:
        if vel_y > 0 and abs(vel_y) > abs(vel_x) * 2:
            self.show("history")

    def _fresh_if_post_equals(self) -> None:
        if self._bright:
            self.builder.clear()
            self._bright = False
            self._bright_text = ""

    # -- evaluation -------------------------------------------------
    def _angle_mode(self) -> AngleMode:
        return AngleMode.RAD if self._rad_mode else AngleMode.DEG

    def _on_edited(self) -> None:
        self._bright = False
        self._bright_text = ""
        self._error_shown = False
        self._render_expression()
        self._refresh_preview()

    def _render_expression(self) -> None:
        text = self.builder.expression
        if self._bright and self._bright_text:
            # The one result line after "=" carries the grouped number
            # (the preview slot under it is blank now).
            text = format_number(self._bright_text)
        try:
            self.expr_label.set_text(text)
        except Exception:
            pass
        self._fit_display()

    def _queue_fit(self) -> None:
        # The width is known (or changed) only after allocation: refit on
        # the next main-loop turn, once per burst.
        if getattr(self, "_fit_pending", False):
            return
        self._fit_pending = True

        def run() -> bool:
            self._fit_pending = False
            self._fit_display()
            return False

        try:
            self._GLib.idle_add(run)
        except Exception:
            self._fit_pending = False

    def _text_width(self, label, text: str, px: float) -> int:
        """Pixel width of ``text`` in the label's font at ``px``."""
        Pango = self._Pango
        layout = label.create_pango_layout(text)
        desc = label.get_pango_context().get_font_description()
        desc = desc.copy() if desc is not None else Pango.FontDescription()
        desc.set_absolute_size(px * Pango.SCALE)
        layout.set_font_description(desc)
        return layout.get_pixel_size()[0]

    def _set_px(self, label, px: float | None) -> None:
        """Override the label's CSS font size (None restores it)."""
        Pango = self._Pango
        if getattr(label, "_fit_px", None) == px:
            return
        label._fit_px = px
        if px is None:
            label.set_attributes(None)
            return
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_size_new_absolute(int(px * Pango.SCALE)))
        label.set_attributes(attrs)

    def _fit_display(self) -> None:
        """Size the display lines to the display width, as Android does:
        the expression steps down its font sizes, then scrolls to its end;
        a committed result, the preview and an error shrink until they
        fit whole, so no number is ever shown cut short."""
        label = getattr(self, "expr_label", None)
        if label is None:
            return
        text = label.get_text()
        scale = getattr(self, "_scale", 1.0)
        steps = [(cls, _APP_CSS_PX[key] * scale) for cls, key in _EXPR_STEPS]
        if self._landscape:
            steps = [(None, _APP_CSS_PX["land_expr"] * scale)]
        try:
            available = self.expr_scroll.get_width()
        except Exception:
            available = 0
        if available > 0 and text:
            widths = [self._text_width(label, text, px) for _c, px in steps]
            index = pick_fit(widths, available)
        else:
            # Not allocated yet: guess from the length; the refit after
            # allocation measures for real.
            index = 0
            for limit, cls in _EXPR_SIZE_CLASSES:
                if len(text) <= limit:
                    index = [c for c, _p in _EXPR_STEPS].index(cls)
                    break
            index = min(index, len(steps) - 1)
            widths = []
        picked = steps[index][0]
        try:
            for cls, _key in _EXPR_STEPS:
                if cls is not None and cls != picked:
                    label.remove_css_class(cls)
            if picked is not None:
                label.add_css_class(picked)
            px = None
            if self._bright and widths:
                px = shrink_to_fit(steps[index][1], widths[index], available,
                                   FIT_FLOOR_PX * scale)
            self._set_px(label, px)
            self._fit_result_line(available)
            hadj = self.expr_scroll.get_hadjustment()
            hadj.set_value(hadj.get_upper() - hadj.get_page_size())
        except Exception:
            pass

    def _fit_result_line(self, available: int) -> None:
        label = self.result_label
        text = label.get_text()
        scale = getattr(self, "_scale", 1.0)
        key = "land_result" if self._landscape else "result"
        px = _APP_CSS_PX[key] * scale
        fit = None
        if available > 0 and text:
            fit = shrink_to_fit(px, self._text_width(label, text, px),
                                available, FIT_FLOOR_PX * scale)
        self._set_px(label, fit)

    def _refresh_preview(self) -> None:
        if self._bright or self._error_shown:
            return
        expr = self.builder.expression
        if not expr:
            self._hide_result()
            return
        outcome = Calculator.evaluate(expr, self._angle_mode())
        if isinstance(outcome, EvalResult.Success):
            self._show_preview(format_number(outcome.text))
        else:
            self._hide_result()

    def _hide_result(self) -> None:
        try:
            self.result_label.set_text("")
            self.result_label.set_visible(False)
        except Exception:
            pass

    def _show_preview(self, text: str) -> None:
        try:
            self.result_label.set_text(text)
            self.result_label.remove_css_class("xx-calc-error")
            self.result_label.remove_css_class("xx-calc-error-light")
            self.result_label.add_css_class("xx-calc-preview")
            self.result_label.set_visible(True)
        except Exception:
            pass
        self._queue_fit()

    def _show_committed(self) -> None:
        # After "=" the expression line already reads the result (the
        # builder is seeded with it), so the slot under it goes blank
        # instead of repeating the number. It stays visible, empty, so
        # it keeps its line height and the big result does not jump.
        try:
            self.result_label.set_text("")
            self.result_label.remove_css_class("xx-calc-preview")
            self.result_label.remove_css_class("xx-calc-error")
            self.result_label.remove_css_class("xx-calc-error-light")
            self.result_label.set_visible(True)
        except Exception:
            pass

    def _show_error(self, text: str) -> None:
        try:
            from xx_ui import theme as theme_mod

            dark = theme_mod.load().dark
        except Exception:
            dark = True
        try:
            self.result_label.set_text(text)
            self.result_label.remove_css_class("xx-calc-preview")
            self.result_label.remove_css_class("xx-calc-error")
            self.result_label.remove_css_class("xx-calc-error-light")
            self.result_label.add_css_class(
                "xx-calc-error" if dark else "xx-calc-error-light")
            self.result_label.set_visible(True)
        except Exception:
            pass
        self._error_shown = True
        self._queue_fit()

    def _on_equals(self, *_args) -> None:
        if self._bright or not self.builder.expression:
            return
        outcome = Calculator.evaluate(
            self.builder.expression, self._angle_mode())
        if isinstance(outcome, EvalResult.Success):
            self.store.add(self.builder.expression, outcome.text,
                           int(time.time() * 1000))
            self._bright_text = outcome.text
            self._show_committed()
            self._bright = True
            self._error_shown = False
            self.builder.seed(outcome.text)
            self._render_expression()
        else:
            self._show_error(error_text(outcome.kind))

    # -- science panel ----------------------------------------------
    def _refresh_science_labels(self) -> None:
        try:
            inv = self._inv_active
            self.sin_btn.set_label("asin" if inv else "sin")
            self.cos_btn.set_label("acos" if inv else "cos")
            self.tan_btn.set_label("atan" if inv else "tan")
            self.ln_btn.set_label("eˣ" if inv else "ln")
            self.log_btn.set_label("10ˣ" if inv else "log")
            self.deg_btn.set_label("RAD" if self._rad_mode else "DEG")
            if inv:
                self.inv_btn.add_css_class("xx-calc-inv-on")
            else:
                self.inv_btn.remove_css_class("xx-calc-inv-on")
        except Exception:
            pass

    def _apply_science_expanded(self, expanded: bool) -> None:
        self._science_expanded = expanded
        self._science_known = True
        try:
            self.sci_reveal.set_reveal_child(expanded)
            self._sci_icon.queue_draw()
        except Exception:
            pass

    # -- navigation ---------------------------------------------------
    def show(self, name: str) -> None:
        if name == "history":
            self._render_history()
        if name in ("licenses", "license") and not self._licenses_built:
            self._licenses_built = True
            self._build_licenses_pages()
        self.stack.set_visible_child_name(name)

    def back(self) -> bool:
        """The phone's back: a sub-page closes; the calculator itself
        keeps its expression (returns False, nothing to go back to)."""
        target = BACK_TARGETS.get(self.stack.get_visible_child_name() or "")
        if target is None:
            return False
        self.show(target)
        return True

    def open_sheet(self, name: str) -> None:
        self.show(name)

    def set_landscape(self, on: bool) -> None:
        if on == self._landscape and self._science_known:
            return
        Gtk = self._Gtk
        self._landscape = on
        try:
            if on:
                self.win.set_default_size(720, 360)
                for child in (self.display, self._sci_row, self.sci_reveal):
                    self.content.remove(child)
                    self.land_left.append(child)
                self.content.remove(self.pad)
                self.content.set_orientation(Gtk.Orientation.HORIZONTAL)
                self.content.set_spacing(0)
                self.land_left.set_hexpand(True)
                self.pad.set_hexpand(True)
                self.pad.set_valign(Gtk.Align.FILL)
                self.pad.set_margin_top(16)
                self.content.append(self.land_left)
                self.content.append(self.pad)
                self._root.add_css_class("xx-calc-landscape")
            else:
                self.win.set_default_size(360, 720)
                for child in (self.display, self._sci_row, self.sci_reveal):
                    self.land_left.remove(child)
                self.content.remove(self.land_left)
                self.content.remove(self.pad)
                self.content.set_orientation(Gtk.Orientation.VERTICAL)
                self.content.set_spacing(0)
                self.content.append(self.display)
                self.content.append(self._sci_row)
                self.content.append(self.sci_reveal)
                self.content.append(self.pad)
                self.pad.set_hexpand(False)
                self.pad.set_valign(Gtk.Align.END)
                self.pad.set_margin_top(4)
                self._root.remove_css_class("xx-calc-landscape")
        except Exception:
            pass
        self._queue_fit()
        if not self._science_known:
            self._apply_science_expanded(on)

    # -- history ------------------------------------------------------
    def _render_history(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        try:
            while True:
                row = self.hist_list.get_row_at_index(0)
                if row is None:
                    break
                self.hist_list.remove(row)
            for entry in self.store.list():
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                box.set_margin_top(10)
                box.set_margin_bottom(10)
                expr = Gtk.Label(label=entry.expression)
                expr.add_css_class("xx-secondary")
                expr.add_css_class("xx-calc-hist-expr")
                # Android keeps the tail of a long expression (ellipsize
                # start); the result wraps instead, so it is never shown
                # as a shorter, different number.
                expr.set_xalign(1.0)
                expr.set_ellipsize(Pango.EllipsizeMode.START)
                expr.set_max_width_chars(24)
                box.append(expr)
                result = Gtk.Label(label=format_number(entry.result))
                result.add_css_class("xx-calc-hist-result")
                result.set_xalign(1.0)
                result.set_justify(Gtk.Justification.RIGHT)
                result.set_wrap(True)
                result.set_wrap_mode(Pango.WrapMode.CHAR)
                result.set_max_width_chars(24)
                box.append(result)
                row = Gtk.ListBoxRow()
                row.set_child(box)
                row._calc_result = entry.result
                self.hist_list.append(row)
        except Exception:
            pass

    def _on_history_row(self, _list, row) -> None:
        result = getattr(row, "_calc_result", "")
        if not result:
            return
        expr = self.builder.expression
        if not expr or expr[-1] in RAW_INSERT_TAIL_CHARS:
            self.builder.raw(result)
        else:
            self.builder.seed(result)
        self.show("calc")
        self._on_edited()

    def _on_clear_history(self, *_args) -> None:
        # A direct menu action: no confirmation popup.
        self.store.clear()
        self._render_history()

    # -- menu / licenses ----------------------------------------------
    def menu_state(self) -> dict[str, bool]:
        """Which overflow entries can act right now (pure read)."""
        return {"copy": bool(self._copied_text()),
                "clear-history": bool(self.store.list()),
                "licenses": True}

    def _on_menu(self, button) -> None:
        Gtk, Gio = self._Gtk, self._Gio
        try:
            if getattr(self, "_menu_popover", None) is None:
                menu = Gio.Menu()
                menu.append("Copy", "calc.copy")
                menu.append("Clear history", "calc.clear-history")
                menu.append("Open-source licenses", "calc.licenses")
                group = Gio.SimpleActionGroup()
                self._menu_actions = {}
                for name, callback in (
                        ("copy", lambda *_: self._on_copy()),
                        ("clear-history", lambda *_: self._on_clear_history()),
                        ("licenses", lambda *_: self._on_licenses())):
                    action = Gio.SimpleAction.new(name, None)
                    action.connect("activate", callback)
                    group.add_action(action)
                    self._menu_actions[name] = action
                self.win.insert_action_group("calc", group)
                self._menu_popover = Gtk.PopoverMenu.new_from_model(menu)
                self._menu_popover.set_parent(button)
            # Nothing to copy, or no history: the entry shows disabled
            # rather than doing nothing.
            for name, enabled in self.menu_state().items():
                self._menu_actions[name].set_enabled(enabled)
            self._menu_popover.popup()
        except Exception:
            pass

    def _on_licenses(self, *_args) -> None:
        self.show("licenses")

    # -- copy / paste ---------------------------------------------------
    def _copied_text(self) -> str:
        if self._bright and self._bright_text:
            return self._bright_text
        return self.builder.expression

    def _on_copy(self, *_args) -> None:
        text = self._copied_text()
        if text:
            _copy_text(text)

    def _on_paste_text(self, text: str | None) -> None:
        if not text:
            return
        cleaned = sanitize_paste(text)
        if not cleaned:
            return
        self._fresh_if_post_equals()
        self.builder.raw(cleaned)
        self._on_edited()

    def _paste(self) -> None:
        try:
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
            if display is None:
                return
            clipboard = display.get_clipboard()
            clipboard.read_text_async(None, self._on_paste_done)
        except Exception:
            pass

    def _on_paste_done(self, clipboard, result) -> None:
        try:
            self._on_paste_text(clipboard.read_text_finish(result))
        except Exception:
            pass

    # -- hardware keyboard ----------------------------------------------
    def _on_key(self, _controller, keyval: int, _keycode: int,
                state) -> bool:
        from gi.repository import Gdk

        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if self.stack.get_visible_child_name() != "calc":
            # The phone's back gesture arrives as Escape: it closes the
            # sub-page. Keys typed here do not edit the hidden display.
            if name in ("Escape", "BackSpace", "Back"):
                return self.back()
            return False
        if name in ("Escape", "Back"):
            # Back on the calculator never wipes the expression.
            return False
        if ctrl and name.lower() == "c":
            self._on_copy()
            return True
        if ctrl and name.lower() == "v":
            self._paste()
            return True
        if ctrl:
            return False
        if name in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
            self._digit(name)()
            return True
        if name.startswith("KP_") and len(name) == 4 and name[3].isdigit():
            self._digit(name[3])()
            return True
        if name in ("period", "comma", "KP_Decimal"):
            self._on_dot()
            return True
        if name in ("plus", "KP_Add"):
            self._on_plus()
            return True
        if name in ("minus", "KP_Subtract"):
            self._on_minus()
            return True
        if name in ("asterisk", "KP_Multiply"):
            self._on_times()
            return True
        if name in ("slash", "KP_Divide", "division"):
            self._on_divide()
            return True
        if name == "percent":
            self._on_percent()
            return True
        if name == "asciicircum":
            self._press_raw("^")
            return True
        if name == "exclam":
            self._press_raw("!")
            return True
        if name == "parenleft":
            self._fresh_paren("open")
            return True
        if name == "parenright":
            self._fresh_paren("close")
            return True
        if name in ("Return", "KP_Enter", "equal"):
            self._on_equals()
            return True
        if name == "BackSpace":
            self.builder.backspace()
            self._on_edited()
            return True
        if name == "Delete":
            self._on_ac()
            return True
        return False

    # -- present / seed ---------------------------------------------------
    def present(self) -> None:
        self.refresh()
        self.win.present()
        if not self._science_known:
            GLib = self._GLib
            try:
                GLib.timeout_add(400, self._auto_expand_science)
            except Exception:
                pass

    def _auto_expand_science(self) -> bool:
        if not self._science_known:
            try:
                wide = self.win.get_width() > self.win.get_height()
            except Exception:
                wide = False
            self._apply_science_expanded(bool(wide))
        return False

    def refresh(self) -> None:
        self._render_expression()
        self._refresh_preview()

    def seed_demo(self, scenario: str) -> None:
        if scenario == "result":
            self.builder.clear()
            self.builder.raw("200+10%")
            self._on_edited()
            self._on_equals()
        elif scenario == "error":
            self.builder.clear()
            self.builder.raw("5÷0")
            self._on_edited()
            self._on_equals()
        elif scenario == "long":
            # Typing past the screen width: the caret end stays in view.
            self.builder.clear()
            self.builder.raw("123456789×987654321+5555÷7−42×3")
            self._on_edited()
        elif scenario == "big":
            # A committed result with an exponent must show whole.
            self.builder.clear()
            self.builder.raw("2^100")
            self._on_edited()
            self._on_equals()
        elif scenario == "science":
            self._apply_science_expanded(True)
            self.builder.clear()
            self.builder.raw("sin(30)")
            self._on_edited()
        elif scenario == "history":
            import time as _time

            now = int(_time.time() * 1000)
            self.store.clear()
            for offset, (expr, result) in enumerate((
                    ("200+10%", "220"),
                    ("sin(30)", "0.5"),
                    ("√(2)×√(2)", "2"),
                    ("16!", "2.0922789888E13"))):
                self.store.add(expr, result, now - offset * 1000)
            self.builder.clear()
            self.builder.raw("9×9")
            self._on_edited()
            self.show("history")
        else:
            self.builder.clear()
            self.builder.raw("2+3×4")
            self._on_edited()


class CalcApp:
    def __init__(self) -> None:
        Adw, _Gdk, Gio, _GLib, _Gtk, _Pango = _require_gtk()
        from . import __app_id__

        self._app = Adw.Application(application_id=__app_id__,
                                    flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._app.connect("activate", self._on_activate)
        self.window = None

    def _on_activate(self, app) -> None:
        if self.window is None:
            self.window = CalcWindow(app)
            app._calc_window = self.window
        scenario = os.environ.get("XX_CALC_SCENARIO", "")
        sheet = os.environ.get("XX_CALC_SHEET", "")
        landscape = os.environ.get("XX_CALC_LANDSCAPE", "")
        science = os.environ.get("XX_CALC_SCIENCE", "")
        if scenario or sheet == "history":
            self.window.seed_demo(scenario or "basic")
        if landscape == "1":
            self.window.set_landscape(True)
        if science == "1":
            self.window._apply_science_expanded(True)
        self.window.present()
        if sheet:
            self.window.open_sheet(sheet)

    def run(self, argv=None) -> int:
        return self._app.run(argv if argv is not None else [])


def main(argv=None) -> int:
    import sys

    args = list(argv) if argv is not None else sys.argv[1:]
    app = CalcApp()
    return app.run(args)
