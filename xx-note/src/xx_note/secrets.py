"""Credential storage for xx-note WebDAV sync.

Passwords live in libsecret (the ``xx-note`` collection, ``webdav-password``
for the configured user@host) and NEVER in config files or logs. Headless
tests use :class:`MemorySecrets`; production uses :class:`LibsecretBackend`.
"""

from __future__ import annotations


class MemorySecrets:
    """In-memory secret store for tests and `--help`-safe paths."""

    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def key(self, host: str, user: str) -> str:
        return f"{user}@{host}"

    def get_password(self, host: str, user: str) -> str | None:
        return self._items.get(self.key(host, user))

    def set_password(self, host: str, user: str, password: str) -> None:
        self._items[self.key(host, user)] = password

    def clear(self, host: str, user: str) -> None:
        self._items.pop(self.key(host, user), None)


class LibsecretBackend(MemorySecrets):
    """libsecret-backed store; falls back to memory when unavailable."""

    COLLECTION = "xx-note"

    def __init__(self) -> None:
        super().__init__()
        self._available = False
        try:
            import gi  # type: ignore[import-not-found]

            gi.require_version("Secret", "1")
            from gi.repository import Secret  # type: ignore[import-not-found]

            self._Secret = Secret
            schema = Secret.Schema.new(
                "io.piercingxx.XXNote.Webdav",
                Secret.SchemaFlags.NONE,
                {"host": Secret.SchemaAttributeType.STRING,
                 "user": Secret.SchemaAttributeType.STRING},
            )
            self._schema = schema
            self._available = True
        except Exception:
            self._Secret = None
            self._schema = None

    @property
    def available(self) -> bool:
        return self._available

    def get_password(self, host: str, user: str) -> str | None:
        if not self._available:
            return super().get_password(host, user)
        try:
            value = self._Secret.password_lookup_sync(
                self._schema, {"host": host, "user": user}, None
            )
            return value
        except Exception:
            return super().get_password(host, user)

    def set_password(self, host: str, user: str, password: str) -> None:
        if not self._available:
            super().set_password(host, user, password)
            return
        try:
            self._Secret.password_store_sync(
                self._schema,
                {"host": host, "user": user},
                self.COLLECTION,
                "xx-note WebDAV password",
                password,
                None,
            )
        except Exception:
            super().set_password(host, user, password)

    def clear(self, host: str, user: str) -> None:
        if not self._available:
            super().clear(host, user)
            return
        try:
            self._Secret.password_clear_sync(
                self._schema, {"host": host, "user": user}, None
            )
        except Exception:
            pass
        super().clear(host, user)
