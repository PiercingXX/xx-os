"""Maps-tile resolution against installed desktop entries (AU7)."""

from __future__ import annotations

from .maps_resolve import Target

XX_MAPS_DESKTOP_ID = "io.piercingxx.XXMaps.desktop"
GNOME_MAPS_DESKTOP_ID = "org.gnome.Maps.desktop"
GEO_TYPE = "x-scheme-handler/geo"
# Tile subtitle with no maps app, the same words the media tiles use.
NOT_INSTALLED = "not installed"


def _lookup(desktop, desktop_id: str):
    """DesktopAppInfo or None. Newer PyGObject raises TypeError for a
    missing id instead of returning None."""
    try:
        return desktop.new(desktop_id)
    except Exception:
        return None


def resolve_maps_app(desktop=None) -> tuple[Target, str | None]:
    """(target, desktop_id) for the Maps tile; None id when missing."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        desktop = desktop or Gio.DesktopAppInfo
        explicit = _lookup(desktop, XX_MAPS_DESKTOP_ID)
        if explicit is not None:
            return Target.DRIVE, explicit.get_id()
        geo = Gio.AppInfo.get_default_for_type(GEO_TYPE, False)
        if geo is not None and geo.get_id():
            return Target.LAUNCHER, geo.get_id()
        gnome = _lookup(desktop, GNOME_MAPS_DESKTOP_ID)
        if gnome is not None:
            return Target.LAUNCHER, gnome.get_id()
        for info in Gio.AppInfo.get_all():
            for handled in (info.get_supported_types() or []):
                if handled == GEO_TYPE:
                    return Target.LAUNCHER, info.get_id()
    except Exception:
        pass
    return Target.NOT_INSTALLED, None


def tile_subtitle(target: Target, desktop_id: str | None,
                  desktop=None) -> str:
    """The Maps tile subtitle: the app the tile opens, or "not installed"
    (like the Radio and Audiobook tiles; a tap opens the store)."""
    if target is Target.NOT_INSTALLED or not desktop_id:
        return NOT_INSTALLED
    try:
        if desktop is None:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            desktop = Gio.DesktopAppInfo
        info = _lookup(desktop, desktop_id)
        if info is not None and info.get_name():
            return info.get_name()
    except Exception:
        pass
    return "Maps"


def launch_target(target: Target, desktop_id: str | None) -> bool:
    """Open the resolved maps entry; False when nothing is installed."""
    if target is Target.NOT_INSTALLED or not desktop_id:
        return False
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        info = _lookup(Gio.DesktopAppInfo, desktop_id)
        if info is None:
            return False
        return bool(info.launch([], None))
    except Exception:
        return False


def open_store_listing() -> bool:
    """Open the app store listing for xx-maps; False when no store."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        store = _lookup(Gio.DesktopAppInfo, "io.piercingxx.Apps.desktop")
        if store is not None:
            return bool(store.launch([], None))
    except Exception:
        pass
    return False
