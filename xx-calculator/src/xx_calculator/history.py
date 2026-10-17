"""History store: newest-first JSON history, capped at 100 entries.

Ports HistoryStore.kt. The Android persistence seam (a string key-value
store backed by SharedPreferences) is kept, so the store stays testable
without GTK or a home directory; the production seam is a JSON file
under the app's XDG data dir.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class HistoryEntry:
    expression: str
    result: str
    timestamp: int


class HistoryKeyValueStore(Protocol):
    def get_string(self, key: str) -> str | None:
        ...

    def put_string(self, key: str, value: str | None) -> None:
        ...


def _data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "xx-calculator"


class FileHistoryKv:
    """HistoryKeyValueStore over one JSON file in the app data dir."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else _data_dir() / "history.json"

    def get_string(self, key: str) -> str | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        value = data.get(key)
        return value if isinstance(value, str) else None

    def put_string(self, key: str, value: str | None) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self._path)


class HistoryStore:
    MAX_ENTRIES = 100
    KEY_ENTRIES = "entries"

    def __init__(self, kv: HistoryKeyValueStore | None = None) -> None:
        self._kv: HistoryKeyValueStore = kv if kv is not None else FileHistoryKv()

    def list(self) -> list[HistoryEntry]:
        return self._decode(self._kv.get_string(self.KEY_ENTRIES))

    def add(self, expression: str, result: str, timestamp: int) -> None:
        entries = [HistoryEntry(expression, result, timestamp)] + self.list()
        self._save(entries[: self.MAX_ENTRIES])

    def clear(self) -> None:
        self._save([])

    def _decode(self, raw: str | None) -> list[HistoryEntry]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(data, list):
            return []
        entries: list[HistoryEntry] = []
        for item in data:
            try:
                entries.append(
                    HistoryEntry(
                        expression=item["expr"],
                        result=item["result"],
                        timestamp=int(item.get("timestamp", 0)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                return []
        return entries

    def _save(self, entries: list[HistoryEntry]) -> None:
        payload = json.dumps(
            [
                {
                    "expr": entry.expression,
                    "result": entry.result,
                    "timestamp": entry.timestamp,
                }
                for entry in entries
            ]
        )
        self._kv.put_string(self.KEY_ENTRIES, payload)