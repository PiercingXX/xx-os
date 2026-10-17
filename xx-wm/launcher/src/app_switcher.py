from __future__ import annotations

import logging
from collections.abc import Callable

from app_names import fallback_app_name
from config import DANGER_RED, DESTRUCTIVE_TINT_BG, ShellConfig, ThemePreset
from toplevel_manager import ToplevelManager

log = logging.getLogger(__name__)

_LAYER_SHELL = False
_GTK_AVAILABLE = True
try:
    import gi

    gi.require_version('Gtk', '4.0')
    from gi.repository import Gdk, GLib, Gtk

    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        from gi.repository import Gtk4LayerShell as LayerShell
        _LAYER_SHELL = True
    except ValueError:
        pass
except (ImportError, ValueError) as exc:
    # GTK/PyGObject is unavailable (ImportError) or the required version is
    # missing (ValueError). The switcher degrades to its static card and
    # empty-state seams so the logic stays testable headlessly; the window
    # itself is never constructed in this mode.
    log.info('GTK unavailable; switcher degrades to headless seams: %s', exc)
    _GTK_AVAILABLE = False
    # Bind the GTK module names to None so that any code path that touches them
    # in headless mode fails fast with a clear AttributeError instead of a
    # NameError. The static headless seams below never reference these names;
    # the window itself is never constructed while _GTK_AVAILABLE is False.
    Gdk = None  # type: ignore[assignment,misc]
    GLib = None  # type: ignore[assignment,misc]
    Gtk = None  # type: ignore[assignment,misc]
    LayerShell = None  # type: ignore[assignment,misc]


class _WindowBase:
    """Base class used only when GTK is unavailable (headless seam tests).

    The switcher's logic is exercised headlessly through the static seams
    (_apps_from_manager, _card_labels, _focus_app_with_manager, ...); the
    Gtk.Window itself is never constructed in this mode. If construction is
    nonetheless attempted, fail loudly with an actionable message rather than
    letting super().__init__ raise a confusing AttributeError deep inside
    AppSwitcher.__init__.
    """

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            'AppSwitcher cannot be constructed: GTK/PyGObject is unavailable '
            'in this environment. Use the static headless seams '
            '(AppSwitcher._apps_from_manager, _card_labels, '
            '_focus_app_with_manager, _kill_app_with_manager) to exercise the '
            'switcher logic without a display.'
        )


_AppSwitcherBase = Gtk.Window if _GTK_AVAILABLE else _WindowBase

def theme_css(preset: ThemePreset) -> str:
    # Opaque background: the home clock and dock must not show through
    # behind the cards. The app name is a caption under each card.
    return f"""
window.switcher-window {{
    background: {preset.background};
}}
.switcher-root {{
    background: transparent;
    color: {preset.foreground};
}}
.switcher-dismiss {{
    background: transparent;
    border: none;
    box-shadow: none;
    min-height: 0;
}}
.switcher-header {{
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: {preset.muted};
}}
.switcher-empty {{
    font-size: 11pt;
    color: {preset.muted};
}}
.app-card {{
    background: {preset.surface};
    border-radius: 16px;
    padding: 12px;
}}
.app-card:hover, .app-card:focus {{ background: {preset.surface_alt}; }}
.app-card-thumb {{
    border-radius: 8px;
}}
.card-name {{
    font-size: 11pt;
    font-weight: 400;
    color: {preset.muted};
}}
.card-title {{
    font-size: 10pt;
    color: {preset.muted};
}}
.card-kill {{
    color: {preset.muted};
    min-width: 32px;
    min-height: 32px;
    border-radius: 16px;
    padding: 0;
    background: transparent;
    border: none;
}}
.card-kill:hover {{
    background: {DESTRUCTIVE_TINT_BG};
    color: {DANGER_RED};
}}
"""

_SWIPE_DISMISS_THRESHOLD = 120  # px upward drag to dismiss a card
_SHEET_CLOSE_DY = 100  # px downward drag to close recents (resume the app)
_EMPTY_STATE_TEXT = 'No open apps.'
# Pixel-style overview: one large centered card, not a 280px bottom sheet.
_CARD_WIDTH_FRAC = 0.82
_CARD_HEIGHT_FRAC = 0.68
_CARD_FALLBACK = (280, 420)

