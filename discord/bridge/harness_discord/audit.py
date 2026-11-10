"""Audit trail: one JSON line per action run or changed from Discord (when, which command, which target, how it ended).
Never the text of a message, never a secret. Mode 600, rotated at about 1 MB (one old file kept)."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("harness_discord.audit")

MAX_BYTES = 1_000_000


class Audit:
    def __init__(self, path: Path, max_bytes: int = MAX_BYTES):
        self.path = Path(path)
        self.max_bytes = max_bytes

    @property
    def old(self) -> Path:
        return self.path.with_name(self.path.name + ".1")

    def log(self, cmd: str, target: str = "", result: str = "ok") -> None:
        rec = {"at": int(time.time()), "cmd": str(cmd)[:40], "target": " ".join(str(target).split())[:120],
               "result": " ".join(str(result).split())[:80]}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode()
        try:
            try:
                if self.path.stat().st_size > self.max_bytes:
                    os.replace(self.path, self.old)
                    os.chmod(self.old, 0o600)
            except FileNotFoundError:
                pass
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            os.chmod(self.path, 0o600)
        except OSError as e:
            log.warning("audit not written: %s", e)

    def tail(self, n: int = 10) -> list[dict]:
        """The last `n` records, oldest first."""
        out: list[dict] = []
        for p in (self.old, self.path):
            try:
                lines = p.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for ln in lines:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    out.append(d)
        return out[-n:]
