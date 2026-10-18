from __future__ import annotations

import gi
from collections.abc import Callable

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk, Pango

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from config import DANGER_RED, THEME_PRESETS, ShellConfig, ThemePreset
from keyboard_layouts import CHOICES as KEYBOARD_CHOICES
from keyboard_layouts import xkb_for
# Single source of truth with the lock screen: a PIN longer than the lock
# screen's entry cap could be set here but never typed back = permanent lockout
from lock_screen import _MAX_PIN


def theme_css(preset: ThemePreset) -> str:
    return f"""
.wizard-root {{
    background: {preset.background};
    color: {preset.foreground};
}}
.wizard-title {{
    font-size: 22pt;
    font-weight: 400;
    color: {preset.foreground};
}}
.wizard-subtitle {{
    font-size: 11pt;
    color: {preset.muted};
}}
.wizard-label {{
    font-size: 12pt;
    color: {preset.foreground};
}}
.pin-dots {{
    font-size: 18pt;
    letter-spacing: 0.3em;
    font-family: monospace;
    color: {preset.foreground};
    min-height: 36px;
}}
.pin-dots.error {{
    color: {DANGER_RED};
}}
.pin-key {{
    font-size: 17pt;
    font-weight: 400;
    min-width: 72px;
    min-height: 56px;
    border-radius: 16px;
    background: {preset.surface};
    color: {preset.foreground};
    border: none;
    padding: 0;
}}
.pin-key:hover {{ background: {preset.surface_alt}; }}
.pin-key.del {{
    font-size: 14pt;
    background: transparent;
    color: {preset.muted};
}}
.pin-key.del:hover {{ background: {preset.surface_alt}; }}
.pin-sub {{
    font-size: 7pt;
    color: {preset.muted};
    margin-top: -2px;
}}
.wizard-next {{
    font-size: 12pt;
    min-height: 48px;
    border-radius: 12px;
    background: {preset.foreground};
    color: {preset.background};
    border: none;
}}
.wizard-next:hover {{
    /* mix() takes the fraction of the SECOND color: 0.85 all but erased
       the button under a resting touch pointer. */
    background: mix({preset.foreground}, {preset.background}, 0.15);
}}
.wizard-skip {{
    font-size: 11pt;
    color: {preset.muted};
}}
.theme-swatch-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 4px;
}}
.theme-cap {{
    font-size: 11px;
    color: {preset.muted};
}}
.theme-row-custom {{
    background: transparent;
    color: {preset.muted};
    border: 1px dashed {preset.border};
    border-radius: 12px;
    min-width: 40px;
    min-height: 40px;
}}
.theme-hex-entry {{
    background: {preset.surface};
    color: {preset.foreground};
    border-radius: 12px;
    min-height: 44px;
    padding: 0 14px;
}}
.kb-card {{
    background: {preset.surface};
    background-image: none;
    color: {preset.foreground};
    border: 1px solid {preset.border};
    border-radius: 16px;
    box-shadow: none;
    padding: 12px 14px;
    font-weight: 400;
}}
.kb-card:hover {{ background: {preset.surface_alt}; }}
.kb-card.selected,
.kb-card.selected:hover {{
    background: {preset.foreground};
    color: {preset.background};
    border-color: {preset.foreground};
}}
.kb-name {{
    font-size: 12pt;
}}
.kb-tag {{
    font-size: 8pt;
    color: {preset.muted};
}}
.kb-card.selected .kb-tag {{
    color: alpha({preset.background}, 0.6);
}}
.kb-key {{
    font-size: 9pt;
    min-height: 24px;
    border-radius: 6px;
    border: 1px solid {preset.border};
    color: {preset.muted};
}}
.kb-card.selected .kb-key {{
    border-color: alpha({preset.background}, 0.25);
    color: {preset.background};
}}
.sb-preview {{
    background: {preset.surface};
    border-bottom: 1px solid alpha({preset.foreground}, 0.6);
}}
.sb-preview-row {{
    color: alpha({preset.foreground}, 0.90);
    padding: 0 28px 6px 28px;
}}
.sb-preview-time {{
    font-size: 13px;
    font-weight: 500;
    font-feature-settings: "tnum";
}}
.sb-preview-battery {{
    font-size: 12px;
    font-feature-settings: "tnum";
}}
.sb-value {{
    font-size: 18pt;
    font-feature-settings: "tnum";
    color: {preset.foreground};
}}
.wizard-scale trough {{
    background-color: {preset.surface_alt};
    min-height: 6px;
    border-radius: 3px;
}}
.wizard-scale highlight {{
    background-color: {preset.foreground};
    border-radius: 3px;
}}
.wizard-scale slider {{
    background-color: {preset.foreground};
    border: none;
    min-width: 24px;
    min-height: 24px;
    border-radius: 12px;
    margin: -9px;
}}
.tz-list,
.tz-list row {{
    background: transparent;
    color: inherit;
    font-size: 12pt;
}}
.tz-list row {{
    min-height: 40px;
    border-radius: 10px;
    padding: 0 12px;
}}
.tz-list row:selected {{
    background: alpha(currentColor, 0.18);
    color: inherit;
}}
"""