_APP_NAME_CACHE: dict[str, str] = {}


def resolve_app_name(app_id: str) -> str:
    """Human display name for a toplevel ``app_id``.

    Results are cached per process; unresolvable ids fall back to the
    id's last component (shared with the settings surfaces) so a card
    always has a short first line.
    """
    cached = _APP_NAME_CACHE.get(app_id)
    if cached is not None:
        return cached
    name = _resolve_app_name(app_id) or fallback_app_name(app_id)
    _APP_NAME_CACHE[app_id] = name
    return name


def _resolve_app_name(app_id: str) -> str | None:
    if not app_id:
        return None
    desktop_id = app_id if app_id.endswith('.desktop') else f'{app_id}.desktop'
    info = _desktop_info_for(desktop_id)
    if info is not None:
        name = _display_name_of(info)
        if name:
            return name
    target = app_id.casefold()
    if target.endswith('.desktop'):
        target = target[: -len('.desktop')]
    for info in _iter_desktop_infos():
        try:
            wm_class = info.get_startup_wm_class() or ''
        except Exception:
            wm_class = ''
        try:
            info_id = info.get_id() or ''
        except Exception:
            info_id = ''
        base = info_id[: -len('.desktop')] if info_id.endswith('.desktop') else info_id
        if (wm_class and wm_class.casefold() == target) or (base and base.casefold() == target):
            name = _display_name_of(info)
            if name:
                return name
    return None


def _desktop_info_for(desktop_id: str):
    """DesktopAppInfo for an exact desktop id, or None (never raises)."""
    try:
        from gi.repository import Gio
    except Exception:
        return None
    try:
        return Gio.DesktopAppInfo.new(desktop_id)
    except Exception:
        return None


def _iter_desktop_infos() -> list:
    """All installed DesktopAppInfo entries, or [] (never raises)."""
    try:
        from gi.repository import Gio
    except Exception:
        return []
    try:
        all_infos = Gio.AppInfo.get_all()
    except Exception:
        return []
    try:
        desktop_cls = Gio.DesktopAppInfo
    except Exception:
        return []
    return [info for info in all_infos if isinstance(info, desktop_cls)]


def _display_name_of(info) -> str | None:
    for method in ('get_display_name', 'get_name'):
        try:
            value = getattr(info, method)()
        except Exception:
            continue
        if value and str(value).strip():
            return str(value).strip()
    return None


class AppInfo:
    __slots__ = ('app_id', 'handle', 'title')

    def __init__(self, app_id: str, title: str, handle: object | None = None) -> None:
        self.app_id = app_id
        self.title = title
        self.handle = handle


