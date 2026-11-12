//! The window manager: river's manage/render cycle, the per-output policy
//! (handset = one app at a time, desktop = dwindle tiling) and the dock
//! hand-over between them.

use std::collections::HashMap;
use std::process::{Command, Stdio};

use wayland_client::backend::ObjectId;
use wayland_client::globals::GlobalListContents;
use wayland_client::protocol::wl_registry;
use wayland_client::{event_created_child, Connection, Dispatch, Proxy, QueueHandle, WEnum};

use crate::config::{Action, Config};
use crate::ipc::{self, json_str};
use crate::keysym::mods;
use crate::layout::{self, centered, dwindle, neighbor, Dir, Rect, Split};
use crate::protocol::layer::river_layer_shell_output_v1::{self, RiverLayerShellOutputV1};
use crate::protocol::layer::river_layer_shell_seat_v1::{self, RiverLayerShellSeatV1};
use crate::protocol::layer::river_layer_shell_v1::RiverLayerShellV1;
use crate::protocol::wm::river_node_v1::RiverNodeV1;
use crate::protocol::wm::river_output_v1::{self, RiverOutputV1};
use crate::protocol::wm::river_pointer_binding_v1::{self, RiverPointerBindingV1};
use crate::protocol::wm::river_seat_v1::{self, Modifiers, RiverSeatV1};
use crate::protocol::wm::river_window_manager_v1::{self, RiverWindowManagerV1};
use crate::protocol::wm::river_window_v1::{self, Capabilities, Edges, RiverWindowV1};
use crate::protocol::xkb::river_xkb_binding_v1::{self, RiverXkbBindingV1};
use crate::protocol::xkb::river_xkb_bindings_seat_v1::{self, RiverXkbBindingsSeatV1};
use crate::protocol::xkb::river_xkb_bindings_v1::RiverXkbBindingsV1;

const BTN_LEFT: u32 = 0x110;
const BTN_RIGHT: u32 = 0x111;
const WORKSPACES: usize = 10;
const FLOAT_STEP: i32 = 40;

macro_rules! log {
    ($($arg:tt)*) => {
        if crate::wm::verbose() { eprintln!("xxwm: {}", format!($($arg)*)); }
    };
}
macro_rules! warn {
    ($($arg:tt)*) => { eprintln!("xxwm: {}", format!($($arg)*)) };
}

pub fn verbose() -> bool {
    use std::sync::OnceLock;
    static V: OnceLock<bool> = OnceLock::new();
    *V.get_or_init(|| std::env::var("XXWM_LOG").map_or(false, |v| v == "debug"))
}

pub struct Window {
    proxy: RiverWindowV1,
    node: RiverNodeV1,
    identifier: String,
    app_id: Option<String>,
    title: Option<String>,
    workspace: usize,
    floating: bool,
    fullscreen: bool,
    dims: Option<(i32, i32)>,
    float_rect: Option<Rect>,
    proposed: Option<(i32, i32)>,
    target: Option<Rect>,
    shown: bool,
    border_active: Option<bool>,
    is_new: bool,
    closed: bool,
    has_parent: bool,
    fixed_size: bool,
}

pub struct Output {
    proxy: RiverOutputV1,
    layer: Option<RiverLayerShellOutputV1>,
    rect: Rect,
    area: Option<Rect>,
    workspace: usize,
    handset: bool,
    removed: bool,
    is_new: bool,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum LayerFocus {
    None,
    Exclusive,
    NonExclusive,
}

struct Op {
    window: ObjectId,
    start: Rect,
    resize: bool,
    delta: (i32, i32),
}

pub struct Seat {
    proxy: RiverSeatV1,
    xkb_seat: Option<RiverXkbBindingsSeatV1>,
    layer_seat: Option<RiverLayerShellSeatV1>,
    binds: Vec<RiverXkbBindingV1>,
    pointer_binds: Vec<RiverPointerBindingV1>,
    ready: bool,
    layer_focus: LayerFocus,
    sent_focus: Option<Option<ObjectId>>,
    hovered: Option<ObjectId>,
    op: Option<Op>,
    op_released: bool,
    super_down: bool,
    super_used: bool,
    removed: bool,
}

enum Pending {
    Action(Action),
    Interact(ObjectId),
    Fullscreen(ObjectId, bool),
    PointerOp { seat: ObjectId, resize: bool },
}

pub struct State {
    wm: RiverWindowManagerV1,
    xkb: Option<RiverXkbBindingsV1>,
    layer: Option<RiverLayerShellV1>,
    config: Config,
    config_path: Option<std::path::PathBuf>,
    windows: HashMap<ObjectId, Window>,
    order: Vec<ObjectId>,
    focus_stack: Vec<ObjectId>,
    outputs: Vec<(ObjectId, Output)>,
    seats: HashMap<ObjectId, Seat>,
    splits: HashMap<usize, Vec<Split>>,
    focused_output: Option<ObjectId>,
    default_layer_output: Option<ObjectId>,
    pending: Vec<Pending>,
    locked: bool,
    rebind: bool,
    ipc: ipc::Queue,
    subscribers: ipc::Subscribers,
    last_snapshot: String,
    last_subscriber_count: usize,
    socket: Option<std::path::PathBuf>,
    pub exit: Option<i32>,
}

impl State {
    pub fn new(
        wm: RiverWindowManagerV1,
        xkb: Option<RiverXkbBindingsV1>,
        layer: Option<RiverLayerShellV1>,
        config: Config,
        config_path: Option<std::path::PathBuf>,
        ipc: ipc::Queue,
        subscribers: ipc::Subscribers,
        socket: Option<std::path::PathBuf>,
    ) -> State {
        State {
            wm,
            xkb,
            layer,
            config,
            config_path,
            windows: HashMap::new(),
            order: Vec::new(),
            focus_stack: Vec::new(),
            outputs: Vec::new(),
            seats: HashMap::new(),
            splits: HashMap::new(),
            focused_output: None,
            default_layer_output: None,
            pending: Vec::new(),
            locked: false,
            rebind: false,
            ipc,
            subscribers,
            last_snapshot: String::new(),
            last_subscriber_count: 0,
            socket,
            exit: None,
        }
    }

