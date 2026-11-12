"""Keybind sheet: Super+/ shows the window manager's live key bindings
as a centered, read-only sheet on the focused monitor.

The table comes from xxwm's `binds` request, so wm.conf overrides and
unbinds show as they are. Rows are compacted the way a person reads
them: H/J/K/L and the arrow keys for one verb become one row, Super+1..0
for one verb becomes one row, two keys for the same action share a row.
Escape, Super+/ again, or a click outside the sheet closes it.

Headless-safe: without GTK only the pure helpers exist.
"""
from __future__ import annotations

import math
import os

_HAS_GTK = True
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gdk, Gtk, Pango
    from gi.repository import Gtk4LayerShell as LayerShell
except (ImportError, ValueError):
    _HAS_GTK = False

SECTIONS = ('Windows', 'Workspaces', 'Shell', 'Apps', 'Media')

COLUMN_WIDTH = 330
CAPTION_ROWS = 2

_KEY_NAMES = {
    'slash': '/', 'grave': '`', 'backslash': '\\', 'minus': '-',
    'equal': '=', 'comma': ',', 'period': '.', 'semicolon': ';',
    'apostrophe': "'", 'bracketleft': '[', 'bracketright': ']',
    'return': 'Enter', 'enter': 'Enter', 'escape': 'Esc', 'esc': 'Esc',
    'xf86audioplay': 'Play', 'xf86audionext': 'Next',
    'xf86audioprev': 'Previous', 'xf86audioraisevolume': 'Volume up',
    'xf86audiolowervolume': 'Volume down', 'xf86audiomute': 'Mute',
    'xf86audiomicmute': 'Mic mute', 'xf86monbrightnessup': 'Brightness up',
    'xf86monbrightnessdown': 'Brightness down', 'xf86poweroff': 'Power',
}

_PLAIN_ACTIONS = {
    'close': 'Close',
    'launcher': 'Launcher',
    'fullscreen': 'Fullscreen',
    'float': 'Float',
    'toggle-split': 'Toggle split',
}

_SHELL_VERBS = {
    'bar': 'Top bar',
    'keybinds': 'Keybinds',
    'launcher': 'Launcher',
}

_DIR_VERBS = {'focus': 'Focus', 'move': 'Move', 'resize': 'Resize'}
_WS_VERBS = {
    'workspace': 'Go to workspace',
    'send': 'Send window',
    'send-silent': 'Send window, stay',
}

_HJKL = {'h': 'left', 'j': 'down', 'k': 'up', 'l': 'right'}
_ARROWS = {'left': 'left', 'down': 'down', 'up': 'up', 'right': 'right'}

_WRAPPERS = ('kitty', 'bash', 'sh', 'exec', 'env', 'foot', 'alacritty')


def pretty_key(key: str) -> str:
    named = _KEY_NAMES.get(key.casefold())
    if named:
        return named
    if len(key) == 1:
        return key.upper()
    return key[:1].upper() + key[1:]


def pretty_combo(combo: str) -> str:
    """`Super+Shift+slash` -> `Super+Shift+/`."""
    parts = [p for p in (combo or '').split('+') if p]
    if not parts:
        return ''
    return '+'.join(parts[:-1] + [pretty_key(parts[-1])])


def _split_combo(combo: str) -> tuple[str, str]:
    parts = [p for p in (combo or '').split('+') if p]
    if not parts:
        return '', ''
    return '+'.join(parts[:-1]), parts[-1]


def _sentence(text: str) -> str:
    text = ' '.join(text.split())
    return text[:1].upper() + text[1:]


def spawn_label(command: str) -> str:
    """A readable name for a spawn command: the first alternative, without
    terminal and shell wrappers; scripts by file name, flags as words.
    `kitty ~/.scripts/Note-Scripts/open_daily_note.sh --split` ->
    `Open daily note split`."""
    first = (command or '').split('||', 1)[0].strip()
    tokens = first.split()
    while len(tokens) > 1 and tokens[0] in _WRAPPERS:
        tokens = tokens[1:]
    if len(tokens) > 2 and tokens[0] == 'flatpak' and tokens[1] == 'run':
        tokens = tokens[2:]
    if not tokens:
        return ''
    head, args = tokens[0], tokens[1:]
    if head == 'xx-wm-ipc' and args:
        return _sentence(args[0].replace('.', ' '))
    words: list[str] = []
    if '/' in head or head.endswith('.sh'):
        name = os.path.basename(head)
        name = name.rsplit('.', 1)[0] if '.' in name else name
        # `wm-` only says the script belongs to the window manager.
        if name.startswith('wm-') and len(name) > 3:
            name = name[3:]
        words.append(name.replace('-', ' ').replace('_', ' '))
        for arg in args:
            if arg.startswith('--'):
                words.append(arg[2:].replace('-', ' '))
            elif not arg.startswith(('-', '~', '/', '$')):
                words.append(arg)
    else:
        if head.count('.') >= 2:
            head = head.rsplit('.', 1)[-1]
        words.append(head)
        words.extend(a for a in args
                     if not a.startswith(('-', '~', '/', '$')))
    return _sentence(' '.join(words))