class AppSwitcher(_AppSwitcherBase):
    """Full-screen recents (Pixel overview). Long swipe-up.

    Tap dim / swipe down / back resume the current app. Card swipe-up or
    the close icon closes that app. Short swipe-up is lisgd home, not
    this surface.
    """

    def __init__(self, manager: ToplevelManager | None = None,
                 config: ShellConfig | None = None,
                 thumbnails: object | None = None,
                 on_activated: Callable[[], None] | None = None,
                 nope_state: object | None = None,
                 on_nope: Callable[[object], None] | None = None) -> None:
        super().__init__(title='PiercingXX Switcher')
        self.add_css_class('switcher-window')

        # The shell window threads its LIVE config so theme == 'custom'
        # renders the derived palette; the fallback keeps direct no-arg
        # construction working (pre-existing snapshot behavior).
        self._config = config if config is not None else ShellConfig()
        # Hands the screen to the activated app (the shell drops to the
        # background layer); stays None for standalone use.
        self._on_activated = on_activated
        self._nope = nope_state
        self._on_nope = on_nope

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            # Full-screen OVERLAY. The window itself is the dim fill —
            # a transparent 4-edge surface never composited on this phoc.
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_exclusive_zone(self, -1)
            # NONE while hidden; show_switcher flips EXCLUSIVE so Escape
            # and taps reach this overlay (phoc v3 has no ON_DEMAND).
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(360, 504)

        self._manager = manager if manager is not None else ToplevelManager()
        self._thumbs = thumbnails
        self._apps: list[AppInfo] = []
        self._card_swiping = False
        self._laid_out: tuple[int, int] | None = None
        self._scroller: object | None = None

        self._theme_provider = Gtk.CssProvider()
        self.apply_theme()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self.card_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=False,
        )

        self.set_child(self._build_content())

        self._wire_change(self._manager, self.refresh)
        self._manager_wired = True
        if thumbnails is not None and hasattr(thumbnails, 'on_change'):
            thumbnails.on_change(self.refresh)
        self.refresh()

        swipe = Gtk.GestureSwipe.new()
        swipe.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        swipe.connect('swipe', self._on_swipe)
        self.add_controller(swipe)

        sheet_drag = Gtk.GestureDrag.new()
        sheet_drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        sheet_drag.connect('drag-update', self._on_sheet_drag_update)
        sheet_drag.connect('drag-end', self._on_sheet_drag_end)
        self.add_controller(sheet_drag)

        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        self.add_controller(key)

    def attach_manager(self, manager: ToplevelManager) -> None:
        """Swap in the live toplevel client after the window is already shown."""
        if manager is self._manager:
            self.refresh()
            return
        self._manager = manager
        self._wire_change(manager, self.refresh)
        self._manager_wired = True
        self.refresh()

    def _display_preset(self) -> ThemePreset:
        """Preset this surface renders with: window.resolve_theme over the
        injected config, so theme == 'custom' derives its palette instead of
        falling back to the default preset (W1-B). Lazy import: window.py
        sits above this module in the shell stack, and a module-level import
        would drag its GTK requirements into headless contexts that import
        app_switcher for its static seams.
        """
        from window import resolve_theme
        return resolve_theme(self._config)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._display_preset())
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build_content(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class('switcher-root')
        outer.set_hexpand(True)
        outer.set_vexpand(True)

        header = Gtk.Label(label='OPEN APPS', xalign=0.5)
        header.add_css_class('switcher-header')
        header.set_margin_top(24)
        header.set_margin_bottom(8)

        self.card_box.set_halign(Gtk.Align.CENTER)
        self.card_box.set_valign(Gtk.Align.CENTER)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_child(self.card_box)
        scroller.set_halign(Gtk.Align.CENTER)
        scroller.set_valign(Gtk.Align.CENTER)
        cw, ch = self._card_pixel_size(0, 0)
        scroller.set_size_request(cw, ch)
        self._scroller = scroller

        def _dismiss_pad() -> Gtk.Widget:
            pad = Gtk.Box()
            pad.add_css_class('switcher-dismiss')
            pad.set_hexpand(True)
            pad.set_vexpand(True)
            tap = Gtk.GestureClick.new()
            tap.connect('released', lambda *_: self.hide_switcher())
            pad.add_controller(tap)
            return pad

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        body.append(_dismiss_pad())
        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        mid.set_vexpand(True)
        mid.append(_dismiss_pad())
        mid.append(scroller)
        mid.append(_dismiss_pad())
        body.append(mid)
        body.append(_dismiss_pad())

        outer.append(header)
        outer.append(body)
        return outer

    @staticmethod
    def _card_pixel_size(width: int, height: int) -> tuple[int, int]:
        """Headless seam: recents card size as a fraction of the overlay."""
        if width <= 0 or height <= 0:
            return _CARD_FALLBACK
        return (
            max(200, int(width * _CARD_WIDTH_FRAC)),
            max(280, int(height * _CARD_HEIGHT_FRAC)),
        )

    def _relayout_cards(self) -> bool:
        if not self.get_visible():
            return False
        width = self.get_width() if hasattr(self, 'get_width') else 0
        height = self.get_height() if hasattr(self, 'get_height') else 0
        size = self._card_pixel_size(width, height)
        if self._laid_out == size:
            return False
        self._laid_out = size
        scroller = getattr(self, '_scroller', None)
        if scroller is not None:
            scroller.set_size_request(*size)
        self._rebuild_cards()
        return False

    def refresh(self, apps: list[AppInfo] | None = None) -> None:
        if apps is None:
            apps = self._apps_from_manager(self._manager)
        self._apps = apps
        self._rebuild_cards()

    @staticmethod
    def _apps_from_manager(manager: ToplevelManager) -> list[AppInfo]:
        """Headless seam: manager.list() -> the AppInfo records rows render from."""
        return [
            AppInfo(t.app_id, t.title, handle=t.handle)
            for t in manager.list()
        ]

    @staticmethod
    def _empty_state_label() -> str:
        """Headless seam: the message shown when there are no open apps."""
        return _EMPTY_STATE_TEXT

    @staticmethod
    def _card_name(app: AppInfo) -> str:
        """Headless seam: the first-line display name a row renders."""
        return resolve_app_name(app.app_id)

    @staticmethod
    def _card_subtitle(app: AppInfo) -> str:
        """Headless seam: the second-line window title, '' when it adds
        nothing (empty, or identical to the display name)."""
        title = (app.title or '').strip()
        if not title or title == AppSwitcher._card_name(app):
            return ''
        return title

    @staticmethod
    def _card_labels(apps: list[AppInfo]) -> list[str]:
        """Headless seam: the ordered first-line names the rows render from."""
        return [AppSwitcher._card_name(a) for a in apps]

    @staticmethod
    def _wire_change(manager: ToplevelManager, callback) -> None:
        """Headless seam: register the switcher's refresh on manager changes."""
        manager.on_change(callback)

    @staticmethod
    def _focus_app_with_manager(manager: ToplevelManager, app: AppInfo,
                                on_activated: Callable[[], None] | None = None) -> None:
        """Headless seam: activating a row dispatches to the manager, then
        hands the screen to the app via on_activated."""
        if app.handle is not None:
            manager.activate(app.handle)
        if on_activated is not None:
            on_activated()

    @staticmethod
    def _kill_app_with_manager(manager: ToplevelManager, app: AppInfo) -> None:
        """Headless seam: killing a row dispatches to the manager."""
        if app.handle is not None:
            manager.close(app.handle)

    def _rebuild_cards(self) -> None:
        if not hasattr(self, 'card_box'):
            return
        child = self.card_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.card_box.remove(child)
            child = nxt

        if not self._apps:
            empty = Gtk.Label(label=_EMPTY_STATE_TEXT, xalign=0.5)
            empty.add_css_class('switcher-empty')
            empty.set_hexpand(True)
            empty.set_vexpand(True)
            empty.set_valign(Gtk.Align.CENTER)
            empty.set_halign(Gtk.Align.CENTER)
            self.card_box.append(empty)
            return

        for app in self._apps:
            self.card_box.append(self._make_card(app))

    @staticmethod
    def _thumb_for_app(store: object | None, app: AppInfo) -> object | None:
        """Headless seam: last-seen still for this app, if the store has one."""
        getter = getattr(store, 'get', None)
        if getter is None:
            return None
        return getter(app.app_id)

    @staticmethod
    def _texture_for_thumb(thumb: object) -> object | None:
        if thumb is None or Gdk is None or GLib is None:
            return None
        width = int(getattr(thumb, 'width', 0))
        height = int(getattr(thumb, 'height', 0))
        rgba = getattr(thumb, 'rgba', b'')
        if width <= 0 or height <= 0 or not rgba:
            return None
        fmt = getattr(Gdk, 'MemoryFormat', None)
        if fmt is None:
            return None
        mem = getattr(fmt, 'R8G8B8A8', None) or getattr(fmt, 'R8G8B8A8_PREMULTIPLIED', None)
        if mem is None:
            return None
        try:
            return Gdk.MemoryTexture.new(
                width, height, mem, GLib.Bytes.new(rgba), width * 4)
        except Exception:
            return None

    def _make_card(self, app: AppInfo) -> Gtk.Widget:
        from gi.repository import Pango
        caption = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        caption.set_hexpand(True)
        name_label = Gtk.Label(label=self._card_name(app).upper(), xalign=0.5)
        name_label.add_css_class('card-name')
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(24)
        caption.append(name_label)
        subtitle = self._card_subtitle(app)
        if subtitle:
            title_label = Gtk.Label(label=subtitle, xalign=0.5)
            title_label.add_css_class('card-title')
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_max_width_chars(24)
            caption.append(title_label)

        kill_btn = Gtk.Button.new_from_icon_name('window-close-symbolic')
        kill_btn.add_css_class('card-kill')
        kill_btn.set_halign(Gtk.Align.END)
        kill_claim = Gtk.GestureClick.new()
        kill_claim.connect(
            'pressed',
            lambda g, *_: g.set_state(Gtk.EventSequenceState.CLAIMED),
        )
        kill_btn.add_controller(kill_claim)
        kill_btn.connect('clicked', lambda _b, a=app: self._kill_app(a))

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.add_css_class('app-card')
        inner.set_hexpand(False)
        inner.set_vexpand(False)
        inner.set_valign(Gtk.Align.CENTER)
        width = self.get_width() if hasattr(self, 'get_width') else 0
        height = self.get_height() if hasattr(self, 'get_height') else 0
        inner.set_size_request(*self._card_pixel_size(width, height))
        inner.append(kill_btn)
        texture = self._texture_for_thumb(self._thumb_for_app(self._thumbs, app))
        if texture is not None:
            pic = Gtk.Picture.new_for_paintable(texture)
            pic.add_css_class('app-card-thumb')
            pic.set_hexpand(True)
            pic.set_vexpand(True)
            pic.set_can_shrink(True)
            fit = getattr(Gtk, 'ContentFit', None)
            if fit is not None:
                pic.set_content_fit(fit.COVER)
            inner.append(pic)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_valign(Gtk.Align.CENTER)
        outer.append(inner)
        outer.append(caption)

        # A wrapping Gtk.Button never saw clicked: the inner GestureDrag
        # stole the sequence. Tap is a click on the card; swipe-up kills.
        # Both live on the outer box so the caption taps through to focus.
        click = Gtk.GestureClick.new()
        click.connect(
            'released',
            lambda _g, _n, _x, _y, a=app: self._on_card_tap(a),
        )
        outer.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect(
            'drag-update',
            lambda g, _dx, dy, card=outer: self._on_card_drag(g, card, dy),
        )
        drag.connect(
            'drag-end',
            lambda _g, _dx, dy, a=app: self._on_card_drag_end(dy, a),
        )
        outer.add_controller(drag)

        return outer

    def _on_card_drag(self, gesture: Gtk.GestureDrag, card: Gtk.Box, dy: float) -> None:
        if dy < -20:
            self._card_swiping = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if dy < 0:
            card.set_margin_bottom(max(0, int(abs(dy))))

    def _on_card_drag_end(self, dy: float, app: AppInfo) -> None:
        swiping = self._card_swiping
        self._card_swiping = False
        if dy < -_SWIPE_DISMISS_THRESHOLD:
            self._kill_app(app)
        elif swiping:
            self._rebuild_cards()

    def _on_card_tap(self, app: AppInfo) -> None:
        if self._card_swiping:
            return
        self._focus_app(app)

    def _focus_app(self, app: AppInfo) -> None:
        try:
            nope = getattr(self, '_nope', None)
            blocked = bool(nope is not None and nope.is_blocked(app.app_id))
        except Exception:
            blocked = False
        if blocked:
            self.hide_switcher()
            callback = getattr(self, '_on_nope', None)
            if callable(callback):
                try:
                    callback(app)
                except Exception:
                    pass
            return
        self._focus_app_with_manager(self._manager, app, self._on_activated)
        self.hide_switcher()

    def _kill_app(self, app: AppInfo) -> None:
        self._kill_app_with_manager(self._manager, app)
        self._apps = [a for a in self._apps if a.handle != app.handle]
        self._rebuild_cards()

    def _on_sheet_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        # Claim only a downward pull so card swipe-up can still kill an app.
        if dy > 40 and abs(dy) > abs(dx):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_sheet_drag_end(self, _g: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if dy > _SHEET_CLOSE_DY and abs(dy) > abs(dx):
            self.hide_switcher()

    def _on_swipe(self, _g: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        if vel_y > 200 and abs(vel_y) > abs(vel_x):
            self.hide_switcher()

    def _on_key(self, _g: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_switcher()
            return True
        return False

    def show_switcher(self) -> None:
        self.refresh()
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        self.set_visible(True)
        self.present()
        GLib.idle_add(self._relayout_cards)

    def hide_switcher(self) -> None:
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.set_visible(False)
        self.hide()