    // ---------------------------------------------------------------- lookup

    fn output(&self, id: &ObjectId) -> Option<&Output> {
        self.outputs.iter().find(|(i, _)| i == id).map(|(_, o)| o)
    }

    fn output_mut(&mut self, id: &ObjectId) -> Option<&mut Output> {
        self.outputs.iter_mut().find(|(i, _)| i == id).map(|(_, o)| o)
    }

    fn live_outputs(&self) -> impl Iterator<Item = &(ObjectId, Output)> {
        self.outputs.iter().filter(|(_, o)| !o.removed)
    }

    fn output_showing(&self, ws: usize) -> Option<ObjectId> {
        self.live_outputs().find(|(_, o)| o.workspace == ws).map(|(i, _)| i.clone())
    }

    fn focused_workspace(&self) -> Option<usize> {
        self.focused_output.as_ref().and_then(|o| self.output(o)).map(|o| o.workspace)
    }

    /// The most recently focused live window on `ws`.
    fn top_of(&self, ws: usize) -> Option<ObjectId> {
        self.focus_stack
            .iter()
            .rev()
            .find(|id| self.windows.get(*id).map_or(false, |w| w.workspace == ws && !w.closed))
            .cloned()
            .or_else(|| {
                self.order
                    .iter()
                    .rev()
                    .find(|id| self.windows.get(*id).map_or(false, |w| w.workspace == ws && !w.closed))
                    .cloned()
            })
    }

    pub fn focused(&self) -> Option<ObjectId> {
        self.top_of(self.focused_workspace()?)
    }

    fn focus(&mut self, id: &ObjectId) {
        let Some(ws) = self.windows.get(id).map(|w| w.workspace) else { return };
        self.focus_stack.retain(|i| i != id);
        self.focus_stack.push(id.clone());
        if let Some(out) = self.output_showing(ws) {
            self.focused_output = Some(out);
        }
    }

    fn windows_on(&self, ws: usize) -> Vec<ObjectId> {
        self.order
            .iter()
            .filter(|id| self.windows.get(*id).map_or(false, |w| w.workspace == ws && !w.closed))
            .cloned()
            .collect()
    }

    fn tiled_on(&self, ws: usize) -> Vec<ObjectId> {
        self.windows_on(ws)
            .into_iter()
            .filter(|id| self.windows.get(id).map_or(false, |w| !w.floating && !w.fullscreen))
            .collect()
    }

    fn area_of(&self, out: &ObjectId) -> Rect {
        self.output(out).map(|o| layout::usable_area(o.rect, o.area)).unwrap_or_default()
    }

    // ---------------------------------------------------------------- manage

    fn manage(&mut self, qh: &QueueHandle<State>) {
        self.reap();
        self.setup_outputs();
        self.setup_seats(qh);
        self.place_new_windows();
        for p in std::mem::take(&mut self.pending) {
            self.apply(p);
        }
        self.serve_ipc();
        self.drive_ops();
        self.layout();
        self.send_focus();
        self.wm.manage_finish();
        self.publish();
    }

    /// Push the window list to subscribers when it (or the set of
    /// subscribers) changed.
    fn publish(&mut self) {
        use std::io::Write;
        let count = self.subscribers.lock().unwrap().len();
        if count == 0 {
            self.last_subscriber_count = 0;
            return;
        }
        let snap = self.window_list_json();
        if snap == self.last_snapshot && count == self.last_subscriber_count {
            return;
        }
        let line = format!("{snap}\n");
        let mut subs = self.subscribers.lock().unwrap();
        subs.retain_mut(|s| s.write_all(line.as_bytes()).is_ok());
        self.last_subscriber_count = subs.len();
        self.last_snapshot = snap;
    }

    fn window_list_json(&self) -> String {
        let focused = self.focused();
        let items: Vec<String> = self
            .order
            .iter()
            .filter_map(|id| self.windows.get(id).map(|w| (id, w)))
            .filter(|(_, w)| !w.closed && !w.identifier.is_empty())
            .map(|(id, w)| {
                format!(
                    "{{\"id\":{},\"app_id\":{},\"title\":{},\"workspace\":{},\"focused\":{},\"visible\":{},\"floating\":{}}}",
                    json_str(&w.identifier),
                    json_str(w.app_id.as_deref().unwrap_or("")),
                    json_str(w.title.as_deref().unwrap_or("")),
                    w.workspace,
                    focused.as_ref() == Some(id),
                    w.target.is_some(),
                    w.floating,
                )
            })
            .collect();
        format!("[{}]", items.join(","))
    }

    fn reap(&mut self) {
        let closed: Vec<ObjectId> =
            self.windows.iter().filter(|(_, w)| w.closed).map(|(i, _)| i.clone()).collect();
        for id in closed {
            if let Some(w) = self.windows.remove(&id) {
                w.node.destroy();
                w.proxy.destroy();
                log!("window gone: {:?}", w.app_id);
            }
            self.order.retain(|i| i != &id);
            self.focus_stack.retain(|i| i != &id);
            for s in self.seats.values_mut() {
                if s.hovered.as_ref() == Some(&id) {
                    s.hovered = None;
                }
                if s.op.as_ref().map_or(false, |o| o.window == id) {
                    s.op = None;
                    s.proxy.op_end();
                }
            }
        }

        let gone: Vec<(ObjectId, usize, bool)> = self
            .outputs
            .iter()
            .filter(|(_, o)| o.removed)
            .map(|(i, o)| (i.clone(), o.workspace, o.handset))
            .collect();
        for (id, ws, was_handset) in gone {
            if let Some(pos) = self.outputs.iter().position(|(i, _)| *i == id) {
                let (_, o) = self.outputs.remove(pos);
                if let Some(l) = o.layer {
                    l.destroy();
                }
                o.proxy.destroy();
            }
            // Undock: the work that was on the monitor comes back to the
            // phone instead of vanishing onto an unshown workspace.
            if !was_handset {
                let target = self
                    .live_outputs()
                    .find(|(_, o)| o.handset)
                    .or_else(|| self.live_outputs().next())
                    .map(|(i, _)| i.clone());
                if let Some(t) = target {
                    if let Some(o) = self.output_mut(&t) {
                        log!("undock: workspace {ws} back on the handset");
                        o.workspace = ws;
                    }
                    self.focused_output = Some(t);
                }
            }
            if self.focused_output.as_ref() == Some(&id) {
                let next = self.live_outputs().next().map(|(i, _)| i.clone());
                self.focused_output = next;
            }
            if self.default_layer_output.as_ref() == Some(&id) {
                self.default_layer_output = None;
            }
        }

        let seats: Vec<ObjectId> =
            self.seats.iter().filter(|(_, s)| s.removed).map(|(i, _)| i.clone()).collect();
        for id in seats {
            if let Some(s) = self.seats.remove(&id) {
                for b in s.binds {
                    b.destroy();
                }
                for b in s.pointer_binds {
                    b.destroy();
                }
                if let Some(x) = s.xkb_seat {
                    x.destroy();
                }
                if let Some(l) = s.layer_seat {
                    l.destroy();
                }
                s.proxy.destroy();
            }
        }
    }

