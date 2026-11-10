"""Web mirror: every recent session opened in the Harness web UI gets a thread in its project channel (like the Telegram
mirror). The thread is renamed with the session's title, archived with the session and brought back when it is un-archived.
Only a welcome message (title + link) is posted: the history is not copied, and new turns started on the web ring the bell."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .discord_api import DiscordError
from .harness import HarnessError

log = logging.getLogger("harness_discord.mirror")

DAYS = 30                 # sessions active in the last 30 days (or running), like Telegram
MAX_SESSIONS = 50
CREATE_PAUSE = 0.7        # seconds between two thread creations (Discord rate limits)
GONE = 10003              # Discord "Unknown Channel"


def is_gone(e: DiscordError) -> bool:
    return e.status == 404 or (isinstance(e.body, dict) and e.body.get("code") == GONE)


class Mirror:
    def __init__(self, bot: Any):
        self.bot = bot

    def eligible(self, sessions: list[dict], now: float | None = None) -> list[dict]:
        """Human sessions (not scheduled jobs, not agents), not archived, recent: most recent first, at most 50."""
        cutoff = (time.time() if now is None else now) - DAYS * 86400
        out = [s for s in sessions if not s.get("archived") and not s.get("cron") and "/agents/" not in s.get("path", "")
               and ((s.get("updated") or 0) >= cutoff or s.get("state") == "running")]
        out.sort(key=lambda s: s.get("updated") or 0, reverse=True)
        return out[:MAX_SESSIONS]

    async def sync(self) -> None:
        """Called by the 10-minute layout sync. A failure here never breaks the layout sync."""
        if not self.bot.cfg.get("mirror_web_sessions", True):
            return
        try:
            await self._sync()
        except (HarnessError, httpx.HTTPError, DiscordError) as e:
            log.warning("mirror: %s", e)
        except Exception:  # noqa: BLE001
            log.exception("mirror")

    async def _sync(self) -> None:
        b = self.bot
        sessions = await b.h.sessions()
        by_id = {s["id"]: s for s in sessions if s.get("id")}
        threads = b.store.data["threads"]
        for sid in list(threads):                                # threads that exist: archive / unarchive / rename
            s = by_id.get(sid)
            if s is not None:
                await self.follow_session(sid, s)
        created = 0
        for s in self.eligible(sessions):
            if s["id"] in threads:
                continue
            if created and CREATE_PAUSE:
                await asyncio.sleep(CREATE_PAUSE)
            if await self.create(s):
                created += 1
        b.store.save()

    async def follow_session(self, sid: str, s: dict) -> None:
        b = self.bot
        link = b.store.link_of(sid)
        if not link:
            return
        ch = link["channel"]
        try:
            if s.get("archived"):
                if not link.get("archived"):
                    await b.rest.modify_channel(ch, archived=True)
                    link["archived"] = True
                return
            if link.get("archived"):                              # un-archived in Harness: the thread comes back
                await b.rest.modify_channel(ch, archived=False)
                link.pop("archived", None)
            if link.get("mirror") and s.get("title"):
                name = b.thread_name(link["path"], s["title"])
                if name != link.get("name"):
                    await b.rest.rename(ch, name)
                    link["name"] = name
                    link["titled"] = True
        except DiscordError as e:
            if is_gone(e):                                        # deleted by hand: recreated below when the session is eligible
                log.info("thread %s of session %s is gone", ch, sid)
                b.store.unlink(sid)
            else:
                log.warning("mirror %s: %s", sid, e)

    async def create(self, s: dict) -> bool:
        b = self.bot
        path = s.get("path", "")
        project = path.split("/")[1] if path.startswith("projects/") else ""
        parent = b.lay.get("projects", {}).get(project)
        if not parent:
            return False
        title = s.get("title") or "…"
        name = b.thread_name(path, title)
        try:
            th = (await b.rest.start_thread(parent, name))["id"]
            b.store.link(s["id"], th, path, parent=parent)
            link = b.store.link_of(s["id"])
            link.update({"mirror": True, "name": name, "titled": bool(s.get("title"))})
            await b.rest.send(th, b.t("mirror_hello", title=title, url=f"{b.web_url}/sessions/{s['id']}"), silent=True)
        except DiscordError as e:
            log.warning("mirror thread for %s: %s", s.get("id"), e)
            return False
        return True
