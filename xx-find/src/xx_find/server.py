"""Owner server core: device store, command queue, pure HTTP handler.

The server stores location reports as opaque ciphertext (`box` field);
`submit_report` rejects any envelope carrying plaintext coordinates, so a
compromised server still yields no locations. The request handler is a
pure function of (method, path, headers, body) for headless tests; the
thin `serve` adapter at the bottom is the only socket code and is never
exercised by tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from xx_find import protocol

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
SESSION_TTL_S = 12 * 3600
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_S = 900
COMMAND_MAX_ATTEMPTS = 10
COMMAND_WINDOW_S = 60


class ServerError(Exception):
    pass


class RateLimiter:
    """In-memory per-key attempt gate (never persisted, never on disk).

    `allowed` records the attempt and returns False once more than
    `max_attempts` land inside the trailing `window_s` seconds.
    Callers pass an injected `now`, so tests drive it without clocks.
    """

    def __init__(self, max_attempts: int = 5,
                 window_s: int = 900) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.window_s = max(1, int(window_s))
        self._hits: dict[str, list[int]] = {}

    def allowed(self, key: str, now: int) -> bool:
        moment = int(now)
        bucket = self._hits.setdefault(str(key), [])
        cutoff = moment - self.window_s
        while bucket and bucket[0] <= cutoff:
            bucket.pop(0)
        if len(bucket) >= self.max_attempts:
            return False
        bucket.append(moment)
        return True

    def clear(self, key: str) -> None:
        self._hits.pop(str(key), None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Slippy-map tile for server-side map rendering (no JS map library,
    no Google: plain OSM tile <img> with a positioned marker)."""
    zoom = max(0, min(19, int(zoom)))
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    lon = float(lon)
    count = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * count)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad))
             / math.pi) / 2.0 * count)
    return (max(0, min(count - 1, x)), max(0, min(count - 1, y)))