    fn handset_rule(&self, rect: Rect) -> bool {
        match self.config.handset {
            crate::config::HandsetRule::Always => true,
            crate::config::HandsetRule::Never => false,
            crate::config::HandsetRule::Auto => rect.h > rect.w,
        }
    }

    fn setup_outputs(&mut self) {
        // Handset-ness follows the output's shape (rotation changes it).
        let rule: Vec<bool> = self.outputs.iter().map(|(_, o)| self.handset_rule(o.rect)).collect();
        for ((_, o), h) in self.outputs.iter_mut().zip(rule) {
            o.handset = h;
        }

        let new: Vec<ObjectId> =
            self.outputs.iter().filter(|(_, o)| o.is_new && !o.removed).map(|(i, _)| i.clone()).collect();
        for id in new {
            let handset = self.output(&id).map_or(false, |o| o.handset);
            let shown: Vec<usize> = self
                .live_outputs()
                .filter(|(i, o)| *i != id && !o.is_new)
                .map(|(_, o)| o.workspace)
                .collect();
            let free = (1..=WORKSPACES).find(|n| !shown.contains(n)).unwrap_or(1);
            let phone = self
                .live_outputs()
                .find(|(i, o)| *i != id && !o.is_new && o.handset)
                .map(|(i, o)| (i.clone(), o.workspace));
            match (handset, phone) {
                // Dock: the monitor takes over what the phone was showing;
                // the phone gets an empty workspace (the shell blacks it out
                // and turns it into a trackpad).
                (false, Some((phone_id, phone_ws))) => {
                    if let Some(o) = self.output_mut(&id) {
                        o.workspace = phone_ws;
                    }
                    if let Some(o) = self.output_mut(&phone_id) {
                        o.workspace = free;
                    }
                    self.focused_output = Some(id.clone());
                    log!("dock: workspace {phone_ws} moves to the monitor, phone shows {free}");
                }
                _ => {
                    if let Some(o) = self.output_mut(&id) {
                        o.workspace = free;
                    }
                }
            }
            if let Some(o) = self.output_mut(&id) {
                o.is_new = false;
            }
        }

        if self.focused_output.as_ref().map_or(true, |f| self.output(f).map_or(true, |o| o.removed)) {
            let pick = self
                .live_outputs()
                .find(|(_, o)| !o.handset)
                .or_else(|| self.live_outputs().next())
                .map(|(i, _)| i.clone());
            self.focused_output = pick;
        }

        // New layer surfaces (the XX-WM shell) open on the phone's screen.
        let want = self
            .live_outputs()
            .find(|(_, o)| o.handset)
            .or_else(|| self.live_outputs().next())
            .map(|(i, _)| i.clone());
        if want.is_some() && want != self.default_layer_output {
            if let Some(o) = want.as_ref().and_then(|i| self.output(i)) {
                if let Some(l) = &o.layer {
                    l.set_default();
                }
            }
            self.default_layer_output = want;
        }
    }

    fn setup_seats(&mut self, qh: &QueueHandle<State>) {
        let rebind = std::mem::take(&mut self.rebind);
        let ids: Vec<ObjectId> = self.seats.keys().cloned().collect();
        for id in ids {
            let Some(seat) = self.seats.get_mut(&id) else { continue };
            if seat.ready && !rebind {
                continue;
            }
            for b in seat.binds.drain(..) {
                b.destroy();
            }
            if let Some(xkb) = &self.xkb {
                for (i, bind) in self.config.binds.iter().enumerate() {
                    let b = xkb.get_xkb_binding(
                        &seat.proxy,
                        bind.keysym,
                        Modifiers::from_bits_truncate(bind.mods),
                        qh,
                        i,
                    );
                    b.enable();
                    seat.binds.push(b);
                }
                if seat.xkb_seat.is_none() {
                    seat.xkb_seat = Some(xkb.get_seat(&seat.proxy, qh, id.clone()));
                }
                if let Some(x) = &seat.xkb_seat {
                    x.modifiers_watch(Modifiers::from_bits_truncate(mods::SUPER));
                }
            }
            if seat.pointer_binds.is_empty() {
                for (button, resize) in [(BTN_LEFT, false), (BTN_RIGHT, true)] {
                    let b = seat.proxy.get_pointer_binding(
                        button,
                        Modifiers::from_bits_truncate(mods::SUPER),
                        qh,
                        (id.clone(), resize),
                    );
                    b.enable();
                    seat.pointer_binds.push(b);
                }
            }
            if seat.layer_seat.is_none() {
                if let Some(layer) = &self.layer {
                    seat.layer_seat = Some(layer.get_seat(&seat.proxy, qh, id.clone()));
                }
            }
            seat.ready = true;
            log!("seat ready: {} key bindings", seat.binds.len());
        }
    }

