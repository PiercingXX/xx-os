# XX-WM

> A minimalist Wayland shell for Linux phones. Text-first, gesture-driven,
> AMOLED black. No icon grids, anywhere.

**"WM" is the name, not the architecture.** phoc composites. XX-WM draws
every surface over it — home, lock, shade, switcher, calls. Same
relationship Phosh has to phoc. The volume/brightness keys drive an
in-shell HUD, not an external daemon.

Most mobile Linux shells are a desktop squeezed onto a phone. This goes the
other way. The Android suite is the rehearsal. This is the venue. Under
construction.

<p align="center">
  <img src="docs/images/home-screen.jpg" alt="XX-WM home screen" width="640">
</p>

![Theme presets](docs/images/theme-presets.jpg)

Python + GTK4/libadwaita. phoc on the FLX1s and Librem 5; river with the xxwm window manager (`wm/`) on the Pixel 9 Pro.
Config is plain text under `~/.config/xx-wm/`. Nothing phones home.

It is not a distro. It installs onto postmarketOS, PureOS, FuriOS. It is not
GNOME with a theme.

| Device | OS |
|---|---|
| Fairphone 5 | postmarketOS |
| Librem 5 | PureOS |
| Furi Phone FLX1 | FuriOS |

Read `design.md`, then `todo.md`, then `launcher/README.md`.

## License

GPL-3.0.
