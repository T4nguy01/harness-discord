"""Harness -> Discord: the notifications feed (alerts, approvals, job reports...) and the results of agents."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from . import __version__, config

from . import fmt
from .bell import Bell
from .dashboard import Dashboard, web_link
from .notifier import Notifier
from .discord_api import DiscordError
from .harness import HarnessError

log = logging.getLogger("harness_discord.relay")

SAFE_ID = re.compile(r"[\w-]{1,32}")
LEVEL = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "🛑"}
STYLE = {"primary": 1, "default": 2, "danger": 4}
QUIET = ("info", "success")
MAX_TRIES = 3              # a feed event whose posting keeps failing for a transient reason is skipped after that many ticks
MAX_PARTS = 5              # an agent result longer than that many Discord messages is cut


def route(e: dict) -> str:
    """Which channel (key of the layout) an event goes to."""
    if e.get("agent") or e.get("source") == "agents":
        return "agents"
    if e.get("source") == "crons":
        return "crons"
    if e.get("source") in ("security", "system", "health", "backup", "telegram") or e.get("actions"):
        return "alerts"
    return "notifications"


COLOR = {"info": 0x5865F2, "success": 0x57F287, "warning": 0xFEE75C, "error": 0xED4245}
DONE_COLOR = 0x95A5A6


def render(e: dict, web_url: str) -> tuple[dict, list[dict]]:
    """A feed event as a Discord embed (colour = level) plus its action buttons."""
    done = bool(e.get("resolved"))
    embed: dict = {"title": f"{LEVEL.get(e.get('level'), 'ℹ️')} {fmt.clip(e.get('title', ''), 240)}",
                   "color": DONE_COLOR if done else COLOR.get(e.get("level"), COLOR["info"])}
    body = (e.get("body") or "").strip()
    if body:
        embed["description"] = fmt.clip_block(body, 3500)
    if done:
        embed["fields"] = [{"name": "✓", "value": fmt.clip(e.get("resolution") or "✓", 500)}]
    foot = (e.get("agent") or e.get("source") or "").split("/")[-1]
    if e.get("count", 1) > 1:
        foot += f" · ×{e['count']}"
    if foot.strip(" ·"):
        embed["footer"] = {"text": foot[:200]}
    if isinstance(e.get("at"), (int, float)) and e["at"] > 1e9:
        embed["timestamp"] = datetime.fromtimestamp(e["at"], timezone.utc).isoformat()
    buttons = []
    for a in e.get("actions") or []:
        if not SAFE_ID.fullmatch(str(a.get("id", ""))):
            continue
        label = str(a.get("label") or a["id"])[:80]
        if a["id"] == "open":
            url = e.get("url") or ""
            url = web_url + url if url.startswith("/") else url
            if url.startswith(web_url + "/"):                       # only links into Harness itself
                buttons.append({"type": 2, "style": 5, "label": label, "url": url})
            continue
        buttons.append({"type": 2, "style": STYLE.get(a.get("style"), 2), "label": label,
                        "custom_id": f"fa:{e['id']}:{a['id']}"})
    comps = [{"type": 1, "components": buttons[:5]}] if buttons else []
    return embed, comps


class Relay:
    def __init__(self, bot: Any):
        self.bot = bot
        self.n = 0
        self.tries: dict[int, int] = {}
        self.bell = Bell(bot)
        self.dashboard = Dashboard(bot, self)
        self.notifier = Notifier(bot)
        self._sessions: tuple[float, list] = (0.0, [])
        self.presence_at = 0.0
        self.presence_n: int | None = None

    @property
    def st(self) -> dict:
        return self.bot.store.data.setdefault("relay", {"feed_last": None, "posted": {}, "agents": {}})

    async def run(self) -> None:
        await self.bot.gw.ready.wait()
        while True:
            await self.tick()
            await asyncio.sleep(10)

    async def tick(self) -> None:
        """One pass. Each part is isolated: a failure in one never blocks the others."""
        async def feed() -> None:
            self.reload()
            await self.flags()
            if self.bot.cfg.get("relay_feed", True):
                await self.feed()

        async def bell() -> None:
            if self.bot.cfg.get("relay_bell", True):
                await self.bell.poll()

        async def agents() -> None:
            if self.n % 3 == 0 and self.bot.cfg.get("relay_agents", True):
                await self.agents()

        for name, part in (("feed", feed), ("bell", bell), ("agents", agents), ("presence", self.presence),
                           ("dashboard", self.dashboard.tick), ("notifier", self.notifier.tick)):
            try:
                await part()
            except (HarnessError, DiscordError) as e:
                log.warning("relay %s: %s", name, e)
            except httpx.HTTPError as e:                      # Harness down or restarting: one short line, no traceback
                log.warning("relay %s: Harness unreachable (%s)", name, type(e).__name__)
            except Exception:  # noqa: BLE001
                log.exception("relay %s", name)
        if self.n % 3 == 0:
            try:
                self.write_status()
            except Exception:  # noqa: BLE001
                log.exception("relay status")
        self.n += 1

    async def sessions_cached(self, max_age: float = 30) -> list[dict]:
        """GET /api/sessions, shared by the presence and the dashboard (at most one call per `max_age` seconds)."""
        at, items = self._sessions
        if time.time() - at >= max_age:
            items = await self.bot.h.request("GET", "/api/sessions")
            self._sessions = (time.time(), items)
        return items

    async def presence(self) -> None:
        """The bot's status line: "N sessions running" / "idle". At most once per 30 s (Discord allows 5 per 20 s), only when it changed."""
        if time.time() - self.presence_at < 30:
            return
        n = sum(1 for s in await self.sessions_cached(30) if s.get("state") == "running")
        if n == self.presence_n:
            return
        t = self.bot.t
        text = t("presence_idle") if n == 0 else t("presence_1") if n == 1 else t("presence_n", n=n)
        await self.bot.gw.update_presence(text, "online" if n else "idle")
        self.presence_n, self.presence_at = n, time.time()

    def reload(self) -> None:
        """Settings changed in the Harness page apply without a restart."""
        new = config.load_settings()
        if not new["owner_id"]:                                  # never go from an owner to nobody on a bad read
            new["owner_id"] = self.bot.owner
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
            try:
                ch = self.channel_for(e)
                if ch and not e.get("resolved"):
                    embed, comps = render(e, self.bot.web_url)
                    m = await self.bot.rest.send(ch, "", embeds=[embed], components=comps or None,
                                                 silent=e.get("level") in QUIET)
                    st["posted"][str(e["id"])] = {"channel": ch, "message": m["id"], "done": bool(e.get("resolved")),
                                                  "buttons": any("custom_id" in c for row in comps for c in row["components"])}
                    st["last_post"] = time.time()
            except DiscordError as err:
                if err.transient:
                    n = self.tries[e["id"]] = self.tries.get(e["id"], 0) + 1
                    if n < MAX_TRIES:                            # try again on the next tick, in order
                        log.warning("feed %s: %s (attempt %d/%d)", e["id"], err, n, MAX_TRIES)
                        break
                    log.warning("feed %s: %s - giving up", e["id"], err)
                else:
                    log.warning("feed %s: %s", e["id"], err)
            except Exception:  # noqa: BLE001                    # a malformed event must not block the ones after it
                log.exception("feed %s skipped", e.get("id"))
            self.tries.pop(e["id"], None)
            st["feed_last"] = e["id"]
        for eid, info in list(st["posted"].items()):             # an alert handled elsewhere: show how it ended
            e = byid.get(int(eid))
            read_only = (e and e.get("read") and info.get("buttons") and not info.get("done") and not e.get("resolved")
                         and not str(e.get("ref") or "").startswith("approval:"))        # "seen" on the web: its button goes
            if e and (e.get("resolved") or read_only) and not info.get("done"):
                try:
                    embed, comps = render(e, self.bot.web_url)
                    if read_only:
                        embed["color"] = DONE_COLOR
                        embed["fields"] = [{"name": "✓", "value": self.bot.t("seen")}]
                    await self.bot.rest.edit(info["channel"], info["message"], embeds=[embed], components=[])
                except DiscordError:
                    pass
                except Exception:  # noqa: BLE001
                    log.exception("feed edit %s", eid)
                info["done"] = True
        for k in sorted(st["posted"], key=int)[:-150]:
            del st["posted"][k]
        self.bot.store.save()

    async def act(self, eid: int, action: str) -> dict:
        if not SAFE_ID.fullmatch(action):
            raise HarnessError(400, "bad_action")
        d = await self.bot.h.request("POST", f"/api/feed/{eid}/act/{action}")
        return d.get("event", d)

    # --- agents ------------------------------------------------------------------------------------------------

    async def agents(self) -> None:
        d = await self.bot.h.request("GET", "/api/agents")
        for a in d.get("agents", []):
            try:
                await self._agent(a)
            except DiscordError as e:
                info = self.st["agents"].get(a.get("path", ""))
                if info and info.get("thread") and (e.status == 404 or (isinstance(e.body, dict) and e.body.get("code") == 10003)):
                    log.warning("agent thread %s is gone: a new one is made on the next result", info["thread"])
                    info["thread"] = ""                           # deleted by hand
                else:
                    log.warning("agent %s: %s", a.get("path"), e)
            except HarnessError:
                continue
            except Exception:  # noqa: BLE001
                log.exception("agent %s", a.get("path"))
        self.bot.store.save()

    async def _agent(self, a: dict) -> None:
        st = self.st["agents"]
        path = a["path"]
        p, t, name = a["project"], a["topic"], a["name"]
        th = await self.bot.h.request("GET", f"/api/agents/{p}/{t}/{name}/thread", params={"limit": 10})
        msgs = [m for m in th.get("messages", []) if m.get("kind") == "result"]
        info = st.setdefault(path, {"seen": max([m["at"] for m in th.get("messages", [])], default=time.time()), "thread": ""})
        fresh = [m for m in msgs if m["at"] > info["seen"]]
        if not fresh:
            return
        if not info["thread"]:
            ch = self.bot.lay.get("agents")
            if not ch:
                return
            label = f"{p}/{t} · {a.get('display_name') or name}"
            info["thread"] = (await self.bot.rest.start_thread(ch, label[:100]))["id"]
            self.bot.store.save()
        for m in sorted(fresh, key=lambda x: x["at"]):
            ok = m.get("ok") is not False
            parts = fmt.chunk(m.get("text") or "…", 4000)
            cut = len(parts) > MAX_PARTS
            parts = parts[:MAX_PARTS]
            if cut:
                parts[-1] = parts[-1] + "\n\n" + self.bot.t("truncated")
            for n, part in enumerate(parts):
                embed: dict = {"description": part, "color": COLOR["success"] if ok else COLOR["error"]}
                if n == len(parts) - 1 and m.get("cost"):
                    embed["footer"] = {"text": f"{m['cost']:.2f} $"}
                link = web_link(self.bot) if n == len(parts) - 1 else None            # "Open in Harness" under the last part
                sent = await self.bot.rest.send(info["thread"], "", embeds=[embed], silent=False, components=link)
                self.bot.remember(sent["id"], "agent:" + path)
                self.st["last_post"] = time.time()
            info["seen"] = m["at"]
            self.bot.store.save()                                 # posted: never again after a restart

    def agent_of_thread(self, channel: str) -> str:
        for path, info in self.st["agents"].items():
            if info.get("thread") == str(channel):
                return path
        return ""

    async def run_agent(self, path: str, text: str, reply_ref: str = "") -> None:
        _, p, _, t, _, name = path.split("/")
        body: dict = {"text": text}
        await self.bot.h.request("POST", f"/api/agents/{p}/{t}/{name}/run", json=body)