# Per-preset swatches for the theme step, generated from the palette:
# a 40 px rounded square of the preset background, selected ringed.
_THEME_ROWS_CSS = ''.join(
    f'.theme-row-{p.key} {{'
    f' background: {p.background}; color: {p.foreground};'
    f' border-radius: 12px; min-width: 40px; min-height: 40px; }}'
    f'.theme-row-{p.key}.selected {{ box-shadow: 0 0 0 2px {p.foreground}; }}'
    for p in THEME_PRESETS.values()
).encode()

#: Top letter row of each first-boot layout, as xx-keyboard draws it
#: (xx-keyboard/src/layouts/<key>.json, first row of the default level).
_KEYBOARD_TOP_ROWS = {
    'us': 'QWERTYUIOP',
    'us+colemak': 'QWFPGJLUY;',
    'piercing-num': 'QRFWG;LPYZ',
}

#: Caption beside a layout name: Piercing Num has no hardware keymap.
_KEYBOARD_TAGS = {'piercing-num': 'ON-SCREEN'}

_DEFAULT_KEYBOARD = 'us'

_TIMEZONES = [
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver',
    'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Europe/Paris',
    'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Australia/Sydney',
]

_TIMEDATE1 = 'org.freedesktop.timedate1'
_TIMEDATE1_PATH = '/org/freedesktop/timedate1'

#: Shown on the timezone step when the system zone cannot be set from the
#: wizard (the installer owns it then).
_TIMEZONE_FAILURE_MESSAGE = 'Set the time zone in the installer'

_wizard_base_provider: Gtk.CssProvider | None = None
_wizard_live_provider: Gtk.CssProvider | None = None
_wizard_providers_added = False


