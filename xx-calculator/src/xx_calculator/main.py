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

APP_CSS = """
.xx-calc-expr { font-size: 52px; font-weight: 400; }
.xx-calc-expr-s1 { font-size: 44px; }
.xx-calc-expr-s2 { font-size: 38px; }
.xx-calc-expr-s3 { font-size: 32px; }
.xx-calc-expr-s4 { font-size: 27px; }
.xx-calc-expr-s5 { font-size: 22px; }
.xx-calc-result { font-size: 22px; font-weight: 400; }
.xx-calc-preview { opacity: 0.55; }
.xx-calc-error { color: @error_color; }
.xx-calc-error-light { color: @theme_fg_color; }
.xx-calc-hist-expr { font-size: 14px; }
.xx-calc-hist-result { font-size: 24px; font-weight: 400; }
button.xx-calc-key {
  min-height: 64px; min-width: 56px; padding: 0;
  border-radius: 9999px; font-size: 22px; font-weight: 400;
  background-color: @card_bg_color; color: @theme_fg_color;
  border: 1px solid @borders;
}
button.xx-calc-key:active { background-color: @theme_selected_bg_color; }
button.xx-calc-key.xx-calc-equals {
  background-color: @theme_fg_color; color: @theme_bg_color;
  border-color: @theme_fg_color;
}
button.xx-calc-chip {
  min-height: 48px; min-width: 56px; padding: 4px 12px;
  border-radius: 9999px; font-size: 15px;
  background-color: @card_bg_color; color: @theme_fg_color;
  border: 1px solid @borders;
}
button.xx-calc-chip.xx-calc-inv-on {
  background-color: @theme_fg_color; color: @theme_bg_color;
}
button.xx-calc-bar {
  min-width: 48px; min-height: 48px; padding: 0;
  border-radius: 9999px; color: @theme_fg_color;
}
.xx-calc-caption { font-size: 11px; }
.xx-calc-landscape button.xx-calc-key { min-height: 44px; font-size: 18px; }
.xx-calc-landscape .xx-calc-expr { font-size: 34px; }
.xx-calc-landscape .xx-calc-result { font-size: 18px; }
.xx-calc-landscape button.xx-calc-chip { min-height: 40px; }
"""

LICENSES_TEXT = """xx-calculator uses:

- Python 3 standard library (PSF License)
- GTK 4 and libadwaita (LGPL-2.1-or-later)
- JetBrains Mono / Space Mono (SIL OFL 1.1)

No network code ships in this app; history stays on this phone."""

RAW_INSERT_TAIL_CHARS = frozenset("+{m}{t}*/{d}^(.".format(m=MINUS, t=TIMES, d=DIVIDE))

