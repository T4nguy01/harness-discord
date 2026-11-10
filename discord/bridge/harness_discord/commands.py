"""Slash commands (/new /sessions /stop /close /model /status /help) and opening a session from Discord."""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

import httpx

from . import fmt
from .discord_api import DiscordError
from .harness import HarnessError

log = logging.getLogger("harness_discord.commands")

HERR = (HarnessError, httpx.HTTPError)
MENU_MAX = 25


@dataclass
class Pending:
    text: str
    files: list[dict]
    channel: str                       # channel the request came from (DM, main channel or a thread made by hand)
    message: str                       # id of the user's message ("" for /new)
    in_dm: bool
    thread: str = ""                   # a thread made by hand: it becomes the session's thread
    title: str = ""
    project: str = ""                 # restricts the topic menu to that project
    at: float = field(default_factory=time.time)



class CommandsMixin:
    """Slash commands for `Bot` (uses its h, rest, gw, store, lay, followers, pending, t, err, menu, where, topics, start...)."""

    async def commands(self) -> list[dict]:
        t = self.t
        return [
            {"name": "new", "description": t("d_new"), "options": [{"type": 3, "name": "title", "description": t("d_title")}]},
            {"name": "sessions", "description": t("d_sessions")},
            {"name": "stop", "description": t("d_stop")},
            {"name": "close", "description": t("d_close")},
            {"name": "model", "description": t("d_model"), "options": [{"type": 3, "name": "id", "description": t("d_modelid")}]},
            {"name": "status", "description": t("d_status")},
            {"name": "help", "description": t("d_help")},
        ]

    async def on_command(self, i: dict) -> None:
        name = i["data"]["name"]
        opts = {o["name"]: o.get("value") for o in i["data"].get("options", [])}
        ch, in_dm = i["channel_id"], not i.get("guild_id")
        if name == "help":
            return await self.rest.respond(i, 4, self.t("help"), ephemeral=True)
        if name in ("new", "sessions") and not self.allowed_here(ch, in_dm):
            return await self.rest.respond(i, 4, self.t("refused_here"), ephemeral=True)
        if name == "new":
            return await self.cmd_new(i, ch, in_dm, str(opts.get("title") or "")[:100])
        await self.rest.respond(i, 5, ephemeral=True)               # deferred: Harness may take a moment
        reply = lambda s: self.rest.followup(i, self.gw.app_id, s, ephemeral=True)    # noqa: E731
        try:
            if name == "sessions":
                return await self.cmd_sessions(i, reply)
            sid = self.current_session(i)
            if not sid:
                return await reply(self.t("no_session"))
            if name == "stop":
                await self.h.stop(sid)
                f = self.followers.get(sid)
                if f:
                    f.expect = 0
                return await reply(self.t("stopped"))
            if name == "close":
                await self.h.archive(sid)
                if self.store.channel_of(sid):
                    c = self.store.channel_of(sid)
                    self.store.unlink(sid)
                    await reply(self.t("closed"))
                    return await self.rest.delete_channel(c)
                if self.store.data.get("dm_current") == sid:
                    self.store.set_dm("")
                return await reply(self.t("closed"))
            if name == "model":
                if opts.get("id"):
                    s = await self.h.set_model(sid, str(opts["id"]).strip())
                    return await reply(self.t("model_set", model=s.get("model") or self.t("default")))
                s = await self.h.session(sid)
                return await reply(self.t("model_now", model=s.get("real_model") or s.get("model") or self.t("default")))
            if name == "status":
                s = await self.h.session(sid)
                return await reply(self.t("status", title=s.get("title") or "…", path=s["path"].split("projects/", 1)[-1].replace("/topics/", "/"),
                                          state=s["state"], queue=s.get("queue", 0), model=s.get("real_model") or s.get("model") or self.t("default"),
                                          cost=s.get("cost_usd", 0), turns=s.get("turns", 0), url=f"{self.web_url}/sessions/{sid}"))
        except HERR as e:
            await reply(self.err(e))
        except DiscordError as e:
            log.warning("command %s: %s", name, e)

    def allowed_here(self, ch: str, in_dm: bool) -> bool:
        """/new and /sessions list private session titles: only in a DM, a project channel or a session thread."""
        if in_dm or self.project_of(ch) or self.store.session_of_channel(ch):
            return True
        return str(ch) in {v for v in self.lay.values() if isinstance(v, str)}

    async def cmd_new(self, i: dict, ch: str, in_dm: bool, title: str) -> None:
        sid = self.store.session_of_channel(ch)
        pid = secrets.token_hex(4)
        self.purge()
        app = self.gw.app_id
        if sid:                                                # inside a session's thread: same topic, new session
            link = self.store.data["threads"][sid]
            path = link["path"]
            await self.rest.respond(i, 5, ephemeral=True)
            p = Pending(text="", files=[], channel=link.get("parent") or ch, message="", in_dm=False, title=title)
            ok = await self.start(p, path)
            msg = self.t("started", path=path.split("projects/", 1)[-1].replace("/topics/", "/")) if ok else self.t("start_failed")
            return await self.rest.followup(i, app, msg, ephemeral=True)
        project = self.project_of(ch)
        if not in_dm and not project:
            return await self.rest.respond(i, 4, self.t("no_session"), ephemeral=True)
        await self.rest.respond(i, 5, ephemeral=True)             # the menu is private to the owner
        try:
            topics = [x for x in await self.topics() if not project or x["project"] == project]
        except HERR as e:
            return await self.rest.edit_original(i, app, self.err(e))
        self.pending[pid] = Pending(text="", files=[], channel=ch, message="", in_dm=in_dm, title=title, project=project)
        await self.rest.edit_original(i, app, self.where(topics, len(topics) > MENU_MAX),
                                      components=self.menu(pid, topics, projects=len(topics) > MENU_MAX))

    async def cmd_sessions(self, i: dict, reply) -> None:
        every = [s for s in await self.h.sessions() if not s.get("archived") and not s.get("cron")]
        items = every[:MENU_MAX]
        if not items:
            return await reply(self.t("sessions_none"))
        opts = [{"label": fmt.clip(s.get("title") or "…", 100), "value": s["id"],
                 "description": fmt.clip(s["path"].split("projects/", 1)[-1].replace("/topics/", "/"), 100)} for s in items]
        comps = [{"type": 1, "components": [{"type": 3, "custom_id": "sess:x", "placeholder": self.t("sessions_ph"), "options": opts}]}]
        note = " " + self.t("menu_cut", n=MENU_MAX) if len(every) > MENU_MAX else ""
        await self.rest.edit_original(i, self.gw.app_id, self.t("sessions_pick") + note, components=comps)     # ephemeral, like the deferral

    async def open_session(self, sid: str, channel: str, in_dm: bool, say=None) -> None:
        say = say or (lambda s: self.rest.send(channel, s))
        try:
            s = await self.h.session(sid)
        except HERR as e:
            return await say(self.err(e))
        if in_dm:
            self.store.set_dm(sid, channel)
            return await say(self.t("opened", title=s.get("title") or "…", where=self.t("hint_dm")))
        th = self.store.channel_of(sid)
        if not th:
            parent = self.lay.get("projects", {}).get(s["path"].split("/")[1] if s["path"].startswith("projects/") else "")
            if not parent:
                return await say(self.err("no channel for this project"))
            try:
                th = (await self.rest.start_thread(parent, self.thread_name(s["path"], s.get("title") or "…")))["id"]
            except DiscordError as e:
                log.warning("thread for session %s: %s", sid, e)
                return await say(self.t("start_failed"))
            self.store.link(sid, th, s["path"], parent=parent)
        await say(self.t("opened", title=s.get("title") or "…", where=f"<#{th}>"))
