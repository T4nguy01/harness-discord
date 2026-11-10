"""Which workspace files may be listed or sent to Discord: the workspace only, never secrets, at most 25 MB."""
from __future__ import annotations

import os
import re

MAX_BYTES = 25 * 1024 * 1024
SECRET_DIRS = {".git", ".ssh", ".gnupg", ".aws", ".config", ".claude", ".docker", ".kube", ".npm"}
SECRET_FILE = re.compile(
    r"(^\.env($|\.)|\.(pem|key|p12|pfx|jks|keystore|kdbx|asc|gpg|ovpn|secrets?)$|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|^\.(netrc|npmrc|pypirc|pgpass|git-credentials)$|secret|credential|password|passwd|token|api[-_]?key)", re.I)


class Refused(ValueError):
    """The path may not be used; `.args[0]` is a short code."""


def hidden(name: str) -> bool:
    """A file or folder name that is never shown nor sent."""
    return name in SECRET_DIRS or bool(SECRET_FILE.search(name))


def _parts_ok(parts: list[str]) -> bool:
    return not any(hidden(p) for p in parts if p)


def resolve(root: str, arg: str) -> str:
    """`arg` (relative to the workspace, or absolute inside it) -> absolute normalised path. Refused when it leaves the
    workspace, goes through a secret folder or names a secret file. Where the path exists on this machine, its real path
    (symbolic links followed) must pass the same checks."""
    root = os.path.normpath(root)
    raw = (arg or "").strip().replace("\x00", "")
    if len(raw) > 400:
        raise Refused("too_long")
    p = os.path.normpath(raw if raw.startswith("/") else os.path.join(root, raw))
    if p != root and not p.startswith(root.rstrip("/") + "/"):
        raise Refused("outside")
    rel = os.path.relpath(p, root)
    if rel != "." and not _parts_ok(rel.split(os.sep)):
        raise Refused("secret")
    real_root = os.path.realpath(root)
    if os.path.lexists(p):
        real = os.path.realpath(p)
        if real != real_root and not real.startswith(real_root.rstrip("/") + "/"):
            raise Refused("outside")
        rr = os.path.relpath(real, real_root)
        if rr != "." and not _parts_ok(rr.split(os.sep)):
            raise Refused("secret")
    return p
