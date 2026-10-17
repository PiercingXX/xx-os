"""The xx-radio player service entry point (headless, D-Bus activatable).

Builds the service from the AppSpec: the YouTube engine from
``xx_radio.youtube.engine.create`` (catalog over ytmusicapi, ``resolve``
over the resolver child, sign-in over the account manager) or the fake
engine when ``XX_RADIO_ENGINE`` picks it, the loopback stream proxy over
the audio cache dir, plus the cache index, download queue and health
monitor the service process owns. D-Bus activation is via
``data/io.piercingxx.XXRadio.Player.service.in`` (``Name`` equals the
owned ``.Player`` name, never the GUI app id); idle exit lives in
``xx_music.player.service.PlayerService`` (empty/Stopped soon, paused
with a queue much later).
"""

from __future__ import annotations

import logging
import os

from xx_music import applog
from xx_music import cache as cache_mod
from xx_music import downloads as downloads_mod
from xx_music import health as health_mod
from xx_music.appspec import AppSpec
from xx_music.player.service import PlayerService
from xx_music.player.service import build_proxy

log = logging.getLogger("xx-radio-service")

_LEAF_MODE = 0o700


def ensure_dirs(spec: AppSpec):
    """Create every XDG dir the service writes, mode 0700 for leaves."""
    paths = spec.paths()
    dirs = paths.ensure_app_dirs()
    for path in (paths.audio_cache_dir(), paths.art_cache_dir(),
                 paths.offline_dir(), paths.engine_dir(),
                 paths.runtime_dir()):
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, _LEAF_MODE)
        except OSError:
            pass
    return dirs


def build_aux(spec: AppSpec):
    """Cache, downloads and health owned by the service process."""
    paths = spec.paths()
    index = cache_mod.CacheIndex(paths.db_path())
    manager = cache_mod.CacheManager(paths.audio_cache_dir(), index)
    try:
        manager.sync_from_disk()
    except Exception as exc:  # noqa: BLE001 - a stale index never blocks boot
        log.debug("cache sync failed: %s", exc)
    queue = downloads_mod.DownloadQueue()
    monitor = health_mod.HealthMonitor(spec.name)
    return manager, queue, monitor


def build_service(spec: AppSpec, *, engine=None, proxy=None,
                  audio_sink=None,
                  audio_sink_name: str | None = None) -> PlayerService:
    """A running-ready PlayerService with the wired engine and proxy."""
    ensure_dirs(spec)
    try:
        applog.setup(spec.paths().log_file())
    except Exception as exc:  # noqa: BLE001 - logging never blocks boot
        log.debug("applog setup failed: %s", exc)
    build_aux(spec)
    if engine is None:
        engine = spec.make_engine("player")
    if proxy is None:
        proxy = build_proxy(spec)
    if audio_sink is None and audio_sink_name is None:
        audio_sink_name = os.environ.get("XX_RADIO_AUDIOSINK",
                                         "autoaudiosink")
    if audio_sink is not None:
        return PlayerService(spec, engine, proxy, audio_sink=audio_sink)
    return PlayerService(spec, engine, proxy,
                         audio_sink_name=audio_sink_name or "autoaudiosink")


def main(spec: AppSpec) -> int:
    """Run the player service until idle exit or Quit."""
    try:
        service = build_service(spec)
    except Exception as exc:  # noqa: BLE001 - activation must log, not trace
        log.warning("player service failed to start: %s", exc)
        return 1
    proxy = service.proxy
    try:
        return service.run()
    finally:
        try:
            account = getattr(service.engine, "account", None)
            if account is not None:
                account.clear_cookie_jar()
        except Exception:  # noqa: BLE001 - exit must not raise
            pass
        try:
            proxy.stop()
        except Exception:  # noqa: BLE001 - exit must not raise
            pass