    fn place_new_windows(&mut self) {
        let ws = self.focused_workspace().unwrap_or(1);
        let area = self.focused_output.clone().map(|o| self.area_of(&o)).unwrap_or_default();
        let new: Vec<ObjectId> = self
            .order
            .iter()
            .filter(|id| self.windows.get(*id).map_or(false, |w| w.is_new))
            .cloned()
            .collect();
        for id in new {
            if let Some(w) = self.windows.get_mut(&id) {
                w.is_new = false;
                w.workspace = ws;
                w.floating = w.has_parent || w.fixed_size;
                if w.floating {
                    if let Some((dw, dh)) = w.dims {
                        w.float_rect = Some(centered(area, dw, dh));
                    }
                }
                w.proxy.set_capabilities(Capabilities::Fullscreen);
                log!("new window {:?} on workspace {ws}{}", w.app_id, if w.floating { " (floating)" } else { "" });
            }
            // A new window opens next to the focused one, and takes focus.
            if let Some(f) = self.focused() {
                if f != id {
                    if let (Some(from), Some(fpos)) = (
                        self.order.iter().position(|i| *i == id),
                        self.order.iter().position(|i| *i == f),
                    ) {
                        let item = self.order.remove(from);
                        let fpos = if from < fpos { fpos - 1 } else { fpos };
                        self.order.insert(fpos + 1, item);
                    }
                }
            }
            self.focus(&id);
        }
    }

    fn apply(&mut self, p: Pending) {
        match p {
            Pending::Action(a) => self.action(a),
            Pending::Interact(id) => {
                if self.windows.contains_key(&id) {
                    self.focus(&id);
                }
            }
            Pending::Fullscreen(id, on) => self.set_fullscreen(&id, on),
            Pending::PointerOp { seat, resize } => self.start_op(&seat, resize),
        }
    }

    fn action(&mut self, a: Action) {
        if self.locked && !matches!(a, Action::Spawn(_)) {
            return;
        }
        log!("action {a:?}");
        match a {
            Action::Spawn(cmd) => self.spawn(&cmd),
            Action::Launcher => {
                let cmd = self.config.launcher.clone();
                self.spawn(&cmd);
            }
            Action::Close => {
                if let Some(w) = self.focused().and_then(|f| self.windows.get(&f)) {
                    w.proxy.close();
                }
            }
            Action::Workspace(n) => self.show_workspace(n),
            Action::Send(n) | Action::SendSilent(n) => {
                let follow = matches!(a, Action::Send(_));
                let Some(f) = self.focused() else { return };
                if let Some(w) = self.windows.get_mut(&f) {
                    w.workspace = n;
                }
                if follow {
                    self.show_workspace(n);
                    self.focus(&f);
                }
            }
            Action::ToggleFloat => {
                let Some(f) = self.focused() else { return };
                let area = self.focused_output.clone().map(|o| self.area_of(&o)).unwrap_or_default();
                if let Some(w) = self.windows.get_mut(&f) {
                    w.floating = !w.floating;
                    if w.floating {
                        let (dw, dh) = w.target.map(|r| (r.w, r.h)).or(w.dims).unwrap_or((area.w * 3 / 5, area.h * 3 / 5));
                        w.float_rect = Some(centered(area, dw.min(area.w * 4 / 5), dh.min(area.h * 4 / 5)));
                    }
                }
            }
            Action::ToggleFullscreen => {
                let Some(f) = self.focused() else { return };
                let on = !self.windows.get(&f).map_or(false, |w| w.fullscreen);
                self.set_fullscreen(&f, on);
            }
            Action::ToggleSplit => {
                let Some(f) = self.focused() else { return };
                let Some(ws) = self.focused_workspace() else { return };
                let tiled = self.tiled_on(ws);
                if let Some(k) = tiled.iter().position(|i| *i == f) {
                    let splits = self.splits.entry(ws).or_default();
                    layout::toggle_split(splits, tiled.len(), k);
                }
            }
            Action::Focus(dir) => self.focus_dir(dir),
            Action::Move(dir) => self.move_dir(dir),
            Action::Resize(dir) => self.resize_dir(dir),
        }
    }

    /// Hyprland semantics: a workspace another output is showing is
    /// reached by moving focus to that output; otherwise the focused
    /// output switches to it.
    fn show_workspace(&mut self, n: usize) {
        let Some(out) = self.focused_output.clone() else { return };
        if let Some(other) = self.output_showing(n) {
            self.focused_output = Some(other);
            return;
        }
        if let Some(o) = self.output_mut(&out) {
            o.workspace = n;
        }
    }

    fn set_fullscreen(&mut self, id: &ObjectId, on: bool) {
        let out = self.windows.get(id).and_then(|w| self.output_showing(w.workspace));
        let out_proxy = out.as_ref().and_then(|o| self.output(o)).map(|o| o.proxy.clone());
        let Some(w) = self.windows.get_mut(id) else { return };
        if w.fullscreen == on {
            return;
        }
        match (on, out_proxy) {
            (true, Some(o)) => {
                w.proxy.fullscreen(&o);
                w.proxy.inform_fullscreen();
                w.fullscreen = true;
            }
            (false, _) => {
                w.proxy.exit_fullscreen();
                w.proxy.inform_not_fullscreen();
                w.fullscreen = false;
                w.proposed = None;
            }
            _ => {}
        }
    }

    /// Rects of everything visible on the focused output, for directional
    /// focus/move: tiled windows by their tile, floating by their own rect.
    fn visible_rects(&self) -> Vec<(ObjectId, Rect)> {
        let Some(ws) = self.focused_workspace() else { return Vec::new() };
        self.windows_on(ws)
            .into_iter()
            .filter_map(|id| self.windows.get(&id).and_then(|w| w.target).map(|r| (id, r)))
            .collect()
    }

    fn adjacent_output(&self, dir: Dir) -> Option<ObjectId> {
        let cur = self.focused_output.as_ref().and_then(|o| self.output(o))?.rect;
        let rects: Vec<(ObjectId, Rect)> = self.live_outputs().map(|(i, o)| (i.clone(), o.rect)).collect();
        let from = rects.iter().position(|(_, r)| *r == cur)?;
        let only: Vec<Rect> = rects.iter().map(|(_, r)| *r).collect();
        neighbor(&only, from, dir).map(|i| rects[i].0.clone())
    }