_EXPR_SIZE_CLASSES = (
    (9, None),
    (12, "xx-calc-expr-s1"),
    (15, "xx-calc-expr-s2"),
    (19, "xx-calc-expr-s3"),
    (24, "xx-calc-expr-s4"),
    (10**9, "xx-calc-expr-s5"),
)


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
    def _load_app_css(self) -> None:
        try:
            Gtk, Gdk = self._Gtk, self._Gdk
            provider = Gtk.CssProvider()
            provider.load_from_data(APP_CSS.encode("utf-8"))
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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
                   "color:%s;border-color:%s;}"
                   % (misc, digit, theme.foreground, theme.background,
                      theme.foreground))
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
        self._install_key_colors(_theme)
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
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_margin_start(20)
        root.set_margin_end(20)
        root.set_margin_top(8)
        root.set_margin_bottom(12)
        self._root = root
        self.land_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.land_left.set_vexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.hist_btn = Gtk.Button()
        self.hist_btn.add_css_class("xx-calc-bar")
        self.hist_btn.set_tooltip_text("History")
        self.hist_btn.set_child(self._icon("document-open-recent-symbolic", 22))
        self.hist_btn.connect("clicked", lambda *_: self.show("history"))
        top.append(self.hist_btn)
        spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        spacer.set_hexpand(True)
        top.append(spacer)
        self.menu_btn = Gtk.Button()
        self.menu_btn.add_css_class("xx-calc-bar")
        self.menu_btn.set_tooltip_text("More options")
        self.menu_btn.set_child(self._icon("open-menu-symbolic", 22))
        self.menu_btn.connect("clicked", self._on_menu)
        top.append(self.menu_btn)
        root.append(top)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        root.append(self.stack)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.set_vexpand(True)
        self.stack.add_named(self.content, "calc")

        self.display = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.display.set_margin_top(4)
        self.expr_scroll = Gtk.ScrolledWindow()
        self.expr_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                   Gtk.PolicyType.NEVER)
        self.expr_scroll.set_propagate_natural_height(True)
        self.expr_label = Gtk.Label(label="")
        self.expr_label.add_css_class("xx-calc-expr")
        self.expr_label.set_xalign(1.0)
        self.expr_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.expr_label.set_max_width_chars(14)
        self.expr_label.set_hexpand(True)
        self.expr_scroll.set_child(self.expr_label)
        self.display.append(self.expr_scroll)
        self.result_label = Gtk.Label(label="")
        self.result_label.add_css_class("xx-calc-result")
        self.result_label.set_xalign(1.0)
        self.result_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.result_label.set_max_width_chars(14)
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

        sci_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._sci_row = sci_row
        sci_row.set_margin_top(4)
        self.sci_toggle = Gtk.Button()
        self.sci_toggle.add_css_class("xx-calc-bar")
        self.sci_toggle.set_tooltip_text("Show or hide scientific keys")
        self._sci_icon = self._icon("pan-down-symbolic", 22)
        self.sci_toggle.set_child(self._sci_icon)
        self.sci_toggle.connect("clicked", self._on_science_toggle)
        sci_row.append(self.sci_toggle)
        sci_cap = Gtk.Label(label="SCIENTIFIC")
        sci_cap.add_css_class("xx-calc-caption")
        sci_cap.add_css_class("xx-caption")
        sci_cap.set_margin_start(8)
        sci_cap.set_valign(Gtk.Align.CENTER)
        sci_row.append(sci_cap)
        self.content.append(sci_row)

        self.sci_reveal = Gtk.Revealer()
        self.sci_reveal.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.sci_reveal.set_reveal_child(False)
        sci_scroll = Gtk.ScrolledWindow()
        sci_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        sci_scroll.set_margin_top(8)
        self.sci_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
        sci_scroll.set_child(self.sci_box)
        self.sci_reveal.set_child(sci_scroll)
        self.content.append(self.sci_reveal)
        self._build_science_chips()

        self.pad = Gtk.Grid(column_spacing=8, row_spacing=8,
                            column_homogeneous=True, row_homogeneous=True)
        self.pad.set_margin_top(8)
        self.pad.set_vexpand(True)
        self._build_pad()
        self.content.append(self.pad)

        self.side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.side.set_vexpand(True)
        self._build_history_page()

        self.win.set_content(root)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.win.add_controller(key)

    def _icon(self, name: str, px: int):
        Gtk = self._Gtk
        img = Gtk.Image.new_from_icon_name(name)
        img.set_pixel_size(px)
        return img

    def _key_button(self, text: str, callback, digit=False):
        Gtk = self._Gtk
        btn = Gtk.Button(label=text)
        btn.add_css_class("xx-calc-key")
        if digit:
            btn.add_css_class("xx-calc-digit")
        btn.connect("clicked", callback)
        return btn

    def _chip(self, label: str, callback):
        Gtk = self._Gtk
        btn = Gtk.Button(label=label)
        btn.add_css_class("xx-calc-chip")
        btn.connect("clicked", callback)
        self.sci_box.append(btn)
        return btn

    def _build_science_chips(self) -> None:
        self.inv_btn = self._chip("INV", lambda *_: self._on_inv())
        self.deg_btn = self._chip("DEG", lambda *_: self._on_deg())
        self.sin_btn = self._chip("sin", lambda *_: self._insert_fn("sin"))
        self.cos_btn = self._chip("cos", lambda *_: self._insert_fn("cos"))
        self.tan_btn = self._chip("tan", lambda *_: self._insert_fn("tan"))
        self.ln_btn = self._chip("ln", lambda *_: self._insert_fn("ln"))
        self.log_btn = self._chip("log", lambda *_: self._insert_fn("log"))
        self._chip("√", lambda *_: self._fresh_fn("√("))
        self._chip("π", lambda *_: self._fresh_const("π"))
        self._chip("^", lambda *_: self._press_raw("^"))
        self._chip("!", lambda *_: self._press_raw("!"))
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

    def _build_history_page(self) -> None:
        Gtk = self._Gtk
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back = Gtk.Button()
        back.add_css_class("xx-calc-bar")
        back.set_tooltip_text("Back")
        back.set_child(self._icon("pan-start-symbolic", 22))
        back.connect("clicked", lambda *_: self.show("calc"))
        bar.append(back)
        title = Gtk.Label(label="History")
        title.add_css_class("xx-title")
        title.set_hexpand(True)
        title.set_xalign(0.0)
        bar.append(title)
        self.clear_btn = Gtk.Button(label="CLEAR")
        self.clear_btn.add_css_class("xx-calc-chip")
        self.clear_btn.connect("clicked", lambda *_: self._on_clear_history())
        bar.append(self.clear_btn)
        bar.set_margin_top(4)
        bar.set_margin_bottom(8)
        self.side.append(bar)
        self.empty_label = Gtk.Label(label="No history")
        self.empty_label.add_css_class("xx-secondary")
        self.empty_label.set_vexpand(True)
        self.empty_label.set_valign(Gtk.Align.CENTER)
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.side.append(self.empty_label)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        self.hist_list = Gtk.ListBox()
        self.hist_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.hist_list.connect("row-activated", self._on_history_row)
        scroll.set_child(self.hist_list)
        self.side.append(scroll)
        self.stack.add_named(self.side, "history")

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
        try:
            self.expr_label.set_text(text)
            for limit, cls in _EXPR_SIZE_CLASSES:
                if len(text) <= limit:
                    picked = cls
                    break
            for _limit, cls in _EXPR_SIZE_CLASSES:
                if cls is not None:
                    self.expr_label.remove_css_class(cls)
            if picked is not None:
                self.expr_label.add_css_class(picked)
        except Exception:
            pass

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

    def _show_committed(self, text: str) -> None:
        try:
            self.result_label.set_text(text)
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

    def _on_equals(self, *_args) -> None:
        if self._bright or not self.builder.expression:
            return
        outcome = Calculator.evaluate(
            self.builder.expression, self._angle_mode())
        if isinstance(outcome, EvalResult.Success):
            self.store.add(self.builder.expression, outcome.text,
                           int(time.time() * 1000))
            self._bright_text = outcome.text
            self._show_committed(format_number(outcome.text))
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
            self._sci_icon.set_from_icon_name(
                "pan-up-symbolic" if expanded else "pan-down-symbolic")
        except Exception:
            pass

    # -- navigation ---------------------------------------------------
    def show(self, name: str) -> None:
        if name == "history":
            self._render_history()
        self.stack.set_visible_child_name(name)

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
                self.content.set_spacing(16)
                self.land_left.set_hexpand(True)
                self.pad.set_hexpand(True)
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
                self._root.remove_css_class("xx-calc-landscape")
        except Exception:
            pass
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
            entries = self.store.list()
            self.empty_label.set_visible(not entries)
            for entry in entries:
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                box.set_margin_top(10)
                box.set_margin_bottom(10)
                box.set_margin_start(4)
                box.set_margin_end(4)
                expr = Gtk.Label(label=entry.expression)
                expr.add_css_class("xx-secondary")
                expr.add_css_class("xx-calc-hist-expr")
                expr.set_xalign(1.0)
                expr.set_ellipsize(Pango.EllipsizeMode.END)
                expr.set_max_width_chars(24)
                box.append(expr)
                result = Gtk.Label(label=format_number(entry.result))
                result.add_css_class("xx-calc-hist-result")
                result.set_xalign(1.0)
                result.set_ellipsize(Pango.EllipsizeMode.END)
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
        Adw = self._Adw
        try:
            dialog = Adw.AlertDialog(
                heading="Clear history?",
                body="All saved calculations will be deleted.")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("clear", "Clear")
            dialog.set_response_appearance(
                "clear", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response", self._on_clear_response)
            dialog.present(self.win)
        except Exception:
            self.store.clear()
            self._render_history()

    def _on_clear_response(self, _dialog, response: str) -> None:
        if response == "clear":
            self.store.clear()
            self._render_history()

    # -- menu / licenses ----------------------------------------------
    def _on_menu(self, button) -> None:
        Gtk, Gio = self._Gtk, self._Gio
        try:
            menu = Gio.Menu()
            menu.append("Copy result", "calc.copy")
            menu.append("Clear history", "calc.clear-history")
            menu.append("Open-source licenses", "calc.licenses")
            app = self.win.get_application()
            if app is not None:
                for name, callback in (
                        ("copy", lambda *_: self._on_copy()),
                        ("clear-history", lambda *_: self._on_clear_history()),
                        ("licenses", lambda *_: self._on_licenses())):
                    action = Gio.SimpleAction.new(f"calc.{name}", None)
                    action.connect("activate", callback)
                    try:
                        app.add_action(action)
                    except Exception:
                        pass
            popover = Gtk.PopoverMenu.new_from_model(menu)
            popover.set_parent(button)
            popover.popup()
        except Exception:
            pass

    def _on_licenses(self, *_args) -> None:
        Adw = self._Adw
        try:
            dialog = Adw.AlertDialog(
                heading="Open-source licenses", body=LICENSES_TEXT)
            dialog.add_response("done", "Done")
            dialog.set_default_response("done")
            dialog.set_close_response("done")
            dialog.present(self.win)
        except Exception:
            pass

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
        if name in ("Escape", "Delete"):
            self._on_ac()
            return True

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
