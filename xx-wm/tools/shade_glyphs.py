#!/usr/bin/env python3
"""Regenerate the shade's symbolic glyphs from their stroke sources.

The shade design draws every glyph on a 24 px grid with a 1.6 px round
stroke. GTK only recolors the FILL of symbolic SVGs on older releases
(the FLX1 ships one), so each stroke is expanded to a filled outline
here and the committed icons stay fill-only:

    python3 -m venv /tmp/gv && /tmp/gv/bin/pip install skia-pathops fonttools
    /tmp/gv/bin/python xx-wm/tools/shade_glyphs.py

Writes xx-wm/launcher/src/icons/xx-shade-<name>-symbolic.svg. Edit the
GLYPHS table, never the generated files.
"""

from __future__ import annotations

from pathlib import Path

import pathops
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.svgLib.path import parse_path

STROKE = 1.6
OUT = Path(__file__).resolve().parent.parent / 'launcher' / 'src' / 'icons'

# name -> [(svg path data, 'stroke' | 'fill')]
GLYPHS: dict[str, list[tuple[str, str]]] = {
    # Quick-settings tiles
    'wifi': [('M3.5 9.5a12 12 0 0 1 17 0M6.5 12.8a7.6 7.6 0 0 1 11 0'
              'M9.5 16a3.4 3.4 0 0 1 5 0M12 19.2h.01', 'stroke')],
    'bt': [('M7 7.5l10 9-5 4.5v-18l5 4.5-10 9', 'stroke')],
    'data': [('M5 19v-3M10 19v-6.5M15 19v-10M20 19V5.5', 'stroke')],
    'airplane': [('M12 3c.9 0 1.4.8 1.4 1.8v4.6L21 14v2l-7.6-2.4v4.2'
                  'l2.3 1.7V21L12 20l-3.7 1v-1.5l2.3-1.7v-4.2L3 16v-2'
                  'l7.6-4.6V4.8C10.6 3.8 11.1 3 12 3z', 'stroke')],
    'dnd': [('M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8 12h8', 'stroke')],
    'focus': [('M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21'
               'M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z', 'stroke')],
    'saver': [('M3.5 8h14v8h-14zM20.5 10.8v2.4'
               'M11.5 9.6l-2.2 2.6h3.4l-2.2 2.6', 'stroke')],
    'captions': [('M3 6h18v12H3zM6.5 11h4M13 11h4.5M6.5 14h7M16 14h1.5',
                  'stroke')],
    'location': [('M12 21s-6.5-6.4-6.5-11.2a6.5 6.5 0 0 1 13 0'
                  'C18.5 14.6 12 21 12 21zM12 7.8a2 2 0 1 0 0 4 2 2 0 0 0 0-4z',
                  'stroke')],
    'hotspot': [('M12 12.5h.01M8.6 15.9a4.8 4.8 0 0 1 0-6.8'
                 'M15.4 9.1a4.8 4.8 0 0 1 0 6.8M5.6 18.9a9 9 0 0 1 0-12.8'
                 'M18.4 6.1a9 9 0 0 1 0 12.8', 'stroke')],
    'night': [('M19.5 14.6A7.8 7.8 0 0 1 9.4 4.5a7.8 7.8 0 1 0 10.1 10.1z',
               'stroke')],
    'tailnet': [('M12 4.2a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 0 0 0-3.8z'
                 'M5.6 16.1a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 0 0 0-3.8z'
                 'M18.4 16.1a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 0 0 0-3.8z'
                 'M11 7.9l-4.4 8.3M13 7.9l4.4 8.3M7.6 18h8.8', 'stroke')],
    # Same language, drawn for this build: a filled-ring record mark,
    # a sun with an A for auto brightness, a flashlight for the torch.
    'record': [('M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z', 'stroke'),
               ('M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z', 'fill')],
    'auto_br': [('M12 2.5v1.8M12 19.7v1.8M2.5 12h1.8M19.7 12h1.8'
                 'M5.3 5.3l1.3 1.3M17.4 17.4l1.3 1.3M5.3 18.7l1.3-1.3'
                 'M17.4 6.6l1.3-1.3'
                 'M9 16l3-8 3 8M10.1 13.2h3.8', 'stroke')],
    'torch': [('M8 3h8v3.2l-2 3V21h-4V9.2l-2-3zM8 6.2h8M12 12.5v2.5',
               'stroke')],
    # Header, section and slider glyphs
    'settings': [('M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2.5v2.2'
                  'M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6'
                  'M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6',
                  'stroke')],
    'power': [('M12 3v8M7.1 6.3a7.5 7.5 0 1 0 9.8 0', 'stroke')],
    'expand': [('M6 9l6 6 6-6', 'stroke')],
    # Thin chevrons: Settings rows, the Settings back bar, the calendar.
    'forward': [('M9 5.5l6.5 6.5-6.5 6.5', 'stroke')],
    'back': [('M15 5.5L8.5 12l6.5 6.5', 'stroke')],
    # Media transport (filled, like every player's keys)
    'play': [('M8 5.6V18.4Q8 19.3 8.8 18.8L18.6 12.7Q19.4 12 18.6 11.3'
              'L8.8 5.2Q8 4.7 8 5.6Z', 'fill')],
    'pause': [('M6.5 5.5H10.3V18.5H6.5ZM13.7 5.5H17.5V18.5H13.7Z', 'fill')],
    'prev': [('M5.5 6H7.5V18H5.5ZM18.5 6.9V17.1Q18.5 18 17.7 17.5L9.9 12.6'
              'Q9.1 12 9.9 11.4L17.7 6.5Q18.5 6 18.5 6.9Z', 'fill')],
    'next': [('M18.5 6H16.5V18H18.5ZM5.5 6.9V17.1Q5.5 18 6.3 17.5L14.1 12.6'
              'Q14.9 12 14.1 11.4L6.3 6.5Q5.5 6 5.5 6.9Z', 'fill')],
    'collapse': [('M6 15l6-6 6 6', 'stroke')],
    'brightness': [('M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z'
                    'M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2'
                    'M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4'
                    'M17.3 6.7l1.4-1.4', 'stroke')],
    'volume': [('M4 9.5h3.5L12 6v12l-4.5-3.5H4zM15.5 9.2a4 4 0 0 1 0 5.6'
                'M18.2 6.6a7.6 7.6 0 0 1 0 10.8', 'stroke')],
}


def _outline(parts: list[tuple[str, str]]) -> str:
    shapes = []
    for data, mode in parts:
        path = pathops.Path()
        parse_path(data, path.getPen())
        if mode == 'stroke':
            path.stroke(STROKE, pathops.LineCap.ROUND_CAP,
                        pathops.LineJoin.ROUND_JOIN, 4)
            path.convertConicsToQuads()
        shapes.append(path)
    merged = pathops.Path()
    pathops.union(shapes, merged.getPen())
    pen = SVGPathPen(None, ntos=lambda v: f'{round(v, 3):g}')
    merged.draw(pen)
    return pen.getCommands()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, parts in GLYPHS.items():
        target = OUT / f"xx-shade-{name.replace('_', '-')}-symbolic.svg"
        target.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
            'viewBox="0 0 24 24">'
            f'<path fill="#2e3436" d="{_outline(parts)}"/></svg>\n',
            encoding='utf-8')
        print(target.name)


if __name__ == '__main__':
    main()
