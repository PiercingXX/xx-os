"""Service wiring added for the UI milestone: history, signals, sends."""
import service as service_module
from config import ShareConfig
from history import HistoryStore
from notify import ACTION_ACCEPT
from protocol import DeviceInfo, UploadFile
from server import Session
from service import PIN_NEEDED, ShareService


class FakeServer:
    last_kwargs = None

    def __init__(self, **kwargs):
        FakeServer.last_kwargs = kwargs
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True


class FakeDiscovery:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _service(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(service_module, "ShareServer", FakeServer)
    monkeypatch.setattr(service_module, "Discovery", FakeDiscovery)
    config = ShareConfig(tmp_path / "config")
    for key, value in overrides.items():
        config.data[key] = value
    signals = []
    svc = ShareService(
        config,
        tmp_path / "cert.pem",
        tmp_path / "key.pem",
        "fp",
        schedule=lambda seconds, cb: None,
        downloads=tmp_path / "dl",
        staging=tmp_path / "staging",
        history=HistoryStore(tmp_path / "history.json"),
        on_signal=lambda name, args: signals.append((name, args)),
    )
    return svc, signals


def _wait_for(getter, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while not getter():
        assert time.monotonic() < deadline, "timed out waiting for send thread"
        time.sleep(0.01)


def _session(sender_fp="sender-fp", alias="sender"):
    sender = DeviceInfo(alias=alias, fingerprint=sender_fp)
    files = {"f1": UploadFile(id="f1", file_name="hello.txt", size=5,
                              file_type="text/plain")}
    return Session(session_id="s1", sender=sender, files=files,
                   tokens={"f1": "tok"})


def test_incoming_request_emitted_before_prompt(tmp_path, monkeypatch):
    svc, signals = _service(tmp_path, monkeypatch)
    seen = []

    class Prompt:
        def __init__(self, _summary, _body, on_action):
            seen.append(on_action)

        def show(self):
            return True

        def close(self):
            pass

    svc.prompt_factory = Prompt
    svc._on_session(_session())
    assert signals and signals[0][0] == "IncomingRequest"
    assert signals[0][1][1] == "sender"
    assert seen, "prompt callback must still be wired to the held request"


def test_prompt_accept_resolves_held_session(tmp_path, monkeypatch):
    svc, _signals = _service(tmp_path, monkeypatch)
    accepted = []

    class Server:
        def accept_session(self, sid):
            accepted.append(sid)
            return []

        def decline_session(self, sid):
            raise AssertionError("should accept")

    svc.server = Server()
    stored = []

    class Prompt:
        def __init__(self, _summary, _body, on_action):
            stored.append(on_action)

        def show(self):
            return True

        def close(self):
            pass

    svc.prompt_factory = Prompt
    svc._on_session(_session())
    stored[0](ACTION_ACCEPT)
    assert accepted == ["s1"]


def test_complete_records_history(tmp_path, monkeypatch):
    svc, _signals = _service(tmp_path, monkeypatch)
    delivered = tmp_path / "dl" / "hello.txt"
    delivered.parent.mkdir(parents=True, exist_ok=True)
    delivered.write_bytes(b"hello")
    session = _session()
    session.delivered.append(delivered)
    svc._on_complete(session)
    assert delivered in svc.delivered
    entries = svc.history.list()
    assert len(entries) == 1
    assert entries[0]["file_name"] == "hello.txt"
    assert entries[0]["sender_alias"] == "sender"


def test_favorite_matching_ignores_case(tmp_path, monkeypatch):
    svc, _signals = _service(tmp_path, monkeypatch,
                             auto_accept_favorites=True,
                             favorites=["AB" * 32])
    assert svc.should_auto_accept("ab" * 32) is True
    assert "auto-off" in svc.visibility_note()
    svc.config.data["visibility_mode"] = "always"
    assert "auto-off" not in svc.visibility_note()


def test_start_send_unknown_device(tmp_path, monkeypatch):
    import pytest

    svc, _signals = _service(tmp_path, monkeypatch)
    svc.discovery = FakeDiscovery()
    svc.discovery.live_peers = lambda: []
    with pytest.raises(ValueError, match="unknown device"):
        svc.start_send("nope", ["a.jpg"])


def test_start_send_runs_and_reports_done(tmp_path, monkeypatch):
    import client as client_module
    from discovery import Peer

    svc, signals = _service(tmp_path, monkeypatch)
    peer = Peer(info=DeviceInfo(alias="you", fingerprint="bb" * 32),
                addr="10.0.0.6", last_seen=0.0)
    svc.discovery = FakeDiscovery()
    svc.discovery.live_peers = lambda: [peer]

    def fake_send_files(*_args, **kwargs):
        kwargs["progress"]("file-0", 4, 4)

    monkeypatch.setattr(client_module, "send_files", fake_send_files)
    done = []
    tid = svc.start_send("bb" * 32, ["a.jpg"], "",
                         lambda ok, msg: done.append((ok, msg)))
    _wait_for(lambda: done)
    assert done == [(True, "Sent to you")]
    kinds = [name for name, _args in signals]
    assert "TransferProgress" in kinds
    assert "TransferDone" in kinds
    assert svc.cancel_send(tid) is False


def test_start_send_pin_message(tmp_path, monkeypatch):
    import client as client_module
    from client import PinRequired
    from discovery import Peer

    svc, _signals = _service(tmp_path, monkeypatch)
    peer = Peer(info=DeviceInfo(alias="you", fingerprint="bb" * 32),
                addr="10.0.0.6", last_seen=0.0)
    svc.discovery = FakeDiscovery()
    svc.discovery.live_peers = lambda: [peer]

    def fake_send_files(*_args, **kwargs):
        raise PinRequired(401, "PIN required")

    monkeypatch.setattr(client_module, "send_files", fake_send_files)
    done = []
    svc.start_send("bb" * 32, ["a.jpg"], "",
                   lambda ok, msg: done.append((ok, msg)))
    _wait_for(lambda: done)
    assert done == [(False, PIN_NEEDED)]
