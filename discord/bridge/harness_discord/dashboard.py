"""A dashboard message pinned in the alerts channel: sessions running, threads, jobs, agents, last relay.
Edited at most every 10 minutes, and only when something changed. Only what the Harness API offers is shown."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from .discord_api import DiscordError
from .harness import HarnessError
from .mirror import is_gone

log = logging.getLogger("harness_discord.dashboard")

EVERY = 600
HERR = (HarnessError, httpx.HTTPError)


def web_link(bot: Any) -> list[dict] | None:
    """A link button to the Harness web interface (its plain address: no token, no session in the URL)."""
    url = str(getattr(bot, "web_url", "") or "")
    if not url.startswith(("http://", "https://")):
        return None
    return [{"type": 1, "components": [{"type": 2, "style": 5, "label": "🌐 " + bot.t("open_harness"), "url": url}]}]


class Dashboard:
    def __init__(self, bot: Any, relay: Any):
        self.bot, self.relay = bot, relay

    @property
    def st(self) -> dict:
        return self.bot.store.data.setdefault("dashboard", {})

    async def data(self) -> dict:
        """What the Harness API tells; an element that cannot be read is left out."""
        b = self.bot
        d: dict[str, Any] = {"threads": len(b.store.data["threads"])}
        try:
            d["running"] = sum(1 for s in await self.relay.sessions_cached(EVERY) if s.get("state") == "running")
        except HERR:
            pass
        try:
            jobs = (await b.h.request("GET", "/api/crons")).get("jobs", [])
            d["crons"] = [sum(1 for j in jobs if j.get("enabled")), sum(1 for j in jobs if (j.get("last_run") or {}).get("ok") is False)]
        except HERR:
            pass
        try:
            ags = (await b.h.request("GET", "/api/agents")).get("agents", [])
            d["agents"] = [len(ags), sum(a.get("running_now") or 0 for a in ags), sum(a.get("waiting") or 0 for a in ags)]
        except HERR:
            pass
        d["relay"] = int(self.relay.st.get("last_post") or 0)
        return d

    def embed(self, d: dict) -> dict:
        t = self.bot.t
        fields = []
        if "running" in d:
            fields.append({"name": t("dash_sessions"), "value": str(d["running"]), "inline": True})
        fields.append({"name": t("dash_threads"), "value": str(d["threads"]), "inline": True})
        if "crons" in d:
            fields.append({"name": t("dash_crons"), "value": t("dash_crons_v", on=d["crons"][0], bad=d["crons"][1]), "inline": True})
        if "agents" in d:
            fields.append({"name": t("dash_agents"), "value": t("dash_agents_v", n=d["agents"][0], run=d["agents"][1], wait=d["agents"][2]), "inline": True})
        fields.append({"name": t("dash_relay"), "value": f"<t:{d['relay']}:R>" if d["relay"] else t("dash_never"), "inline": True})
        return {"title": "📊 " + t("dash_title"), "color": 0x5865F2, "fields": fields}

    async def tick(self) -> None:
        b = self.bot
        ch = b.lay.get("alerts")
        if not b.cfg.get("dashboard", True) or not ch:
            return
        st = self.st
        now = time.time()
        if now - st.get("at", 0) < EVERY:
            return
        st["at"] = now
        d = await self.data()
        h = hashlib.sha1(json.dumps([d, b.web_url], sort_keys=True).encode()).hexdigest()
        embed = self.embed(d)
        link = web_link(b)
        if st.get("message") and st.get("channel") == ch:
            if st.get("hash") != h:
                try:
                    await b.rest.edit(ch, st["message"], content="", embeds=[embed], components=link or [])
                    st["hash"] = h
                except DiscordError as e:
                    if not is_gone(e):
                        raise
                    log.info("dashboard message is gone: a new one is posted")
                    st.pop("message", None)
        if not st.get("message") or st.get("channel") != ch:
            sent = await b.rest.send(ch, "", embeds=[embed], silent=True, components=link)
            st.update({"message": sent["id"], "channel": ch, "hash": h, "pin": False})
        if not st.get("pin"):
            try:
                await b.rest.pin(ch, st["message"])
                st["pin"] = True
            except DiscordError as e:
                if not st.get("pin_warned"):
                    log.warning("dashboard not pinned (Manage Messages / Pin Messages missing?): %s", e)
                    st["pin_warned"] = True
        b.store.save()