    fn focus_dir(&mut self, dir: Dir) {
        let handset = self.focused_output.as_ref().and_then(|o| self.output(o)).map_or(false, |o| o.handset);
        let Some(ws) = self.focused_workspace() else { return };
        if handset {
            // One app at a time: left/right step through the apps on this
            // workspace in order, like swiping between them.
            if !dir.horizontal() {
                return;
            }
            let all = self.windows_on(ws);
            let Some(f) = self.focused() else { return };
            let Some(i) = all.iter().position(|x| *x == f) else { return };
            let next = if dir == Dir::Right { (i + 1) % all.len() } else { (i + all.len() - 1) % all.len() };
            let id = all[next].clone();
            self.focus(&id);
            return;
        }
        let rects = self.visible_rects();
        let only: Vec<Rect> = rects.iter().map(|(_, r)| *r).collect();
        let from = self.focused().and_then(|f| rects.iter().position(|(i, _)| *i == f));
        match from.and_then(|i| neighbor(&only, i, dir)) {
            Some(n) => {
                let id = rects[n].0.clone();
                self.focus(&id);
            }
            None => {
                if let Some(out) = self.adjacent_output(dir) {
                    self.focused_output = Some(out.clone());
                    let ws = self.output(&out).map(|o| o.workspace);
                    if let Some(id) = ws.and_then(|w| self.top_of(w)) {
                        self.focus(&id);
                    }
                }
            }
        }
    }

    fn move_dir(&mut self, dir: Dir) {
        let Some(f) = self.focused() else { return };
        let Some(ws) = self.focused_workspace() else { return };
        if self.windows.get(&f).map_or(false, |w| w.floating) {
            let (dx, dy) = match dir {
                Dir::Left => (-FLOAT_STEP, 0),
                Dir::Right => (FLOAT_STEP, 0),
                Dir::Up => (0, -FLOAT_STEP),
                Dir::Down => (0, FLOAT_STEP),
            };
            if let Some(r) = self.windows.get_mut(&f).and_then(|w| w.float_rect.as_mut()) {
                r.x += dx;
                r.y += dy;
            }
            return;
        }
        let tiled = self.tiled_on(ws);
        let rects: Vec<Rect> =
            tiled.iter().map(|id| self.windows.get(id).and_then(|w| w.target).unwrap_or_default()).collect();
        let Some(k) = tiled.iter().position(|i| *i == f) else { return };
        match neighbor(&rects, k, dir) {
            Some(n) => {
                let a = self.order.iter().position(|i| *i == tiled[k]);
                let b = self.order.iter().position(|i| *i == tiled[n]);
                if let (Some(a), Some(b)) = (a, b) {
                    self.order.swap(a, b);
                }
            }
            None => {
                // Off the edge: onto the next output, if there is one.
                if let Some(out) = self.adjacent_output(dir) {
                    let target_ws = self.output(&out).map(|o| o.workspace);
                    if let (Some(t), Some(w)) = (target_ws, self.windows.get_mut(&f)) {
                        w.workspace = t;
                    }
                    self.focused_output = Some(out);
                    self.focus(&f);
                }
            }
        }
    }

    fn resize_dir(&mut self, dir: Dir) {
        let Some(f) = self.focused() else { return };
        let Some(ws) = self.focused_workspace() else { return };
        let grow = matches!(dir, Dir::Right | Dir::Down);
        if self.windows.get(&f).map_or(false, |w| w.floating) {
            if let Some(r) = self.windows.get_mut(&f).and_then(|w| w.float_rect.as_mut()) {
                let d = if grow { FLOAT_STEP } else { -FLOAT_STEP };
                if dir.horizontal() {
                    r.w = (r.w + d).max(100);
                } else {
                    r.h = (r.h + d).max(80);
                }
            }
            return;
        }
        let tiled = self.tiled_on(ws);
        let Some(k) = tiled.iter().position(|i| *i == f) else { return };
        let Some(out) = self.output_showing(ws) else { return };
        let inner = self.area_of(&out).shrink(self.config.gaps_out);
        let splits = self.splits.entry(ws).or_default();
        let t = dwindle(tiled.len(), inner, self.config.gaps_in, splits);
        layout::resize(splits, &t.axes, k, dir.horizontal(), grow, self.config.resize_step);
    }

    fn start_op(&mut self, seat_id: &ObjectId, resize: bool) {
        let Some(win) = self.seats.get(seat_id).and_then(|s| s.hovered.clone()) else { return };
        let Some(w) = self.windows.get_mut(&win) else { return };
        if w.fullscreen {
            return;
        }
        // Dragging a tiled window lifts it out of the tiling, as on Hyprland.
        if !w.floating {
            w.floating = true;
            w.float_rect = w.target;
        }
        let Some(start) = w.float_rect.or(w.target) else { return };
        let Some(seat) = self.seats.get_mut(seat_id) else { return };
        seat.proxy.op_start_pointer();
        seat.op = Some(Op { window: win.clone(), start, resize, delta: (0, 0) });
        seat.op_released = false;
        self.focus(&win);
    }

    fn drive_ops(&mut self) {
        let mut updates = Vec::new();
        for seat in self.seats.values_mut() {
            if let Some(op) = &seat.op {
                let (dx, dy) = op.delta;
                let r = if op.resize {
                    Rect::new(op.start.x, op.start.y, (op.start.w + dx).max(100), (op.start.h + dy).max(80))
                } else {
                    Rect::new(op.start.x + dx, op.start.y + dy, op.start.w, op.start.h)
                };
                updates.push((op.window.clone(), r));
            }
            if seat.op_released {
                seat.op_released = false;
                seat.op = None;
                seat.proxy.op_end();
            }
        }
        for (id, r) in updates {
            if let Some(w) = self.windows.get_mut(&id) {
                w.float_rect = Some(r);
            }
        }
    }

