"""The server layout: private categories and channels, one text channel per Harness project (a thread per session)."""
from __future__ import annotations

import logging
import re

from .discord_api import DiscordError, Rest

log = logging.getLogger("harness_discord.layout")

VIEW, SEND = 1 << 10, 1 << 11
CATEGORY, TEXT = 4, 0
ALERTS_SLOWMODE = 2          # seconds between two messages in #alerts

NAMES = {
    "en": {"cat_system": "Harness", "cat_projects": "Projects", "cat_archive": "Archives",
           "alerts": "alerts", "notifications": "notifications", "crons": "crons", "agents": "agents",
           "t_alerts": "Server problems, failed jobs, update and sign-in alerts.",
           "t_notifications": "The Harness notifications feed (sales, orders, your scripts).",
           "t_crons": "Reports of scheduled jobs.", "t_agents": "Agent results and their requests (Allow / Deny).",
           "t_project": "Project {name}: write here to start a session (a thread per session)."},
    "fr": {"cat_system": "Harness", "cat_projects": "Projets", "cat_archive": "Archives",
           "alerts": "alertes", "notifications": "notifications", "crons": "crons", "agents": "agents",
           "t_alerts": "Problèmes serveur, jobs en échec, alertes de mise à jour et de connexion.",
           "t_notifications": "Le flux de notifications de Harness (ventes, commandes, tes scripts).",
           "t_crons": "Rapports des tâches planifiées.", "t_agents": "Résultats des agents et leurs demandes (Autoriser / Refuser).",
           "t_project": "Projet {name} : écris ici pour démarrer une session (un fil par session)."},
}


def slug(name: str) -> str:
    s = re.sub(r"[^\w-]+", "-", name.strip().lower().replace("_", "-"), flags=re.U).strip("-")
    return re.sub(r"-{2,}", "-", s) or "projet"


def private(guild: str, owner: str, bot: str) -> list[dict]:
    """@everyone cannot see it; the owner and the bot can."""
    return [{"id": guild, "type": 0, "allow": "0", "deny": str(VIEW)},
            {"id": owner, "type": 1, "allow": str(VIEW | SEND), "deny": "0"},
            {"id": bot, "type": 1, "allow": str(VIEW | SEND), "deny": "0"}]


async def pick_guild(rest: Rest, wanted: str = "") -> str | None:
    gs = await rest.guilds()
    if wanted:
        return wanted if any(g["id"] == wanted for g in gs) else None
    return gs[0]["id"] if len(gs) == 1 else None


def is_private(channel: dict, guild: str) -> bool:
    """True when @everyone is denied VIEW_CHANNEL on that channel (or the channel carries no overwrites info at all: unknown)."""
    for o in channel.get("permission_overwrites") or []:
        if str(o.get("id")) == guild and int(o.get("deny") or 0) & VIEW:
            return True
    return False


class _Ensure:
    """What `ensure` needs while it works: the channels that exist, and the ones taken over by name."""

    def __init__(self, rest: Rest, lay: dict, guild: str, owner: str, bot: str, chans: dict):
        self.rest, self.lay, self.guild, self.owner, self.bot, self.chans = rest, lay, guild, owner, bot, chans
        self.reused: set[str] = set()                              # existing channels the bridge took over by name

    def overwrites(self) -> list:
        return private(self.guild, self.owner, self.bot)

    async def lock(self, c: dict) -> bool:
        """A reused category / channel must be private: make it so, or refuse it if that is impossible."""
        if is_private(c, self.guild):
            return True
        try:
            await self.rest.modify_channel(c["id"], permission_overwrites=self.overwrites())
        except DiscordError as e:
            log.warning("not reusing %s (%s): visible to @everyone and cannot be made private: %s", c["name"], c["id"], e)
            return False
        c["permission_overwrites"] = self.overwrites()
        return True

    async def cat(self, key: str, title: str, overwrites: list | None) -> str:
        cid = self.lay.get(key)
        if cid and cid in self.chans:
            return cid
        for c in self.chans.values():                              # reuse a category of that name
            if c["type"] == CATEGORY and c["name"].lower() == title.lower():
                if overwrites is not None and not await self.lock(c):
                    continue
                self.lay[key] = c["id"]
                self.reused.add(c["id"])
                return c["id"]
        c = await self.rest.create_channel(self.guild, title, CATEGORY, overwrites=overwrites)
        self.chans[c["id"]] = c
        self.lay[key] = c["id"]
        return c["id"]

    async def text(self, key: str, name: str, parent: str, topic: str) -> str:
        cid = self.lay.get(key)
        if cid and cid in self.chans:
            return cid
        for c in self.chans.values():
            if c["type"] == TEXT and c.get("parent_id") == parent and c["name"] == name:
                if not await self.lock(c):
                    continue
                self.reused.add(c["id"])
                return c["id"]
        c = await self.rest.create_channel(self.guild, name, TEXT, parent=parent, topic=topic, overwrites=self.overwrites())
        self.chans[c["id"]] = c
        return c["id"]

    async def archive(self, old: list[str], title: str) -> None:
        archive = await self.cat("cat_archive", title, None)
        for cid in old:
            try:
                await self.rest.modify_channel(cid, parent_id=archive)
            except DiscordError as e:
                log.warning("could not archive channel %s: %s", cid, e)


async def ensure(rest: Rest, lay: dict, guild: str, owner: str, bot: str, projects: list[dict], lang: str,
                 archive_old: bool = True, state_lost: bool = False) -> dict:
    """Create what is missing (idempotent). `lay` is the stored layout; returns the updated one.

    `state_lost`: the stored state was unreadable; never archive anything in that case."""
    n = NAMES.get(lang, NAMES["en"])
    chans = {c["id"]: c for c in await rest.guild_channels(guild)}
    first_run = not lay.get("guild")
    lay["guild"] = guild
    e = _Ensure(rest, lay, guild, owner, bot, chans)
    old = [c["id"] for c in chans.values() if c["type"] == TEXT] if first_run else []       # what existed before us
    system = await e.cat("cat_system", n["cat_system"], e.overwrites())
    projs = await e.cat("cat_projects", n["cat_projects"], e.overwrites())
    for k in ("alerts", "notifications", "crons", "agents"):
        lay[k] = await e.text(k, n[k], system, n["t_" + k])
    lay.setdefault("projects", {})
    for p in projects:
        pid = lay["projects"].get(p["name"])
        if pid and pid in chans:
            continue
        lay["projects"][p["name"]] = await e.text("p:" + p["name"], slug(p["name"]), projs,
                                                  (p.get("emoji", "") + " " if p.get("emoji") else "") + n["t_project"].format(name=p["name"]))
    old = [c for c in old if c not in e.reused]
    if archive_old and old and not state_lost and not e.reused:   # reused = a previous run of the bridge: never touch the rest
        await e.archive(old, n["cat_archive"])
    return lay


async def slow_alerts(rest: Rest, lay: dict) -> None:
    """A 2 second slow mode on #alerts, once per channel (a burst of alerts cannot flood it). A refusal (no Manage Channels
    on a channel taken over by name...) is only logged; the channel is not tried again until its id changes."""
    cid = lay.get("alerts")
    if not cid or lay.get("alerts_slow") == cid:
        return
    try:
        await rest.modify_channel(cid, rate_limit_per_user=ALERTS_SLOWMODE)
    except DiscordError as e:
        log.warning("slow mode not set on the alerts channel: %s", e)
    lay["alerts_slow"] = cid
