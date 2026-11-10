"""The bridge: Discord messages and slash commands -> Harness sessions, and Claude's answers back to Discord."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections import OrderedDict
from typing import Any

import httpx

from . import fmt, layout
from .commands import HERR, MENU_MAX, CommandsMixin, Pending
from .discord_api import DiscordError, Gateway, Rest
from .follow import (FOLLOW_GIVE_UP, FOLLOW_MAX_FAILS, FOLLOW_RESUME_MAX, FOLLOW_SAVE_EVERY, STATUS_EVERY,  # noqa: F401
                     Follower, FollowMixin)
from .harness import Harness, HarnessError
from .relay import Relay, render
from .store import Store
from .texts import T

log = logging.getLogger("harness_discord.bot")

VOICE_FLAG = 1 << 13          # IS_VOICE_MESSAGE
PENDING_TTL = 600
EID = re.compile(r"[0-9]{1,20}")


class Bot(CommandsMixin, FollowMixin):
    def __init__(self, harness: Harness, rest: Rest, gw: Gateway, store: Store, settings: dict, lang: str, web_url: str):
        self.h, self.rest, self.gw, self.store, self.cfg = harness, rest, gw, store, settings
        self.lang = lang if lang in T else "en"
        self.web_url = web_url
        self.pending: dict[str, Pending] = {}
        self.followers: dict[str, Follower] = {}
        self.follow_end: dict[str, float] = {}                      # session -> when its last Discord turn ended (bell)
        self.msg_session: OrderedDict[str, str] = OrderedDict()     # bot message id -> session (DM replies)
        self.owner = settings["owner_id"]
        self.dm_channel = store.data.get("dm_channel", "")
        self.lay: dict = store.data.setdefault("layout", {})
        self.locks: dict[str, asyncio.Lock] = {}                    # one per session: messages go to Harness in order
        self.sync_task: asyncio.Task | None = None
        self.relay = Relay(self)
        self.relay_task: asyncio.Task | None = None

    def t(self, key: str, **kw: Any) -> str:
        s = T[self.lang].get(key) or T["en"][key]
        return s.format(**kw) if kw else s

    def err(self, e: Any) -> str:
        """What the user sees of a Harness failure: a short status/code only. The detail goes to the log."""
        log.warning("harness error: %s", e)
        if isinstance(e, HarnessError):
            short = f"{e.status} {e.code}" if e.status else e.code
        elif isinstance(e, str):
            short = e
        else:
            short = "unreachable"
        return self.t("err", e=short)

    # --- startup -------------------------------------------------------------------------------------------

    async def on_ready(self) -> None:
        await self.rest.register_commands(self.gw.app_id, await self.commands())
        await self.sync_layout()
        self.resume_followers()
        if not self.sync_task:
            self.sync_task = asyncio.create_task(self._sync_loop())
        if not self.relay_task:
            self.relay_task = asyncio.create_task(self.relay.run())
        log.info("ready: commands registered, owner %s, %d project channels", self.owner, len(self.lay.get("projects", {})))

    async def _sync_loop(self) -> None:
        while True:
            await asyncio.sleep(600)
            try:
                await self.sync_layout()
            except Exception:  # noqa: BLE001
                log.exception("layout sync")

    async def sync_layout(self) -> None:
        guild = await layout.pick_guild(self.rest, self.cfg.get("guild_id", ""))
        if not guild:
            log.warning("the bot is in several servers: set guild_id in config.json")
            return
        projects = [{"name": p["name"], "emoji": p.get("emoji") or ""} for p in await self.h.tree()]
        await layout.ensure(self.rest, self.lay, guild, self.owner, self.gw.user_id, projects, self.lang,
                            state_lost=self.store.damaged)
        self.store.damaged = False
        self.store.save()

    def project_of(self, channel: str) -> str:
        for name, cid in self.lay.get("projects", {}).items():
            if cid == str(channel):
                return name
        return ""

    async def dispatch(self, name: str, d: dict) -> None:
        if name == "MESSAGE_CREATE":
            await self.on_message(d)
        elif name == "INTERACTION_CREATE":
            await self.on_interaction(d)
        elif name in ("THREAD_DELETE", "CHANNEL_DELETE"):
            sid = self.store.session_of_channel(d.get("id", ""))
            if sid:
                self.store.unlink(sid)                       # the session stays; it gets a new thread when used again
            for info in self.relay.st["agents"].values():
                if info.get("thread") and info["thread"] == str(d.get("id", "")):
                    info["thread"] = ""                      # an agent gets a new thread with its next result
                    self.store.save()

    # --- topics ---------------------------------------------------------------------------------------------

    async def topics(self) -> list[dict]:
        out = []
        for p in await self.h.tree():
            for tp in p.get("topics", []):
                out.append({"path": tp["path"], "project": p["name"], "name": tp["name"], "emoji": p.get("emoji") or "",
                            "desc": tp.get("description") or "", "running": tp.get("running", 0), "n": tp.get("session_count", 0)})
        out.sort(key=lambda x: (-x["running"], -x["n"], x["path"]))
        return out

    def menu(self, pid: str, topics: list[dict], projects: bool = False, cont: str = "") -> list[dict]:
        rows: list[dict] = []
        if projects:
            names = sorted({x["project"] for x in topics}, key=str.lower)[:25]
            opts = [{"label": n[:100], "value": f"p:{n}"[:100]} for n in names]
            rows.append({"type": 1, "components": [{"type": 3, "custom_id": f"pick:{pid}", "placeholder": self.t("pick_project_ph"), "options": opts}]})
        else:
            opts = []
            for x in topics[:25]:
                o = {"label": f"{x['project']}/{x['name']}"[:100], "value": x["path"][:100]}
                if x["desc"]:
                    o["description"] = fmt.clip(x["desc"], 100)
                opts.append(o)
            rows.append({"type": 1, "components": [{"type": 3, "custom_id": f"pick:{pid}", "placeholder": self.t("pick_ph"), "options": opts}]})
        if cont:
            rows.insert(0, {"type": 1, "components": [{"type": 2, "style": 1, "label": fmt.clip(self.t("cont", title=cont), 80), "custom_id": f"cont:{pid}"}]})
        return rows

    def where(self, topics: list[dict], projects: bool = False) -> str:
        n = len({x["project"] for x in topics}) if projects else len(topics)
        return self.t("where") + (" " + self.t("menu_cut", n=MENU_MAX) if n > MENU_MAX else "")

    # --- incoming messages ---------------------------------------------------------------------------------

    async def on_message(self, m: dict) -> None:
        a = m.get("author") or {}
        if a.get("bot") or a.get("id") != self.owner:
            return
        ch, in_dm = m["channel_id"], not m.get("guild_id")
        if m.get("type") not in (0, 19):                      # default message or reply only
            return
        sid = self.store.session_of_channel(ch)
        if sid:
            return await self.forward(sid, m, ch)
        agent = self.relay.agent_of_thread(ch)
        if agent:
            try:
                await self.relay.run_agent(agent, (m.get("content") or "").strip())
                await self.rest.react(ch, m["id"], "✅")
            except HERR as e:
                await self.rest.send(ch, self.err(e))
            return
        if in_dm:
            self.dm_channel = ch
            ref = (m.get("message_reference") or {}).get("message_id")
            sid = self.msg_session.get(ref or "")
            if sid and not sid.startswith("agent:"):
                return await self.forward(sid, m, ch)
            return await self.ask_topic(m, ch, in_dm=True)
        if self.project_of(ch):
            return await self.ask_topic(m, ch, in_dm=False)
        try:                                                  # a thread made by hand under a project channel
            info = await self.rest.channel(ch)
        except DiscordError:
            return
        if info.get("type") in (11, 12) and self.project_of(info.get("parent_id", "")):
            await self.ask_topic(m, ch, in_dm=False, thread=ch)

    async def ask_topic(self, m: dict, channel: str, in_dm: bool, thread: str = "") -> None:
        pid = secrets.token_hex(4)
        self.purge()
        project = self.project_of(channel)
        if thread:
            try:
                project = self.project_of((await self.rest.channel(thread)).get("parent_id", ""))
            except DiscordError:
                pass
        p = self.pending[pid] = Pending(text=m.get("content", ""), files=m.get("attachments", []), channel=channel,
                                        message=m["id"], in_dm=in_dm, thread=thread, project=project)
        only = [x for x in await self.topics() if x["project"] == project] if project else []
        if len(only) == 1:                                      # one topic in that project: no question
            del self.pending[pid]
            return await self.start(p, only[0]["path"])
        await self.send_menu(pid, channel, m["id"])

    async def send_menu(self, pid: str, channel: str, reply_to: str | None) -> None:
        p = self.pending[pid]
        topics = [x for x in await self.topics() if not p.project or x["project"] == p.project]
        cont = ""
        if p.in_dm and self.store.data.get("dm_current"):
            try:
                cont = (await self.h.session(self.store.data["dm_current"]))["title"] or "…"
            except HERR:
                cont = ""
        comps = self.menu(pid, topics, projects=len(topics) > 25, cont=cont)
        await self.rest.send(channel, self.where(topics, len(topics) > 25), components=comps, reply_to=reply_to)

    def purge(self) -> None:
        now = time.time()
        for k in [k for k, v in self.pending.items() if now - v.at > PENDING_TTL]:
            del self.pending[k]

    # --- attachments -------------------------------------------------------------------------------------------

    async def ingest(self, path: str, files: list[dict], channel: str) -> tuple[list[str], str | None]:
        """Download Discord attachments and hand them to Harness: (file paths, voice path)."""
        paths: list[str] = []
        voice: str | None = None
        for f in files:
            try:
                if f.get("size", 0) > 25 * 1024 * 1024:
                    raise DiscordError(413, "too large")
                data = await self.rest.download(f["url"])
                up = await self.h.upload(path, f.get("filename", "file"), data, f.get("content_type") or "application/octet-stream")
            except (DiscordError, httpx.HTTPError, HarnessError):
                await self.rest.send(channel, self.t("toolong", name=f.get("filename", "?")))
                continue
            if (f.get("flags", 0) & VOICE_FLAG) and voice is None:
                voice = up
            else:
                paths.append(up)
        return paths, voice

    # --- starting and continuing sessions ----------------------------------------------------------------------

    async def start(self, p: Pending, path: str) -> bool:
        """Create the session and follow it. False when it could not be started."""
        try:
            files, voice = await self.ingest(path, p.files, p.channel)
            s = await self.h.create_session(path, message=p.text, attachments=files, voice=voice, title=p.title)
        except HERR as e:
            await self.rest.send(p.channel, self.err(e))
            return False
        sid = s["id"]
        out = p.channel
        ok = True
        if not p.in_dm:
            if p.thread:
                out = p.thread
                self.store.link(sid, out, path, parent=p.channel)
            else:
                try:
                    th = await self.rest.start_thread(p.channel, self.thread_name(path, s.get("title") or self.t("working")), p.message or None)
                except DiscordError as e:                 # the session exists in Harness: answer in the channel, tell the user
                    log.warning("thread for session %s: %s", sid, e)
                    ok = False
                    await self.rest.send(p.channel, self.t("start_failed"))
                else:
                    out = th["id"]
                    self.store.link(sid, out, path, parent=p.channel)
        else:
            self.store.set_dm(sid, p.channel)
        if p.text or files or voice:
            await self.follow(sid, out, 0, 1)
        elif ok:
            await self.rest.send(out, self.t("started", path=path.split("projects/", 1)[-1].replace("/topics/", "/")))
        return ok

    @staticmethod
    def thread_name(path: str, title: str) -> str:
        return f"{path.split('/topics/', 1)[-1]} · {title}"[:100]

    async def forward(self, sid: str, m: dict, channel: str) -> None:
        async with self.locks.setdefault(sid, asyncio.Lock()):       # two quick messages keep their order
            await self._forward(sid, m, channel)

    async def _forward(self, sid: str, m: dict, channel: str) -> None:
        try:
            s = await self.h.session(sid)
        except HERR as e:
            if isinstance(e, HarnessError) and e.status == 404:
                self.store.unlink(sid)
                return await self.rest.send(channel, self.t("gone"))
            return await self.rest.send(channel, self.err(e))
        if s.get("archived"):
            return await self.rest.send(channel, self.t("archived_s"))
        try:
            offset = await self.h.tail(sid) if sid not in self.followers else self.followers[sid].offset
            files, voice = await self.ingest(s["path"], m.get("attachments", []), channel)
            text = (m.get("content") or "").strip()
            ref = m.get("referenced_message")
            if ref and ref.get("content") and not (ref.get("author") or {}).get("bot"):
                text = f"> {fmt.clip(ref['content'], 400)}\n\n{text}"
            if not text and not files and not voice:
                return
            await self.h.send(sid, text or ("(voice)" if voice else "(file)"), attachments=files, voice=voice)
        except HERR as e:
            return await self.rest.send(channel, self.err(e))
        await self.follow(sid, channel, offset, 1)

    # --- interactions (menus, buttons, slash commands) -----------------------------------------------------------

    async def on_interaction(self, i: dict) -> None:
        user = (i.get("member") or {}).get("user") or i.get("user") or {}
        if user.get("id") != self.owner:
            return await self.rest.respond(i, 4, self.t("not_allowed"), ephemeral=True)
        if i["type"] == 3:
            return await self.on_component(i)
        if i["type"] == 2:
            return await self.on_command(i)

    async def on_component(self, i: dict) -> None:
        kind, _, pid = i["data"]["custom_id"].partition(":")
        p = self.pending.get(pid)
        app = self.gw.app_id
        if kind in ("pick", "cont") and not p:
            return await self.rest.respond(i, 7, self.t("expired"), components=[])
        if kind == "cont":
            sid = self.store.data.get("dm_current")
            self.pending.pop(pid, None)
            await self.rest.respond(i, 7, "↩", components=[])
            return await self.forward_pending(sid, p)
        if kind == "pick":
            await self.rest.respond(i, 7, "⏳", components=[])            # answer first: listing topics may take a moment
            try:
                v = i["data"]["values"][0]
                topics = await self.topics()
                if v.startswith("p:"):
                    only = [x for x in topics if x["project"] == v[2:]]
                    return await self.rest.edit_original(i, app, self.where(only), components=self.menu(pid, only))
                if v not in {x["path"] for x in topics}:                  # only a topic that really exists
                    return await self.rest.edit_original(i, app, self.t("expired"), components=[])
                self.pending.pop(pid, None)
                label = v.split("projects/", 1)[-1].replace("/topics/", "/")
                await self.rest.edit_original(i, app, f"✅ {label}", components=[])
            except HERR as e:
                return await self.rest.edit_original(i, app, self.err(e), components=[])
            return await self.start(p, v)
        if kind == "fa":                                       # a feed alert button: Allow / Deny / Seen...
            parts = i["data"]["custom_id"].split(":", 2)
            if len(parts) != 3 or not EID.fullmatch(parts[1]):
                return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
            _, eid, action = parts
            await self.rest.respond(i, 6)                           # acknowledge now, the click may take a while
            try:
                e = await self.relay.act(int(eid), action)
            except HERR as err:
                return await self.rest.followup(i, app, self.err(err), ephemeral=True)
            embed, comps = render(e, self.web_url)
            return await self.rest.edit_original(i, app, "", components=comps, embeds=[embed])
        if kind == "sess":                                     # a session chosen in /sessions
            sid = i["data"]["values"][0]
            await self.rest.respond(i, 7, self.t("sessions_pick"), components=[])
            if not isinstance(sid, str) or not re.fullmatch(r"[\w-]{1,64}", sid, re.ASCII):
                return await self.rest.followup(i, app, self.t("expired"), ephemeral=True)
            say = lambda s: self.rest.followup(i, app, s, ephemeral=True)    # noqa: E731
            return await self.open_session(sid, i["channel_id"], not i.get("guild_id"), say)

    async def forward_pending(self, sid: str, p: Pending) -> None:
        fake = {"id": p.message, "content": p.text, "attachments": p.files}
        await self.forward(sid, fake, p.channel)

    def current_session(self, i: dict) -> str | None:
        return self.store.session_of_channel(i["channel_id"]) or (self.store.data.get("dm_current") if not i.get("guild_id") else None)