    fn layout(&mut self) {
        let focused = self.focused();
        let displayed: Vec<(ObjectId, usize, bool, Rect)> = self
            .live_outputs()
            .map(|(i, o)| (i.clone(), o.workspace, o.handset, layout::usable_area(o.rect, o.area)))
            .collect();

        let mut targets: HashMap<ObjectId, Rect> = HashMap::new();
        let mut proposals: HashMap<ObjectId, (i32, i32)> = HashMap::new();
        let mut bordered: HashMap<ObjectId, bool> = HashMap::new();

        for (_, ws, handset, area) in &displayed {
            if *handset {
                // One app at a time, filling the space the shell leaves.
                if let Some(top) = self.top_of(*ws) {
                    targets.insert(top.clone(), *area);
                    if !self.windows.get(&top).map_or(false, |w| w.fullscreen) {
                        proposals.insert(top, (area.w, area.h));
                    }
                }
                continue;
            }
            let bw = self.config.border;
            let tiled = self.tiled_on(*ws);
            let inner = area.shrink(self.config.gaps_out);
            let splits = self.splits.get(ws).cloned().unwrap_or_default();
            let t = dwindle(tiled.len(), inner, self.config.gaps_in, &splits);
            for (id, rect) in tiled.iter().zip(t.rects) {
                let content = rect.shrink(bw);
                targets.insert(id.clone(), content);
                proposals.insert(id.clone(), (content.w, content.h));
                bordered.insert(id.clone(), focused.as_ref() == Some(id));
            }
            for id in self.windows_on(*ws) {
                let Some(w) = self.windows.get(&id) else { continue };
                if w.fullscreen {
                    targets.insert(id.clone(), *area);
                } else if w.floating {
                    match w.float_rect {
                        Some(r) => {
                            targets.insert(id.clone(), r);
                            proposals.insert(id.clone(), (r.w, r.h));
                        }
                        // No size yet: let the window pick, centre it once
                        // its dimensions arrive.
                        None => {
                            proposals.insert(id.clone(), (0, 0));
                        }
                    }
                    bordered.insert(id.clone(), focused.as_ref() == Some(&id));
                }
            }
        }

        let (bw, active, inactive) = (self.config.border, self.config.border_active, self.config.border_inactive);
        for (id, w) in self.windows.iter_mut() {
            if w.closed {
                continue;
            }
            w.target = targets.get(id).copied();
            if let Some(&(pw, ph)) = proposals.get(id) {
                if w.proposed != Some((pw, ph)) {
                    w.proxy.propose_dimensions(pw, ph);
                    w.proposed = Some((pw, ph));
                }
            }
            let want = bordered.get(id).copied();
            if want != w.border_active {
                match want {
                    Some(on) => {
                        let c = if on { active } else { inactive };
                        let ch = |shift: u32| ((c >> shift) & 0xff) * 0x0101_0101;
                        w.proxy.set_borders(
                            Edges::Top | Edges::Bottom | Edges::Left | Edges::Right,
                            bw,
                            ch(16),
                            ch(8),
                            ch(0),
                            u32::MAX,
                        );
                        w.proxy.set_tiled(if w.floating {
                            Edges::None
                        } else {
                            Edges::Top | Edges::Bottom | Edges::Left | Edges::Right
                        });
                    }
                    None => w.proxy.set_borders(Edges::None, 0, 0, 0, 0, 0),
                }
                w.border_active = want;
            }
        }
    }

    fn send_focus(&mut self) {
        let want = if self.locked { None } else { self.focused() };
        for seat in self.seats.values_mut() {
            if seat.layer_focus == LayerFocus::Exclusive {
                continue;
            }
            if seat.sent_focus.as_ref() == Some(&want) {
                continue;
            }
            match want.as_ref().and_then(|id| self.windows.get(id)) {
                Some(w) => seat.proxy.focus_window(&w.proxy),
                None => seat.proxy.clear_focus(),
            }
            seat.sent_focus = Some(want.clone());
        }
    }

    // ---------------------------------------------------------------- render

    fn render(&mut self) {
        let focused = self.focused();
        let mut tiled = Vec::new();
        let mut floating = Vec::new();
        for id in &self.order {
            let Some(w) = self.windows.get_mut(id) else { continue };
            if w.closed {
                continue;
            }
            // A floating window that just learned its size gets centred.
            if w.floating && w.float_rect.is_none() {
                if let Some((dw, dh)) = w.dims {
                    let out = self
                        .outputs
                        .iter()
                        .find(|(_, o)| !o.removed && o.workspace == w.workspace)
                        .map(|(_, o)| layout::usable_area(o.rect, o.area));
                    if let Some(area) = out {
                        let r = centered(area, dw, dh);
                        w.float_rect = Some(r);
                        w.target = Some(r);
                    }
                }
            }
            match w.target {
                Some(r) if w.dims.is_some() || w.fullscreen => {
                    if !w.fullscreen {
                        w.node.set_position(r.x, r.y);
                    }
                    if !w.shown {
                        w.proxy.show();
                        w.shown = true;
                    }
                    if w.floating {
                        floating.push(id.clone());
                    } else {
                        tiled.push(id.clone());
                    }
                }
                _ => {
                    if w.shown {
                        w.proxy.hide();
                        w.shown = false;
                    }
                }
            }
        }
        for id in tiled.iter().chain(floating.iter()) {
            if let Some(w) = self.windows.get(id) {
                w.node.place_top();
            }
        }
        if let Some(w) = focused.as_ref().and_then(|f| self.windows.get(f)) {
            if w.floating {
                w.node.place_top();
            }
        }
        self.wm.render_finish();
    }

    // ---------------------------------------------------------------- misc

    fn spawn(&self, cmd: &str) {
        let mut c = Command::new("sh");
        c.arg("-c").arg(cmd).stdin(Stdio::null());
        if let Some(s) = &self.socket {
            c.env("XXWM_SOCKET", s);
        }
        match c.spawn() {
            Ok(mut child) => {
                std::thread::spawn(move || {
                    let _ = child.wait();
                });
            }
            Err(e) => warn!("could not run {cmd:?}: {e}"),
        }
    }

    fn serve_ipc(&mut self) {
        let requests: Vec<ipc::Request> = std::mem::take(&mut *self.ipc.lock().unwrap());
        for r in requests {
            let answer = self.ipc_answer(r.line.trim());
            let _ = r.reply.send(answer);
        }
    }

