"""Small JSON state (session <-> thread links), written atomically."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"threads": {}, "dm_current": "", "dm_channel": ""}
        try:
            self.data.update(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    # session id -> {"channel": thread id, "path": topic path}
    def link(self, sid: str, channel: str, path: str) -> None:
        self.data["threads"][sid] = {"channel": str(channel), "path": path}
        self.save()

    def unlink(self, sid: str) -> None:
        if self.data["threads"].pop(sid, None) is not None:
            self.save()

    def session_of_channel(self, channel: str) -> str | None:
        for sid, v in self.data["threads"].items():
            if v["channel"] == str(channel):
                return sid
        return None

    def channel_of(self, sid: str) -> str | None:
        v = self.data["threads"].get(sid)
        return v["channel"] if v else None

    def set_dm(self, sid: str, channel: str = "") -> None:
        self.data["dm_current"] = sid
        if channel:
            self.data["dm_channel"] = str(channel)
        self.save()
