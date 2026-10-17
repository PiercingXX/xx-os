#!/usr/bin/env python3
"""SIGTERM lifecycle regression test for xx-keyboard.

Runs the real xx-keyboard binary against a minimal in-process fake
Wayland compositor (no display needed) and a private D-Bus session bus,
activates the input method, then stops the app the way systemd does
(SIGTERM) and requires a clean exit.

Covers the shutdown-order crash where tearing down the input surface
changed the deck's visible child, re-entered layout-switch logic and
dereferenced the already-cleared osk list (SEGV in
pos_input_surface_update_glide), plus the registry global-order crash
in pos_output/pos_wayland.

Stdlib only. Skips (exit 77) when dbus-daemon is unavailable.
"""
import argparse
import os
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

WIDTH = 360
HEIGHT = 720

SKIP = 77


def pad4(n):
    return (4 - n % 4) % 4


def pack_str(s):
    b = s.encode() + b"\x00"
    return struct.pack("I", len(b)) + b + b"\x00" * pad4(len(b))


def pack_u(*words):
    return struct.pack("I" * len(words), *words)


def msg(sender, opcode, payload=b""):
    return struct.pack("IHH", sender, opcode, 8 + len(payload)) + payload


class FakeComp:
    """Bare-bones Wayland server: advertises the globals xx-keyboard and
    GTK3 need, answers syncs, sends one layer-surface configure and swallows
    everything else by size. Unknown requests never desync the stream."""

    def __init__(self, rundir, name):
        self.logf = open(os.path.join(rundir, "comp.log"), "w")
        self.sockpath = os.path.join(rundir, name)
        try:
            os.unlink(self.sockpath)
        except FileNotFoundError:
            pass
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.sockpath)
        self.listener.listen(4)
        self.listener.settimeout(0.2)
        self.objs = {}
        self.serial = 0
        self.im_ids = []
        self.frames_seen = threading.Event()
        self.out = bytearray()
        self.conn = None
        self._stop = threading.Event()
        self.send_lock = threading.Lock()

    def send(self, data):
        with self.send_lock:
            self.out += data

    def flush(self):
        with self.send_lock:
            if not self.out or self.conn is None:
                return
            try:
                self.conn.sendall(bytes(self.out))
            except OSError:
                pass
            self.out = bytearray()

    def event(self, sender, opcode, payload=b""):
        if os.environ.get("KB_LIFECYCLE_DEBUG"):
            print("fakecomp: ev sender=%d(%s) op=%d len=%d" % (
                sender, self.objs.get(sender), opcode, len(payload)),
                file=self.logf, flush=True)
        self.send(msg(sender, opcode, payload))

    def next_serial(self):
        self.serial += 1
        return self.serial

    def send_globals(self, reg):
        for i, (iface, ver) in enumerate([
                ("wl_compositor", 4),
                ("wl_shm", 1),
                ("wl_seat", 7),
                ("wl_output", 4),
                ("xdg_wm_base", 3),
                ("zxdg_output_manager_v1", 3),
                ("zwp_input_method_manager_v2", 1),
                ("zwp_virtual_keyboard_manager_v1", 1),
                ("zwlr_layer_shell_v1", 4),
                ("zwlr_foreign_toplevel_manager_v1", 3),
                ("zphoc_device_state_v1", 2),
                ("ext_data_control_manager_v1", 2),
        ], start=1):
            self.event(reg, 0, pack_u(i) + pack_str(iface) + pack_u(ver))

    def bind_init(self, new_id, iface):
        if iface == "wl_seat":
            self.event(new_id, 0, pack_u(7))  # pointer|keyboard|touch
            self.event(new_id, 1, pack_str("fake-seat"))
        elif iface == "wl_output":
            self.event(new_id, 0, pack_u(0, 0, 100, 200, 0)
                       + pack_str("") + pack_str("FakeOut") + pack_u(0))
            self.event(new_id, 1, pack_u(3, WIDTH, HEIGHT, 60000))
            self.event(new_id, 3, pack_u(1))
            self.event(new_id, 4, pack_str("Fake-1"))
            self.event(new_id, 5, pack_str("fake output"))
            self.event(new_id, 2)
        elif iface == "wl_shm":
            self.event(new_id, 0, pack_u(0))
            self.event(new_id, 0, pack_u(1))
        elif iface == "zphoc_device_state_v1":
            self.event(new_id, 0, pack_u(0))

    def handle(self, sender, opcode, body, fds):
        if os.environ.get("KB_LIFECYCLE_DEBUG"):
            print("fakecomp: req sender=%d(%s) op=%d len=%d nfds=%d" % (
                sender, self.objs.get(sender), opcode, len(body), len(fds)),
                file=self.logf, flush=True)
        for f in fds:
            try:
                os.close(f)
            except OSError:
                pass
        iface = self.objs.get(sender)
        if sender == 1:  # wl_display
            if opcode == 0:  # sync
                (new_id,) = struct.unpack("I", body[:4])
                self.objs[new_id] = "wl_callback"
                self.event(new_id, 0, pack_u(self.next_serial()))
            elif opcode == 1:  # get_registry
                (new_id,) = struct.unpack("I", body[:4])
                self.objs[new_id] = "wl_registry"
                self.send_globals(new_id)
            return
        if iface == "wl_registry" and opcode == 0:  # bind
            slen, = struct.unpack("I", body[4:8])
            siface = body[8:8 + slen - 1].decode()
            off = 8 + slen + pad4(slen)
            _ver, new_id = struct.unpack("II", body[off:off + 8])
            self.objs[new_id] = siface
            print("bind %s -> %d" % (siface, new_id), file=self.logf,
                  flush=True)
            self.bind_init(new_id, siface)
            return
        creations = {
            ("wl_compositor", 0): "wl_surface",
            ("wl_compositor", 1): "wl_region",
            ("wl_shm", 0): "wl_shm_pool",
            ("wl_shm_pool", 0): "wl_buffer",
            ("wl_surface", 3): "wl_callback",
            ("wl_seat", 0): "wl_pointer",
            ("wl_seat", 1): "wl_keyboard",
            ("wl_seat", 2): "wl_touch",
            ("xdg_wm_base", 1): "xdg_positioner",
            ("zxdg_output_manager_v1", 1): "zxdg_output_v1",
            ("zwp_input_method_manager_v2", 0): "zwp_input_method_v2",
            ("zwp_virtual_keyboard_manager_v1", 0): "zwp_virtual_keyboard_v1",
            ("ext_data_control_manager_v1", 0): "ext_data_control_source_v1",
            ("ext_data_control_manager_v1", 1): "ext_data_control_device_v1",
            ("zwlr_layer_shell_v1", 0): "zwlr_layer_surface_v1",
            ("zwlr_layer_surface_v1", 5): "xdg_popup",
        }
        # These two declare (seat, id): new_id comes last on the wire.
        new_id_last = {
            ("zwp_input_method_manager_v2", 0),
            ("zwp_virtual_keyboard_manager_v1", 0),
        }
        key = (iface, opcode)
        if key in creations:
            off = 4 if key in new_id_last else 0
            (new_id,) = struct.unpack("I", body[off:off + 4])
            self.objs[new_id] = creations[key]
            if os.environ.get("KB_LIFECYCLE_DEBUG"):
                print("fakecomp: track %d as %s body=%s" % (
                    new_id, creations[key], body.hex()),
                    file=self.logf, flush=True)
            if key == ("zxdg_output_manager_v1", 1):
                self.event(new_id, 0, pack_u(0, 0))
                self.event(new_id, 1, pack_u(WIDTH, HEIGHT))
                self.event(new_id, 3, pack_str("Fake-1"))
                self.event(new_id, 4, pack_str("fake"))
                self.event(new_id, 2)
            elif key == ("zwlr_layer_shell_v1", 0):
                self.event(new_id, 0, pack_u(self.next_serial(), 0, 0))
            elif key == ("wl_surface", 3):
                self.frames_seen.set()
                self.event(new_id, 0, pack_u(self.next_serial()))
            return

    def send_im_activate(self):
        for im in self.im_ids:
            self.event(im, 0)  # activate
            self.event(im, 5)  # done
        self.flush()

    def serve(self):
        buf = bytearray()
        while not self._stop.is_set():
            try:
                conn = self.conn
                if conn is None:
                    buf = bytearray()
                    try:
                        conn, _ = self.listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        continue
                    conn.setblocking(True)
                    self.conn = conn
                    print("accept", file=self.logf, flush=True)
                    continue
                r, _, _ = select.select([conn], [], [], 0.1)
                if not r:
                    continue
                data, anc, _, _ = conn.recvmsg(
                    65536, socket.CMSG_SPACE(64))
                if not data:
                    self._drop(conn)
                    continue
                fds = []
                for _lvl, _typ, cmsg in anc or []:
                    for i in range(0, len(cmsg), 4):
                        fd = struct.unpack("i", cmsg[i:i + 4])[0]
                        if fd >= 0:
                            fds.append(fd)
                buf += data
                while len(buf) >= 8:
                    sender, opcode, size = struct.unpack("IHH", bytes(buf[:8]))
                    if size < 8 or len(buf) < size:
                        break
                    body = bytes(buf[8:size])
                    del buf[:size]
                    if (self.objs.get(sender)
                            == "zwp_input_method_manager_v2" and opcode == 0):
                        (new_id,) = struct.unpack("I", body[4:8])
                        self.im_ids.append(new_id)
                    self.handle(sender, opcode, body, fds)
                    fds = []
                self.flush()
            except Exception:
                if self._stop.is_set():
                    break
                import traceback
                import datetime
                print("fakecomp: server error at %s" %
                      datetime.datetime.now().isoformat(), flush=True)
                traceback.print_exc()
                conn = self.conn
                if conn is not None:
                    self._drop(conn)
                buf = bytearray()

    def _drop(self, conn):
        if self.conn is conn:
            self.conn = None
        try:
            conn.close()
        except OSError:
            pass

    def _close_conn(self):
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        self._close_conn()
        try:
            self.listener.close()
        except OSError:
            pass


