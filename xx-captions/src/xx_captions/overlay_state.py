"""Pure caption-overlay state: no GTK, no D-Bus, no network.

CaptionBuffer renders the Text-signal stream as 2-3 lines: a partial
replaces the last line in place and the matching final commits it, so
the overlay updates labels instead of rebuilding widgets (no
flicker). Tile-state, margin, and download-label helpers keep the GTK
layer and the shell-integration doc from drifting apart.
"""
from __future__ import annotations

from xx_captions import config, models

MAX_LINES = 3

MARGIN_MIN = 0
MARGIN_MAX = 512
DEFAULT_BOTTOM_MARGIN = 72

TILE_STATES = ("off", "starting", "on", "no-model", "error")


class CaptionBuffer:
    """Last N committed finals plus one live partial line."""

    def __init__(self, max_lines: int = MAX_LINES) -> None:
        self._max_lines = max(1, int(max_lines))
        self._finals: list[str] = []
        self._partial = ""

    def push_partial(self, text: object) -> bool:
        """Replace the live line in place; empty text clears it."""
        cleaned = text.strip() if isinstance(text, str) else ""
        self._partial = cleaned
        return bool(cleaned)

    def push_final(self, text: object) -> bool:
        """Commit one final line and clear the live partial."""
        cleaned = text.strip() if isinstance(text, str) else ""
        if not cleaned:
            self._partial = ""
            return False
        self._finals.append(cleaned)
        self._finals = self._finals[-self._max_lines:]
        self._partial = ""
        return True

    def lines(self) -> tuple[str, ...]:
        """Oldest first; the live partial (if any) is always last."""
        shown = list(self._finals[-(self._max_lines):])
        if self._partial:
            shown = (shown + [self._partial])[-self._max_lines:]
        return tuple(shown)

    def live_partial(self) -> str:
        return self._partial

    def clear(self) -> None:
        self._finals = []
        self._partial = ""


def clamp_margin(value: object, screen_height: int = 720) -> int:
    """Vertical bottom margin, clamped to the phone viewport."""
    try:
        margin = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_BOTTOM_MARGIN
    top = max(MARGIN_MIN, min(int(screen_height) - 64, MARGIN_MAX))
    return max(MARGIN_MIN, min(top, margin))


def load_margin(home=None) -> int:
    return clamp_margin(config.load(home).get("overlay_margin_bottom",
                                             DEFAULT_BOTTOM_MARGIN))


def save_margin(margin: int, home=None) -> int:
    """Persist the dragged position; returns the clamped value kept."""
    clamped = clamp_margin(margin)
    data = config.load(home)
    data["overlay_margin_bottom"] = clamped
    config.save(data, home)
    return clamped


def tile_state(active: bool, starting: bool = False,
               installed: bool = True, error: bool = False) -> str:
    """Quick-settings tile state for the shell lane.

    Precedence is fixed: error beats missing-model beats on beats
    starting beats off, so the tile never claims "on" while broken.
    """
    if error:
        return "error"
    if not installed:
        return "no-model"
    if active:
        return "on"
    if starting:
        return "starting"
    return "off"


def phone_default_model() -> str:
    """Catalog id the first-run screen downloads."""
    return str(config.DEFAULT_CONFIG["model"])


def model_missing(home=None) -> bool:
    """True when no catalog model is fully installed (first run)."""
    return not models.list_installed(home)


def download_label(model_id: str | None = None) -> str:
    """First-run subtitle: the size, shown before the download starts
    (the title and the Download button already say the rest). Empty
    when the catalog does not know the size."""
    entry = config.MODEL_CATALOG.get(model_id or phone_default_model(), {})
    size_mb = int(entry.get("size_mb") or 0)
    if size_mb > 0:
        return f"{size_mb} MB"
    return ""


def progress_fraction(done: int, total: int | None) -> float:
    """0..1 download fraction; 0 when the total is unknown."""
    try:
        total_int = int(total) if total is not None else 0
    except (TypeError, ValueError):
        total_int = 0
    if total_int <= 0:
        return 0.0
    try:
        done_int = max(0, int(done))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, done_int / total_int))


__all__ = [
    "DEFAULT_BOTTOM_MARGIN",
    "MARGIN_MAX",
    "MARGIN_MIN",
    "MAX_LINES",
    "TILE_STATES",
    "CaptionBuffer",
    "clamp_margin",
    "download_label",
    "load_margin",
    "model_missing",
    "phone_default_model",
    "progress_fraction",
    "save_margin",
    "tile_state",
]
