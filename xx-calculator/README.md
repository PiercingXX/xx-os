# xx-calculator

Linux phone port of the PiercingXX Android **xx-calculator** (Play
edition behavior per `ref/xx-calculator/docs/SPEC.md`; see
[`PORT_SPEC.md`](PORT_SPEC.md) for the feature mapping). Exact decimal
arithmetic (`0.1+0.2` is `0.3`), live preview, shopkeeper percent
(`200+10%` is `220`), scientific panel with INV and DEG/RAD, big touch
keypad plus hardware keyboard, copy/paste, and a 100-entry on-phone
history. Local-only: no network, no accounts, no uploads.

## Layout

- `src/xx_calculator/engine.py` — lexer, parser, evaluator, formatting
  (port of the Android `engine/` package; no `eval` of user input)
- `src/xx_calculator/builder.py` — input-grammar state machine
- `src/xx_calculator/history.py` — JSON history store (newest first, 100 cap)
- `src/xx_calculator/main.py` — GTK4/libadwaita window (display, 4x5
  keypad, science panel, history page, keyboard, clipboard)

## Develop

```sh
PYTHONPATH=src:../common python3 -m pytest -q tests
sh tools/snap_all.sh lane_shots
```

## Install

```sh
cd ../common && meson setup build --prefix=/usr && sudo meson install -C build
cd ../xx-calculator && meson setup build --prefix=/usr && sudo meson install -C build
```

## License

GPL-3.0-only.
