"""QR scan gates and image-file decoding. Port of Android
`setup/QrScanner.kt`: `account_payload` gates every raw barcode through the
real parsers so junk QRs never reach the vault. Decoding itself uses
`zbarimg` (zbar-tools), the ZBar GI typelib, or pyzbar — whichever is
installed; the camera comes later. All decoding runs on worker threads."""

from __future__ import annotations

import shutil
import subprocess

from xx_auth import migration_uri
from xx_auth.otp_uri import parse as parse_otp


def otpauth_payload(raw: str) -> str | None:
    try:
        parse_otp(raw)
    except ValueError:
        return None
    return raw.strip()


def account_payload(raw: str) -> str | None:
    payload = otpauth_payload(raw)
    if payload is not None:
        return payload
    stripped = raw.strip()
    if migration_uri.is_migration(stripped):
        try:
            migration_uri.parse(stripped)
        except ValueError:
            return None
        return stripped
    return None


def available_backends() -> list[str]:
    backends = []
    if shutil.which("zbarimg"):
        backends.append("zbarimg")
    try:
        import gi  # noqa: F401

        gi.require_version("ZBar", "1.0")
        from gi.repository import ZBar  # noqa: F401

        backends.append("zbar-gi")
    except Exception:
        pass
    try:
        import pyzbar  # noqa: F401

        backends.append("pyzbar")
    except Exception:
        pass
    return backends


def backend_help() -> str:
    if available_backends():
        return ""
    return "No QR decoder is installed (zbarimg, ZBar GI or pyzbar). Paste the URI instead."


def decode_image_file(path: str, backend: str | None = None) -> list[str]:
    """Decode QR payloads from an image file. Raises when undecodable."""
    choice = backend or (available_backends()[0] if available_backends() else None)
    if choice == "zbarimg":
        return _decode_zbarimg(path)
    if choice == "zbar-gi":
        return _decode_zbar_gi(path)
    if choice == "pyzbar":
        return _decode_pyzbar(path)
    raise ValueError("no QR decoder is installed")


def _decode_zbarimg(path: str) -> list[str]:
    proc = subprocess.run(
        ["zbarimg", "--raw", "--quiet", path],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ValueError("that image holds no readable QR code")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _decode_zbar_gi(path: str) -> list[str]:
    import gi

    gi.require_version("ZBar", "1.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, ZBar

    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    if pixbuf.get_n_channels() < 3:
        raise ValueError("that image holds no readable QR code")
    pixels = pixbuf.get_pixels()
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    gray = bytearray(width * height)
    for y in range(height):
        base = y * stride
        for x in range(width):
            off = base + x * pixbuf.get_n_channels()
            gray[y * width + x] = (pixels[off] * 30 + pixels[off + 1] * 59 + pixels[off + 2] * 11) // 100
    image = ZBar.Image(width, height, "Y800", bytes(gray))
    scanner = ZBar.ImageScanner()
    scanner.parse_config("enable")
    if scanner.scan(image) <= 0:
        raise ValueError("that image holds no readable QR code")
    return [symbol.data for symbol in image.symbols]


def _decode_pyzbar(path: str) -> list[str]:
    from PIL import Image
    from pyzbar.pyzbar import decode

    symbols = decode(Image.open(path))
    if not symbols:
        raise ValueError("that image holds no readable QR code")
    return [symbol.data.decode("utf-8", "replace") for symbol in symbols]
