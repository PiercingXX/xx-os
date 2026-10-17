"""Third-party components and their licenses, for the About screen.

The shared shell (GTK, fonts, icons) comes from
``xx_ui.widgets.standard_license_entries``; this module appends the five
pieces the playback engine depends on (PORT_SPEC.md section 2): the
catalog library, the stream resolver, the PO-token helper, the static
installer, and the audio backend. Entry shapes match
``xx_ui.widgets.LicenseEntry`` so the About screen passes them straight
to ``about_content`` / ``licenses_page``.
"""

from __future__ import annotations


def entries(app_name: str = "xx-radio") -> list:
    """The About screen's license table: the app first, then everything
    it uses, shared shell first and engine pieces after."""
    from xx_ui import widgets

    return widgets.standard_license_entries(app_name) + [
        widgets.LicenseEntry(
            "ytmusicapi", "MIT",
            "https://github.com/sigma67/ytmusicapi",
            ("ytmusicapi", "py3-ytmusicapi", "python-ytmusicapi",
             "python3-ytmusicapi")),
        widgets.LicenseEntry(
            "yt-dlp", "Unlicense",
            "https://github.com/yt-dlp/yt-dlp",
            ("yt-dlp",)),
        widgets.LicenseEntry(
            "bgutil-ytdlp-pot-provider", "GPL-3.0-only",
            "https://github.com/brainicism/bgutil-ytdlp-pot-provider",
            ("bgutil-ytdlp-pot-provider",)),
        widgets.LicenseEntry(
            "uv", "MIT OR Apache-2.0",
            "https://github.com/astral-sh/uv",
            ("uv",)),
        widgets.LicenseEntry(
            "GStreamer", "LGPL-2.1-or-later",
            "https://gstreamer.freedesktop.org/",
            ("gstreamer", "gst-plugins-base", "gst-plugins-good",
             "gst-plugins-bad", "gst-libav")),
    ]