def action_label(action: str) -> str:
    verb, _, arg = (action or '').partition(' ')
    if verb in _PLAIN_ACTIONS:
        return _PLAIN_ACTIONS[verb]
    if verb == 'shell':
        return _SHELL_VERBS.get(arg, _sentence(arg.replace('-', ' ')))
    if verb in _DIR_VERBS:
        return f'{_DIR_VERBS[verb]} {arg}'
    if verb in _WS_VERBS:
        return f'{_WS_VERBS[verb]} {arg}'
    if verb == 'spawn':
        return spawn_label(arg)
    return _sentence(action or '')


def section_of(combo: str, action: str) -> str:
    verb, _, arg = (action or '').partition(' ')
    if verb in _DIR_VERBS or verb in _PLAIN_ACTIONS and verb != 'launcher':
        return 'Windows'
    if verb in _WS_VERBS:
        return 'Workspaces'
    if verb == 'launcher' or verb == 'shell':
        return 'Shell'
    if verb == 'spawn' and arg.startswith('xx-wm-ipc'):
        return 'Shell'
    _mods, key = _split_combo(combo)
    if key.casefold().startswith('xf86'):
        return 'Media'
    return 'Apps'


def _compact_dirs(binds: list[tuple[str, str]], used: set[int]) -> list:
    """Rows for focus/move/resize where one modifier set covers all four
    directions with H/J/K/L or with the arrows."""
    rows = []
    for verb in _DIR_VERBS:
        by_mods: dict[tuple[str, str], dict[str, tuple[int, str]]] = {}
        for i, (combo, action) in enumerate(binds):
            v, _, direction = action.partition(' ')
            if v != verb:
                continue
            mods, key = _split_combo(combo)
            k = key.casefold()
            for family, table in (('hjkl', _HJKL), ('arrows', _ARROWS)):
                if table.get(k) == direction:
                    by_mods.setdefault((mods, family), {})[direction] = (i, k)
        keys = []
        first = None
        for (mods, family), found in by_mods.items():
            if len(found) != 4:
                continue
            idx = [i for i, _k in found.values()]
            used.update(idx)
            first = min(idx) if first is None else min(first, *idx)
            tail = 'H/J/K/L' if family == 'hjkl' else 'arrows'
            keys.append(f'{mods}+{tail}' if mods else tail)
        if keys:
            rows.append((first, '  '.join(keys), _DIR_VERBS[verb]))
    return rows


def _compact_workspaces(binds: list[tuple[str, str]], used: set[int]) -> list:
    rows = []
    for verb in _WS_VERBS:
        by_mods: dict[str, dict[int, int]] = {}
        for i, (combo, action) in enumerate(binds):
            v, _, arg = action.partition(' ')
            if v != verb:
                continue
            mods, key = _split_combo(combo)
            if len(key) == 1 and key.isdigit():
                n = int(key) or 10
                if str(n) == arg:
                    by_mods.setdefault(mods, {})[n] = i
        for mods, found in by_mods.items():
            if sorted(found) != list(range(1, 11)):
                continue
            used.update(found.values())
            keys = f'{mods}+1…0' if mods else '1…0'
            rows.append((min(found.values()), keys, _WS_VERBS[verb]))
    return rows


def bind_sections(binds: list) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(section, [(keys, label), ...]), ...] in SECTIONS order, rows in
    bind-table order, compacted for reading."""
    table: list[tuple[str, str]] = []
    for b in binds or []:
        if not isinstance(b, dict):
            continue
        combo, action = b.get('combo'), b.get('action')
        if isinstance(combo, str) and isinstance(action, str) and combo:
            table.append((combo, action))
    used: set[int] = set()
    rows: list[tuple[int, str, str, str]] = []
    for first, keys, label in (_compact_dirs(table, used)
                               + _compact_workspaces(table, used)):
        rows.append((first, section_of(*table[first]), keys, label))
    by_action: dict[str, int] = {}
    for i, (combo, action) in enumerate(table):
        if i in used:
            continue
        keys = pretty_combo(combo)
        if action in by_action:
            j = by_action[action]
            first, section, prev, label = rows[j]
            rows[j] = (first, section, f'{prev}  {keys}', label)
            continue
        by_action[action] = len(rows)
        rows.append((i, section_of(combo, action), keys, action_label(action)))
    rows.sort(key=lambda r: r[0])
    out = []
    for section in SECTIONS:
        items = [(keys, label) for _i, s, keys, label in rows if s == section]
        if items:
            out.append((section, items))
    return out


def column_count(width: int) -> int:
    return max(1, min(3, (int(width) - 96) // COLUMN_WIDTH))


def split_columns(sections: list, columns: int) -> list[list[tuple]]:
    """Flow captions and rows into ``columns`` columns of about equal
    height. A section that would overflow a column well filled already
    starts the next one; a long section continues into the next column.
    A column never ends on a caption. Items are ('caption', title) and
    ('row', keys, label)."""
    columns = max(1, int(columns))
    remaining = sum(CAPTION_ROWS + len(rows) for _t, rows in sections)
    out: list[list[tuple]] = [[]]
    height = 0
    target = math.ceil(remaining / columns) if sections else 0

    def room() -> bool:
        return len(out) < columns and bool(out[-1])

    def next_column() -> int:
        out.append([])
        # Spread what is left over the columns left.
        return math.ceil(remaining / (columns - len(out) + 1))

    for title, rows in sections:
        size = CAPTION_ROWS + len(rows)
        if (room() and height + size > target * 1.15
                and height >= target * 0.6):
            target = next_column()
            height = 0
        out[-1].append(('caption', title))
        height += CAPTION_ROWS
        remaining -= CAPTION_ROWS
        for keys, label in rows:
            if room() and height >= target and out[-1][-1][0] != 'caption':
                target = next_column()
                height = 0
            out[-1].append(('row', keys, label))
            height += 1
            remaining -= 1
    return out


def theme_css(preset) -> str:
    bg = preset.background
    fg = preset.foreground
    return f"""
