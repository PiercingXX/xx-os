"""Minimal WebDAV client for xx-note sync (Nextcloud and others).

Pure-stdlib (``urllib``) so the app gains no new dependency. One-host
guard: the client is constructed for exactly one configured origin and
refuses any other host; cleartext ``http://`` is refused except for
loopback. All blocking I/O runs on the caller's worker thread — never
call these from the GTK main loop. Passwords are passed in per call and
never logged (see ``secrets.py``).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib import error as _urlerror
from urllib import parse as _parse
from urllib import request as _request
import xml.etree.ElementTree as _ET


class WebDavError(Exception):
    pass


class HostMismatchError(WebDavError):
    pass


class AuthError(WebDavError):
    pass


@dataclass(frozen=True)
class RemoteEntry:
    href: str
    etag: str | None
    last_modified: str | None
    is_dir: bool


def normalize_base(url: str) -> str:
    """Validate and normalize the configured base URL.

    Raises :class:`WebDavError` for non-http(s) schemes and for cleartext
    to non-loopback hosts.
    """
    parts = _parse.urlparse(url.strip())
    if parts.scheme not in ("http", "https"):
        raise WebDavError("WebDAV URL must start with https://")
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        raise WebDavError("cleartext http is refused except for loopback")
    if not host:
        raise WebDavError("WebDAV URL has no host")
    return url.strip().rstrip("/")


def guard_host(configured_base: str, url: str) -> None:
    """Raise :class:`HostMismatchError` unless ``url`` is on the one host."""
    want = _parse.urlparse(configured_base).hostname or ""
    got = _parse.urlparse(url).hostname or ""
    if want.lower() != got.lower():
        raise HostMismatchError(f"refusing off-host request to {got or url}")


def parse_propfind(xml_text: str) -> list[RemoteEntry]:
    """Parse a WebDAV multistatus listing (``getetag``/``getlastmodified``)."""
    try:
        root = _ET.fromstring(xml_text)
    except _ET.ParseError as exc:
        raise WebDavError(f"bad PROPFIND response: {exc}") from exc

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    entries: list[RemoteEntry] = []
    for response in root.iter():
        if _local(response.tag) != "response":
            continue
        href: str | None = None
        etag: str | None = None
        modified: str | None = None
        is_dir = False
        for child in response.iter():
            name = _local(child.tag)
            if name == "href" and href is None and child.text:
                href = child.text.strip()
            elif name == "getetag" and child.text:
                etag = child.text.strip()
            elif name == "getlastmodified" and child.text:
                modified = child.text.strip()
            elif name == "collection":
                is_dir = True
        if href is not None:
            entries.append(RemoteEntry(href, etag, modified, is_dir))
    return entries


class WebDavClient:
    def __init__(self, base_url: str, user: str, password: str,
                 timeout_s: float = 20.0) -> None:
        self.base = normalize_base(base_url)
        self.user = user
        self._password = password
        self.timeout_s = timeout_s
        parts = _parse.urlparse(self.base)
        self.host = parts.hostname or ""
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = f"Basic {token}"

    def _open(self, method: str, url: str, body: bytes | None = None,
              headers: dict[str, str] | None = None) -> tuple[int, bytes, str]:
        guard_host(self.base, url)
        req = _request.Request(url, data=body, method=method)
        req.add_header("Authorization", self._auth)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with _request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = resp.read()
                etag = resp.headers.get("ETag", "")
                return resp.status, payload, etag
        except _urlerror.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthError("sign-in rejected the user or password") from exc
            raise WebDavError(f"server answered {exc.code}") from exc
        except OSError as exc:
            raise WebDavError(f"network problem: {exc}") from exc

    def check(self) -> bool:
        status, _body, _etag = self._open("PROPFIND", self.base + "/", None,
                                          {"Depth": "0"})
        return 200 <= status < 300

    def list(self, path: str) -> list[RemoteEntry]:
        url = self.base + "/" + path.strip("/")
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop><d:getetag/><d:getlastmodified/><d:resourcetype/>"
            "</d:prop></d:propfind>"
        ).encode()
        status, payload, _etag = self._open(
            "PROPFIND", url, body, {"Depth": "1", "Content-Type": "text/xml"})
        if not 200 <= status < 300:
            raise WebDavError(f"listing failed ({status})")
        return parse_propfind(payload.decode("utf-8", "replace"))

    def read(self, path: str) -> tuple[str, str]:
        url = self.base + "/" + path.strip("/")
        status, payload, etag = self._open("GET", url)
        if not 200 <= status < 300:
            raise WebDavError(f"fetch failed ({status})")
        return payload.decode("utf-8", "replace"), etag

    def write(self, path: str, text: str, etag: str | None = None) -> str:
        url = self.base + "/" + path.strip("/")
        headers = {"Content-Type": "text/markdown; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        status, _body, new_etag = self._open(
            "PUT", url, text.encode("utf-8"), headers)
        if status == 412:
            raise WebDavError("remote changed first (412); will re-plan")
        if not 200 <= status < 300:
            raise WebDavError(f"upload failed ({status})")
        return new_etag

    def remove(self, path: str) -> None:
        url = self.base + "/" + path.strip("/")
        status, _body, _etag = self._open("DELETE", url)
        if status not in (200, 202, 204, 404):
            raise WebDavError(f"delete failed ({status})")

    def mkdir(self, path: str) -> None:
        url = self.base + "/" + path.strip("/")
        status, _body, _etag = self._open("MKCOL", url)
        if status not in (200, 201, 405):
            raise WebDavError(f"folder setup failed ({status})")
