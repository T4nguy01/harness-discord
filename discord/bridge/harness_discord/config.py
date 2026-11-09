"""Settings: environment first, then Harness' Keys vault (`maxxing keys get`). Nothing secret is ever stored here."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


def key(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v.strip()
    try:
        r = subprocess.run(["maxxing", "keys", "get", name], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def harness_url() -> str:
    v = os.environ.get("HARNESS_URL")
    if v:
        return v.rstrip("/")
    try:
        out = subprocess.run(["maxxing", "status"], capture_output=True, text=True, timeout=20).stdout
        m = re.search(r"^URL\s+(\S+)", out, re.M)
        if m:
            return m.group(1).rstrip("/")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "http://127.0.0.1:8787"


def state_dir() -> Path:
    p = Path(os.environ.get("HD_STATE_DIR") or "/var/lib/claude-maxxing/extensions/discord")
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS = {
    "owner_id": "",          # the only Discord user the bot listens to
    "guild_id": "",          # the Discord server (empty = the only one the bot is in)
    "reply_mode": "live",    # live = a status message updated while Claude works | final = only the answer
    "relay_feed": True,      # post the Harness notifications feed (alerts, approvals, job reports)
    "relay_agents": True,    # post the results of agents (one thread per agent)
    "relay_bell": True,      # ring in the thread when a turn started elsewhere (web UI) ends in a session with its bell on
}


def load_settings() -> dict:
    f = state_dir() / "config.json"
    d = dict(DEFAULTS)
    try:
        d.update(json.loads(f.read_text()))
    except (OSError, ValueError):
        pass
    if os.environ.get("DISCORD_OWNER_ID"):
        d["owner_id"] = os.environ["DISCORD_OWNER_ID"].strip()
    d["owner_id"] = str(d["owner_id"])
    d["guild_id"] = str(d["guild_id"] or "")
    return d