class Store:
    """JSON-backed device/command/ciphertext store."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.devices: dict = {}
        self.commands: dict = {}
        self.owner_password_hash: str = ""
        self.sessions: dict = {}
        self.login_limiter = RateLimiter(LOGIN_MAX_ATTEMPTS,
                                         LOGIN_WINDOW_S)
        self.command_limiter = RateLimiter(COMMAND_MAX_ATTEMPTS,
                                           COMMAND_WINDOW_S)
        if self.path is not None:
            self.load()

    def _device(self, device_id: str) -> dict:
        device = self.devices.get(device_id)
        if not isinstance(device, dict):
            raise ServerError("unknown device")
        return device

    def pair_device(self, device_id: str) -> str:
        if not device_id or not isinstance(device_id, str):
            raise ServerError("bad device id")
        token = secrets.token_urlsafe(24)
        self.devices[device_id] = {
            "token_hash": _hash_token(token),
            "last_counter": 0,
            "last_report": None,
            "last_report_epoch": 0,
        }
        self.commands.setdefault(device_id, [])
        self.save()
        return token

    def check_device_token(self, device_id: str, token: str) -> bool:
        try:
            device = self._device(device_id)
        except ServerError:
            return False
        return hmac.compare_digest(device.get("token_hash", ""),
                                   _hash_token(token or ""))

    def submit_report(self, device_id: str, envelope: dict,
                      now: int) -> dict:
        device = self._device(device_id)
        for banned in ("lat", "lon", "latitude", "longitude", "plaintext"):
            if banned in envelope:
                raise ServerError("server never stores plaintext location")
        checked = protocol.validate_report(
            envelope, device.get("last_counter", 0), now)
        device["last_counter"] = int(checked["counter"])
        device["last_report"] = checked
        device["last_report_epoch"] = int(checked["epoch"])
        self.save()
        return {"ok": True}

    def queue_envelope(self, device_id: str, envelope: dict) -> dict:
        self._device(device_id)
        cmd, _ = envelope.get("cmd", {}), envelope.get("sig")
        if not isinstance(cmd, dict) or not isinstance(envelope.get("sig"),
                                                       str):
            raise ServerError("bad command envelope")
        raw_counter = cmd.get("counter", -1)
        if isinstance(raw_counter, bool) \
                or not isinstance(raw_counter, int):
            raise ServerError("bad command envelope")
        counter = raw_counter
        pending = self.commands.setdefault(device_id, [])
        seen = [int(c["cmd"]["counter"]) for c in pending
                if isinstance(c.get("cmd"), dict)]
        if seen and counter <= max(seen):
            raise ServerError("counter not newer than queued")
        pending.append(envelope)
        self.save()
        return {"ok": True, "queued": len(pending)}

    def pending(self, device_id: str, since: int) -> list:
        self._device(device_id)
        out = []
        for envelope in self.commands.get(device_id, []):
            cmd = envelope.get("cmd", {})
            if isinstance(cmd, dict) and int(cmd.get("counter", -1)) > since:
                out.append(envelope)
        return out

    def set_owner_password(self, password: str) -> None:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     salt, 200_000)
        self.owner_password_hash = salt.hex() + "$" + digest.hex()
        self.save()

    def check_owner_password(self, password: str) -> bool:
        try:
            salt_hex, digest_hex = self.owner_password_hash.split("$")
            expect = bytes.fromhex(digest_hex)
        except (ValueError, AttributeError):
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(expect, digest)

    def open_session(self, now: int) -> str:
        token = secrets.token_urlsafe(24)
        self.sessions[token] = int(now) + SESSION_TTL_S
        return token

    def check_session(self, token: str, now: int) -> bool:
        return int(now) < int(self.sessions.get(token or "", 0))

    def to_dict(self) -> dict:
        return {"devices": self.devices, "commands": self.commands,
                "owner_password_hash": self.owner_password_hash}

    def load(self) -> None:
        assert self.path is not None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            if isinstance(raw.get("devices"), dict):
                self.devices = raw["devices"]
            if isinstance(raw.get("commands"), dict):
                self.commands = raw["commands"]
            if isinstance(raw.get("owner_password_hash"), str):
                self.owner_password_hash = raw["owner_password_hash"]

    def save(self) -> None:
        """Atomic store write (tmp + fsync + rename + dir fsync) so a
        crash never leaves a torn store.json behind."""
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            text = json.dumps(self.to_dict()) + "\n"
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(str(tmp),
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            try:
                dfd = os.open(str(self.path.parent), os.O_DIRECTORY)
            except OSError:
                return
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


def _unauthorized() -> tuple:
    return (401, "text/plain; charset=utf-8", "unauthorized")


def _limited() -> tuple:
    return (429, "text/plain; charset=utf-8",
            "too many attempts, try again later")


def handle_request(method: str, path: str, headers: dict,
                   body: str, store: Store, now: int,
                   client_ip: str = "127.0.0.1") -> tuple:
    """Pure request handler. Returns (status, content_type, body_text).

    `client_ip` keys the per-IP login/command rate limits; the thin
    `serve` adapter fills it from the socket peer. Login is additionally
    limited per account, command submission per device.
    """
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    parsed = urlparse(path)
    route, query = parsed.path, parse_qs(parsed.query)
    peer = str(client_ip or "127.0.0.1")

    if route == "/v1/report" and method == "POST":
        try:
            payload = json.loads(body or "{}")
        except ValueError:
            return (400, "text/plain; charset=utf-8", "bad json")
        if not isinstance(payload, dict):
            return (400, "text/plain; charset=utf-8", "bad json")
        device_id = payload.get("device_id", "")
        if not store.check_device_token(
                device_id, headers.get("x-device-token", "")):
            return _unauthorized()
        try:
            store.submit_report(device_id, payload.get("report", {}), now)
        except (ServerError, protocol.ProtocolError) as exc:
            return (400, "text/plain; charset=utf-8", str(exc))
        return (200, "application/json", '{"ok": true}')

    if route == "/v1/commands" and method == "GET":
        device_id = query.get("device", [""])[0]
        try:
            since = int(query.get("since", ["0"])[0])
        except ValueError:
            since = 0
        if not store.check_device_token(
                device_id, headers.get("x-device-token", "")):
            return _unauthorized()
        try:
            items = store.pending(device_id, since)
        except ServerError:
            return _unauthorized()
        return (200, "application/json",
                json.dumps({"commands": items}))

    if route == "/v1/command" and method == "POST":
        if not store.check_session(headers.get("x-session", ""), now):
            return _unauthorized()
        try:
            payload = json.loads(body or "{}")
            device_id = payload["device_id"]
        except (ValueError, KeyError) as exc:
            return (400, "text/plain; charset=utf-8", str(exc))
        if not store.command_limiter.allowed("ip:%s" % peer, now):
            return _limited()
        if not store.command_limiter.allowed("device:%s" % device_id,
                                             now):
            return _limited()
        try:
            store.queue_envelope(device_id, payload["envelope"])
        except (ValueError, KeyError, ServerError) as exc:
            return (400, "text/plain; charset=utf-8", str(exc))
        return (200, "application/json", '{"ok": true}')

    if route == "/login" and method == "POST":
        try:
            payload = json.loads(body or "{}")
        except ValueError:
            return (400, "text/plain; charset=utf-8", "bad json")
        if not isinstance(payload, dict):
            return (400, "text/plain; charset=utf-8", "bad json")
        if not store.login_limiter.allowed("ip:%s" % peer, now):
            return _limited()
        if not store.login_limiter.allowed("account:owner", now):
            return _limited()
        if store.check_owner_password(payload.get("password", "")):
            store.login_limiter.clear("ip:%s" % peer)
            store.login_limiter.clear("account:owner")
            token = store.open_session(now)
            return (200, "application/json",
                    json.dumps({"session": token}))
        time.sleep(0)
        return _unauthorized()

    if route == "/api/devices" and method == "GET":
        if not store.check_session(headers.get("x-session", ""), now):
            return _unauthorized()
        devices = []
        for device_id, device in store.devices.items():
            pending = store.commands.get(device_id, [])
            devices.append({
                "device_id": device_id,
                "last_report_epoch": device.get("last_report_epoch", 0),
                "last_counter": device.get("last_counter", 0),
                "has_report": device.get("last_report") is not None,
                "pending": len(pending),
            })
        return (200, "application/json",
                json.dumps({"devices": devices}))

    if route == "/api/report" and method == "GET":
        if not store.check_session(headers.get("x-session", ""), now):
            return _unauthorized()
        device_id = query.get("device", [""])[0]
        try:
            device = store._device(device_id)
        except ServerError:
            return (404, "text/plain; charset=utf-8", "unknown device")
        return (200, "application/json",
                json.dumps({"report": device.get("last_report")}))

    if route in ("/", "/devices") and method == "GET":
        if not store.check_session(headers.get("x-session", ""), now):
            return (200, "text/html; charset=utf-8", _login_page())
        return (200, "text/html; charset=utf-8", _devices_page(store))

    return (404, "text/plain; charset=utf-8", "not found")


def _owner_js() -> str:
    """Pure owner-side crypto (canonical JSON, Ed25519, sealed-box
    open, tile math). Served verbatim inside the UI pages; also used
    by the committed vector check under node/deno/qjs."""
    try:
        return (Path(__file__).with_name("owner.js")).read_text(
            encoding="utf-8")
    except OSError:
        return ""


def _app_shell() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>find owner console</title>
<style>
body{font-family:system-ui,sans-serif;max-width:44rem;margin:0 auto;
padding:1rem;line-height:1.4;color:#111;background:#fff}
@media(prefers-color-scheme:dark){body{color:#eee;background:#111}
input,select,button{color:#eee;background:#222;border-color:#555}}
input,select,button{font:inherit;margin:.2rem 0;padding:.3rem .5rem;
max-width:100%;box-sizing:border-box}
label{display:block;margin-top:.6rem}
#mapwrap{position:relative;width:256px;height:256px;border:1px solid #888}
#mapwrap img{width:256px;height:256px;display:block}
#marker{position:absolute;width:10px;height:10px;margin:-5px;border-radius:50%;
background:#c00;border:2px solid #fff}
pre{white-space:pre-wrap;word-break:break-all;background:#f4f4f4;padding:.5rem}
@media(prefers-color-scheme:dark){pre{background:#1e1e1e}}
.err{color:#a00}@media(prefers-color-scheme:dark){.err{color:#f88}}
</style>
</head>
<body>
<h1>find owner console</h1>
<p>Locations decrypt in this browser with your key; this server stores
ciphertext only. Map tiles: OpenStreetMap contributors.</p>
<section id="login">
<h2>owner login</h2>
<label>Password <input type="password" id="password" autocomplete="off"></label>
<button type="button" id="signin">Sign in</button>
<p class="err" id="loginerr"></p>
</section>
<section id="app" hidden>
<h2>devices</h2>
<ul id="devlist"></ul>
<div id="detail" hidden>
<h2 id="dname"></h2>
<p id="dmeta"></p>
<h3>last report (ciphertext)</h3>
<pre id="box"></pre>
<h3>decrypt in browser</h3>
<label>Owner secret key (hex, stays in this browser)
<input type="password" id="seckey" autocomplete="off"></label>
<button type="button" id="decrypt">Decrypt</button>
<p class="err" id="decerr"></p>
<pre id="plain" hidden></pre>
<div id="mapwrap" hidden>
<img id="tile" alt="OpenStreetMap tile around the last fix">
<div id="marker"></div>
</div>
<p id="attrib" hidden>Tile: OpenStreetMap contributors.</p>
<h3>send command (signed in browser)</h3>
<label>Owner signing key (hex seed, stays in this browser)
<input type="password" id="signkey" autocomplete="off"></label>
<label>Type <select id="ctype">
<option value="locate">locate</option><option value="ring">ring</option>
<option value="message">message</option><option value="lock">lock</option>
<option value="erase">erase</option></select></label>
<label>Message text <input id="atext" maxlength="280"></label>
<label>Callback number <input id="acallback" maxlength="64"></label>
<label>Erase confirmation (device id, verbatim)
<input id="aconfirm" autocomplete="off"></label>
<label>Counter <input id="acounter" inputmode="numeric"></label>
<button type="button" id="send">Sign and queue</button>
<p class="err" id="cmderr"></p>
<p id="cmdok"></p>
</div>
<p><button type="button" id="logout" hidden>Sign out</button></p>
</section>
<script>
""" + _owner_js() + """
</script>
<script>
(function () {
  "use strict";
  var session = null;
  var current = null;
  function $(id) { return document.getElementById(id); }
  function fail(el, msg) { $(el).textContent = msg; }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = { "x-session": session || "" };
    if (opts.body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
    }
    return fetch(path, opts).then(function (r) {
      if (r.status === 401) { throw new Error("not signed in"); }
      if (r.status === 429) {
        throw new Error("too many attempts, try again later");
      }
      return r.json().then(function (body) {
        if (!r.ok) { throw new Error(body || "request failed"); }
        return body;
      });
    });
  }
  $("signin").onclick = function () {
    fail("loginerr", "");
    fetch("/login", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("password").value }) })
      .then(function (r) {
        if (r.status === 429) {
          throw new Error("too many attempts, try again later");
        }
        return r.json().then(function (body) {
          if (!r.ok || !body.session) { throw new Error("rejected"); }
          return body.session;
        });
      })
      .then(function (token) {
        session = token;
        $("password").value = "";
        $("login").hidden = true;
        $("app").hidden = false;
        $("logout").hidden = false;
        loadDevices();
      })
      .catch(function (e) { fail("loginerr", String(e.message || e)); });
  };
  $("logout").onclick = function () {
    session = null;
    $("app").hidden = true;
    $("logout").hidden = true;
    $("login").hidden = false;
  };
  function loadDevices() {
    api("/api/devices").then(function (body) {
      var list = $("devlist");
      list.textContent = "";
      if (!body.devices.length) {
        var li = document.createElement("li");
        li.textContent = "no devices paired";
        list.appendChild(li);
        return;
      }
      body.devices.forEach(function (dev) {
        var item = document.createElement("li");
        var btn = document.createElement("button");
        btn.type = "button";
        var seen = dev.last_report_epoch ?
          new Date(dev.last_report_epoch * 1000).toISOString() : "never";
        btn.textContent = dev.device_id + " last report: " + seen +
          " queued: " + dev.pending;
        btn.onclick = function () { showDevice(dev); };
        item.appendChild(btn);
        list.appendChild(item);
      });
    }).catch(function (e) { fail("loginerr", String(e.message || e)); });
  }
  function showDevice(dev) {
    current = dev.device_id;
    $("detail").hidden = false;
    $("dname").textContent = dev.device_id;
    $("dmeta").textContent = "counter " + dev.last_counter +
      ", queued " + dev.pending;
    $("plain").hidden = true;
    $("mapwrap").hidden = true;
    $("attrib").hidden = true;
    $("cmdok").textContent = "";
    fail("decerr", "");
    fail("cmderr", "");
    $("acounter").value = String(dev.last_counter + 1);
    api("/api/report?device=" + encodeURIComponent(dev.device_id))
      .then(function (body) {
        $("box").textContent = body.report ?
          body.report.box : "no report yet";
        current = { id: dev.device_id, report: body.report };
      })
      .catch(function (e) { fail("decerr", String(e.message || e)); });
  }
  $("decrypt").onclick = function () {
    fail("decerr", "");
    try {
      if (!current || !current.report) { throw new Error("no report"); }
      if (typeof XXFIND === "undefined") {
        throw new Error("owner crypto unavailable");
      }
      var sec = XXFIND.hexToBytes($("seckey").value.trim());
      var pub = XXFIND.x25519Base(sec);
      var pt = XXFIND.boxSealOpen(
        XXFIND.base64ToBytes(current.report.box), pub, sec);
      var fix = JSON.parse(XXFIND.bytesToUtf8(pt));
      $("plain").textContent = JSON.stringify(fix);
      $("plain").hidden = false;
      var zoom = 15;
      var t = XXFIND.latlonToTile(fix.lat, fix.lon, zoom);
      var f = XXFIND.tileMarkerFraction(fix.lat, fix.lon, zoom);
      $("tile").src = XXFIND.tileUrl(zoom, t[0], t[1]);
      $("marker").style.left = (f[0] * 100) + "%";
      $("marker").style.top = (f[1] * 100) + "%";
      $("mapwrap").hidden = false;
      $("attrib").hidden = false;
    } catch (e) { fail("decerr", String(e.message || e)); }
  };
  $("send").onclick = function () {
    fail("cmderr", "");
    $("cmdok").textContent = "";
    try {
      if (!current) { throw new Error("pick a device first"); }
      if (typeof XXFIND === "undefined") {
        throw new Error("owner crypto unavailable");
      }
      var deviceId = current.id || current;
      var type = $("ctype").value;
      var args = {};
      if (type === "message") {
        args.text = $("atext").value;
        args.callback = $("acallback").value;
      } else if (type === "erase") {
        args.confirmation = $("aconfirm").value;
      }
      var now = Math.floor(Date.now() / 1000);
      // One idle poll (900 s) plus skew/jitter margin: the phone only
      // polls about every 15 min, and the counter prevents replay.
      var cmd = { v: 1, device_id: deviceId,
        counter: parseInt($("acounter").value, 10),
        exp: now + 1200, type: type, args: args };
      if (!(cmd.counter > 0)) { throw new Error("bad counter"); }
      var seed = XXFIND.hexToBytes($("signkey").value.trim());
      var sig = XXFIND.bytesToBase64(
        XXFIND.edSign(XXFIND.canonical(cmd), seed));
      api("/v1/command", { method: "POST",
        body: JSON.stringify({ device_id: deviceId,
          envelope: { cmd: cmd, sig: sig } }) })
        .then(function () {
          $("cmdok").textContent = "queued";
          loadDevices();
        })
        .catch(function (e) { fail("cmderr", String(e.message || e)); });
    } catch (e) { fail("cmderr", String(e.message || e)); }
  };
})();
</script>
</body>
</html>"""


def _login_page() -> str:
    return _app_shell()


def _devices_page(store: Store) -> str:
    del store
    return _app_shell()


class _Adapter(BaseHTTPRequestHandler):
    store: Store | None = None

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        try:
            peer = self.client_address[0]
        except (AttributeError, IndexError):
            peer = "127.0.0.1"
        status, ctype, text = handle_request(
            self.command, self.path, dict(self.headers), body,
            self.store or Store(), int(time.time()), peer)
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def log_message(self, *args: object) -> None:
        pass


def serve(store: Store, host: str, port: int) -> None:
    """Blocking stdlib server (plain-Python fallback on the NAS). TLS and
    tailnet ACLs terminate outside this process; see PORT_SPEC.md."""
    _Adapter.store = store
    HTTPServer((host, port), _Adapter).serve_forever()
