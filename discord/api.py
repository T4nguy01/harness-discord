"""Harness Discord - settings page API. Reads/writes the bridge's config.json and status.json (its state folder) and
asks the bridge for jobs through flag files. Secrets stay in Settings -> Keys: only 'is it set' is ever returned."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

SERVICE = "harness-discord"
MODES = ("live", "final")
PERMISSIONS = 326686043216   # View/Manage Channels, Manage Roles, Send (+ in threads), Create/Manage Threads, History, Reactions, Embeds, Manage Messages
FLAGS = ("relay_feed", "relay_agents", "relay_bell", "mirror_web_sessions", "reactions", "answer_buttons", "dashboard", "proactive", "tool_approvals")


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _audit(d, n=10):
    out = []
    for name in ("audit.jsonl.1", "audit.jsonl"):
        try:
            lines = (d / name).read_text(errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines[-n:]:
            try:
                r = json.loads(ln)
                out.append({"at": int(r.get("at", 0)), "cmd": str(r.get("cmd", ""))[:40], "target": str(r.get("target", ""))[:120], "result": str(r.get("result", ""))[:80]})
            except (ValueError, TypeError, AttributeError):
                continue
    return out[-n:][::-1]


def _invite(bot_id):
    if not re.fullmatch(r"\d{15,25}", str(bot_id or "")):
        return ""
    return f"https://discord.com/oauth2/authorize?client_id={bot_id}&permissions={PERMISSIONS}&scope=bot+applications.commands"


def _service():
    try:
        r = subprocess.run(["systemctl", "is-active", SERVICE], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _status(ctx):
    d = Path(ctx["state_dir"])
    cfg = _read(d / "config.json", {})
    st = _read(d / "status.json", {})
    state = _read(d / "state.json", {})
    lay = state.get("layout", {})
    age = time.time() - st["at"] if st.get("at") else None
    return {
        "service": _service(),
        "alive": age is not None and age < 90,
        "connected": bool(st.get("connected")) and age is not None and age < 90,
        "running": st.get("running", 0),
        "version": st.get("version", ""),
        "age": int(age) if age is not None else None,
        "bot_id": str(st.get("bot_id", "")),
        "invite": _invite(st.get("bot_id")),
        "permissions": PERMISSIONS,
        "audit": _audit(d, 25),
        "error": str(st.get("error", ""))[:300],
        "keys": {"token": bool(ctx["secret"]("DISCORD_BOT_TOKEN")), "password": bool(ctx["secret"]("HARNESS_PASSWORD"))},
        "config": {
            "owner_id": str(cfg.get("owner_id", "")),
            "guild_id": str(cfg.get("guild_id", "")),
            "reply_mode": cfg.get("reply_mode", "live"),
            "workspace_dir": str(cfg.get("workspace_dir", "")),
            **{k: cfg.get(k, True) for k in FLAGS},
        },
        "layout": {"guild": lay.get("guild", ""), "projects": sorted(lay.get("projects", {})),
                   "system": [k for k in ("alerts", "notifications", "crons", "agents") if lay.get(k)],
                   "dashboard": bool(state.get("dashboard")),
                   "links": {**{k: lay[k] for k in ("alerts", "notifications", "crons", "agents") if lay.get(k)},
                             **{"p:" + n: c for n, c in sorted(lay.get("projects", {}).items())}}},
        "threads": len(state.get("threads", {})),
    }


def handle(ctx, method, path, query, body):
    d = Path(ctx["state_dir"])
    if method == "GET" and path in ("", "status"):
        return _status(ctx)
    if method == "POST" and path == "config":
        body = body or {}
        cfg = _read(d / "config.json", {})
        if "owner_id" in body:
            v = str(body["owner_id"]).strip()
            if v and not re.fullmatch(r"\d{15,25}", v):
                return 400, {"error": "bad_owner_id", "message": "A Discord user id is 15 to 25 digits."}
            cfg["owner_id"] = v
        if "guild_id" in body:
            v = str(body["guild_id"]).strip()
            if v and not re.fullmatch(r"\d{15,25}", v):
                return 400, {"error": "bad_guild_id", "message": "A Discord server id is 15 to 25 digits."}
            cfg["guild_id"] = v
        if "reply_mode" in body:
            if body["reply_mode"] not in MODES:
                return 400, {"error": "bad_mode", "message": "reply_mode: live or final."}
            cfg["reply_mode"] = body["reply_mode"]
        if "workspace_dir" in body:
            v = str(body["workspace_dir"]).strip()
            if v and (not v.startswith("/") or ".." in v.split("/") or not Path(v).is_dir()):
                return 400, {"error": "bad_workspace", "message": "workspace_dir: an existing absolute folder, or empty for the default."}
            cfg["workspace_dir"] = v
        for k in FLAGS:
            if k in body:
                if not isinstance(body[k], bool):
                    return 400, {"error": "bad_flag", "message": f"{k}: true or false."}
                cfg[k] = body[k]
        _write(d / "config.json", cfg)
        return _status(ctx)
    if method == "POST" and path == "action":
        act = (body or {}).get("action")
        if act in ("sync", "test"):
            (d / f"{act}.flag").write_text(str(time.time()))
            return {"ok": True}
        if act == "restart":
            r = subprocess.run(["systemctl", "restart", SERVICE], capture_output=True, text=True, timeout=10)
            return {"ok": r.returncode == 0, "error": r.stderr.strip()[:200]}
        return 400, {"error": "bad_action", "message": "action: sync, test or restart."}
    return 404, {"error": "not_found", "message": path}
