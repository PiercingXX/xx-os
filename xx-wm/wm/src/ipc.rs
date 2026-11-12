//! Control socket: how the XX-WM shell (and scripts) steer the window
//! manager. river lists windows to other clients through
//! ext-foreign-toplevel-list but gives them no way to activate or close
//! one; that goes through here, by the same identifier.
//!
//! One request per connection, one line in, one line (or a JSON array)
//! out:
//!
//! ```text
//! list                     -> [{"id":..,"app_id":..,"title":..,"workspace":N,"focused":b,"visible":b}]
//! outputs                  -> [{"x":..,"y":..,"w":..,"h":..,"handset":b,"workspace":N}]
//! focus <identifier>       -> ok | error ...
//! close <identifier>       -> ok | error ...
//! action <action>          -> ok | error ...    (same words as wm.conf binds)
//! reload                   -> ok                (re-read wm.conf)
//! subscribe                -> the `list` answer now and again on every
//!                             change, one JSON array per line, until the
//!                             client hangs up
//! ```

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub struct Request {
    pub line: String,
    pub reply: Sender<String>,
}

pub type Queue = Arc<Mutex<Vec<Request>>>;

/// Clients that asked to be told about every change (the shell's window
/// list). Written from the main loop, non-blocking: a client that stops
/// reading is dropped rather than stalling the window manager.
pub type Subscribers = Arc<Mutex<Vec<UnixStream>>>;

pub fn socket_path() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("XXWM_SOCKET") {
        return Some(PathBuf::from(p));
    }
    let dir = std::env::var_os("XDG_RUNTIME_DIR")?;
    let display = std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".into());
    let display = display.rsplit('/').next().unwrap_or("wayland-0").to_string();
    Some(Path::new(&dir).join(format!("xxwm-{display}.sock")))
}

/// Start the listener thread. `wake` is called after a request is queued; it
/// must make the main loop run a manage sequence (river's manage_dirty).
pub fn start(
    path: &Path,
    queue: Queue,
    subscribers: Subscribers,
    wake: impl Fn() + Send + 'static,
) -> std::io::Result<()> {
    if path.exists() {
        // A live window manager answers; a stale socket from a crash does not.
        if UnixStream::connect(path).is_ok() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::AddrInUse,
                "another xxwm owns the control socket",
            ));
        }
        let _ = std::fs::remove_file(path);
    }
    let listener = UnixListener::bind(path)?;
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    std::thread::Builder::new().name("xxwm-ipc".into()).spawn(move || {
        for stream in listener.incoming().flatten() {
            let _ = serve(stream, &queue, &subscribers, &wake);
        }
    })?;
    Ok(())
}

fn serve(stream: UnixStream, queue: &Queue, subscribers: &Subscribers, wake: &impl Fn()) -> std::io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    let mut line = String::new();
    BufReader::new(&stream).read_line(&mut line)?;
    let line = line.trim().to_string();
    if line.is_empty() {
        return Ok(());
    }
    if line == "subscribe" {
        stream.set_nonblocking(true)?;
        subscribers.lock().unwrap().push(stream);
        // A manage sequence sends the new subscriber the current list.
        wake();
        return Ok(());
    }
    let (tx, rx) = channel();
    queue.lock().unwrap().push(Request { line, reply: tx });
    wake();
    let answer = rx
        .recv_timeout(Duration::from_secs(3))
        .unwrap_or_else(|_| "error: window manager did not answer".into());
    let mut out = &stream;
    out.write_all(answer.as_bytes())?;
    out.write_all(b"\n")
}

pub fn json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_escapes() {
        assert_eq!(json_str("a\"b\\c\nd\u{1}"), "\"a\\\"b\\\\c\\nd\\u0001\"");
    }
}
