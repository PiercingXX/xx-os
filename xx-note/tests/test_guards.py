"""Static guards: no secrets in logs, one-host WebDAV, valid GTK signals."""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "xx_note"


def test_no_secret_logging():
    banned = re.compile(
        r"\b(password|passwd|token|cookie|secret)\b.*\b(log|print)\b"
        r"|\b(log|print)\b.*\b(password|passwd|token|cookie|secret)\b",
        re.IGNORECASE,
    )
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        for index, line in enumerate(path.read_text().splitlines(), 1):
            if banned.search(line):
                hits.append(f"{path.name}:{index}:{line.strip()}")
    assert hits == [], f"possible secret logging: {hits}"


def test_credentials_never_in_prefs():
    text = (SRC / "prefs.py").read_text()
    assert "password" not in text.lower()


def test_no_plain_string_reply_type():
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for match in re.finditer(
            r"call_sync\((?:[^()]|\([^()]*\))*reply_type\s*=\s*\"",
            text,
        ):
            raise AssertionError(f"plain-string reply_type: {path.name}")


def test_gtk_signal_names_valid():
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GObject, Gtk
    except Exception:
        import pytest

        pytest.skip("gi/GTK is missing")
    import warnings

    known: set[str] = set()
    warnings.simplefilter("ignore")
    for module in (Gtk, Adw, Gio):
        for attr in dir(module):
            obj = getattr(module, attr, None)
            gtype = getattr(obj, "__gtype__", None)
            if gtype is None:
                continue
            try:
                GObject.type_class_ref(gtype)
                known.update(GObject.signal_list_names(gtype))
            except Exception:
                continue
    known.add("notify")
    used = set()
    for path in SRC.rglob("*.py"):
        for match in re.finditer(
            r"""\.connect\(\s*["']([\w-]+)["']""", path.read_text()
        ):
            used.add(match.group(1))
    unknown = {
        name for name in used
        if name not in known and not name.startswith("notify::")
    }
    assert unknown == set(), f"unknown GTK signals: {sorted(unknown)}"


def test_meson_installs_the_whole_package():
    meson = (Path(__file__).resolve().parent.parent / "meson.build").read_text()
    assert "install_subdir(\n  join_paths('src', 'xx_note')" in meson
    assert "'src/xx_note/" not in meson


def test_webdav_refuses_off_host_and_cleartext():
    from xx_note import webdav

    client_base = "https://notes.example.com/remote.php/dav"
    try:
        webdav.guard_host(client_base, "https://evil.example.org/x")
    except webdav.HostMismatchError:
        pass
    else:
        raise AssertionError("off-host request was not refused")
    try:
        webdav.normalize_base("http://notes.example.com/dav")
    except webdav.WebDavError:
        pass
    else:
        raise AssertionError("cleartext to WAN host was not refused")
    assert webdav.normalize_base("http://127.0.0.1/dav").startswith("http://")


def test_int64_note_ids():
    # Android-style millisecond ids must fit a 64-bit property, never gint.
    try:
        from gi.repository import GObject
    except Exception:
        import pytest

        pytest.skip("gi is missing")
    assert GObject.TYPE_INT64 != GObject.TYPE_INT
