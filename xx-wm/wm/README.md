# xxwm

The XX-WM window manager for the [river](https://codeberg.org/river/river)
compositor. river draws and handles input; xxwm decides where windows go,
over river's stable `river-window-management-v1` protocol.

One session covers both screens:

- **Handset output** (portrait, or `set handset always`): one app at a time,
  filling the space the shell's status bar and keyboard leave.
- **Desktop output** (an external monitor): dwindle tiling with the owner's
  piercing-dots binds — `Super+HJKL` focus, `+Shift` move, `+Alt` resize,
  `Super+1..0` workspaces, `Super+F` float, `Super+X` toggle split, tap
  `Super` for the launcher.
- **Dock**: when a monitor appears, the workspace the phone was showing moves
  to it and the phone gets an empty one. **Undock**: it comes back.

## Build and run

```sh
cargo build            # in this directory
cargo test
sh dev/run-nested.sh   # river nested in the current session, two outputs
```

Make one of the two river windows portrait and it becomes the handset.
`sh dev/run-shell.sh`, started through the window manager
(`xxwm msg action spawn sh dev/run-shell.sh`), runs the XX-WM shell from
this checkout inside it, sealed off from the host desktop.

## Configuration

`$XDG_CONFIG_HOME/xx-wm/wm.conf` (or `$XXWM_CONFIG`) adds to and overrides
the built-in defaults; `xxwm --print-defaults` prints them.

```text
set gaps_in 8
bind Super+W spawn foot
unbind Super+D
```

## Control socket

`$XDG_RUNTIME_DIR/xxwm-$WAYLAND_DISPLAY.sock`. `xxwm msg <request>` sends one:
`list`, `outputs`, `focus <id>`, `close <id>`, `action <action>`, `reload`,
`subscribe`. The shell's window list and switcher use it on river
(`launcher/src/xxwm_backend.py`): river shows windows to other clients but
gives them no way to activate or close one.
