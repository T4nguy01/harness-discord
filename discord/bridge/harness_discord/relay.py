"""Harness -> Discord: the notifications feed (alerts, approvals, job reports...) and the results of agents."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from . import __version__, config

from . import fmt
from .bell import Bell
from .discord_api import DiscordError
from .harness import HarnessError

log = logging.getLogger("harness_discord.relay")

LEVEL = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "🛑"}
STYLE = {"primary": 1, "default": 2, "danger": 4}
QUIET = ("info", "success")


def route(e: dict) -> str:
    """Which channel (key of the layout) an event goes to."""
    if e.get("agent") or e.get("source") == "agents":
        return "agents"
    if e.get("source") == "crons":
        return "crons"
    if e.get("source") in ("security", "system", "health", "backup", "telegram") or e.get("actions"):
        return "alerts"
    return "notifications"


def render(e: dict, web_url: str) -> tuple[str, list[dict]]:
    head = f"{LEVEL.get(e.get('level'), 'ℹ️')} **{fmt.clip(e.get('title', ''), 200)}**"
    if e.get("count", 1) > 1:
        head += f" ×{e['count']}"
    body = (e.get("body") or "").strip()
    text = head + (("\n" + fmt.clip_block(body, 1500)) if body else "")
    if e.get("resolved"):
        text += f"\n— *{e.get('resolution') or '✓'}*"
    buttons = []
    for a in e.get("actions") or []:
        if a["id"] == "open":
            url = e.get("url") or ""
            url = web_url + url if url.startswith("/") else url
            if url.startswith("http"):
                buttons.append({"type": 2, "style": 5, "label": a["label"][:80], "url": url})
            continue
        buttons.append({"type": 2, "style": STYLE.get(a.get("style"), 2), "label": a["label"][:80],
                        "custom_id": f"fa:{e['id']}:{a['id']}"})
    comps = [{"type": 1, "components": buttons[:5]}] if buttons else []
    return text[:2000], comps


class Relay:
    def __init__(self, bot: Any):
        self.bot = bot
        self.n = 0
        self.bell = Bell(bot)

    @property
    def st(self) -> dict:
        return self.bot.store.data.setdefault("relay", {"feed_last": None, "posted": {}, "agents": {}})

    async def run(self) -> None:
        await self.bot.gw.ready.wait()
        while True:
            try:
                self.reload()
                await self.flags()
                if self.bot.cfg.get("relay_feed", True):
                    await self.feed()
                if self.bot.cfg.get("relay_bell", True):
                    await self.bell.poll()
                if self.n % 3 == 0 and self.bot.cfg.get("relay_agents", True):
                    await self.agents()
                if self.n % 3 == 0:
                    self.write_status()
            except (HarnessError, DiscordError) as e:
                log.warning("relay: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("relay")
            self.n += 1
            await asyncio.sleep(10)

    def reload(self) -> None:
        """Settings changed in the Harness page apply without a restart."""
        new = config.load_settings()
        self.bot.cfg.update(new)
        self.bot.owner = self.bot.cfg["owner_id"]

    def write_status(self) -> None:
        b = self.bot
        d = {"at": time.time(), "version": __version__, "connected": b.gw.ready.is_set(), "bot_id": b.gw.user_id,
             "guild": b.lay.get("guild", ""), "running": len(b.followers), "threads": len(b.store.data["threads"]),
             "projects": len(b.lay.get("projects", {}))}
        try:
            (config.state_dir() / "status.json").write_text(json.dumps(d))
        except OSError:
            pass

    async def flags(self) -> None:
        """Buttons of the Harness page leave a flag file; the bridge does the job on its next tick."""
        d = config.state_dir()
        if (d / "sync.flag").exists():
            (d / "sync.flag").unlink(missing_ok=True)
            await self.bot.sync_layout()
        if (d / "test.flag").exists():
            (d / "test.flag").unlink(missing_ok=True)
            ch = self.bot.lay.get("alerts")
            if ch:
                await self.bot.rest.send(ch, "✅ " + self.bot.t("test_ok"))

    def channel_for(self, e: dict) -> str | None:
        if e.get("agent"):
            th = self.st["agents"].get(e["agent"], {}).get("thread")
            if th:
                return th
        return self.bot.lay.get(route(e))

    # --- feed ------------------------------------------------------------------------------------------------

    async def feed(self) -> None:
        d = await self.bot.h.request("GET", "/api/feed", params={"limit": 50})
        events = d.get("events", [])
        st = self.st
        if st["feed_last"] is None:                              # first run: start from now, never replay history
            st["feed_last"] = max((e["id"] for e in events), default=0)
            return self.bot.store.save()
        byid = {e["id"]: e for e in events}
        for e in sorted((x for x in events if x["id"] > st["feed_last"]), key=lambda x: x["id"]):
            ch = self.channel_for(e)
            if ch and not e.get("resolved"):
                text, comps = render(e, self.bot.web_url)
                try:
                    m = await self.bot.rest.send(ch, text, components=comps or None, silent=e.get("level") in QUIET)
                    st["posted"][str(e["id"])] = {"channel": ch, "message": m["id"], "done": bool(e.get("resolved"))}
                except DiscordError as err:
                    log.warning("feed %s: %s", e["id"], err)
            st["feed_last"] = e["id"]
        for eid, info in list(st["posted"].items()):             # an alert handled elsewhere: show how it ended
            e = byid.get(int(eid))
            if e and e.get("resolved") and not info.get("done"):
                text, comps = render(e, self.bot.web_url)
                try:
                    await self.bot.rest.edit(info["channel"], info["message"], text, components=[])
                except DiscordError:
                    pass
                info["done"] = True
        for k in sorted(st["posted"], key=int)[:-150]:
            del st["posted"][k]
        self.bot.store.save()

    async def act(self, eid: int, action: str) -> dict:
        d = await self.bot.h.request("POST", f"/api/feed/{eid}/act/{action}")
        return d.get("event", d)

    # --- agents ------------------------------------------------------------------------------------------------

    async def agents(self) -> None:
        d = await self.bot.h.request("GET", "/api/agents")
        st = self.st["agents"]
        for a in d.get("agents", []):
            path = a["path"]
            p, t, name = a["project"], a["topic"], a["name"]
            try:
                th = await self.bot.h.request("GET", f"/api/agents/{p}/{t}/{name}/thread", params={"limit": 10})
            except HarnessError:
                continue
            msgs = [m for m in th.get("messages", []) if m.get("kind") == "result"]
            info = st.setdefault(path, {"seen": max([m["at"] for m in th.get("messages", [])], default=time.time()), "thread": ""})
            fresh = [m for m in msgs if m["at"] > info["seen"]]
            if not fresh:
                continue
            if not info["thread"]:
                ch = self.bot.lay.get("agents")
                if not ch:
                    continue
                label = f"{p}/{t} · {a.get('display_name') or name}"
                info["thread"] = (await self.bot.rest.start_thread(ch, label[:100]))["id"]
            for m in fresh:
                tail = f"\n-# {m.get('cost', 0):.2f} $" if m.get("cost") else ""
                body = m.get("text") or "…"
                if m.get("ok") is False:
                    body = "⚠ " + body
                for part in fmt.chunk(body + tail):
                    sent = await self.bot.rest.send(info["thread"], part, silent=False)
                    self.bot.remember(sent["id"], "agent:" + path)
                info["seen"] = m["at"]
        self.bot.store.save()

    def agent_of_thread(self, channel: str) -> str:
        for path, info in self.st["agents"].items():
            if info.get("thread") == str(channel):
                return path
        return ""

    async def run_agent(self, path: str, text: str, reply_ref: str = "") -> None:
        _, p, _, t, _, name = path.split("/")
        body: dict = {"text": text}
        await self.bot.h.request("POST", f"/api/agents/{p}/{t}/{name}/run", json=body)
