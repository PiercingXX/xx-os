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
  to it, the phone gets an empty one and the pointer goes to the middle of
  the monitor. **Undock**: it comes back. The shell's dock controller
  (`launcher/src/dock_controller.py`) handles the phone side: black screen
  and trackpad, second screen, or off (`docked_phone_screen` in
  `docs/config.md`).

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

`shell <verb>` hands a verb to the XX-WM shell instead of running
anything: `Super+R` is `shell bar` (the top bar on or off) and `Super+/`
is `shell keybinds` (the keybind sheet). Tapping Super and `Alt+Tab` run
`launcher`, which the shell shows as its own search; with no shell
listening, `set launcher` runs instead.

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

**River text-input gap.** Upstream river sends `zwp_text_input_v3.enter`
only when keyboard focus changes or an input method appears, never to a
text input created while its client already has focus. GTK4 creates its
text input on the first frame that has a text field, after river has
focused the new window, so a tap on the field never shows the keyboard.
xxwm works around the first frame by taking focus off each new window
once, 300 ms after its first frame, and giving it back (moving focus
closes popups, so only once). A text field that first appears later (a
page opened after start) needs the river patch below; on an unpatched
river it gets no keyboard until focus leaves the app and comes back.

## Patched river

xx-os builds river itself (Debian has no river package) and carries its
own patches in `river/`:

- `river/build.sh` fetches river v0.5.0 (commit
  `cc4a291b2b9375fe3d5fe5a64b6f510b463f888b`, the one Arch's river 0.5.0-1
  builds), refuses any other commit, applies `river/patches/*.patch` in
  name order and builds with Zig 0.16.x (`ZIG=/path/to/zig`). Zig checks
  river's Zig dependencies against the hashes in its `build.zig.zon`.
  `--install` copies the result to `$PREFIX` (default `/usr/local`).
  Build dependencies on Debian forky: `libwlroots-0.20-dev`,
  `libwayland-dev`, `wayland-protocols`, `libxkbcommon-dev`,
  `libinput-dev`, `libevdev-dev`, `libpixman-1-dev`, `pkg-config`, `git`.
- Zig: the official release tarball from ziglang.org, checked against
  the sha256 in `https://ziglang.org/download/index.json` and its minisign
  signature (key `RWSGOq2NVecA2UPNdBUZykf1CCb147pkmdtYxgb3Ti+JO/wCYvhbAb/U`),
  unpacked to `/opt/zig-0.16.0`.
- `river/install-session.sh` installs xxwm (`cargo build --release` here
  first) and the greeter session "XX-WM (river)":
  `xx-wm-river-session` sets up the same environment as the phoc session
  and starts river with `xx-wm-river-init`, which sets each output's
  scale from the device's `phoc.ini` (with `wlr-randr`), keeps xxwm
  running and runs the `xx-wm` shell wrapper; when the shell ends the
  session, river stops. Logs: `~/.local/share/xx-wm/river.log` and
  `xxwm.log`. The phoc session and Phosh are left as they are.

Patches:

- `0001-text-input-enter-for-the-focused-client.patch`: on a new text
  input, send `enter` when the seat's focused surface belongs to the same
  client (labwc does the same). Checked on headless river with xxwm and
  xx-keyboard, a GTK4 window whose text field appears 2.5 s after its
  first frame: upstream sends no `enter` and the keyboard never shows;
  patched, the field is entered, xx-keyboard is activated and the window
  shrinks above it. A field present from the first frame works on both.

## Control socket

`$XDG_RUNTIME_DIR/xxwm-$WAYLAND_DISPLAY.sock`. `xxwm msg <request>` sends one:
`list`, `outputs`, `binds`, `focus <id>`, `close <id>`, `action <action>`,
`workspace <n> [output]`, `reload`, `subscribe`, `subscribe state` (the
full list with answers is in `src/ipc.rs`). The shell's window list and
switcher use it on river (`launcher/src/xxwm_backend.py`): river shows
windows to other clients but gives them no way to activate or close one.
The shell's docked surfaces (`launcher/src/desk_session.py`) follow
`subscribe state`: outputs by connector name, which one is the handset,
the workspace and front window title of each, and the shell verbs. The
dock controller follows the same stream; each output also names its
`wl_output` global, which is how the shell powers the phone's output off.
