# xx-note

> Delete this app and lose nothing — the notes were plain Markdown the whole time.

Linux phone port of the PiercingXX Android **xx-note** app. Every note is
one `.md` file with YAML frontmatter in a folder you own
(`~/Documents/Notes` by default):

```markdown
---
id: 01J9F2K3M4N5P6Q7R8S9T0V1W2
title: Grocery list
pinned: true
labels: [home, errands]
type: checklist
---

- [ ] oat milk
- [x] bin bags
```

Card grid with Pinned/Others, capture in one tap, checklists that sort
themselves, labels, archive, trash with 7-day expiry, offline search, a
live-styled editor with read view and undo, and optional WebDAV sync
(Nextcloud and others — off by default, password in the keyring, never in
settings). No home-server sync, no accounts, no log upload.

`PORT_SPEC.md` maps every Android feature to its Linux counterpart.

## Build / test

```sh
cd xx-note && meson setup build --prefix=/usr && sudo meson install -C build
PYTHONPATH=src:../common python3 -m pytest -q tests
sh tools/snap_all.sh lane_shots
xx-note --sync-once   # headless WebDAV pass (systemd timer entry)
```

## License

GPL-3.0-only. See [LICENSE](../LICENSE).