def wait_for(log, needles, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with log["lock"]:
            data = bytes(log["buf"])
        if all(n in data for n in needles):
            return True
        time.sleep(0.1)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--schema-dir", required=True)
    ap.add_argument("--dict-dir", required=True)
    args = ap.parse_args()

    if shutil.which("dbus-daemon") is None:
        print("SKIP: dbus-daemon not available")
        return SKIP
    if not os.path.isfile(args.binary) or not os.access(args.binary, os.X_OK):
        print("FAIL: keyboard binary missing: %s" % args.binary)
        return 1

    rundir = tempfile.mkdtemp(prefix="kb-lifecycle-")
    comp = FakeComp(rundir, "fake-0")
    server = threading.Thread(target=comp.serve, daemon=True)
    server.start()

    daemon = None
    app = None
    try:
        daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--print-address=1", "--nofork"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        bus = daemon.stdout.readline().strip()
        if not bus:
            print("FAIL: session bus did not start")
            return 1
        if os.environ.get("KB_LIFECYCLE_DEBUG"):
            print("lifecycle-debug: rundir=%s bus=%s thread=%s" %
                  (rundir, bus, server.is_alive()), flush=True)

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/root"),
            "XDG_CONFIG_HOME": os.path.join(rundir, "config"),
            "XDG_RUNTIME_DIR": rundir,
            "WAYLAND_DISPLAY": "fake-0",
            "DBUS_SESSION_BUS_ADDRESS": bus,
            "GSETTINGS_SCHEMA_DIR": args.schema_dir,
            "GSETTINGS_BACKEND": "memory",
            "NO_AT_BRIDGE": "1",
            "XX_KEYBOARD_DICT_DIR": args.dict_dir,
            "G_MESSAGES_DEBUG": "all",
        }
        for var in ("ASAN_OPTIONS", "LSAN_OPTIONS", "UBSAN_OPTIONS",
                    "MSAN_OPTIONS", "WAYLAND_DEBUG"):
            if var in os.environ:
                env[var] = os.environ[var]

        log = {"buf": bytearray(), "lock": threading.Lock()}

        def pump(stream):
            for chunk in iter(stream.readline, b""):
                if isinstance(chunk, str):
                    chunk = chunk.encode()
                with log["lock"]:
                    log["buf"] += chunk

        app = subprocess.Popen(
            [args.binary],
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for stream in (app.stdout,):
            threading.Thread(target=pump, args=(stream,), daemon=True).start()
        if os.environ.get("KB_LIFECYCLE_DEBUG"):
            print("lifecycle-debug: app pid=%d thread=%s" %
                  (app.pid, server.is_alive()), flush=True)

        if not wait_for(log, [b"Creating new input surface",
                              b"Has DBus name: 1"], 30):
            rc = app.poll()
            if rc is not None:
                print("FAIL: keyboard exited early with status %d" % rc)
            else:
                app.kill()
                print("FAIL: keyboard did not reach running state")
            with log["lock"]:
                data = bytes(log["buf"])
                with open(os.path.join(rundir, "app.log"), "wb") as f:
                    f.write(data)
                seen = [n for n in (b"Creating new input surface",
                                    b"Has DBus name: 1", b"Has DBus name: 0",
                                    b"Wayland not yet ready",
                                    b"Bound all reqeuired",
                                    b"Caught signal") if n in data]
                print("lifecycle: markers seen: %s" % seen)
                sys.stdout.buffer.write(data[-4000:])
            return 1
        print("lifecycle: keyboard running, surface created")

        # GDK never binds wl_seat here, so no wl_touch exists to inject a
        # real swipe with; activating the input method still exercises the
        # live glide state paths (update/clear) before shutdown.
        if comp.frames_seen.wait(15):
            print("lifecycle: window mapped, activating input method")
        else:
            print("lifecycle: no frame seen, activating input method anyway")
        comp.send_im_activate()
        time.sleep(1.0)

        app.send_signal(signal.SIGTERM)
        try:
            rc = app.wait(timeout=15)
        except subprocess.TimeoutExpired:
            app.kill()
            print("FAIL: keyboard did not stop on SIGTERM")
            return 1

        with log["lock"]:
            out = bytes(log["buf"])
            with open(os.path.join(rundir, "app.log"), "wb") as f:
                f.write(out)
        print("lifecycle: app log %d bytes" % len(out), flush=True)
        if rc != 0:
            print("FAIL: keyboard exited with status %d on SIGTERM" % rc)
            sys.stdout.buffer.write(out[-4000:])
            sys.stdout.buffer.flush()
            return 1
        if b"Caught signal" not in out:
            print("FAIL: SIGTERM handler did not run")
            return 1
        if b"Glide decoded" in out or b"glide decoded" in out:
            print("lifecycle: swipe decoded during run")
        print("PASS: keyboard stopped cleanly on SIGTERM")
        return 0
    finally:
        for proc, sig in ((app, signal.SIGKILL), (daemon, signal.SIGTERM)):
            try:
                if proc is not None and proc.poll() is None:
                    proc.send_signal(sig)
            except (OSError, ValueError):
                pass
        comp.stop()
        server.join(timeout=5)
        try:
            comp.logf.close()
        except OSError:
            pass
        if "KB_LIFECYCLE_KEEP" not in os.environ:
            shutil.rmtree(rundir, ignore_errors=True)
        else:
            print("lifecycle: rundir kept at %s" % rundir)


if __name__ == "__main__":
    sys.exit(main())
