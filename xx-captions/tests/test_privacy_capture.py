"""Captions capture nodes light the shell privacy dot (real classifier).

Loads the REAL classify_node from the shell tree (lane copy first,
sibling checkout second, skip when neither exists) and proves a
captions stream — Stream/Input/Audio with our honest properties —
classifies as mic use.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from xx_captions import capture
from xx_captions.dbus_api import BUS_NAME

ROOT = Path(__file__).resolve().parent.parent


def _load_privacy_monitor():
    candidates = [
        ROOT / "ref" / "xx-wm" / "launcher" / "src" / "privacy_monitor.py",
        ROOT.parent / "xx-wm" / "launcher" / "src" / "privacy_monitor.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location(
                "privacy_monitor", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.skip("no xx-wm tree beside this lane (ref/ nor ../xx-wm)")


def _captions_node(state: str = "running",
                   media_class: str = "Stream/Input/Audio") -> dict:
    return {
        "id": 53,
        "type": "PipeWire:Interface:Node",
        "info": {
            "state": state,
            "props": {
                "node.name": "alsa_output.platform-sound.HiFi__hw__0__sink",
                "media.class": media_class,
                **capture.STREAM_PROPS,
            },
        },
    }


def test_captions_capture_classifies_as_mic() -> None:
    privacy = _load_privacy_monitor()
    assert privacy.classify_node(
        _captions_node()) == ("mic", "captions")


def test_suspended_or_sink_nodes_stay_dark() -> None:
    privacy = _load_privacy_monitor()
    assert privacy.classify_node(
        _captions_node(state="suspended")) is None
    assert privacy.classify_node(
        _captions_node(media_class="Audio/Sink")) is None


def test_stream_identity_matches_service_name() -> None:
    assert capture.APP_ID == BUS_NAME
    assert capture.STREAM_PROPS["application.id"] == BUS_NAME
    assert capture.STREAM_PROPS["media.role"] == "Accessibility"