    fn find_by_identifier(&self, ident: &str) -> Option<ObjectId> {
        self.windows.iter().find(|(_, w)| w.identifier == ident && !w.closed).map(|(i, _)| i.clone())
    }

    fn ipc_answer(&mut self, line: &str) -> String {
        let (verb, arg) = match line.split_once(char::is_whitespace) {
            Some((v, a)) => (v, a.trim()),
            None => (line, ""),
        };
        match verb {
            "list" => self.window_list_json(),
            "outputs" => {
                let items: Vec<String> = self
                    .live_outputs()
                    .map(|(i, o)| {
                        format!(
                            "{{\"x\":{},\"y\":{},\"w\":{},\"h\":{},\"handset\":{},\"workspace\":{},\"focused\":{}}}",
                            o.rect.x,
                            o.rect.y,
                            o.rect.w,
                            o.rect.h,
                            o.handset,
                            o.workspace,
                            self.focused_output.as_ref() == Some(i),
                        )
                    })
                    .collect();
                format!("[{}]", items.join(","))
            }
            "focus" | "close" => {
                let Some(id) = self.find_by_identifier(arg) else {
                    return format!("error: no window {arg:?}");
                };
                if verb == "close" {
                    if let Some(w) = self.windows.get(&id) {
                        w.proxy.close();
                    }
                    return "ok".into();
                }
                // Switching to a window on an unshown workspace brings that
                // workspace to the focused output (the phone's switcher).
                let ws = self.windows.get(&id).map(|w| w.workspace).unwrap_or(1);
                if self.output_showing(ws).is_none() {
                    self.show_workspace(ws);
                }
                self.focus(&id);
                "ok".into()
            }
            "action" => match crate::config::parse_action(arg) {
                Ok(a) => {
                    self.action(a);
                    "ok".into()
                }
                Err(e) => format!("error: {e}"),
            },
            "reload" => {
                let text = self.config_path.as_ref().and_then(|p| std::fs::read_to_string(p).ok());
                let (c, errors) = Config::load(text.as_deref());
                self.config = c;
                self.rebind = true;
                for w in self.windows.values_mut() {
                    w.border_active = None;
                }
                if errors.is_empty() {
                    "ok".into()
                } else {
                    format!("ok, with errors: {}", errors.join("; "))
                }
            }
            _ => format!("error: unknown request {verb:?}"),
        }
    }
}

// -------------------------------------------------------------------- dispatch

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
    fn event(_: &mut Self, _: &wl_registry::WlRegistry, _: wl_registry::Event, _: &GlobalListContents, _: &Connection, _: &QueueHandle<Self>) {}
}

impl Dispatch<RiverWindowManagerV1, ()> for State {
    fn event(state: &mut Self, _: &RiverWindowManagerV1, event: river_window_manager_v1::Event, _: &(), _: &Connection, qh: &QueueHandle<Self>) {
        use river_window_manager_v1::Event as E;
        match event {
            E::Unavailable => {
                warn!("window management is not available (is another window manager running?)");
                state.exit = Some(1);
            }
            E::Finished => state.exit = Some(0),
            E::ManageStart => state.manage(qh),
            E::RenderStart => state.render(),
            E::SessionLocked => state.locked = true,
            E::SessionUnlocked => state.locked = false,
            E::Window { id } => {
                let node = id.get_node(qh, ());
                let key = id.id();
                state.windows.insert(
                    key.clone(),
                    Window {
                        proxy: id,
                        node,
                        identifier: String::new(),
                        app_id: None,
                        title: None,
                        workspace: 1,
                        floating: false,
                        fullscreen: false,
                        dims: None,
                        float_rect: None,
                        proposed: None,
                        target: None,
                        shown: true,
                        border_active: None,
                        is_new: true,
                        closed: false,
                        has_parent: false,
                        fixed_size: false,
                    },
                );
                state.order.push(key);
            }
            E::Output { id } => {
                let layer = state.layer.as_ref().map(|l| l.get_output(&id, qh, id.id()));
                state.outputs.push((
                    id.id(),
                    Output {
                        proxy: id,
                        layer,
                        rect: Rect::default(),
                        area: None,
                        workspace: 0,
                        handset: false,
                        removed: false,
                        is_new: true,
                    },
                ));
            }
            E::Seat { id } => {
                state.seats.insert(
                    id.id(),
                    Seat {
                        proxy: id,
                        xkb_seat: None,
                        layer_seat: None,
                        binds: Vec::new(),
                        pointer_binds: Vec::new(),
                        ready: false,
                        layer_focus: LayerFocus::None,
                        sent_focus: None,
                        hovered: None,
                        op: None,
                        op_released: false,
                        super_down: false,
                        super_used: false,
                        removed: false,
                    },
                );
            }
        }
    }

    event_created_child!(State, RiverWindowManagerV1, [
        river_window_manager_v1::EVT_WINDOW_OPCODE => (RiverWindowV1, ()),
        river_window_manager_v1::EVT_OUTPUT_OPCODE => (RiverOutputV1, ()),
        river_window_manager_v1::EVT_SEAT_OPCODE => (RiverSeatV1, ()),
    ]);
}

impl Dispatch<RiverWindowV1, ()> for State {
    fn event(state: &mut Self, proxy: &RiverWindowV1, event: river_window_v1::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        use river_window_v1::Event as E;
        let id = proxy.id();
        match event {
            E::FullscreenRequested { .. } => state.pending.push(Pending::Fullscreen(id, true)),
            E::ExitFullscreenRequested => state.pending.push(Pending::Fullscreen(id, false)),
            event => {
                let Some(w) = state.windows.get_mut(&id) else { return };
                match event {
                    E::Closed => w.closed = true,
                    E::Dimensions { width, height } => w.dims = Some((width, height)),
                    E::AppId { app_id } => w.app_id = app_id,
                    E::Title { title } => w.title = title,
                    E::Identifier { identifier } => w.identifier = identifier,
                    E::Parent { parent } => w.has_parent = parent.is_some(),
                    E::DimensionsHint { min_width, min_height, max_width, max_height } => {
                        w.fixed_size = max_width > 0 && min_width == max_width && max_height > 0 && min_height == max_height;
                    }
                    _ => {}
                }
            }
        }
    }
}

