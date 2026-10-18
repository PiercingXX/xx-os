"""Background service logic: visibility, auto-off, accept policy.

GLib-free: the auto-off timer goes through an injectable scheduler so
headless tests can fire it manually. The GTK/UI layer passes
GLib.timeout_add_seconds; the --service runner uses a small thread-based
scheduler.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from config import AUTO_OFF_SECONDS, VISIBILITY_ALWAYS, ShareConfig
from discovery import Discovery
from favorites import is_favorite
from history import HistoryStore
from notify import (
    ACTION_ACCEPT,
    ACTION_DECLINE,
    ReceivePrompt,
    prompt_text,
)
from protocol import DEFAULT_PORT, DeviceInfo, UploadFile
from server import ACCEPTED, CANCELLED, DECLINED, FINISHED, Session, ShareServer
from share_bus import incoming_args, peer_to_dict, progress_args
from transfers import TransferManager

logger = logging.getLogger(__name__)

DOWNLOAD_SUBDIR = "xx-share"
#: Send outcome when the receiver asks for its on-screen PIN; the share
#: window shows its PIN field on exactly this text (it crosses the bus).
PIN_NEEDED = "Enter the receiver's PIN"
STAGING_SUBDIR = "xx-share"


def download_dir() -> Path:
    return Path.home() / "Downloads" / DOWNLOAD_SUBDIR


def staging_root() -> Path:
    import tempfile

    base = Path(tempfile.gettempdir()) / f"xx-share-{__import__('os').getuid()}"
    return base / "staging"


def build_device_info(config: ShareConfig, fingerprint: str) -> DeviceInfo:
    alias = str(config.data.get("alias") or socket.gethostname())
    model = None
    try:
        model = Path("/sys/firmware/devicetree/base/model").read_text(
            encoding="utf-8").strip(" \n\x00")
    except OSError:
        pass
    return DeviceInfo(
        alias=alias,
        fingerprint=fingerprint,
        port=int(config.data.get("port", DEFAULT_PORT)),
        protocol="https",
        download=False,
        device_model=model or None,
        device_type="mobile",
    )


class ShareService:
    def __init__(
        self,
        config: ShareConfig,
        cert_path: str | Path,
        key_path: str | Path,
        fingerprint: str,
        schedule: Callable[[int, Callable[[], bool]], object] | None = None,
        prompt_factory: Callable[..., ReceivePrompt] | None = None,
        downloads: str | Path | None = None,
        staging: str | Path | None = None,
        history: HistoryStore | None = None,
        on_signal: Callable[[str, tuple], None] | None = None,
    ) -> None:
        self.config = config
        self.cert_path = Path(cert_path)
        self.key_path = Path(key_path)
        self.fingerprint = fingerprint
        self.schedule = schedule or (lambda _s, _cb: None)
        self.prompt_factory = prompt_factory or ReceivePrompt
        self.downloads = Path(downloads) if downloads else download_dir()
        self.staging = Path(staging) if staging else staging_root()
        self.info = build_device_info(config, fingerprint)
        self.server: ShareServer | None = None
        self.discovery: Discovery | None = None
        self._timer = None
        self._lock = threading.Lock()
        self.delivered: list[Path] = []
        self.history = history or HistoryStore()
        self.on_signal = on_signal or (lambda _name, _args: None)
        self.outgoing = TransferManager()
        self._send_threads: dict[str, threading.Thread] = {}
        self._prompts: dict[str, ReceivePrompt] = {}

    @property
    def running(self) -> bool:
        return self.server is not None

    def should_auto_accept(self, fingerprint: str) -> bool:
        if not self.config.data.get("auto_accept_favorites"):
            return False
        return is_favorite(self.config, fingerprint)

    def visibility_note(self) -> str:
        if self.config.data.get("visibility_mode") == VISIBILITY_ALWAYS:
            return "Visible to nearby devices"
        return "Visible to nearby devices (auto-off after 10 min)"

    def _on_http_peer(self, peer: DeviceInfo, addr: str = "",
                        _verified: bool = False) -> None:
        discovery = self.discovery
        if discovery is None:
            return
        try:
            discovery.note_http_peer(peer, addr)
        except (ValueError, AttributeError):
            pass

    def start(self, bind_addrs: list[str]) -> None:
        with self._lock:
            if self.server is not None:
                return
            self.info = build_device_info(self.config, self.fingerprint)
            self.discovery = Discovery(
                self.info, bind_addrs=bind_addrs,
                on_peers=self._on_peers,
                cert_path=str(self.cert_path),
                key_path=str(self.key_path))
            self.server = ShareServer(
                device_info=self.info,
                cert_path=self.cert_path,
                key_path=self.key_path,
                bind_addrs=bind_addrs,
                port=int(self.config.data.get("port", DEFAULT_PORT)),
                staging_root=self.staging,
                download_dir=self.downloads,
                require_pin=str(self.config.data.get("require_pin", "")),
                require_pin_getter=self._current_pin,
                max_transfer_bytes=self.config.max_transfer_bytes,
                decide=self._decide,
                on_session=self._on_session,
                on_complete=self._on_complete,
                on_peer=self._on_http_peer,
                on_cancel=self._on_cancelled,
            )
            self.server.start()
            try:
                self.discovery.start()
            except OSError:
                pass
            if self.config.data.get("visibility_mode") != VISIBILITY_ALWAYS:
                self._cancel_timer()
                self._timer = self.schedule(AUTO_OFF_SECONDS, self._auto_off)

    def stop(self) -> None:
        with self._lock:
            self._cancel_timer()
            discovery, self.discovery = self.discovery, None
            server, self.server = self.server, None
        if discovery is not None:
            discovery.stop()
        if server is not None:
            server.stop()

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is None:
            return
        cancel = getattr(timer, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception as error:
                logger.warning("auto-off cancel failed: %s", error)
            return
        if isinstance(timer, int):
            try:
                from gi.repository import GLib

                GLib.source_remove(timer)
            except Exception as error:
                logger.warning("auto-off cancel failed: %s", error)

    def _auto_off(self) -> bool:
        self.set_visible(False)
        return False

    def _current_pin(self) -> str:
        self.config.load()
        return str(self.config.data.get("require_pin", ""))

    def set_visible(self, visible: bool, bind_addrs: list[str] | None = None) -> None:
        self.config.load()
        self.config.data["visible"] = bool(visible)
        self.config.save()
        if visible:
            if not bind_addrs:
                from netif import allowed_bind_addrs

                try:
                    bind_addrs = allowed_bind_addrs()
                except OSError:
                    bind_addrs = []
            self.start(bind_addrs or [])
        else:
            self.stop()

    def _decide(self, session: Session) -> bool:
        self.config.load()
        if not getattr(session, "verified", False):
            return False
        return self.should_auto_accept(session.sender.fingerprint)

    def _on_session(self, session: Session) -> None:
        names = [item.file_name for item in session.files.values()]
        total = sum(item.size for item in session.files.values())
        try:
            self.on_signal("IncomingRequest", incoming_args(
                session.session_id, session.sender.alias,
                session.sender.fingerprint, names, total))
        except Exception as error:
            logger.warning("incoming request signal failed: %s", error)
        if session.state == ACCEPTED:
            return
        alias = session.sender.alias
        if not getattr(session, "verified", False):
            alias = f"{alias} (unverified)"
        summary, body = prompt_text(alias, names, total)
        prompt = self.prompt_factory(summary, body,
                                     lambda action, sid=session.session_id:
                                     self._resolve(sid, action))
        if not prompt.show():
            return
        with self._lock:
            self._prompts[session.session_id] = prompt

    def _close_prompt(self, session_id: str) -> None:
        with self._lock:
            prompt = self._prompts.pop(session_id, None)
        if prompt is not None:
            prompt.close()

    def _resolve(self, session_id: str, action: str) -> None:
        if self.server is None:
            return
        sessions = getattr(self.server, "sessions", None)
        if sessions is not None:
            session = sessions.get(session_id)
            if session is None or session.state in (DECLINED, CANCELLED, FINISHED):
                self._close_prompt(session_id)
                return
        try:
            if action == ACTION_ACCEPT:
                delivered = self.server.accept_session(session_id)
                with self._lock:
                    self.delivered.extend(delivered)
            elif action == ACTION_DECLINE:
                self.server.decline_session(session_id)
        finally:
            self._close_prompt(session_id)

    def _on_cancelled(self, session: Session) -> None:
        self._close_prompt(session.session_id)

    def _on_complete(self, session: Session) -> None:
        self._close_prompt(session.session_id)
        with self._lock:
            for path in session.delivered:
                if path not in self.delivered:
                    self.delivered.append(path)
        for path in session.delivered:
            meta = next((item for item in session.files.values()
                         if path.name == item.file_name
                         or str(path).endswith(item.file_name)), None)
            try:
                self.history.record(
                    session.sender.alias, session.sender.fingerprint,
                    meta.file_name if meta else path.name,
                    meta.size if meta else 0, path)
            except (OSError, ValueError):
                pass

    def _on_peers(self, _peers: list) -> None:
        pass

    def peers(self) -> list:
        if self.discovery is None:
            return []
        return self.discovery.live_peers()

    def list_peers(self) -> list[dict]:
        result = []
        for peer in self.peers():
            result.append(peer_to_dict(
                peer.info.alias, peer.info.fingerprint, peer.addr,
                peer.info.device_type, peer.info.port))
        return result

    def start_send(self, fingerprint: str, files: list[str], pin: str = "",
                   on_done: Callable[[bool, str], None] | None = None) -> str:
        wanted = str(fingerprint).strip().lower()
        peers = {str(peer.info.fingerprint).lower(): peer
                 for peer in self.peers()}
        peer = peers.get(wanted)
        if peer is None:
            raise ValueError("unknown device")
        transfer_id = self.outgoing.start(peer.info.fingerprint, files)
        sender = self.info
        paths = list(files)
        pin_value = str(pin or "") or None

        def work() -> None:
            from client import (
                FingerprintMismatch,
                TransferError,
                send_files,
            )

            def progress(file_id: str, sent: int, total: int) -> None:
                self.outgoing.record_progress(transfer_id, file_id, sent, total)
                try:
                    self.on_signal("TransferProgress", progress_args(
                        transfer_id, file_id, sent, total))
                except Exception as error:
                    logger.warning("transfer progress signal failed: %s", error)

            try:
                try:
                    send_files(peer.info, peer.addr, sender, paths, pin_value,
                               progress=progress,
                               is_cancelled=lambda: self.outgoing.is_cancelled(
                                   transfer_id),
                               cert_path=str(self.cert_path),
                               key_path=str(self.key_path))
                except (TransferError, FingerprintMismatch, OSError) as error:
                    logger.info("send %s failed: %s", transfer_id, error)
                    outcome = (False, send_error_text(peer.info.alias, error))
                except Exception as error:
                    logger.warning("send %s failed: %s", transfer_id, error)
                    outcome = (False, "Send failed")
                else:
                    outcome = (True, f"Sent to {peer.info.alias}")
                try:
                    self.on_signal("TransferDone",
                                   (transfer_id, outcome[0], outcome[1]))
                except Exception as error:
                    logger.warning("transfer done signal failed: %s", error)
                if on_done is not None:
                    on_done(outcome[0], outcome[1])
            finally:
                self.outgoing.finish(transfer_id)
                with self._lock:
                    self._send_threads.pop(transfer_id, None)

        thread = threading.Thread(target=work, daemon=True)
        with self._lock:
            self._send_threads[transfer_id] = thread
        thread.start()
        return transfer_id

    def cancel_send(self, transfer_id: str) -> bool:
        return self.outgoing.request_cancel(str(transfer_id))


def send_error_text(alias: str, error: BaseException) -> str:
    """One short line for a failed send: no HTTP codes, no jargon."""
    from client import FingerprintMismatch, PinRequired, TransferError

    if isinstance(error, PinRequired):
        return PIN_NEEDED
    if isinstance(error, TransferError):
        if error.status == 403:
            return f"{alias} declined" if alias else "Declined"
        if error.message == "cancelled":
            return "Cancelled"
        text = str(error.message or "Send failed")
        return text[:1].upper() + text[1:]
    if isinstance(error, FingerprintMismatch):
        return f"{alias or 'Device'} changed identity, send stopped"
    if isinstance(error, OSError):
        return f"Couldn't reach {alias}" if alias else "Couldn't reach device"
    return "Send failed"


def thread_scheduler(seconds: int, callback: Callable[[], bool]) -> object:
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


def utc_now() -> float:
    return time.time()


def describe_outgoing(paths: list[str | Path]) -> list[UploadFile]:
    from client import describe_files

    return describe_files([str(item) for item in paths])
