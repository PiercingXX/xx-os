# xx-files

Tree-first local file manager. Linux phone port of the PiercingXX
Android xx-files app (`ref/xx-files`).

A real directory tree — not Recent/Images/Downloads: Home plus
Documents, Downloads, Pictures, Music and removable media, with list,
tree and grid views, breadcrumbs, sort (name/size/date), search,
multi-select, copy/move with progress and cancel, rename, new folder,
open-with, share via xx-share, properties, and the freedesktop Trash
with restore and empty. Registers as the default `inode/directory`
handler. Every listing and file operation runs on a worker thread.

Local-only: no network. Settings in `~/.config/xx-files/settings.json`.

```sh
cd xx-files && meson setup build --prefix=/usr && sudo meson install -C build
sh tools/snap_all.sh lane_shots
```

See [PORT_SPEC.md](PORT_SPEC.md) for the Android → Linux mapping.
License: GPL-3.0. See [LICENSE](../LICENSE).
