"""Vault encryption at rest. Linux mapping of Android `SecretCipher`: the
same `IV ‖ ciphertext ‖ tag` blob (12-byte IV, AES-256-GCM, 128-bit tag).
The key is a random 32-byte data-encryption key kept in the Secret Service
(same `Secret.password_*_sync` pattern as xx-vitals `LibsecretBackend`) when
a keyring answers, else derived with scrypt from the app password the
operator sets on first run. All blocking secret calls run on worker threads;
callers post back with GLib.idle_add."""

from __future__ import annotations

import hashlib
import os
import secrets

from xx_auth import scrypt_kdf

APP_ID = "io.piercingxx.XXAuth"
_KEY_LABEL = "xx-auth vault key"
_NONCE_LEN = 12
_TAG_LEN = 16


class SecretCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("vault key must be 32 bytes")
        self._key = bytes(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(_NONCE_LEN)
        return nonce + AESGCM(self._key).encrypt(nonce, bytes(plaintext), None)

    def decrypt(self, blob: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(blob) < _NONCE_LEN + _TAG_LEN + 1:
            raise ValueError("vault blob is too short")
        try:
            return AESGCM(self._key).decrypt(blob[:_NONCE_LEN], blob[_NONCE_LEN:], None)
        except Exception as exc:
            raise ValueError("vault blob failed authentication") from exc


SERVICE_NAME = "org.freedesktop.secrets"


def service_present() -> bool:
    """True when a Secret Service owns the bus name. A short D-Bus round
    trip so a missing keyring fails fast instead of hanging in libsecret
    service activation."""
    try:
        from gi.repository import Gio, GLib
    except Exception:
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (SERVICE_NAME,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 3000, None,
        )
        return bool(reply.unpack()[0])
    except Exception:
        return False


def _secret():  # type: ignore[no-untyped-def]
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    return Secret


class SecretServiceKeyring:
    schema_name = APP_ID

    def _schema(self):  # type: ignore[no-untyped-def]
        Secret = _secret()

        return Secret.Schema.new(
            self.schema_name, Secret.SchemaFlags.NONE, {"application": Secret.SchemaAttributeType.STRING},
        )

    def _attributes(self) -> dict:
        return {"application": APP_ID}

    def _require_service(self) -> None:
        if not service_present():
            raise ValueError("no Secret Service on the bus")

    def load_key(self) -> bytes | None:
        Secret = _secret()

        self._require_service()
        found = Secret.password_lookup_sync(self._schema(), self._attributes(), None)
        if not found:
            return None
        try:
            return bytes.fromhex(found.strip())
        except ValueError:
            return None

    def store_key(self, key: bytes) -> None:
        Secret = _secret()

        self._require_service()
        Secret.password_store_sync(
            self._schema(), self._attributes(), Secret.COLLECTION_DEFAULT,
            _KEY_LABEL, key.hex(), None,
        )

    def available(self) -> bool:
        try:
            _secret()
        except Exception:
            return False
        try:
            self.load_key()
            return True
        except Exception:
            return False


class MemoryKeyring:
    def __init__(self) -> None:
        self._key: bytes | None = None

    def load_key(self) -> bytes | None:
        return self._key

    def store_key(self, key: bytes) -> None:
        self._key = bytes(key)

    def available(self) -> bool:
        return True


def derive_key(password: str, salt: bytes,
                n: int = scrypt_kdf.AEGIS_N,
                r: int = scrypt_kdf.AEGIS_R,
                p: int = scrypt_kdf.AEGIS_P) -> bytes:
    return scrypt_kdf.derive(password.encode("utf-8"), salt, n, r, p, 32)


CHECK_PLAINTEXT = b"xx-auth vault check v1"


def make_check(key: bytes) -> str:
    """A key check value: the fixed check sentence encrypted under ``key``.

    Stored in the vault header so a crash-stale settings copy can be told
    apart from the key the vault is really under. It reveals nothing about
    the key, the password or any 2FA secret (AES-GCM of public plaintext)."""
    return SecretCipher(key).encrypt(CHECK_PLAINTEXT).hex()


def verify_check(key: bytes, check_hex: str) -> bool:
    """True when ``key`` decrypts the header check value back to the check
    sentence. Any failure (wrong key, damaged header) is just False."""
    try:
        return SecretCipher(key).decrypt(bytes.fromhex(check_hex)) == CHECK_PLAINTEXT
    except Exception:
        return False


def new_salt() -> bytes:
    return secrets.token_bytes(scrypt_kdf.AEGIS_SALT_LEN)


def load_or_create_key(keyring: SecretServiceKeyring | MemoryKeyring) -> bytes:
    key = keyring.load_key()
    if key is not None:
        if len(key) != 32:
            raise ValueError("stored vault key has the wrong length")
        return key
    key = secrets.token_bytes(32)
    keyring.store_key(key)
    return key


def password_verifier(password: str, salt: bytes) -> str:
    return hashlib.sha256(salt + b"\x00" + password.encode("utf-8")).hexdigest()


def keyring_label() -> str:
    return _KEY_LABEL


def default_salt() -> bytes:
    return os.urandom(scrypt_kdf.AEGIS_SALT_LEN)
