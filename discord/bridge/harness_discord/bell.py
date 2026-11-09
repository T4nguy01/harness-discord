"""The bell: a turn that did not come from Discord ends in a followed session -> a short audible message in its thread."""
from __future__ import annotations

import logging
import time
from typing import Any

from . import fmt
from .discord_api import DiscordError

log = logging.getLogger("harness_discord.bell")

EVERY = 15  # seconds between two polls of /api/sessions


class Bell:
    def __init__(self, bot: Any):
        self.bot = bot
        self.seen: dict[str, tuple[int, str]] | None = None     # session -> (turns, state) at the last poll
        self.last = 0.0                                          # when the last poll ran

    async def poll(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if now - self.last < EVERY:
            return
        prev, self.last = self.last, now
        sessions = await self.bot.h.request("GET", "/api/sessions")
        threads = self.bot.store.data["threads"]
        cur = {s["id"]: s for s in sessions if s.get("id") in threads}
        first = self.seen is None
        old = self.seen or {}
        self.seen = {sid: (s.get("turns", 0), s.get("state", "")) for sid, s in cur.items()}
        if first:                                                # first pass = starting state, replay nothing
            return
        for sid, s in cur.items():
            before = old.get(sid)
            turns, state = self.seen[sid]
            if before is None or turns <= before[0] or state == "running":
                continue
            if sid in self.bot.followers or self.bot.follow_end.get(sid, -1) >= prev:
                continue                                         # the turn came from Discord
            if not s.get("notify"):
                continue
            await self.ring(sid, s)

    async def ring(self, sid: str, s: dict) -> None:
        ch = self.bot.store.channel_of(sid)
        if not ch:
            return
        title = fmt.clip(s.get("title") or "…", 80)
        text = f"✅ **{title}**"
        if s.get("last_text"):
            text += " - " + fmt.clip(s["last_text"].strip(), 200)
        try:
            await self.bot.rest.send(ch, text[:2000], silent=False)
        except DiscordError as e:
            log.warning("bell %s: %s", sid, e)
