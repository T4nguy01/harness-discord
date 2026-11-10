"""Following a session: stream its events and mirror them to Discord (status line, answer, retitle)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from . import fmt
from .discord_api import DiscordError
from .harness import SID, HarnessError, check_sid

log = logging.getLogger("harness_discord.follow")

STATUS_EVERY = 1.5
HERR = (HarnessError, httpx.HTTPError)
FOLLOW_RESUME_MAX = 1800          # a follower saved longer ago than this is not resumed after a restart (seconds)
FOLLOW_GIVE_UP = 300              # a stream failing for this long is abandoned
FOLLOW_MAX_FAILS = 10             # ... or after this many failures in a row
FOLLOW_SAVE_EVERY = 5.0


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
    fails: int = 0                                     # consecutive stream failures
    last_ok: float = field(default_factory=time.time)  # last time the stream answered
    saved_at: float = 0.0
    marks: list = field(default_factory=list)          # [(channel, message)] of the user messages waiting for their final reaction



class FollowMixin:
    """Turn following for `Bot` (uses its h, rest, store, cfg, followers, follow_end, msg_session, t, err, thread_name)."""

    async def react_state(self, mark: tuple[str, str] | None, old: str, new: str) -> None:
        """Status reaction on the user's message: remove `old`, add `new` (setting `reactions`)."""
        if not mark or not mark[1] or not self.cfg.get("reactions", True):
            return
        if old:
            await self.rest.unreact(mark[0], mark[1], old)
        if new:
            await self.rest.react(mark[0], mark[1], new)

    async def follow(self, sid: str, channel: str, offset: int, expect: int, mark: tuple[str, str] | None = None) -> None:
        f = self.followers.get(sid)
        if f:
            f.expect += expect
            f.active_at = time.time()
            if mark and mark[1]:
                f.marks.append(mark)
            self.persist(f, force=True)
            return
        f = Follower(sid=sid, channel=channel, offset=offset, expect=expect, started=time.time())
        if mark and mark[1]:
            f.marks.append(mark)
        self.followers[sid] = f
        self.persist(f, force=True)
        f.typing = asyncio.create_task(self._typing(f))
        f.task = asyncio.create_task(self._run(f))

    def persist(self, f: Follower | None, force: bool = False, sid: str = "") -> None:
        """Remember the running followers so that a restart picks them up again (None = forget `sid`)."""
        saved = self.store.data.setdefault("followers", {})
        if f is None:
            if saved.pop(sid, None) is not None:
                self.store.save()
            return
        now = time.time()
        if not force and now - f.saved_at < FOLLOW_SAVE_EVERY:
            return
        f.saved_at = now
        saved[f.sid] = {"channel": f.channel, "offset": f.offset, "expect": f.expect, "at": now, "marks": [list(m) for m in f.marks[:10]]}
        self.store.save()

    def resume_followers(self) -> None:
        saved = self.store.data.get("followers") or {}
        for sid, v in list(saved.items()):
            try:
                if time.time() - float(v["at"]) > FOLLOW_RESUME_MAX or sid in self.followers:
                    saved.pop(sid, None)
                    continue
                check_sid(sid)
                f = Follower(sid=sid, channel=str(v["channel"]), offset=int(v["offset"]), expect=max(1, int(v["expect"])), started=time.time())
                f.marks = [(str(a), str(b)) for a, b in (v.get("marks") or [])[:10]]
            except (KeyError, TypeError, ValueError, HarnessError):
                saved.pop(sid, None)
                continue
            self.followers[sid] = f
            f.typing = asyncio.create_task(self._typing(f))
            f.task = asyncio.create_task(self._run(f))
            log.info("following session %s again after a restart", sid)
        self.store.save()

    async def _typing(self, f: Follower) -> None:
        while True:
            await self.rest.typing(f.channel)
            await asyncio.sleep(8)

    def _failed(self, f: Follower, e: Exception) -> bool:
        """A stream failure. True when the follower must give up."""
        f.fails += 1
        if isinstance(e, HarnessError) and e.status in (403, 404):
            log.warning("stream %s: %s - session gone, not following it", f.sid, e)
            return True
        if f.fails >= FOLLOW_MAX_FAILS or time.time() - f.last_ok > FOLLOW_GIVE_UP:
            log.warning("stream %s: %s - giving up after %d failures", f.sid, e, f.fails)
            return True
        log.warning("stream %s: %s", f.sid, e)
        return False

    async def _run(self, f: Follower) -> None:
        try:
            while f.expect > 0:
                try:
                    it = self.h.stream(f.sid, f.offset).__aiter__()
                    while f.expect > 0:
                        try:
                            kind, data = await asyncio.wait_for(it.__anext__(), 20)
                        except asyncio.TimeoutError:
                            f.last_ok = time.time()          # connected, just quiet
                            if f.state == "idle" and time.time() - f.active_at > 45:
                                f.expect = 0              # nothing more is coming for the messages we counted
                            continue
                        except StopAsyncIteration:
                            await asyncio.sleep(1)           # the stream closed: do not spin while reconnecting
                            if time.time() - f.last_ok > FOLLOW_GIVE_UP:
                                f.expect = 0
                            break
                        f.active_at = f.last_ok = time.time()
                        f.fails = 0
                        if kind == "state":
                            f.state = data.get("state", "")
                        elif kind == "reset":
                            f.offset = await self.h.tail(f.sid)
                            break
                        elif kind == "events":
                            f.offset = data.get("next", f.offset)
                            for ev in data.get("events", []):
                                await self._event(f, ev)
                            self.persist(f)
                    await it.aclose()
                except HERR as e:
                    if self._failed(f, e):
                        f.expect = 0
                        break
                    await asyncio.sleep(3)
            await self.clear_marks(f)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("follower %s", f.sid)
        finally:
            if f.typing:
                f.typing.cancel()
            self.followers.pop(f.sid, None)
            self.persist(None, sid=f.sid)
            now = time.time()
            self.follow_end[f.sid] = now
            for k in [k for k, t in self.follow_end.items() if now - t > 3600]:     # the bell only looks at recent ends
                del self.follow_end[k]

    async def clear_marks(self, f: Follower) -> None:
        """The follower ends (stop, give-up) with messages still marked as waiting: take the hourglass off."""
        marks, f.marks = f.marks, []
        for mark in marks:
            try:
                await self.react_state(mark, "⏳", "")
            except DiscordError:
                pass

    async def clear_buttons(self, sid: str) -> None:
        """The answer buttons of the previous turn go away when the next turn starts."""
        old = self.answer_msg.pop(sid, None)
        if old:
            try:
                await self.rest.edit(old[0], old[1], components=[])
            except DiscordError:
                pass

    def answer_buttons(self, sid: str, more: bool) -> list[dict] | None:
        """Stop (only while another turn is queued behind this answer) and Regenerate, under the final answer."""
        if not self.cfg.get("answer_buttons", True) or not SID.fullmatch(sid or ""):
            return None
        btns = []
        if more:
            btns.append({"type": 2, "style": 4, "label": "⏹ " + self.t("btn_stop"), "custom_id": f"ab:stop:{sid}"})
        btns.append({"type": 2, "style": 2, "label": "🔁 " + self.t("btn_regen"), "custom_id": f"ab:regen:{sid}"})
        return [{"type": 1, "components": btns}]

    async def _event(self, f: Follower, ev: dict) -> None:
        """One event of the session; whatever goes wrong with it, the next ones are still handled."""
        try:
            await self.on_event(f, ev)
        except asyncio.CancelledError:
            raise
        except (DiscordError, httpx.HTTPError, HarnessError) as e:
            log.warning("event %s of session %s: %s", ev.get("type"), f.sid, e)
        except Exception:  # noqa: BLE001
            log.exception("event %s of session %s", ev.get("type"), f.sid)

    async def on_event(self, f: Follower, ev: dict) -> None:
        typ = ev.get("type")
        if typ == "user":
            f.cur, f.steps, f.last_text, f.started = [], 0, "", time.time()
            await self.clear_buttons(f.sid)
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
        try:
            await self._finish(f, ev)
        finally:                                               # even if Discord failed, this turn is over
            f.cur, f.steps = [], 0
            f.expect = max(0, f.expect - 1)
            mark = f.marks.pop(0) if f.marks else None
        try:
            await self.react_state(mark, "⏳", "✅" if ev.get("ok", True) else "❌")
        except DiscordError:
            pass
        await self.retitle(f)

    async def _finish(self, f: Follower, ev: dict) -> None:
        secs = (ev.get("duration_ms") or 0) / 1000
        ok = ev.get("ok", True)
        if f.status_id:
            cost = ev.get("cost_usd") or 0
            summary = f"{'✅' if ok else '⚠'} {self.t('done') if ok else self.t('failed')} · {self.t('steps', n=f.steps)} · {secs:.0f}s · {cost:.2f} $"
            try:
                await self.rest.edit(f.channel, f.status_id, summary)
            except DiscordError:
                pass
            f.status_id = ""
        text = "\n\n".join(x for x in f.cur if x.strip()) or f.last_text
        if not ok and not text:
            text = "⚠ " + self.t("failed")
        parts = fmt.chunk(text)
        for n, part in enumerate(parts):
            comps = self.answer_buttons(f.sid, f.expect > 1) if n == len(parts) - 1 else None
            sent = await self.rest.send(f.channel, part, components=comps)
            self.remember(sent["id"], f.sid)
            if comps:
                self.answer_msg[f.sid] = (f.channel, sent["id"])

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
        except HERR:
            return
        if s.get("title"):
            try:
                await self.rest.rename(link["channel"], self.thread_name(link["path"], s["title"]))
            except DiscordError:
                return
            link["titled"] = True
            self.store.save()
