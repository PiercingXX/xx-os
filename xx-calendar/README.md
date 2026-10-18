# xx-calendar (Linux)

Linux phone port of the PiercingXX Android app `xx-calendar`. Built for
XX-WM and any phoc/Phosh session: Python + GTK4/libadwaita, text-first.

## What it is

An owner of its own event store (SQLite): Agenda, Day and Month pages,
event detail and editor, calendars, search, and .ics import/export. It
reads the XX-WM shell theme from `~/.config/xx-wm/config.json` when present and falls back to
libadwaita dark/light otherwise. There is no in-app theme picker. EDS is
import/publish only — see `PORT_SPEC.md` Revision 2.

## Run from the checkout

```bash
PYTHONPATH=src:../common python3 -m xx_calendar
```

Needs GTK4 + libadwaita with Python bindings (`gi`). No pip packages.
The shared design system lives in `common/xx_ui` and is never vendored.

## Install

```bash
meson setup ../common/build ../common
meson install -C ../common/build
meson setup build
meson install -C build
```

Install `common` first: it provides the `xx_ui` python package this app
requires at runtime.

This installs the Python package to the datadir (like XX-WM does), a
`xx-calendar` launcher in bindir, the `.desktop` file with app id
`io.piercingxx.XXCalendar`, and the metainfo file.

## Test

```bash
python3 -m pytest -q tests
ruff check src tests
```
