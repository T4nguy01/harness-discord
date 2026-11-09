"""The bridge: Discord messages and slash commands -> Harness sessions, and Claude's answers back to Discord."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import fmt
from .discord_api import DiscordError, Gateway, Rest
from .harness import Harness, HarnessError
from .store import Store
from .texts import T

log = logging.getLogger("harness_discord.bot")

VOICE_FLAG = 1 << 13          # IS_VOICE_MESSAGE
PENDING_TTL = 600
STATUS_EVERY = 1.5


@dataclass
class Pending:
    text: str
    files: list[dict]
    channel: str                       # channel the request came from (DM, main channel or a thread made by hand)
    message: str                       # id of the user's message ("" for /new)
    in_dm: bool
    thread: str = ""                   # a thread made by hand: it becomes the session's thread
    title: str = ""
    at: float = field(default_factory=time.time)


@dataclass
class Follower:
    sid: str
    channel: str
    offset: int
    expect: int = 0
    cur: list[str] = field(default_factory=list)       # assistant texts since the last tool call
    last_text: str = ""
    steps: int = 0
    started: float = 0.0
    status_id: str = ""
    status_at: float = 0.0
    state: str = ""
    active_at: float = field(default_factory=time.time)
    task: asyncio.Task | None = None
    typing: asyncio.Task | None = None


class Bot:
    def __init__(self, harness: Harness, rest: Rest, gw: Gateway, store: Store, settings: dict, lang: str, web_url: str):
        self.h, self.rest, self.gw, self.store, self.cfg = harness, rest, gw, store, settings
        self.lang = lang if lang in T else "en"
        self.web_url = web_url
        self.pending: dict[str, Pending] = {}
        self.followers: dict[str, Follower] = {}
        self.msg_session: OrderedDict[str, str] = OrderedDict()     # bot message id -> session (DM replies)
        self.main = settings["channel_id"]
        self.owner = settings["owner_id"]
        self.dm_channel = store.data.get("dm_channel", "")

    def t(self, key: str, **kw: Any) -> str:
        s = T[self.lang].get(key) or T["en"][key]
        return s.format(**kw) if kw else s

    # --- startup -------------------------------------------------------------------------------------------

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

    async def on_ready(self) -> None:
        await self.rest.register_commands(self.gw.app_id, await self.commands())
        log.info("ready: commands registered, owner %s, channel %s", self.owner, self.main or "(DM only)")

    async def dispatch(self, name: str, d: dict) -> None:
        if name == "MESSAGE_CREATE":
            await self.on_message(d)
        elif name == "INTERACTION_CREATE":
            await self.on_interaction(d)
        elif name in ("THREAD_DELETE", "CHANNEL_DELETE"):
            sid = self.store.session_of_channel(d.get("id", ""))
            if sid:
                self.store.unlink(sid)                       # the session stays; it gets a new thread when used again

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
        if in_dm:
            self.dm_channel = ch
            ref = (m.get("message_reference") or {}).get("message_id")
            sid = self.msg_session.get(ref or "")
            if sid:
                return await self.forward(sid, m, ch)
            return await self.ask_topic(m, ch, in_dm=True)
        if ch == self.main:
            return await self.ask_topic(m, ch, in_dm=False)
        try:                                                  # a thread made by hand under the main channel
            info = await self.rest.channel(ch)
        except DiscordError:
            return
        if info.get("type") in (11, 12) and info.get("parent_id") == self.main and self.main:
            await self.ask_topic(m, ch, in_dm=False, thread=ch)

    async def ask_topic(self, m: dict, channel: str, in_dm: bool, thread: str = "") -> None:
        pid = secrets.token_hex(4)
        self.purge()
        self.pending[pid] = Pending(text=m.get("content", ""), files=m.get("attachments", []), channel=channel,
                                    message=m["id"], in_dm=in_dm, thread=thread)
        await self.send_menu(pid, channel, m["id"])

    async def send_menu(self, pid: str, channel: str, reply_to: str | None) -> None:
        topics = await self.topics()
        cont = ""
        p = self.pending[pid]
        if p.in_dm and self.store.data.get("dm_current"):
            try:
                cont = (await self.h.session(self.store.data["dm_current"]))["title"] or "…"
            except HarnessError:
                cont = ""
        comps = self.menu(pid, topics, projects=len({x["project"] for x in topics}) > 0 and len(topics) > 25, cont=cont)
        await self.rest.send(channel, self.t("where"), components=comps, reply_to=reply_to)

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

    async def start(self, p: Pending, path: str) -> None:
        try:
            files, voice = await self.ingest(path, p.files, p.channel)
            s = await self.h.create_session(path, message=p.text, attachments=files, voice=voice, title=p.title)
        except HarnessError as e:
            return await self.rest.send(p.channel, self.t("err", e=e))
        sid = s["id"]
        out = p.channel
        if not p.in_dm:
            if p.thread:
                out = p.thread
            else:
                name = f"{path.split('projects/', 1)[-1].replace('/topics/', '/')} · {s.get('title') or self.t('working')}"
                th = await self.rest.start_thread(p.channel, name, p.message or None)
                out = th["id"]
            self.store.link(sid, out, path)
        else:
            self.store.set_dm(sid, p.channel)
        if p.text or files or voice:
            await self.follow(sid, out, 0, 1)
        else:
            await self.rest.send(out, self.t("started", path=path.split("projects/", 1)[-1].replace("/topics/", "/")))

    async def forward(self, sid: str, m: dict, channel: str) -> None:
        try:
            s = await self.h.session(sid)
        except HarnessError as e:
            if e.status == 404:
                self.store.unlink(sid)
                return await self.rest.send(channel, self.t("gone"))
            return await self.rest.send(channel, self.t("err", e=e))
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
        except HarnessError as e:
            return await self.rest.send(channel, self.t("err", e=e))
        await self.follow(sid, channel, offset, 1)

    # --- following a session ----------------------------------------------------------------------------------

    async def follow(self, sid: str, channel: str, offset: int, expect: int) -> None:
        f = self.followers.get(sid)
        if f:
            f.expect += expect
            f.active_at = time.time()
            return
        f = Follower(sid=sid, channel=channel, offset=offset, expect=expect, started=time.time())
        self.followers[sid] = f
        f.typing = asyncio.create_task(self._typing(f))
        f.task = asyncio.create_task(self._run(f))

    async def _typing(self, f: Follower) -> None:
        while True:
            await self.rest.typing(f.channel)
            await asyncio.sleep(8)

    async def _run(self, f: Follower) -> None:
        try:
            while f.expect > 0:
                try:
                    it = self.h.stream(f.sid, f.offset).__aiter__()
                    while f.expect > 0:
                        try:
                            kind, data = await asyncio.wait_for(it.__anext__(), 20)
                        except asyncio.TimeoutError:
                            if f.state == "idle" and time.time() - f.active_at > 45:
                                f.expect = 0              # nothing more is coming for the messages we counted
                            continue
                        except StopAsyncIteration:
                            break
                        f.active_at = time.time()
                        if kind == "state":
                            f.state = data.get("state", "")
                        elif kind == "reset":
                            f.offset = await self.h.tail(f.sid)
                            break
                        elif kind == "events":
                            f.offset = data.get("next", f.offset)
                            for ev in data.get("events", []):
                                await self.on_event(f, ev)
                    await it.aclose()
                except (HarnessError, httpx.HTTPError) as e:
                    log.warning("stream %s: %s", f.sid, e)
                    await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("follower %s", f.sid)
        finally:
            if f.typing:
                f.typing.cancel()
            self.followers.pop(f.sid, None)

    async def on_event(self, f: Follower, ev: dict) -> None:
        typ = ev.get("type")
        if typ == "user":
            f.cur, f.steps, f.last_text, f.started = [], 0, "", time.time()
        elif typ == "assistant":
            f.cur.append(ev.get("text", ""))
            f.last_text = ev.get("text", "") or f.last_text
            await self.status(f, "💬 " + fmt.clip(ev.get("text", ""), 120))
        elif typ == "thinking":
            await self.status(f, "🤔 …")
        elif typ == "tool_use":
            f.steps += 1
            f.cur = []
            await self.status(f, "🔧 " + fmt.clip(ev.get("summary") or ev.get("name", ""), 120))
        elif typ == "note" and ev.get("level") == "error":
            await self.rest.send(f.channel, "⚠ " + fmt.clip(ev.get("text", ""), 500))
        elif typ == "result":
            await self.finish(f, ev)

    async def status(self, f: Follower, line: str) -> None:
        if self.cfg.get("reply_mode") != "live":
            return
        now = time.time()
        body = f"⏳ {self.t('working')} ({self.t('steps', n=f.steps)})\n{line}"
        try:
            if not f.status_id:
                f.status_id = (await self.rest.send(f.channel, body, silent=True))["id"]
                f.status_at = now
            elif now - f.status_at >= STATUS_EVERY:
                await self.rest.edit(f.channel, f.status_id, body)
                f.status_at = now
        except DiscordError:
            pass

    async def finish(self, f: Follower, ev: dict) -> None:
        secs = ev.get("duration_ms", 0) / 1000
        ok = ev.get("ok", True)
        if f.status_id:
            summary = f"{'✅' if ok else '⚠'} {self.t('done') if ok else self.t('failed')} · {self.t('steps', n=f.steps)} · {secs:.0f}s · {ev.get('cost_usd', 0):.2f} $"
            try:
                await self.rest.edit(f.channel, f.status_id, summary)
            except DiscordError:
                pass
            f.status_id = ""
        text = "\n\n".join(x for x in f.cur if x.strip()) or f.last_text
        if not ok and not text:
            text = "⚠ " + self.t("failed")
        sent = None
        for part in fmt.chunk(text):
            sent = await self.rest.send(f.channel, part)
            self.remember(sent["id"], f.sid)
        f.cur, f.steps = [], 0
        f.expect = max(0, f.expect - 1)
        await self.retitle(f)

    def remember(self, mid: str, sid: str) -> None:
        self.msg_session[mid] = sid
        while len(self.msg_session) > 500:
            self.msg_session.popitem(last=False)

    async def retitle(self, f: Follower) -> None:
        link = self.store.data["threads"].get(f.sid)
        if not link or link.get("titled"):
            return
        try:
            s = await self.h.session(f.sid)
        except HarnessError:
            return
        if s.get("title"):
            name = f"{link['path'].split('projects/', 1)[-1].replace('/topics/', '/')} · {s['title']}"
            try:
                await self.rest.rename(link["channel"], name)
            except DiscordError:
                return
            link["titled"] = True
            self.store.save()

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
        if kind in ("pick", "cont") and not p:
            return await self.rest.respond(i, 7, self.t("expired"), components=[])
        if kind == "cont":
            sid = self.store.data.get("dm_current")
            self.pending.pop(pid, None)
            await self.rest.respond(i, 7, "↩", components=[])
            return await self.forward_pending(sid, p)
        if kind == "pick":
            v = i["data"]["values"][0]
            if v.startswith("p:"):
                proj = v[2:]
                topics = [x for x in await self.topics() if x["project"] == proj]
                return await self.rest.respond(i, 7, self.t("where"), components=self.menu(pid, topics))
            self.pending.pop(pid, None)
            label = v.split("projects/", 1)[-1].replace("/topics/", "/")
            await self.rest.respond(i, 7, f"✅ {label}", components=[])
            return await self.start(p, v)
        if kind == "sess":                                     # a session chosen in /sessions
            await self.rest.respond(i, 7, self.t("sessions_pick"), components=[])
            return await self.open_session(i["data"]["values"][0], i["channel_id"], not i.get("guild_id"))

    async def forward_pending(self, sid: str, p: Pending) -> None:
        fake = {"id": p.message, "content": p.text, "attachments": p.files}
        await self.forward(sid, fake, p.channel)

    def current_session(self, i: dict) -> str | None:
        return self.store.session_of_channel(i["channel_id"]) or (self.store.data.get("dm_current") if not i.get("guild_id") else None)

    async def on_command(self, i: dict) -> None:
        name = i["data"]["name"]
        opts = {o["name"]: o.get("value") for o in i["data"].get("options", [])}
        ch, in_dm = i["channel_id"], not i.get("guild_id")
        if name == "help":
            return await self.rest.respond(i, 4, self.t("help"), ephemeral=True)
        if name == "new":
            return await self.cmd_new(i, ch, in_dm, opts.get("title", ""))
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
        except HarnessError as e:
            await reply(self.t("err", e=e))

    async def cmd_new(self, i: dict, ch: str, in_dm: bool, title: str) -> None:
        sid = self.store.session_of_channel(ch)
        pid = secrets.token_hex(4)
        self.purge()
        if sid:                                                # inside a session's thread: same topic, new session
            path = self.store.data["threads"][sid]["path"]
            main = self.main or ch
            await self.rest.respond(i, 5, ephemeral=True)
            p = Pending(text="", files=[], channel=main, message="", in_dm=False, title=title)
            await self.start(p, path)
            return await self.rest.followup(i, self.gw.app_id, self.t("started", path=path.split("projects/", 1)[-1].replace("/topics/", "/")), ephemeral=True)
        if not in_dm and ch != self.main:
            return await self.rest.respond(i, 4, self.t("no_session"), ephemeral=True)
        self.pending[pid] = Pending(text="", files=[], channel=ch, message="", in_dm=in_dm, title=title)
        topics = await self.topics()
        await self.rest.respond(i, 4, self.t("where"), components=self.menu(pid, topics, projects=len(topics) > 25))

    async def cmd_sessions(self, i: dict, reply) -> None:
        items = [s for s in await self.h.sessions() if not s.get("archived") and not s.get("cron")][:25]
        if not items:
            return await reply(self.t("sessions_none"))
        opts = [{"label": fmt.clip(s.get("title") or "…", 100), "value": s["id"],
                 "description": fmt.clip(s["path"].split("projects/", 1)[-1].replace("/topics/", "/"), 100)} for s in items]
        comps = [{"type": 1, "components": [{"type": 3, "custom_id": "sess:x", "placeholder": self.t("sessions_ph"), "options": opts}]}]
        await self.rest.send(i["channel_id"], self.t("sessions_pick"), components=comps)
        await reply("↓")

    async def open_session(self, sid: str, channel: str, in_dm: bool) -> None:
        try:
            s = await self.h.session(sid)
        except HarnessError as e:
            return await self.rest.send(channel, self.t("err", e=e))
        if in_dm:
            self.store.set_dm(sid, channel)
            return await self.rest.send(channel, self.t("opened", title=s.get("title") or "…", where=self.t("hint_dm")))
        th = self.store.channel_of(sid)
        if not th:
            name = f"{s['path'].split('projects/', 1)[-1].replace('/topics/', '/')} · {s.get('title') or '…'}"
            th = (await self.rest.start_thread(self.main or channel, name))["id"]
            self.store.link(sid, th, s["path"])
        await self.rest.send(channel, self.t("opened", title=s.get("title") or "…", where=f"<#{th}>"))
