"""File cache of model answers, used for record and replay."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CACHE_MODES = ("off", "read_write", "replay_only")


class ResponseCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, value: dict) -> None:
        # Write to a temp file and rename, so a crash can't leave half a file.
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(value, fh, indent=2)
        os.replace(tmp, self._path(key))

    def __len__(self) -> int:
        return sum(1 for _ in self.directory.glob("*.json"))
