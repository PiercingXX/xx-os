//! xxwm — the XX-WM window manager for the river compositor.
//!
//! river draws and handles input; xxwm decides where windows go. One
//! session covers both screens: a handset output shows one app at a time,
//! a desktop output (an external monitor) tiles with the owner's binds.

#[macro_use]
mod wm;
mod config;
mod ipc;
mod keysym;
mod layout;
mod protocol;

use std::sync::{Arc, Mutex};

use wayland_client::globals::registry_queue_init;
use wayland_client::Connection;

use protocol::layer::river_layer_shell_v1::RiverLayerShellV1;
use protocol::wm::river_window_manager_v1::RiverWindowManagerV1;
use protocol::xkb::river_xkb_bindings_v1::RiverXkbBindingsV1;

fn config_path() -> Option<std::path::PathBuf> {
    if let Some(p) = std::env::var_os("XXWM_CONFIG") {
        return Some(p.into());
    }
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::Path::new(&h).join(".config")))?;
    Some(base.join("xx-wm").join("wm.conf"))
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("--print-defaults") {
        print!("{}", config::DEFAULTS);
        return;
    }
    if args.first().map(String::as_str) == Some("msg") {
        std::process::exit(msg(&args[1..].join(" ")));
    }
    if let Err(e) = run() {
        eprintln!("xxwm: {e}");
        std::process::exit(1);
    }
}

/// `xxwm msg <request>`: send one control-socket request, print the answer.
fn msg(line: &str) -> i32 {
    use std::io::{Read, Write};
    let Some(path) = ipc::socket_path() else {
        eprintln!("xxwm: no XDG_RUNTIME_DIR");
        return 2;
    };
    let mut s = match std::os::unix::net::UnixStream::connect(&path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("xxwm: {}: {e}", path.display());
            return 2;
        }
    };
    let _ = writeln!(s, "{line}");
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    print!("{out}");
    if out.starts_with("error") { 1 } else { 0 }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let conn = Connection::connect_to_env()?;
    let (globals, mut queue) = registry_queue_init::<wm::State>(&conn)?;
    let qh = queue.handle();

    let manager: RiverWindowManagerV1 = globals
        .bind(&qh, 1..=6, ())
        .map_err(|_| "this compositor does not offer river-window-management-v1 (run xxwm inside river)")?;
    let xkb: Option<RiverXkbBindingsV1> = globals.bind(&qh, 1..=3, ()).ok();
    if xkb.is_none() {
        eprintln!("xxwm: river-xkb-bindings-v1 missing: no key bindings");
    }
    let layer: Option<RiverLayerShellV1> = globals.bind(&qh, 1..=1, ()).ok();

    let path = config_path();
    let text = path.as_ref().and_then(|p| std::fs::read_to_string(p).ok());
    let (config, errors) = config::Config::load(text.as_deref());
    for e in errors {
        eprintln!("xxwm: {}: {e}", path.as_ref().map(|p| p.display().to_string()).unwrap_or_default());
    }

    let queue_ipc: ipc::Queue = Arc::new(Mutex::new(Vec::new()));
    let subscribers: ipc::Subscribers = Arc::new(Mutex::new(Vec::new()));
    let socket = ipc::socket_path();
    if let Some(sock) = &socket {
        let wake_manager = manager.clone();
        let wake_conn = conn.clone();
        match ipc::start(sock, queue_ipc.clone(), subscribers.clone(), move || {
            wake_manager.manage_dirty();
            let _ = wake_conn.flush();
        }) {
            Ok(()) => eprintln!("xxwm: control socket {}", sock.display()),
            Err(e) => eprintln!("xxwm: no control socket: {e}"),
        }
    }

    let mut state = wm::State::new(manager, xkb, layer, config, path, queue_ipc, subscribers, socket.clone());
    let code = loop {
        queue.blocking_dispatch(&mut state)?;
        if let Some(code) = state.exit {
            break code;
        }
    };
    if let Some(sock) = socket {
        let _ = std::fs::remove_file(sock);
    }
    std::process::exit(code);
}
