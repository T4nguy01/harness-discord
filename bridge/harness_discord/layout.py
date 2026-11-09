"""The server layout: private categories and channels, one text channel per Harness project (a thread per session)."""
from __future__ import annotations

import logging
import re

from .discord_api import DiscordError, Rest

log = logging.getLogger("harness_discord.layout")

VIEW, SEND = 1 << 10, 1 << 11
CATEGORY, TEXT = 4, 0

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


async def ensure(rest: Rest, lay: dict, guild: str, owner: str, bot: str, projects: list[dict], lang: str,
                 archive_old: bool = True) -> dict:
    """Create what is missing (idempotent). `lay` is the stored layout; returns the updated one."""
    n = NAMES.get(lang, NAMES["en"])
    chans = {c["id"]: c for c in await rest.guild_channels(guild)}
    first_run = not lay.get("guild")
    lay["guild"] = guild
    ow = private(guild, owner, bot)

    async def cat(key: str, title: str, overwrites: list | None) -> str:
        cid = lay.get(key)
        if cid and cid in chans:
            return cid
        for c in chans.values():                                   # reuse a category of that name
            if c["type"] == CATEGORY and c["name"].lower() == title.lower():
                lay[key] = c["id"]
                return c["id"]
        c = await rest.create_channel(guild, title, CATEGORY, overwrites=overwrites)
        chans[c["id"]] = c
        lay[key] = c["id"]
        return c["id"]

    async def text(key: str, name: str, parent: str, topic: str) -> str:
        cid = lay.get(key)
        if cid and cid in chans:
            return cid
        for c in chans.values():
            if c["type"] == TEXT and c.get("parent_id") == parent and c["name"] == name:
                return c["id"]
        c = await rest.create_channel(guild, name, TEXT, parent=parent, topic=topic)
        chans[c["id"]] = c
        return c["id"]

    old = [c["id"] for c in chans.values() if c["type"] == TEXT] if first_run else []       # what existed before us
    system = await cat("cat_system", n["cat_system"], ow)
    projs = await cat("cat_projects", n["cat_projects"], ow)
    for k in ("alerts", "notifications", "crons", "agents"):
        lay[k] = await text(k, n[k], system, n["t_" + k])
    lay.setdefault("projects", {})
    for p in projects:
        pid = lay["projects"].get(p["name"])
        if pid and pid in chans:
            continue
        lay["projects"][p["name"]] = await text("p:" + p["name"], slug(p["name"]), projs,
                                                (p.get("emoji", "") + " " if p.get("emoji") else "") + n["t_project"].format(name=p["name"]))
    if archive_old and old:
        archive = await cat("cat_archive", n["cat_archive"], None)
        for cid in old:
            try:
                await rest.modify_channel(cid, parent_id=archive)
            except DiscordError as e:
                log.warning("could not archive channel %s: %s", cid, e)
    return lay
