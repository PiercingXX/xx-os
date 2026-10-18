"""Every module and stylesheet the app imports is in meson's install list:
a file left out runs from the checkout and breaks only once installed."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_package_file_is_installed():
    meson = (ROOT / "meson.build").read_text(encoding="utf-8")
    package = ROOT / "src" / "xx_calendar"
    shipped = sorted(p.name for p in package.iterdir()
                     if p.suffix in (".py", ".css"))
    missing = [name for name in shipped
               if f"'src/xx_calendar/{name}'" not in meson]
    assert not missing, f"not in meson.build install_data: {missing}"
