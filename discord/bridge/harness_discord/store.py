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
        self.damaged = False                       # state.json existed but could not be read: the layout may be lost
        self._saved = ""                           # JSON of the last write: save() does nothing when nothing changed
        try:
            raw = path.read_text()
        except OSError:
            raw = None
        if raw is not None:
            try:
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise ValueError("not an object")
                self.data.update(loaded)
            except ValueError:
                self.damaged = True
                try:                               # keep the broken file: it may be repairable by hand
                    (path.parent / (path.name + ".bak")).write_text(raw)
                except OSError:
                    pass

    def save(self) -> None:
        blob = json.dumps(self.data)
        if blob == self._saved:
            return
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self._saved = blob

    # session id -> {"channel": thread id, "path": topic path}
    def link(self, sid: str, channel: str, path: str, parent: str = "") -> None:
        self.data["threads"][sid] = {"channel": str(channel), "path": path, "parent": str(parent)}
        self.save()

    def link_of(self, sid: str) -> dict:
        return self.data["threads"].get(sid) or {}

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
