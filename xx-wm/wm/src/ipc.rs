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
//! outputs                  -> [{"name":..,"x":..,"y":..,"w":..,"h":..,"handset":b,"workspace":N,
//!                               "focused":b,"title":..,"wl_output":N|null}]
//! binds                    -> [{"combo":"Super+Q","action":"close"}, ...]  (the live table)
//! focus <identifier>       -> ok | error ...
//! close <identifier>       -> ok | error ...
//! action <action>          -> ok | error ...    (same words as wm.conf binds)
//! workspace <N> [output]   -> ok | error ...    (show N on that output, by name)
//! reload                   -> ok                (re-read wm.conf)
//! subscribe                -> the `list` answer now and again on every
//!                             change, one JSON array per line, until the
//!                             client hangs up
//! subscribe state          -> {"outputs":[..],"windows":[..]} now and on
//!                             every change, plus {"shell":"<verb>"} for
//!                             each `shell` action and the launcher (the
//!                             XX-WM shell's top bar and launcher)
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
pub type Subscribers = Arc<Mutex<Vec<Subscriber>>>;

pub struct Subscriber {
    pub stream: UnixStream,
    /// `subscribe state`: outputs + windows objects and shell verbs,
    /// instead of the bare window list.
    pub state: bool,
    /// The last snapshot written, so each client hears every change once.
    pub sent: String,
}

impl Subscriber {
    /// Write one line; false when the client is gone or stopped reading.
    pub fn send(&mut self, line: &str) -> bool {
        let mut buf = String::with_capacity(line.len() + 1);
        buf.push_str(line);
        buf.push('\n');
        self.stream.write_all(buf.as_bytes()).is_ok()
    }
}

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
    if line == "subscribe" || line == "subscribe state" {
        stream.set_nonblocking(true)?;
        subscribers.lock().unwrap().push(Subscriber { stream, state: line != "subscribe", sent: String::new() });
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

    fn subscribe(line: &str) -> (Subscribers, UnixStream) {
        let (mut client, server) = UnixStream::pair().unwrap();
        client.write_all(line.as_bytes()).unwrap();
        let queue: Queue = Arc::new(Mutex::new(Vec::new()));
        let subs: Subscribers = Arc::new(Mutex::new(Vec::new()));
        serve(server, &queue, &subs, &|| {}).unwrap();
        (subs, client)
    }

    #[test]
    fn subscribe_kinds() {
        let (subs, _c) = subscribe("subscribe\n");
        assert!(!subs.lock().unwrap()[0].state);
        let (subs, _c) = subscribe("subscribe state\n");
        assert!(subs.lock().unwrap()[0].state);
    }

    #[test]
    fn subscriber_gets_lines_and_notices_hang_up() {
        let (subs, client) = subscribe("subscribe state\n");
        let mut s = subs.lock().unwrap().pop().unwrap();
        assert!(s.send("{\"shell\":\"launcher\"}"));
        let mut line = String::new();
        BufReader::new(&client).read_line(&mut line).unwrap();
        assert_eq!(line, "{\"shell\":\"launcher\"}\n");
        drop(client);
        assert!(!s.send("x"));
    }

    #[test]
    fn json_escapes() {
        assert_eq!(json_str("a\"b\\c\nd\u{1}"), "\"a\\\"b\\\\c\\nd\\u0001\"");
    }
}
