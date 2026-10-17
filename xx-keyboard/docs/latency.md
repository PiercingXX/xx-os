# xx-keyboard show latency

Goal: keys on screen as close to text-field focus as possible. Measured on
the phone with:

```sh
G_MESSAGES_DEBUG=pos-input-surface,pos-input-method ./xx-keyboard --replace
```

and tapping a field. Look for the three `osk-latency` lines (monotonic
microseconds); `first-frame − activate` is the show cost.

## 1. Show/hide slide animation — ~250 ms+ (fixed)

`pos_input_surface_set_visible()` added a tick callback (`animate_cb` in
`src/pos-input-surface.c`) that advanced `progress += 0.06666` per
16.67 ms frame: 15 frames ≈ 250 ms at a perfect 60 fps, longer whenever
frames drop on a phone GPU. `pos_input_surface_move()` kept the surface
off-screen (`margin = -height`) until the slide finished, so nothing was
visible before ~250 ms.

Changed: show is now a ≤ 100 ms ease-out. `animate_cb` advances show
progress at `0.20`/frame (≈5 frames ≈ 83 ms); hide keeps the upstream
15-frame slide. `set_visible(TRUE)` commits the first frame
synchronously via `pos_input_surface_move()` — the keys are mapped from
frame 1 and the ticks only finish the short ease — and honours
`org.gnome.desktop.interface enable-animations=false` with zero
animation (progress snaps to 1.0, one `move`, no tick, no watchdog).

## 2. Lazy first build — none on the show path (verified, no change)

Surface, current layout key widgets and keymap are all built at startup
in `pos_input_surface_constructed()`: `on_input_setting_changed()` inserts
the layouts (GSettings sources, then primary/QWERTY/Piercing ordered),
`set_keymap()` pushes symbols to the vk driver, and
`pos_input_surface_update_glide()` arms the decoder. First show only flips
visibility.

- `delayed_init` (`g_timeout_add_seconds_once(1, …)`): sends one BackSpace
  key press as a serial workaround for the GTK shell merge-request 5628.
  Fires once 1 s after startup, never on focus/show.
- `set_keymap_delayed` (`g_timeout_add_once(25, set_keymap)`): only runs
  after a popover hides or an emoji is sent via the vk driver, to let an
  X11 client finish with the overlay keymap. Show calls `set_keymap()`
  directly instead.

## 3. D-Bus / completion / sync before map — none (verified, no change)

Show path is `im notify::active` → `surface-visible` binding
(`set_surface_prop_surface_visible` in `phosh-osk-stevia.c`, pure flag
check) → `pos_input_surface_set_visible()` → `move` → layer-surface
commit. It touches no D-Bus, spawns no completer switch
(`pos_input_surface_switch_completion()` only runs on layout change via
`on_visible_child_changed()`), and does no file or prediction I/O.
Emoji DB loads once in `pos_app_init()` and the glide decoder/dicts in
`pos_input_surface_init()` — both at startup, off the show path.

## 4. Shell side — already immediate (verified, no change)

`xx-wm/launcher/src/osk.py::set_visible()` sends `SetVisible` with the
async `bus.call()` (never `call_sync`); `attach()` invokes `on_show`
directly from the focus-`enter` and tap-`pressed` handlers with no
`idle_add`/`timeout_add` first. Callers (`window.py::_arm_search_keyboard`,
`sms.py`, `polkit_agent.py`, `app_item_actions.py`) call it synchronously
on focus-enter/tap. `window.py` sets the layer-shell keyboard mode
(`EXCLUSIVE`, local, v3 has no `ON_DEMAND`) before `SetVisible` without
waiting on the D-Bus reply.

## Debug timestamps

`g_debug` lines under `G_LOG_DOMAIN "pos-input-surface"`:

- `osk-latency: activate …` — in `set_visible(TRUE)`, input-method
  activate arrived.
- `osk-latency: map …` — same function, widget mapped synchronously.
- `osk-latency: first-frame …` — first `animate_cb` tick for a show
  (absent when animations are off, where map is the whole show).

All carry `g_get_monotonic_time()` so the orchestrator can diff them.