.xx-keybinds-panel {{
    background: {bg};
    border: 1px solid alpha({fg}, 0.16);
    border-radius: 8px;
    padding: 20px 24px 22px 24px;
}}
.xx-keybinds-col {{
    min-width: {COLUMN_WIDTH - 30}px;
}}
.xx-keybinds-caption {{
    font-size: 10px;
    letter-spacing: 0.18em;
    color: {preset.muted};
    margin: 14px 0 6px 0;
}}
.xx-keybinds-caption.first {{
    margin-top: 0;
}}
.xx-keybinds-label {{
    font-size: 13px;
    color: alpha({fg}, 0.90);
}}
.xx-keybinds-keys {{
    font-size: 12px;
    font-feature-settings: "tnum";
    color: {preset.muted};
}}
.xx-keybinds-row {{
    min-height: 24px;
}}
"""


if _HAS_GTK:

    class KeybindSheet(Gtk.Window):
        """A transparent overlay over one monitor holding the centered
        sheet; keyboard-exclusive while shown so Escape reaches it."""

        def __init__(self) -> None:
            super().__init__()
            self.set_decorated(False)
            self.add_css_class('xx-keybinds')
            from desk_bar import see_through
            see_through(self)
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, 'xx-wm-keybinds')
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(
                self, LayerShell.KeyboardMode.EXCLUSIVE)

            self._panel = Gtk.Box()
            self._panel.add_css_class('xx-keybinds-panel')
            self._panel.set_halign(Gtk.Align.CENTER)
            self._panel.set_valign(Gtk.Align.CENTER)
            self._scroller = Gtk.ScrolledWindow()
            self._scroller.set_policy(Gtk.PolicyType.NEVER,
                                      Gtk.PolicyType.AUTOMATIC)
            self._scroller.set_propagate_natural_height(True)
            self._scroller.set_propagate_natural_width(True)
            self._panel.append(self._scroller)
            self.set_child(self._panel)

            keys = Gtk.EventControllerKey()
            keys.connect('key-pressed', self._on_key)
            self.add_controller(keys)
            click = Gtk.GestureClick()
            click.connect('released', self._on_click)
            self.add_controller(click)

        def show_on(self, monitor, binds: list) -> None:
            geometry = monitor.get_geometry() if monitor is not None else None
            width = geometry.width if geometry is not None else 1280
            height = geometry.height if geometry is not None else 720
            self._scroller.set_max_content_height(max(200, height - 120))
            self._fill(bind_sections(binds), column_count(width))
            if monitor is not None:
                LayerShell.set_monitor(self, monitor)
            self.present()

        def close_sheet(self) -> None:
            self.set_visible(False)

        def _fill(self, sections: list, columns: int) -> None:
            grid = Gtk.Box(spacing=36)
            for column in split_columns(sections, columns):
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                col.add_css_class('xx-keybinds-col')
                for n, item in enumerate(column):
                    if item[0] == 'caption':
                        label = Gtk.Label(label=item[1].upper(), xalign=0)
                        label.add_css_class('xx-keybinds-caption')
                        if n == 0:
                            label.add_css_class('first')
                        col.append(label)
                        continue
                    row = Gtk.Box(spacing=16)
                    row.add_css_class('xx-keybinds-row')
                    title = Gtk.Label(label=item[2], xalign=0)
                    title.add_css_class('xx-keybinds-label')
                    title.set_hexpand(True)
                    title.set_ellipsize(Pango.EllipsizeMode.END)
                    keys = Gtk.Label(label=item[1], xalign=1)
                    keys.add_css_class('xx-keybinds-keys')
                    row.append(title)
                    row.append(keys)
                    col.append(row)
                grid.append(col)
            self._scroller.set_child(grid)

        def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
            if keyval == Gdk.KEY_Escape:
                self.close_sheet()
                return True
            return False

        def _on_click(self, _gesture, _n, x, y) -> None:
            ok, rect = self._panel.compute_bounds(self)
            if ok and rect.contains_point(_point(x, y)):
                return
            self.close_sheet()

    def _point(x: float, y: float):
        from gi.repository import Graphene
        p = Graphene.Point()
        p.init(x, y)
        return p
