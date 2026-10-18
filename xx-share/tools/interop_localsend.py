"""xx-share <-> LocalSend opt-in interop (manual, not run by pytest).

Mirrors interop_harness.py without its HARNESS PATCH: the device cert
is threaded explicitly through every client call (info, register,
prepare-upload, upload, cancel) instead of monkey-patching
client._client_context.

Usage (LocalSend 1.18.x with quick-save on, same LAN):
    python tools/interop_localsend.py <src_dir> <state_dir> \
        [--host 127.0.0.1] [--port 53317] [--ours 53318]

Steps:
1. GET LocalSend /info with fingerprint pinning.
2. Start xx-share ShareServer on :--ours, announce via multicast, wait
   for LocalSend to register back (discovery both ways).
3. Send two files (text + 3 MB binary) to LocalSend; print SHA-256 so
   the receiver side can be compared by hand.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src_dir", help="path to xx-share src/")
    parser.add_argument("state_dir", help="writable state directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=53317)
    parser.add_argument("--ours", type=int, default=53318)
    return parser.parse_args(argv)


def _ensure_files(state: Path) -> list[Path]:
    hello = state / "hello.txt"
    if not hello.exists():
        hello.write_bytes(b"hello localsend interop\n")
    payload = state / "payload.bin"
    if not payload.exists() or payload.stat().st_size != 3 * 1024 * 1024:
        payload.write_bytes(bytes(index % 251 for index in
                                  range(3 * 1024 * 1024)))
    return [hello, payload]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    sys.path.insert(0, args.src_dir)
    import certs
    import client
    import discovery
    import protocol
    from server import ShareServer

    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    cert, key = certs.ensure_certpair(state / "id")
    fingerprint = certs.cert_fingerprint(cert)
    me = protocol.DeviceInfo(alias="xx-share-test", fingerprint=fingerprint,
                             port=args.ours, device_model="Linux",
                             device_type="mobile")
    raw = client.fetch_info(args.host, args.port, timeout=10.0,
                            cert_path=str(cert), key_path=str(key))
    peer = discovery.parse_info_payload(raw)
    print("LOCALSEND INFO:", peer.alias, peer.version, peer.protocol,
          peer.fingerprint[:12], "...")
    seen: list[str] = []
    srv = ShareServer(me, cert, key, ["0.0.0.0"], args.ours,
                      state / "stage", state / "recv",
                      decide=lambda _session: True,
                      on_peer=lambda info, *_rest: seen.append(info.alias))
    srv.start()
    disc = discovery.Discovery(me, bind_addrs=[],
                               on_peers=lambda _peers: None,
                               cert_path=str(cert), key_path=str(key))
    disc.start()
    try:
        deadline = time.time() + 12
        while time.time() < deadline and not (seen or disc.peers):
            time.sleep(0.5)
        print("DISCOVERY: LocalSend registered with xx-share:",
              seen or "no",
              "| xx-share saw peers:",
              [item.info.alias for item in disc.peers.values()] or "none")
        files = _ensure_files(state)
        started = time.time()
        client.send_files(peer, args.host, me, files,
                          cert_path=str(cert), key_path=str(key))
        print("SEND OK in %.2fs" % (time.time() - started))
        for path in files:
            print(" ", path.name,
                  hashlib.sha256(path.read_bytes()).hexdigest()[:16])
    finally:
        disc.stop()
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
