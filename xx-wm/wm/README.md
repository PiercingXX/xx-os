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

## Keyboard, layer surfaces and the usable area (river 0.5)

What river 0.5.0 offers, read from its protocol files, its source and the
globals a nested session advertises:

- **Input method**: `zwp_input_method_manager_v2`, `zwp_text_input_manager_v3`
  and `zwp_virtual_keyboard_manager_v1`, all version 1. river relays between
  the focused client's text input and the input method itself; the window
  manager only decides keyboard focus, and the text input of the focused
  client is the one entered. xx-keyboard needs nothing else (the phoc-only
  `zphoc_device_state_v1` and the foreign-toplevel filter are optional).
  `river_virtual_keyboard_manager_v1` is for the window manager's own key
  injection, not for keyboards.
- **Layer shell**: `zwlr_layer_shell_v1` version 4, so `ON_DEMAND` keyboard
  interactivity works here (phoc has version 3). river applies exclusive
  zones and tells the window manager what is left of each output with
  `river_layer_shell_output_v1.non_exclusive_area`, in global coordinates.
  xxwm gives the handset app exactly that area and tiles the desktop inside
  it, so the app shrinks above the keyboard and grows back when it folds
  (xx-keyboard sets its exclusive zone to its height when shown, 0 when
  hidden). Until river reports an area, or if it does not lie on the
  output, the whole output is used. New layer surfaces (the shell, the
  keyboard) open on the handset (`set_default`).
- **Layer focus**: a layer surface with exclusive focus keeps the keyboard;
  one with non-exclusive focus keeps it until a window is tapped or another
  window takes focus.

**Known river gap.** river sends `zwp_text_input_v3.enter` only when keyboard
focus changes or an input method appears, never to a text input created
while its client already has focus. GTK4 creates its text input on the
first frame that has a text field, after river has focused the new window,
so a tap on the field never shows the keyboard. xxwm works around it by
taking focus off each new window once, 300 ms after its first frame, and
giving it back (moving focus closes popups, so only once). Still broken: an
app whose first text field appears later (a page opened after start) gets
no keyboard until focus leaves the app and comes back. The fix belongs in
river: on a new text input, send `enter` if the seat's focused surface
belongs to the same client (sway does this).

## Control socket

`$XDG_RUNTIME_DIR/xxwm-$WAYLAND_DISPLAY.sock`. `xxwm msg <request>` sends one:
`list`, `outputs`, `focus <id>`, `close <id>`, `action <action>`, `reload`,
`subscribe`. The shell's window list and switcher use it on river
(`launcher/src/xxwm_backend.py`): river shows windows to other clients but
gives them no way to activate or close one.