def _apply_wizard_theme(theme: ThemePreset) -> None:
    """Wizard display sheets: register both providers once, reload per build.

    Replaying the tour (`xx-wm --welcome`) rebuilds the wizard window; a
    new display-level provider per replay would accumulate without bound —
    same add-once discipline as font_theme and the lock screen. The live
    provider holds the theme-row preview the base sheet cannot (it carries
    the per-preset swatch rows).
    """
    global _wizard_base_provider, _wizard_live_provider, _wizard_providers_added
    if _wizard_base_provider is None:
        _wizard_base_provider = Gtk.CssProvider()
    if _wizard_live_provider is None:
        _wizard_live_provider = Gtk.CssProvider()
    display = Gdk.Display.get_default()
    if display is None:
        return
    if not _wizard_providers_added:
        Gtk.StyleContext.add_provider_for_display(
            display,
            _wizard_base_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 4,
        )
        Gtk.StyleContext.add_provider_for_display(
            display,
            _wizard_live_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        _wizard_providers_added = True
    _wizard_base_provider.load_from_data(
        theme_css(theme).encode('utf-8') + _THEME_ROWS_CSS)
    _wizard_live_provider.load_from_data(theme_css(theme).encode('utf-8'))


def set_timezone_async(timezone: str,
                       on_done: Callable[[bool, str], None] | None = None,
                       bus=None) -> None:
    """Set the system timezone via timedate1 without blocking the UI thread.

    Shelling out to set the zone prompts polkit with no agent in-session,
    so it fails silently; the D-Bus call carries interactive=true and
    `on_done(ok, message)` fires on the main loop either way. Never raises.
    """
    from gi.repository import Gio, GLib

    def _fail(reason: str) -> None:
        if on_done is None:
            return

        def _deliver() -> bool:
            if on_done is not None:
                on_done(False, reason)
            return False
        GLib.idle_add(_deliver)

    if bus is None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception as error:
            _fail(str(error) or 'no system bus')
            return

    def _reply(conn, result) -> None:
        if on_done is None:
            return
        try:
            conn.call_finish(result)
        except Exception as error:
            message = (getattr(error, 'message', None) or str(error)
                       or 'SetTimezone failed')
            on_done(False, message)
            return
        on_done(True, '')

    try:
        bus.call(
            _TIMEDATE1, _TIMEDATE1_PATH, _TIMEDATE1, 'SetTimezone',
            GLib.Variant('(sb)', (timezone, True)),
            None, Gio.DBusCallFlags.NONE, 30000, None, _reply,
        )
    except Exception as error:
        _fail(str(error) or 'SetTimezone failed')


def keyboard_step_available() -> bool:
    """True when an on-screen keyboard is installed (18.3).

    xx-keyboard ships its layouts in the binary; squeekboard (the
    fallback) still needs the Colemak layout files on disk.
    """
    import shutil
    from pathlib import Path
    if shutil.which('xx-keyboard'):
        return True
    if not shutil.which('squeekboard'):
        return False
    return any(
        path.exists() for path in (
            Path.home() / '.local' / 'share' / 'squeekboard' / 'keyboards',
            Path('/usr/share/xx-wm/squeekboard'),
        )
    )


def _say(label, text: str) -> None:
    """Set a hint line, hidden while it has nothing to say."""
    label.set_text(text)
    label.set_visible(bool(text))


def setup_pending(config_path) -> bool:
    """True when first boot must run: no config yet, or a wizard that
    wrote config (Keyboard step) but never reached Finish. Configs from
    before the marker have no `setup_complete` key and count as done."""
    import json
    try:
        data = json.loads(config_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get('setup_complete') is False


def status_bar_start(data, device_default: Callable[[], int]) -> int:
    """Slider position the Status bar step opens at: a height an earlier,
    unfinished run saved, else the device default (preselected)."""
    import status_bar
    saved = status_bar.clamp_height((data or {}).get(status_bar.CONFIG_KEY))
    return saved if saved is not None else int(device_default())


class FirstBootWizard(Gtk.Window):
    """
    First-run setup (keyboard, PIN, theme, status bar, timezone) followed by the usage walkthrough:
    an interactive gesture tour, shade / DnD & Focus / config-file pages, and
    a try-the-keyboard step when squeekboard is present. `tour_only=True`
    replays just the walkthrough (`xx-wm --welcome`).
    """

    def __init__(self, on_complete: Callable[[], None],
                 tour_only: bool = False) -> None:
        super().__init__(title='PiercingXX Setup')
        self._tour_only = tour_only

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            # Zone 0 so the OSK pushes the wizard up (keyboard tour page).
            # NONE until an Entry is focused — PIN is buttons, not OSK.
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self._on_complete = on_complete
        self._config = ShellConfig()
        self._pin_entered = ''
        self._pin_buf = ''
        self._confirm_buf = ''

        # Display sheets register once per display and reload per build —
        # replaying the tour must not stack a new provider every time.
        _apply_wizard_theme(self._config.theme)
        self._theme_provider = _wizard_live_provider

        # Instant page switches — slide animations stutter on phone GPUs at
        # scale 3, and text-first means no transition chrome anyway
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        # Same homogeneous-stack trap as the shell window: the widest title
        # ("Shade & quick settings" at 22pt measured 436 px) stretched every
        # page, clipping "Get started" on the Welcome page.
        self._stack.set_hhomogeneous(False)
        self._stack.set_vhomogeneous(False)
        self._stack.set_interpolate_size(False)

        self._stack.add_named(self._build_welcome(), 'welcome')
        # Layout before the PIN: the keypad is numeric, but every later
        # text field (custom theme hex, Wi-Fi) types on this layout.
        self._stack.add_named(self._build_keyboard_step(), 'keyboard')
        self._stack.add_named(self._build_pin_step(), 'pin')
        self._stack.add_named(self._build_pin_confirm_step(), 'pin_confirm')
        self._stack.add_named(self._build_theme_step(), 'theme')
        # After the look is picked: the top strip that keeps apps clear of
        # the camera hole, sized on the real screen before first use.
        self._stack.add_named(self._build_status_bar_step(), 'status_bar')
        self._stack.add_named(self._build_timezone_step(), 'timezone')

        self._tour_pages = self._build_tour_pages()
        for name, widget in self._tour_pages:
            self._stack.add_named(widget, name)

        if tour_only:
            self._stack.set_visible_child_name(self._tour_pages[0][0])

        root = Gtk.Box()
        root.add_css_class('wizard-root')
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.append(self._stack)
        self.set_child(root)

    @staticmethod
    def is_needed() -> bool:
        from pathlib import Path
        return setup_pending(Path.home() / '.config' / 'xx-wm' / 'config.json')

    def _page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_bottom(32)
        return page

    def _title_block(self, title_text: str, sub_text: str | None = None) -> Gtk.Box:
        # Top margin drops the title clear of the camera notch so the block
        # sits centered in the top third of the screen
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(88)
        title = Gtk.Label(label=title_text)
        title.add_css_class('wizard-title')
        # Long titles ("Do Not Disturb & Focus") wrap instead of forcing the
        # page wider than the phone screen.
        title.set_xalign(0.5)
        title.set_justify(Gtk.Justification.CENTER)
        title.set_wrap(True)
        title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.set_max_width_chars(18)
        box.append(title)
        if sub_text:
            sub = Gtk.Label(label=sub_text, justify=Gtk.Justification.CENTER, wrap=True)
            sub.add_css_class('wizard-subtitle')
            sub.set_xalign(0.5)
            sub.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            sub.set_max_width_chars(30)
            box.append(sub)
        return box

    @staticmethod
    def _centered(*widgets: Gtk.Widget) -> Gtk.Box:
        """Vertically centered content zone between title block and footer."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_vexpand(True)
        box.set_valign(Gtk.Align.CENTER)
        for widget in widgets:
            box.append(widget)
        return box

    def _build_welcome(self) -> Gtk.Widget:
        page = self._page()

        next_btn = Gtk.Button(label='Get started')
        next_btn.add_css_class('wizard-next')
        next_btn.set_hexpand(True)
        next_btn.connect('clicked',
                         lambda _b: self._stack.set_visible_child_name('keyboard'))

        page.append(self._title_block(
            'PiercingXX', 'Let\'s set up your device.\nThis takes about a minute.'))
        page.append(self._centered())
        page.append(next_btn)
        return page

    def _build_keyboard_step(self) -> Gtk.Widget:
        page = self._page()

        # One card per first-boot layout: name, then its top letter row
        # drawn as keycaps so the choice reads before anything is typed.
        # Selected inverts (foreground fill), like every XX selection.
        self._keyboard_choice = _DEFAULT_KEYBOARD
        self._keyboard_applied: str | None = None
        self._keyboard_cards: dict[str, Gtk.Widget] = {}
        cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for key, name in KEYBOARD_CHOICES:
            card = Gtk.Button()
            card.add_css_class('kb-card')
            card.set_hexpand(True)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name_label = Gtk.Label(label=name, xalign=0)
            name_label.add_css_class('kb-name')
            name_label.set_hexpand(True)
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            head.append(name_label)
            tag = _KEYBOARD_TAGS.get(key)
            if tag:
                tag_label = Gtk.Label(label=tag, xalign=1)
                tag_label.add_css_class('kb-tag')
                head.append(tag_label)
            inner.append(head)
            # Homogeneous row: ten caps share the card width, so the
            # preview fits 320 px and large text without a fixed key size.
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            row.set_homogeneous(True)
            for glyph in _KEYBOARD_TOP_ROWS.get(key, ''):
                cap = Gtk.Label(label=glyph)
                cap.add_css_class('kb-key')
                row.append(cap)
            inner.append(row)
            card.set_child(inner)
            card.connect('clicked', lambda _b, k=key: self._on_keyboard_card(k))
            self._keyboard_cards[key] = card
            cards.append(card)
        self._mark_keyboard_card(self._keyboard_choice)

        more = Gtk.Label(label='More in Settings › Keyboard.',
                         justify=Gtk.Justification.CENTER)
        more.add_css_class('wizard-subtitle')
        more.set_xalign(0.5)
        more.set_wrap(True)
        more.set_wrap_mode(Pango.WrapMode.WORD_CHAR)

        # Cards scroll instead of pushing the page off a short screen at
        # large text; centered while they fit.
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_valign(Gtk.Align.CENTER)
        content.append(cards)
        content.append(more)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(content)

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.set_hexpand(True)
        next_btn.connect('clicked', self._on_keyboard_next)

        page.append(self._title_block(
            'Keyboard', 'Your main layout. QWERTY and Piercing Num stay '
            'one switch away on the keyboard.'))
        page.append(scroller)
        page.append(next_btn)
        return page

    def _mark_keyboard_card(self, choice: str) -> None:
        for key, card in self._keyboard_cards.items():
            if key == choice:
                card.add_css_class('selected')
            else:
                card.remove_css_class('selected')

    def _on_keyboard_card(self, choice: str) -> None:
        self._keyboard_choice = choice
        self._mark_keyboard_card(choice)
        self._apply_keyboard(choice)

    def _on_keyboard_next(self, _btn: Gtk.Widget | None) -> None:
        # The untouched default is a choice too: write and apply it once.
        self._apply_keyboard(self._keyboard_choice)
        self._stack.set_visible_child_name('pin')

    def _apply_keyboard(self, choice: str) -> None:
        """Write `keyboard_layout` (xx-keyboard follows the file live)
        and set the hardware keymap. Idempotent: a choice already
        applied writes and spawns nothing again."""
        if choice == self._keyboard_applied:
            return
        self._keyboard_applied = choice
        # The first write creates config.json; mark setup unfinished so a
        # reboot before Finish reruns the wizard instead of skipping the PIN.
        if 'setup_complete' not in self._config.data:
            self._config.data['setup_complete'] = False
        self._config.set('keyboard_layout', choice)
        from settings_keyboard import apply_keyboard_layout, parse_layout_key
        # Fire-and-forget keymap write (Popen, never waited on); Piercing Num
        # is on-screen only, so xkb gets its hardware keymap (us).
        apply_keyboard_layout(*parse_layout_key(xkb_for(choice)))

    def _build_pin_step(self) -> Gtk.Widget:
        page = self._page()

        # Fill width with centered text: halign CENTER + max_width_chars
        # would allocate ~1 char and ellipsize immediately ("…" on first key)
        self._pin_dots = Gtk.Label(label='')
        self._pin_dots.add_css_class('pin-dots')
        self._pin_dots.set_xalign(0.5)
        self._pin_dots.set_max_width_chars(1)
        self._pin_dots.set_hexpand(True)
        self._pin_dots.set_ellipsize(Pango.EllipsizeMode.START)
        self._pin_dots.set_margin_top(4)

        self._pin_hint = Gtk.Label(label='')
        self._pin_hint.add_css_class('wizard-subtitle')
        self._pin_hint.set_halign(Gtk.Align.CENTER)
        # Hidden while empty: an empty line still took a row of the page,
        # pushing Next off a 640 px screen.
        self._pin_hint.set_visible(False)

        self._pin_buf = ''
        keypad = self._build_pin_keypad(
            on_digit=self._pin_add,
            on_delete=self._pin_del,
        )

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.set_hexpand(True)
        next_btn.connect('clicked', self._on_pin_next)

        skip_btn = Gtk.Button(label='Skip (no PIN)')
        skip_btn.add_css_class('flat')
        skip_btn.add_css_class('wizard-skip')
        skip_btn.set_hexpand(True)
        skip_btn.connect('clicked', lambda _b: self._stack.set_visible_child_name('theme'))

        page.append(self._title_block(
            'Set a PIN', 'At least 6 digits.'))
        page.append(self._centered(self._pin_dots, self._pin_hint, keypad))
        page.append(next_btn)
        page.append(skip_btn)
        return page

    def _build_pin_confirm_step(self) -> Gtk.Widget:
        page = self._page()

        self._confirm_dots = Gtk.Label(label='')
        self._confirm_dots.add_css_class('pin-dots')
        self._confirm_dots.set_xalign(0.5)
        self._confirm_dots.set_max_width_chars(1)
        self._confirm_dots.set_hexpand(True)
        self._confirm_dots.set_ellipsize(Pango.EllipsizeMode.START)
        self._confirm_dots.set_margin_top(4)

        self._confirm_buf = ''
        self._pin_error = Gtk.Label(label='')
        self._pin_error.add_css_class('wizard-subtitle')
        self._pin_error.set_halign(Gtk.Align.CENTER)
        self._pin_error.set_visible(False)

        keypad = self._build_pin_keypad(
            on_digit=self._confirm_add,
            on_delete=self._confirm_del,
        )

        confirm_btn = Gtk.Button(label='Set PIN')
        confirm_btn.add_css_class('wizard-next')
        confirm_btn.set_hexpand(True)
        confirm_btn.connect('clicked', self._on_pin_confirm)

        page.append(self._title_block('Confirm PIN', 'Enter the same PIN again.'))
        page.append(self._centered(self._confirm_dots, self._pin_error, keypad))
        page.append(confirm_btn)
        return page

    def _build_pin_keypad(self, on_digit: object, on_delete: object) -> Gtk.Grid:
        _KEYS = [
            ('1', ''),    ('2', 'ABC'), ('3', 'DEF'),
            ('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO'),
            ('7', 'PQRS'),('8', 'TUV'), ('9', 'WXYZ'),
            ('',  ''),    ('0', ''),    ('←', ''),
        ]
        # Columns share the available width equally (no fixed pixel keys),
        # so the pad fits a 320 px screen and stretches on wider ones.
        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        grid.set_halign(Gtk.Align.FILL)
        grid.set_hexpand(True)
        grid.set_column_homogeneous(True)
        for idx, (digit, sub) in enumerate(_KEYS):
            col, row = idx % 3, idx // 3
            if not digit:
                spacer = Gtk.Box()
                spacer.set_hexpand(True)
                grid.attach(spacer, col, row, 1, 1)
            elif digit == '←':
                btn = Gtk.Button(label='←')
                btn.add_css_class('pin-key')
                btn.add_css_class('del')
                btn.set_hexpand(True)
                btn.connect('clicked', lambda _b, fn=on_delete: fn())
                grid.attach(btn, col, row, 1, 1)
            else:
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                inner.set_halign(Gtk.Align.CENTER)
                inner.set_valign(Gtk.Align.CENTER)
                inner.append(Gtk.Label(label=digit))
                if sub:
                    s = Gtk.Label(label=sub)
                    s.add_css_class('pin-sub')
                    inner.append(s)
                btn = Gtk.Button()
                btn.add_css_class('pin-key')
                btn.set_hexpand(True)
                btn.set_child(inner)
                btn.connect('clicked', lambda _b, d=digit, fn=on_digit: fn(d))
                grid.attach(btn, col, row, 1, 1)
        return grid

    def _pin_add(self, digit: str) -> None:
        if len(self._pin_buf) >= _MAX_PIN:
            return
        self._pin_buf += digit
        self._pin_dots.set_text('●' * len(self._pin_buf))
        if len(self._pin_buf) >= 6:
            _say(self._pin_hint, '')

    def _pin_del(self) -> None:
        if self._pin_buf:
            self._pin_buf = self._pin_buf[:-1]
            self._pin_dots.set_text('●' * len(self._pin_buf))

    def _confirm_add(self, digit: str) -> None:
        if len(self._confirm_buf) >= _MAX_PIN:
            return
        self._confirm_buf += digit
        self._confirm_dots.set_text('●' * len(self._confirm_buf))

    def _confirm_del(self) -> None:
        if self._confirm_buf:
            self._confirm_buf = self._confirm_buf[:-1]
            self._confirm_dots.set_text('●' * len(self._confirm_buf))

    def _build_theme_step(self) -> Gtk.Widget:
        page = self._page()

        # Swatch grid: every preset plus custom — a 40 px rounded square of
        # the preset background with an "Aa" in its foreground, captioned.
        # A popover dialog does not open reliably inside a layer-shell
        # overlay, so Custom reveals an inline hex row instead.
        self._selected_theme = next(iter(THEME_PRESETS))
        self._theme_rows: dict[str, Gtk.Widget] = {}
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_column_homogeneous(True)
        grid.set_halign(Gtk.Align.CENTER)
        for index, key in enumerate(list(THEME_PRESETS) + ['custom']):
            btn = Gtk.Button()
            btn.add_css_class('theme-swatch-btn')
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            swatch = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            swatch.set_size_request(40, 40)
            swatch.set_halign(Gtk.Align.CENTER)
            if key == 'custom':
                swatch.add_css_class('theme-row-custom')
            else:
                swatch.add_css_class(f'theme-row-{key}')
            glyph = Gtk.Label(label='Aa')
            glyph.set_halign(Gtk.Align.CENTER)
            glyph.set_valign(Gtk.Align.CENTER)
            glyph.set_hexpand(True)
            glyph.set_vexpand(True)
            swatch.append(glyph)
            name = 'Custom' if key == 'custom' else THEME_PRESETS[key].name
            cap = Gtk.Label(label=name)
            cap.add_css_class('theme-cap')
            cap.set_max_width_chars(10)
            # Preset names are short: unconditional ellipsize misfires on
            # fractional rounding and shows "Graphit…" for fitting text.
            if len(name) > 10:
                cap.set_ellipsize(Pango.EllipsizeMode.END)
            else:
                cap.set_ellipsize(Pango.EllipsizeMode.NONE)
            inner.append(swatch)
            inner.append(cap)
            btn.set_child(inner)
            btn.connect('clicked', self._on_theme_row, key)
            self._theme_rows[key] = swatch
            grid.attach(btn, index % 3, index // 3, 1, 1)

        self._custom_hex_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._custom_hex_row.set_halign(Gtk.Align.CENTER)
        self._custom_hex_entry = Gtk.Entry()
        self._custom_hex_entry.add_css_class('theme-hex-entry')
        self._custom_hex_entry.set_placeholder_text('#3a2a10')
        self._custom_hex_entry.set_max_width_chars(10)
        self._custom_hex_entry.set_hexpand(True)
        from osk import attach, set_layer_keyboard, set_visible

        def _hex_show(*_a: object) -> None:
            set_layer_keyboard(self, True)
            set_visible(True)

        def _hex_hide(*_a: object) -> None:
            set_layer_keyboard(self, False)
            set_visible(False)

        attach(self._custom_hex_entry, on_show=_hex_show, on_hide=_hex_hide)
        hex_apply = Gtk.Button(label='Apply')
        hex_apply.add_css_class('wizard-next')
        hex_apply.connect('clicked', self._on_custom_apply)
        self._custom_hex_row.append(self._custom_hex_entry)
        self._custom_hex_row.append(hex_apply)
        self._custom_hex_row.set_visible(False)

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.set_hexpand(True)
        next_btn.connect('clicked', self._on_theme_next)

        page.append(self._title_block('Pick a theme'))
        page.append(self._centered(grid, self._custom_hex_row))
        page.append(next_btn)
        self._on_theme_row(None, self._selected_theme)
        return page

    def _build_timezone_step(self) -> Gtk.Widget:
        page = self._page()

        self._tz_list = Gtk.ListBox()
        self._tz_list.add_css_class('tz-list')
        self._tz_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for tz in _TIMEZONES:
            label = Gtk.Label(label=tz, xalign=0)
            self._tz_list.append(label)
        self._tz_list.select_row(self._tz_list.get_row_at_index(0))

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self._tz_list)

        self._tz_status = Gtk.Label(label='')
        self._tz_status.add_css_class('wizard-subtitle')
        self._tz_status.set_halign(Gtk.Align.CENTER)
        self._tz_status.set_wrap(True)

        finish_btn = Gtk.Button(label='Done')
        finish_btn.add_css_class('wizard-next')
        finish_btn.set_hexpand(True)
        finish_btn.connect('clicked', self._on_finish)

        page.append(self._title_block('Timezone'))
        page.append(scroller)
        page.append(self._tz_status)
        page.append(finish_btn)
        return page

    # ------------------------------------------------------------------
    # Walkthrough (Workstream 18) — gesture tour + info pages
    # ------------------------------------------------------------------

    def _build_tour_pages(self) -> list[tuple[str, Gtk.Widget]]:
        pages: list[tuple[str, Gtk.Widget]] = [
            ('tour_swipe_up', self._gesture_page(
                'Swipe up', 'Opens the app drawer — every app, searchable.\n\nTry it now.',
                kind='swipe_up')),
            ('tour_swipe_down', self._gesture_page(
                'Swipe down', 'Opens notifications and quick settings.\n\nTry it now.',
                kind='swipe_down')),
            ('tour_swipe_side', self._gesture_page(
                'Swipe sideways', 'Opens your side apps — camera on the '
                'right.\n\nTry it now.',
                kind='swipe_side')),
            ('info_shade', self._info_page(
                'Shade & quick settings',
                'Swipe down anytime: toggles, sliders, notifications, and a '
                'calendar behind the date.')),
            ('info_focus', self._info_page(
                'Do Not Disturb & Focus',
                'DnD lets only starred contacts and repeat callers through.'
                '\n\nFocus pauses distracting apps until you are done. Both '
                'are quick settings tiles.')),
        ]
        if keyboard_step_available():
            pages.append(('tour_keyboard', self._keyboard_page()))
        pages.append(('info_files', self._final_page()))
        return pages

    def _advance_tour(self) -> None:
        names = [name for name, _w in self._tour_pages]
        current = self._stack.get_visible_child_name()
        if current not in names:
            self._stack.set_visible_child_name(names[0])
            return
        idx = names.index(current)
        if idx + 1 < len(names):
            self._stack.set_visible_child_name(names[idx + 1])
        else:
            self._finish_tour()

    def _finish_tour(self) -> None:
        # Once only: repeated taps on the last page must not complete twice.
        if getattr(self, '_finished', False):
            return
        self._finished = True
        try:
            self._on_complete()
        finally:
            # Close even if completing raised; never leave the wizard up.
            GLib.idle_add(self.close)

    def _tour_scaffold(self, title_text: str, body_text: str) -> tuple[Gtk.Box, Gtk.Box]:
        page = self._page()
        page.append(self._title_block(title_text, body_text))
        page.append(self._centered())

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.append(footer)
        return page, footer

    def _skip_button(self) -> Gtk.Button:
        skip = Gtk.Button(label='Skip')
        skip.add_css_class('flat')
        skip.add_css_class('wizard-skip')
        skip.set_hexpand(True)
        skip.connect('clicked', lambda _b: self._advance_tour())
        return skip

    def _gesture_page(self, title_text: str, body_text: str, kind: str) -> Gtk.Widget:
        page, footer = self._tour_scaffold(title_text, body_text)
        footer.append(self._skip_button())

        if kind == 'long_press':
            gesture = Gtk.GestureLongPress.new()
            gesture.set_touch_only(False)
            gesture.connect('pressed', lambda _g, _x, _y: self._advance_tour())
            page.add_controller(gesture)
        else:
            swipe = Gtk.GestureSwipe.new()
            swipe.set_touch_only(False)

            def _on_swipe(_g: Gtk.GestureSwipe, vx: float, vy: float,
                          k: str = kind) -> None:
                if k == 'swipe_up' and vy < -300 and abs(vy) > abs(vx):
                    self._advance_tour()
                elif k == 'swipe_down' and vy > 300 and abs(vy) > abs(vx):
                    self._advance_tour()
                elif k == 'swipe_side' and abs(vx) > 300 and abs(vx) > abs(vy):
                    self._advance_tour()

            swipe.connect('swipe', _on_swipe)
            page.add_controller(swipe)
        return page

    def _info_page(self, title_text: str, body_text: str) -> Gtk.Widget:
        page, footer = self._tour_scaffold(title_text, body_text)
        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.set_hexpand(True)
        next_btn.connect('clicked', lambda _b: self._advance_tour())
        footer.append(next_btn)
        footer.append(self._skip_button())
        return page

    def _keyboard_page(self) -> Gtk.Widget:
        page, footer = self._tour_scaffold(
            'Try the keyboard',
            'Tap the field — the keyboard appears whenever you need to '
            'type. Layouts live in Settings › Keyboard.')
        entry = Gtk.Entry()
        entry.set_placeholder_text('Type something…')
        entry.set_hexpand(True)
        from osk import attach, set_layer_keyboard, set_visible

        def _show(*_a: object) -> None:
            set_layer_keyboard(self, True)
            set_visible(True)

        def _hide(*_a: object) -> None:
            set_layer_keyboard(self, False)
            set_visible(False)

        attach(entry, on_show=_show, on_hide=_hide)
        # The centered content zone sits between the title block and footer
        page.get_first_child().get_next_sibling().append(entry)
        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.set_hexpand(True)
        next_btn.connect('clicked', lambda _b: self._advance_tour())
        footer.append(next_btn)
        footer.append(self._skip_button())
        return page

    def _final_page(self) -> Gtk.Widget:
        page, footer = self._tour_scaffold(
            'Make it yours',
            'Long-press home to edit your slots.\n\nEverything else lives '
            'in\n~/.config/xx-wm/\nand reloads live.\n\nReplay this tour:\n'
            'xx-wm --welcome')
        done_btn = Gtk.Button(label='Done')
        done_btn.add_css_class('wizard-next')
        done_btn.set_hexpand(True)
        done_btn.connect('clicked', lambda _b: self._finish_tour())
        footer.append(done_btn)
        return page

    def _on_theme_row(self, _btn: Gtk.Button | None, theme_key: str) -> None:
        if theme_key == 'custom':
            self._custom_hex_row.set_visible(True)
            return
        self._custom_hex_row.set_visible(False)
        self._selected_theme = theme_key
        for key, swatch in self._theme_rows.items():
            if key == theme_key:
                swatch.add_css_class('selected')
            else:
                swatch.remove_css_class('selected')
        preset = THEME_PRESETS[theme_key]
        self._theme_provider.load_from_data(theme_css(preset).encode('utf-8'))

    def _on_custom_apply(self, _btn: Gtk.Button) -> None:
        from xx_ui.theme import _resolve as _resolve_custom
        text = self._custom_hex_entry.get_text().strip()
        if not self._config.set_custom_background(text):
            self._custom_hex_entry.set_text('')
            return
        self._selected_theme = 'custom'
        for key, swatch in self._theme_rows.items():
            if key == 'custom':
                swatch.add_css_class('selected')
            else:
                swatch.remove_css_class('selected')
        preset = _resolve_custom(dict(self._config.data)) or THEME_PRESETS['amoled']
        self._theme_provider.load_from_data(theme_css(preset).encode('utf-8'))

    def _on_pin_next(self, _btn: Gtk.Widget) -> None:
        if len(self._pin_buf) < 6:
            self._pin_dots.add_css_class('error')
            _say(self._pin_hint, 'PIN must be at least 6 digits')
            return
        self._pin_dots.remove_css_class('error')
        self._pin_entered = self._pin_buf
        self._confirm_buf = ''
        self._confirm_dots.set_text('')
        _say(self._pin_error, '')
        self._stack.set_visible_child_name('pin_confirm')

    def _on_pin_confirm(self, _btn: Gtk.Widget) -> None:
        if self._confirm_buf != self._pin_entered:
            _say(self._pin_error, 'PINs do not match — try again')
            self._confirm_buf = ''
            self._confirm_dots.set_text('')
            return
        self._config.set_pin(self._pin_entered)
        self._stack.set_visible_child_name('theme')

    def _on_theme_next(self, _btn: Gtk.Button) -> None:
        self._config.set_theme(self._selected_theme)
        self._stack.set_visible_child_name('status_bar')

    # ------------------------------------------------------------------
    # Status bar height (status_bar.py): the strip that reserves the
    # camera area. The real bar stays hidden while the wizard is up
    # (status_bar.decide_visible), so this page draws its own copy at the
    # very top of the screen at the real height.
    # ------------------------------------------------------------------

    def _status_bar_default(self) -> int:
        """The device default (scale-aware on punch-hole devices), as the
        bar resolves it when `status_bar_height` is null."""
        import status_bar
        try:
            panel = status_bar.monitor_panel(self.get_display())
        except Exception:
            panel = None
        return status_bar.device_default_height(status_bar.detect_device(),
                                                panel)

    def _build_status_bar_step(self) -> Gtk.Widget:
        import status_bar

        page = self._page()
        self._sb_height = status_bar_start(self._config.data,
                                           self._status_bar_default)

        # The preview: a band of exactly the chosen height (the colored
        # box has no content, so nothing can stretch it) with the clock
        # and battery laid over its bottom edge as the real bar draws them.
        self._sb_strip = Gtk.Box()
        self._sb_strip.add_css_class('sb-preview')
        self._sb_strip.set_valign(Gtk.Align.START)
        self._sb_strip.set_can_target(False)
        self._sb_row = Gtk.CenterBox()
        self._sb_row.add_css_class('sb-preview-row')
        self._sb_row.set_valign(Gtk.Align.START)
        self._sb_row.set_can_target(False)
        clock = Gtk.Label(label='12:30', xalign=0)
        clock.add_css_class('sb-preview-time')
        clock.set_valign(Gtk.Align.END)
        self._sb_row.set_start_widget(clock)
        keepout = Gtk.Box()
        keepout.set_size_request(status_bar.CENTER_KEEPOUT_PX, -1)
        self._sb_row.set_center_widget(keepout)
        battery = Gtk.Label(label='84%', xalign=1)
        battery.add_css_class('sb-preview-battery')
        battery.set_valign(Gtk.Align.END)
        self._sb_row.set_end_widget(battery)

        self._sb_value = Gtk.Label(label='')
        self._sb_value.add_css_class('sb-value')
        self._sb_value.set_halign(Gtk.Align.CENTER)

        self._sb_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, status_bar.MIN_HEIGHT,
            status_bar.MAX_HEIGHT, 1)
        self._sb_scale.add_css_class('wizard-scale')
        self._sb_scale.set_draw_value(False)
        self._sb_scale.set_hexpand(True)
        self._sb_scale.set_value(float(self._sb_height))
        self._sb_scale.connect(
            'value-changed',
            lambda scale: self._on_status_bar_slider(scale.get_value()))
        self._show_status_bar_height(self._sb_height)

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.set_hexpand(True)
        next_btn.connect('clicked', self._on_status_bar_next)

        default_btn = Gtk.Button(label='Use default')
        default_btn.add_css_class('flat')
        default_btn.add_css_class('wizard-skip')
        default_btn.set_hexpand(True)
        default_btn.connect('clicked', self._on_status_bar_use_default)

        title = self._title_block('Status bar',
                                  'Cover the camera with the bar.')
        # The preview overlays the page top: keep the title below the
        # tallest bar the slider allows.
        title.set_margin_top(max(title.get_margin_top(),
                                 status_bar.MAX_HEIGHT + 16))
        page.append(title)
        page.append(self._centered(self._sb_value, self._sb_scale))
        page.append(next_btn)
        page.append(default_btn)

        overlay = Gtk.Overlay()
        overlay.set_child(page)
        overlay.add_overlay(self._sb_strip)
        overlay.add_overlay(self._sb_row)
        return overlay

    def _show_status_bar_height(self, height: int) -> None:
        for widget in (self._sb_strip, self._sb_row):
            widget.set_size_request(-1, height)
        self._sb_value.set_text('%d px' % height)

    def _on_status_bar_slider(self, value: float) -> None:
        import status_bar
        height = status_bar.clamp_height(round(value))
        if height is None:
            return
        self._sb_height = height
        self._show_status_bar_height(height)

    def _save_status_bar_height(self, height: int | None) -> None:
        """Write `status_bar_height` (None = the device default). The
        bar reads it when the shell starts after Finish."""
        import status_bar
        # Same marker as the Keyboard step: a config written mid-setup
        # reruns the wizard after a reboot instead of skipping the PIN.
        if 'setup_complete' not in self._config.data:
            self._config.data['setup_complete'] = False
        self._config.set(status_bar.CONFIG_KEY,
                         status_bar.clamp_height(height))

    def _on_status_bar_next(self, _btn: Gtk.Widget | None) -> None:
        self._save_status_bar_height(self._sb_height)
        self._stack.set_visible_child_name('timezone')

    def _on_status_bar_use_default(self, _btn: Gtk.Widget | None) -> None:
        self._save_status_bar_height(None)
        self._stack.set_visible_child_name('timezone')

    def _on_finish(self, _btn: Gtk.Button) -> None:
        row = self._tz_list.get_selected_row()
        tz = _TIMEZONES[row.get_index() if row is not None else 0]
        # Setup is durable from here even if the walkthrough is skipped
        self._config.data['setup_complete'] = True
        self._config.save()
        self._advance_tour()
        # timedate1 answers asynchronously, so the wizard moves on while the
        # zone lands in the background. The keyboard layout was applied
        # on its own step, before the PIN.
        set_timezone_async(tz, on_done=self._on_timezone_result)

    def _on_timezone_result(self, ok: bool, message: str) -> None:
        """Timedate1 reply lands here on the main loop: success stays silent,
        failure pages back to the timezone step with the installer pointer
        instead of pretending the setting landed."""
        if ok:
            return
        from shell_log import get_logger
        get_logger('first_boot').warning('SetTimezone failed: %s', message)
        try:
            self._tz_status.set_text(_TIMEZONE_FAILURE_MESSAGE)
            self._stack.set_visible_child_name('timezone')
        except Exception as error:
            get_logger('first_boot').warning(
                'could not show timezone error: %s', error)