impl Dispatch<RiverNodeV1, ()> for State {
    fn event(_: &mut Self, _: &RiverNodeV1, _: <RiverNodeV1 as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {}
}

impl Dispatch<RiverOutputV1, ()> for State {
    fn event(state: &mut Self, proxy: &RiverOutputV1, event: river_output_v1::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        use river_output_v1::Event as E;
        let Some(o) = state.output_mut(&proxy.id()) else { return };
        match event {
            E::Removed => o.removed = true,
            E::Position { x, y } => {
                o.rect.x = x;
                o.rect.y = y;
            }
            E::Dimensions { width, height } => {
                o.rect.w = width;
                o.rect.h = height;
            }
            _ => {}
        }
    }
}

impl Dispatch<RiverSeatV1, ()> for State {
    fn event(state: &mut Self, proxy: &RiverSeatV1, event: river_seat_v1::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        use river_seat_v1::Event as E;
        let id = proxy.id();
        match event {
            E::WindowInteraction { window } => state.pending.push(Pending::Interact(window.id())),
            event => {
                let Some(s) = state.seats.get_mut(&id) else { return };
                match event {
                    E::Removed => s.removed = true,
                    E::PointerEnter { window } => s.hovered = Some(window.id()),
                    E::PointerLeave => s.hovered = None,
                    E::OpDelta { dx, dy } => {
                        if let Some(op) = s.op.as_mut() {
                            op.delta = (dx, dy);
                        }
                    }
                    E::OpRelease => s.op_released = true,
                    _ => {}
                }
            }
        }
    }
}

impl Dispatch<RiverPointerBindingV1, (ObjectId, bool)> for State {
    fn event(state: &mut Self, _: &RiverPointerBindingV1, event: river_pointer_binding_v1::Event, data: &(ObjectId, bool), _: &Connection, _: &QueueHandle<Self>) {
        if let river_pointer_binding_v1::Event::Pressed = event {
            if let Some(s) = state.seats.get_mut(&data.0) {
                s.super_used = true;
            }
            state.pending.push(Pending::PointerOp { seat: data.0.clone(), resize: data.1 });
        }
    }
}

impl Dispatch<RiverXkbBindingsV1, ()> for State {
    fn event(_: &mut Self, _: &RiverXkbBindingsV1, _: <RiverXkbBindingsV1 as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {}
}

impl Dispatch<RiverXkbBindingV1, usize> for State {
    fn event(state: &mut Self, _: &RiverXkbBindingV1, event: river_xkb_binding_v1::Event, index: &usize, _: &Connection, _: &QueueHandle<Self>) {
        if let river_xkb_binding_v1::Event::Pressed = event {
            for s in state.seats.values_mut() {
                if s.super_down {
                    s.super_used = true;
                }
            }
            if let Some(bind) = state.config.binds.get(*index) {
                state.pending.push(Pending::Action(bind.action.clone()));
            }
        }
    }
}

impl Dispatch<RiverXkbBindingsSeatV1, ObjectId> for State {
    fn event(state: &mut Self, proxy: &RiverXkbBindingsSeatV1, event: river_xkb_bindings_seat_v1::Event, seat: &ObjectId, _: &Connection, _: &QueueHandle<Self>) {
        use river_xkb_bindings_seat_v1::Event as E;
        let tap = state.config.super_tap;
        let Some(s) = state.seats.get_mut(seat) else { return };
        match event {
            E::ModifiersUpdate { old, new } => {
                let bits = |m: WEnum<Modifiers>| match m {
                    WEnum::Value(v) => v.bits(),
                    WEnum::Unknown(u) => u,
                };
                let was = bits(old) & mods::SUPER != 0;
                let is = bits(new) & mods::SUPER != 0;
                if !was && is {
                    s.super_down = true;
                    s.super_used = false;
                    // Eat the next unbound key so Super+<unbound> counts as
                    // "used" and does not open the launcher on release.
                    proxy.ensure_next_key_eaten();
                } else if was && !is {
                    proxy.cancel_ensure_next_key_eaten();
                    let fire = s.super_down && !s.super_used && tap;
                    s.super_down = false;
                    if fire {
                        state.pending.push(Pending::Action(Action::Launcher));
                    }
                }
            }
            E::AteUnboundKey => s.super_used = true,
        }
    }
}

impl Dispatch<RiverLayerShellV1, ()> for State {
    fn event(_: &mut Self, _: &RiverLayerShellV1, _: <RiverLayerShellV1 as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {}
}

impl Dispatch<RiverLayerShellOutputV1, ObjectId> for State {
    fn event(state: &mut Self, _: &RiverLayerShellOutputV1, event: river_layer_shell_output_v1::Event, output: &ObjectId, _: &Connection, _: &QueueHandle<Self>) {
        let river_layer_shell_output_v1::Event::NonExclusiveArea { x, y, width, height } = event;
        if let Some(o) = state.output_mut(output) {
            o.area = Some(Rect::new(x, y, width, height));
        }
    }
}

impl Dispatch<RiverLayerShellSeatV1, ObjectId> for State {
    fn event(state: &mut Self, _: &RiverLayerShellSeatV1, event: river_layer_shell_seat_v1::Event, seat: &ObjectId, _: &Connection, _: &QueueHandle<Self>) {
        use river_layer_shell_seat_v1::Event as E;
        let Some(s) = state.seats.get_mut(seat) else { return };
        match event {
            E::FocusExclusive => s.layer_focus = LayerFocus::Exclusive,
            E::FocusNonExclusive => {
                s.layer_focus = LayerFocus::NonExclusive;
                // The layer surface has focus now; our record is stale.
                s.sent_focus = None;
            }
            E::FocusNone => {
                s.layer_focus = LayerFocus::None;
                // Give focus back to the window that had it.
                s.sent_focus = None;
            }
        }
    }
}
