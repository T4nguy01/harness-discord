"""Tool-call approvals: the requests an agent waits on (Harness: GET /api/approvals, POST /api/approvals/{id}) as an orange
embed with Allow / Deny / Always allow / Deny with a reason, in the thread of the session (or the alerts channel).

It is the same queue as the web UI and Telegram: the first answer wins, the others get a 409 and see how it ended.
Never stored or logged: the content of a request (only tool name, id and decision go to the audit trail)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from . import fmt
from .discord_api import DiscordError
from .harness import HarnessError

log = logging.getLogger("harness_discord.approvals")

HERR = (HarnessError, httpx.HTTPError)
RID = re.compile(r"[0-9a-f]{16}", re.ASCII)
CID = re.compile(r"ap:([ynarc]):([0-9a-f]{16})", re.ASCII)      # y allow, n deny, a always (asks), r reason (form), c confirm always
MODAL = re.compile(r"apm:([0-9a-f]{16})", re.ASCII)
ORANGE, GREEN, RED, GREY = 0xE67E22, 0x57F287, 0xED4245, 0x95A5A6
EXCERPT = 1500
MAX_TRIES = 3
KEEP = 100
PRIMARY = ("command", "file_path", "notebook_path", "path", "url", "pattern", "query", "prompt", "description")
MINOR = {"Bash": {"description", "timeout", "run_in_background"}, "Read": {"offset", "limit"}}

# obvious secrets: name=value pairs, --password value flags and well-known token shapes
_NAMED = re.compile(r"(?i)\b([\w.-]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|apikey|access[_-]?key|private[_-]?key|auth(?:orization)?|credential)[\w.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)")
_FLAG = re.compile(r"(?i)(--?(?:[\w-]*(?:token|secret|passw(?:or)?d|pwd|api-?key)[\w-]*))(\s+)(?!-)(\"[^\"]*\"|'[^']*'|\S+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]{8,}")
_SHAPES = re.compile(r"\b(?:sk-[\w-]{16,}|gh[pousr]_\w{20,}|github_pat_\w{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}|AIza[\w-]{30,}"
                     r"|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}|(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{40,})\b")


def mask(text: str) -> str:
    """Hide what looks like a secret (token=..., --password x, Bearer ..., ghp_..., long random strings)."""
    text = _BEARER.sub(lambda m: f"{m[1]} ***", text)
    text = _NAMED.sub(lambda m: f"{m[1]}{m[2]}***", text)
    text = _FLAG.sub(lambda m: f"{m[1]}{m[2]}***", text)
    return _SHAPES.sub("***", text)


def excerpt(item: dict) -> tuple[str, bool]:
    """(masked text of the request, whether it is the whole request). Anything left out makes Allow unavailable."""
    inp = item.get("input") if isinstance(item.get("input"), dict) else {}
    tool = str(item.get("tool") or "")
    rest: set = set()
    for k in PRIMARY:
        if isinstance(inp.get(k), str) and inp[k].strip():
            text = inp[k].strip()
            rest = {o for o, v in inp.items() if o != k and v not in (None, "", [], {})} - MINOR.get(tool, set())
            break
    else:
        text = json.dumps(inp, ensure_ascii=False, indent=1) if inp else ""
    text = mask(text)
    whole = not rest and len(text) <= EXCERPT and not item.get("clipped")
    return (text if len(text) <= EXCERPT else text[: EXCERPT - 1] + "…"), whole


class Approvals:
    def __init__(self, bot: Any):
        self.bot = bot
        self.deciding: set[str] = set()                 # being answered from here: the poller leaves them alone
        self.embeds: dict[str, dict] = {}               # rid -> the embed as posted (memory only: the request text is not saved)
        self.tries: dict[str, int] = {}

    @property
    def st(self) -> dict:
        return self.bot.store.data.setdefault("approvals", {})

    def waiting(self, sid: str) -> int:
        return sum(1 for v in self.st.values() if not v.get("done") and v.get("session") == sid and sid)

    def active(self) -> bool:
        return bool(self.bot.followers) or any(not v.get("done") for v in self.st.values())

    # --- poller ------------------------------------------------------------------------------------------------------

    async def run(self) -> None:
        await self.bot.gw.ready.wait()
        while True:
            try:
                await self.tick()
            except (HarnessError, DiscordError) as e:
                log.warning("approvals: %s", e)
            except httpx.HTTPError as e:
                log.warning("approvals: Harness unreachable (%s)", type(e).__name__)
            except Exception:  # noqa: BLE001
                log.exception("approvals")
            await asyncio.sleep(3 if self.active() else 10)

    async def tick(self) -> None:
        b = self.bot
        if not b.cfg.get("tool_approvals", True):
            return
        d = await b.h.request("GET", "/api/approvals")
        live = {x["id"]: x for x in d.get("approvals", []) if isinstance(x, dict) and RID.fullmatch(str(x.get("id") or ""))
                and x.get("status", "pending") == "pending"}
        st = self.st
        for rid, item in live.items():
            if rid not in st:
                await self.post(rid, item)
        for rid, info in list(st.items()):
            if not info.get("done") and rid not in live and rid not in self.deciding:
                await self.finish(rid, "gone", "", "")
        for k in [k for k, v in st.items() if v.get("done")][:-KEEP]:
            del st[k]
            self.embeds.pop(k, None)

    def channel_for(self, item: dict) -> str | None:
        b = self.bot
        sid = str(item.get("session") or "")
        return (b.store.channel_of(sid) if sid else None) or b.relay.st["agents"].get(str(item.get("agent_path") or ""), {}).get("thread") \
            or b.lay.get("alerts")

    def build(self, rid: str, item: dict, ch: str) -> tuple[dict, list[dict], bool]:
        b = self.bot
        t = b.t
        tool = str(item.get("tool") or "?")[:60]
        text, whole = excerpt(item)
        desc = "```\n" + text.replace("```", "'''") + "\n```" if text else ""
        if not whole:
            desc += "\n" + t("ap_clipped" if item.get("clipped") else "ap_cut")
        sid = str(item.get("session") or "")
        link = f"<#{ch}>" if b.store.channel_of(sid) == ch else (sid[:12] or "–")
        fields = [{"name": t("ap_f_session"), "value": link, "inline": True}]
        rule = str(item.get("always_rule") or "")
        if isinstance(item.get("expires"), (int, float)) and item["expires"] > 1e9:
            fields.append({"name": t("ap_f_expires"), "value": f"<t:{int(item['expires'])}:R>", "inline": True})
        embed = {"title": "🔐 " + fmt.clip(t("ap_title", tool=tool), 240), "color": ORANGE, "description": desc, "fields": fields,
                 "footer": {"text": rid[:8]}}
        row = []
        if whole:
            row.append({"type": 2, "style": 3, "label": "✅ " + t("ap_allow"), "custom_id": f"ap:y:{rid}"})
        row.append({"type": 2, "style": 4, "label": "❌ " + t("ap_deny"), "custom_id": f"ap:n:{rid}"})
        if whole and rule:
            row.append({"type": 2, "style": 2, "label": "🔁 " + t("ap_always"), "custom_id": f"ap:a:{rid}"})
        row.append({"type": 2, "style": 2, "label": "💬 " + t("ap_reason"), "custom_id": f"ap:r:{rid}"})
        comps = [{"type": 1, "components": row}]
        if not whole and str(b.web_url).startswith(("http://", "https://")):
            comps[0]["components"].append({"type": 2, "style": 5, "label": "🌐 " + t("open_harness"), "url": b.web_url})
        return embed, comps, whole

    async def post(self, rid: str, item: dict) -> None:
        b = self.bot
        ch = self.channel_for(item)
        if not ch:
            return
        embed, comps, whole = self.build(rid, item, ch)
        try:
            m = await b.rest.send(ch, "", embeds=[embed], components=comps)
        except DiscordError as e:
            n = self.tries[rid] = self.tries.get(rid, 0) + 1
            if e.transient and n < MAX_TRIES:
                return log.warning("approval %s: %s (attempt %d/%d)", rid, e, n, MAX_TRIES)
            log.warning("approval %s not posted: %s", rid, e)
            self.st[rid] = {"done": True, "failed": True, "session": "", "tool": ""}
            return b.store.save()
        self.tries.pop(rid, None)
        self.embeds[rid] = embed
        self.st[rid] = {"channel": ch, "message": m["id"], "session": str(item.get("session") or ""), "tool": str(item.get("tool") or "")[:60],
                        "rule": str(item.get("always_rule") or "") if whole else "", "done": False}
        b.store.save()
        await self.mark_waiting(str(item.get("session") or ""))

    async def mark_waiting(self, sid: str) -> None:
        """The live status line of a session being followed says it waits for an approval."""
        f = self.bot.followers.get(sid)
        if f and self.bot.cfg.get("reply_mode") == "live":
            f.status_at = 0.0
            await self.bot.status(f, "🔐 " + self.bot.t("ap_waiting"))

    async def finish(self, rid: str, decision: str, who: str, reason: str, always: bool = False) -> None:
        """Show how a request ended (green / red / grey) and take its buttons away."""
        b, t = self.bot, self.bot.t
        info = self.st.get(rid)
        if not info or info.get("done"):
            return
        info["done"] = True
        embed = dict(self.embeds.pop(rid, None) or {"title": "🔐 " + t("ap_title", tool=info.get("tool") or "?"), "description": ""})
        if decision == "allow":
            color, line = GREEN, t("ap_always_done" if always else "ap_allowed", who=who)
        elif decision == "deny":
            color, line = RED, t("ap_denied", who=who)
        else:
            color, line = GREY, t("ap_gone")
        fields = [f for f in embed.get("fields", []) if f.get("name") != t("ap_f_expires")]
        fields.append({"name": ("✅" if decision == "allow" else "❌" if decision == "deny" else "✓"), "value": line})
        if reason:
            fields.append({"name": t("ap_reason_f"), "value": fmt.clip(reason, 500)})
        embed.update(color=color, fields=fields)
        b.store.save()
        try:
            await b.rest.edit(info["channel"], info["message"], embeds=[embed], components=[])
        except DiscordError as e:
            log.warning("approval %s edit: %s", rid, e)

    # --- clicks and forms (owner only: Bot.on_interaction checks it) ---------------------------------------------------

    @staticmethod
    def who(i: dict) -> str:
        u = (i.get("member") or {}).get("user") or i.get("user") or {}
        return f"<@{u['id']}>" if re.fullmatch(r"\d{1,20}", str(u.get("id", ""))) else "?"

    async def stale(self, i: dict) -> None:
        await self.bot.rest.respond(i, 4, self.bot.t("ap_stale"), ephemeral=True)

    async def on_component(self, i: dict) -> None:
        b = self.bot
        m = CID.fullmatch(i["data"]["custom_id"])
        if not m:
            return await self.stale(i)
        act, rid = m[1], m[2]
        info = self.st.get(rid)
        if not info or info.get("done"):
            await self.stale(i)
            mid = (i.get("message") or {}).get("id")
            if mid and act != "c":                                  # the buttons of an answered / unknown request go away
                try:
                    await b.rest.edit(i["channel_id"], mid, components=[])
                except DiscordError:
                    pass
            return
        if act != "c" and (i.get("message") or {}).get("id") != info["message"]:
            return await self.stale(i)
        who = self.who(i)
        app = b.gw.app_id
        if act == "r":
            return await b.rest.modal(i, f"apm:{rid}", b.t("ap_modal"), [{"type": 1, "components": [
                {"type": 4, "custom_id": "reason", "label": b.t("ap_modal_f")[:45], "style": 2, "required": False, "max_length": 500,
                 "placeholder": b.t("ap_modal_ph")[:100]}]}])
        if act == "a":
            if not info.get("rule"):
                return await b.rest.respond(i, 4, b.t("ap_no_rule"), ephemeral=True)
            return await b.rest.respond(i, 4, b.t("ap_confirm_always", rule=fmt.clip(info["rule"], 200)), ephemeral=True, components=[
                {"type": 1, "components": [{"type": 2, "style": 4, "label": b.t("ap_confirm_btn"), "custom_id": f"ap:c:{rid}"}]}])
        if act == "c":
            await b.rest.respond(i, 7, "⏳", components=[])
            msg = await self.decide(rid, "allow", who, always=True)
            return await b.rest.edit_original(i, app, msg or "✅", components=[])
        await b.rest.respond(i, 6)                                   # acknowledge now, the answer is edited into the message
        msg = await self.decide(rid, "allow" if act == "y" else "deny", who)
        if msg:
            await b.rest.followup(i, app, msg, ephemeral=True)

    async def on_modal(self, i: dict) -> None:
        b = self.bot
        m = MODAL.fullmatch(i["data"].get("custom_id") or "")
        info = self.st.get(m[1]) if m else None
        if not m or not info or info.get("done"):
            return await self.stale(i)
        reason = ""
        for row in i["data"].get("components") or []:
            for c in row.get("components") or []:
                if isinstance(c, dict) and c.get("custom_id") == "reason":
                    reason = " ".join(str(c.get("value") or "").split())[:500]
        await b.rest.respond(i, 6)
        msg = await self.decide(m[1], "deny", self.who(i), reason)
        if msg:
            await b.rest.followup(i, b.gw.app_id, msg, ephemeral=True)

    async def decide(self, rid: str, decision: str, who: str, reason: str = "", always: bool = False) -> str | None:
        """Send the answer to Harness. None when it worked, else the (short) text to show privately."""
        b = self.bot
        info = self.st.get(rid) or {}
        body: dict = {"decision": decision}
        if reason:
            body["message"] = reason
        if always:
            body["always"] = True
        self.deciding.add(rid)
        try:
            try:
                await b.h.request("POST", f"/api/approvals/{rid}", json=body)
            except HarnessError as e:
                if e.status == 409 and "clipped" in f"{e.code} {e.message}":
                    return b.t("ap_clipped")
                if e.status in (404, 409):                           # answered elsewhere, timed out, session stopped
                    b.audit.log("approval", f"{info.get('tool', '')} {rid}", "stale")
                    await self.finish(rid, "gone", "", "")
                    return b.t("ap_stale")
                if e.status == 400 and always:
                    return b.t("ap_no_rule")
                b.audit.log("approval", f"{info.get('tool', '')} {rid}", f"error {e.status}")
                return b.err(e)
            except httpx.HTTPError as e:
                return b.err(e)
            b.audit.log("approval", f"{info.get('tool', '')} {rid}", "always" if always else decision + ("+reason" if reason else ""))
            await self.finish(rid, decision, who, reason, always)
            return None
        finally:
            self.deciding.discard(rid)
