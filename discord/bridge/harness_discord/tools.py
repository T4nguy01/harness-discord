"""The /harness tools (tasks, jobs, agents, usage, health, search, backup, infra, queue, files, mail, audit) and the
forms (/new, task add). Everything is for the owner only; what lists private data answers privately (ephemeral); what runs
or changes something asks for a button confirmation first (short random ids, 2 minutes), except the explicit one-click
buttons (task status, mark a health issue seen, refresh infra), and every action is written to the audit trail.
Never offered: keys / secrets, restoring a backup, deleting anything, updating, restarting a service, sending mail directly."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from . import filesafe, fmt, views
from .discord_api import DiscordError
from .harness import HarnessError, seg
from .texts import T

log = logging.getLogger("harness_discord.tools")

HERR = (HarnessError, httpx.HTTPError)
CONFIRM_TTL = 120
CF = re.compile(r"cf:(y|n):([0-9a-f]{8})", re.ASCII)
TK = re.compile(r"tk:(todo|doing|done):([\w-]{1,40}):([otdxa])", re.ASCII)
HS = re.compile(r"hs:([0-9a-f]{12})", re.ASCII)
QC = re.compile(r"qc:([\w-]{1,64})", re.ASCII)
ML = re.compile(r"([a-z0-9][a-z0-9-]{0,40}):(\d{1,12})", re.ASCII)
MA = re.compile(r"ma:(seen|archive):([a-z0-9][a-z0-9-]{0,40}):(\d{1,12})", re.ASCII)
OB = re.compile(r"ob:(send|deny):([\w-]{1,40})", re.ASCII)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
CONFIRM_ACTIONS = ("cron_run", "cron_toggle", "agent_run", "backup_run", "queue_clear", "mail_seen", "mail_archive",
                   "outbox_send", "outbox_deny")
CTX_NEW_FORM = "nm"


@dataclass
class Confirm:
    action: str
    args: dict
    at: float = field(default_factory=time.time)


def flatten(options: list[dict]) -> tuple[list[str], dict]:
    """Slash command options -> (sub-command path, {option: value}) of the leaf."""
    path: list[str] = []
    while options and options[0].get("type") in (1, 2):
        path.append(options[0]["name"])
        options = options[0].get("options") or []
    return path, {o["name"]: o.get("value") for o in options}


def focused_option(options: list[dict]) -> tuple[list[str], dict | None]:
    path: list[str] = []
    while options and options[0].get("type") in (1, 2):
        path.append(options[0]["name"])
        options = options[0].get("options") or []
    return path, next((o for o in options if o.get("focused")), None)


def pick_named(arg: str, items: list[dict], idk: str = "id", namek: str = "name") -> dict | None:
    """An item typed or chosen: its id, its exact name, or part of a name when only one fits."""
    a = " ".join(str(arg or "").split()).lower()
    if not a:
        return None
    for key in (idk, namek):
        hit = [x for x in items if str(x.get(key, "")).lower() == a]
        if len(hit) == 1:
            return hit[0]
    part = [x for x in items if a in str(x.get(namek, "")).lower()]
    return part[0] if len(part) == 1 else None


def modal_values(i: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in i["data"].get("components") or []:
        for c in row.get("components") or []:
            if isinstance(c, dict) and "custom_id" in c:
                out[c["custom_id"]] = str(c.get("value") or "").strip()
    return out


def text_input(cid: str, label: str, *, paragraph: bool = False, required: bool = False, max_length: int = 100,
               placeholder: str = "", min_length: int = 0) -> dict:
    c: dict[str, Any] = {"type": 4, "custom_id": cid, "label": label[:45], "style": 2 if paragraph else 1, "required": required,
                         "max_length": max_length}
    if placeholder:
        c["placeholder"] = placeholder[:100]
    if min_length:
        c["min_length"] = min_length
    return {"type": 1, "components": [c]}


def agent_key(a: dict) -> str:
    return f"{a.get('project')}/{a.get('topic')}/{a.get('name')}"


class ToolsMixin:
    """Methods of `Bot` (uses its h, rest, gw, store, cfg, t, err, audit, current_session, ...)."""

    # --- plumbing ----------------------------------------------------------------------------------------------------

    def workspace_root(self) -> str:
        return str(self.cfg.get("workspace_dir") or os.environ.get("HD_WORKSPACE") or "/root/maxxing")

    async def show(self, i: dict, text: str = "", embeds: list | None = None, comps: list | None = None) -> None:
        await self.rest.edit_original(i, self.gw.app_id, text, components=comps or [], embeds=embeds or [])

    async def say(self, i: dict, text: str = "", embeds: list | None = None, comps: list | None = None) -> None:
        """A new private message after the interaction was acknowledged."""
        await self.rest.followup(i, self.gw.app_id, text, ephemeral=True, components=comps, embeds=embeds)

    def purge_confirms(self) -> None:
        now = time.time()
        for k in [k for k, v in self.confirms.items() if now - v.at > CONFIRM_TTL]:
            del self.confirms[k]

    async def ask(self, i: dict, action: str, args: dict, text: str, follow: bool = False) -> None:
        """Show `text` with Confirm / Cancel buttons; nothing happens before the click."""
        assert action in CONFIRM_ACTIONS
        self.purge_confirms()
        tok = secrets.token_hex(4)
        self.confirms[tok] = Confirm(action, args)
        comps = [{"type": 1, "components": [
            {"type": 2, "style": 4 if action in ("queue_clear", "outbox_deny") else 1, "label": self.t("cf_yes"), "custom_id": f"cf:y:{tok}"},
            {"type": 2, "style": 2, "label": self.t("cf_no"), "custom_id": f"cf:n:{tok}"}]}]
        text = fmt.clip_block(text, 1800) + "\n" + self.t("cf_hint")
        if follow:
            await self.say(i, text, comps=comps)
        else:
            await self.show(i, text, comps=comps)

    def done(self, cmd: str, target: str = "", result: str = "ok") -> None:
        self.audit.log(cmd, target, result)

    def short_error(self, e: Any) -> str:
        if isinstance(e, HarnessError):
            return f"error {e.status} {e.code}".strip()
        return "error"

    # --- /harness ----------------------------------------------------------------------------------------------------

    def tool_table(self) -> dict:
        return {"tasks": self.tool_tasks, "cron list": self.tool_cron_list, "cron run": self.tool_cron_run,
                "cron toggle": self.tool_cron_toggle, "agent run": self.tool_agent_run, "usage": self.tool_usage,
                "health": self.tool_health, "search": self.tool_search, "backup status": self.tool_backup_status,
                "backup run": self.tool_backup_run, "infra": self.tool_infra, "queue": self.tool_queue,
                "files ls": self.tool_files_ls, "files get": self.tool_files_get, "mail unread": self.tool_mail_unread,
                "mail outbox": self.tool_mail_outbox, "audit": self.tool_audit}

    async def cmd_harness(self, i: dict) -> None:
        path, opts = flatten(i["data"].get("options") or [])
        key = " ".join(path)
        if key == "task add":
            return await self.modal_task_add(i)
        fn = self.tool_table().get(key)
        if not fn:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        await self.rest.respond(i, 5, ephemeral=True)                      # deferred and private
        try:
            await fn(i, opts)
        except HERR as e:
            await self.show(i, self.err(e))
        except DiscordError as e:
            log.warning("/harness %s: %s", key, e)
            try:
                await self.show(i, self.t("err", e=f"discord {e.status}"))
            except DiscordError:
                pass

    # --- tasks -------------------------------------------------------------------------------------------------------

    async def tasks_message(self, f: str) -> tuple[dict, list[dict]]:
        return views.tasks_view(self.t, await self.h.tasks(), f, date.today().isoformat())

    async def tool_tasks(self, i: dict, opts: dict) -> None:
        f = views.FILTER_OF.get(str(opts.get("status") or "open"), "o")
        embed, rows = await self.tasks_message(f)
        await self.show(i, "", [embed], rows)

    async def modal_task_add(self, i: dict) -> None:
        t = self.t
        await self.rest.modal(i, "ta", t("ta_title"), [
            text_input("title", t("ta_f_title"), required=True, max_length=500),
            text_input("notes", t("ta_f_notes"), paragraph=True, max_length=2000),
            text_input("due", t("ta_f_due"), max_length=10, min_length=10, placeholder="YYYY-MM-DD"),
            text_input("priority", t("ta_f_prio"), max_length=1, placeholder="0 · 1 · 2 · 3")])

    async def submit_task_add(self, i: dict) -> None:
        v = modal_values(i)
        body: dict[str, Any] = {"title": v.get("title", "")[:500]}
        err = ""
        if not body["title"]:
            err = self.t("ta_bad_title")
        if v.get("notes"):
            body["notes"] = v["notes"][:2000]
        if v.get("due"):
            try:
                if not DATE.fullmatch(v["due"]):
                    raise ValueError
                date.fromisoformat(v["due"])
                body["due"] = v["due"]
            except ValueError:
                err = self.t("ta_bad_due")
        if v.get("priority"):
            if v["priority"] not in ("0", "1", "2", "3"):
                err = self.t("ta_bad_prio")
            else:
                body["priority"] = int(v["priority"])
        if err:
            return await self.rest.respond(i, 4, err, ephemeral=True)
        await self.rest.respond(i, 5, ephemeral=True)
        try:
            task = await self.h.create_task(body)
        except HERR as e:
            self.done("task.add", "", self.short_error(e))
            return await self.show(i, self.err(e))
        self.done("task.add", str(task.get("id", "")))
        await self.show(i, self.t("ta_done", title=fmt.clip(body["title"], 200)))

    async def on_task_button(self, i: dict) -> None:
        m = TK.fullmatch(i["data"]["custom_id"])
        if not m:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        status, tid, f = m.groups()
        await self.rest.respond(i, 6)
        try:
            await self.h.set_task_status(tid, status)
        except HERR as e:
            self.done("task.status", tid, self.short_error(e))
            return await self.say(i, self.err(e))
        self.done("task.status", tid, status)
        embed, rows = await self.tasks_message(f)
        await self.show(i, "", [embed], rows)

    # --- jobs --------------------------------------------------------------------------------------------------------

    async def tool_cron_list(self, i: dict, opts: dict) -> None:
        await self.show(i, "", [views.crons_embed(self.t, await self.h.crons())])

    async def find_job(self, i: dict, opts: dict) -> dict | None:
        j = pick_named(str(opts.get("job") or ""), await self.h.crons())
        if not j or not re.fullmatch(r"[\w.@-]{1,80}", str(j.get("id") or "")):
            await self.show(i, self.t("not_found"))
            return None
        return j

    async def tool_cron_run(self, i: dict, opts: dict) -> None:
        j = await self.find_job(i, opts)
        if not j:
            return
        if j.get("running"):
            return await self.show(i, self.t("cr_running", name=j["name"]))
        await self.ask(i, "cron_run", {"id": j["id"], "name": j["name"], "target": j["id"]}, self.t("cr_ask_run", name=fmt.clip(j["name"], 80)))

    async def tool_cron_toggle(self, i: dict, opts: dict) -> None:
        j = await self.find_job(i, opts)
        if not j:
            return
        want = not j.get("enabled")
        await self.ask(i, "cron_toggle", {"id": j["id"], "name": j["name"], "enabled": want, "target": j["id"]},
                       self.t("cr_ask_on" if want else "cr_ask_off", name=fmt.clip(j["name"], 80)))

    async def do_cron_run(self, a: dict) -> str:
        await self.h.run_cron(a["id"])
        return self.t("cr_started", name=fmt.clip(a["name"], 80))

    async def do_cron_toggle(self, a: dict) -> str:
        await self.h.enable_cron(a["id"], a["enabled"])
        return self.t("cr_is_on" if a["enabled"] else "cr_is_off", name=fmt.clip(a["name"], 80))

    # --- agents ------------------------------------------------------------------------------------------------------

    async def tool_agent_run(self, i: dict, opts: dict) -> None:
        items = [{**a, "key": agent_key(a), "label": a.get("display_name") or a.get("name")} for a in await self.h.agents()
                 if a.get("project") and a.get("topic") and a.get("name")]
        a = pick_named(str(opts.get("agent") or ""), items, "key", "label") or pick_named(str(opts.get("agent") or ""), items, "key", "name")
        if not a:
            return await self.show(i, self.t("not_found"))
        text = str(opts.get("text") or "").strip()[:4000]
        await self.ask(i, "agent_run", {"key": a["key"], "text": text, "target": a["key"]},
                       self.t("ag_ask", name=fmt.clip(str(a["label"]), 80), text=(": " + fmt.clip(text, 300)) if text else ""))

    async def do_agent_run(self, a: dict) -> str:
        p, tp, n = a["key"].split("/")
        await self.h.run_agent_path(p, tp, n, a.get("text", ""))
        return self.t("ag_started", name=a["key"])

    # --- usage, health -----------------------------------------------------------------------------------------------

    async def tool_usage(self, i: dict, opts: dict) -> None:
        usage = await self.h.request("GET", "/api/usage")
        try:
            stats = await self.h.request("GET", "/api/stats/tokens")
        except HERR:
            stats = None
        await self.show(i, "", [views.usage_embed(self.t, usage, stats, date.today().isoformat())])

    async def health_message(self, h: dict | None = None) -> tuple[dict, list[dict]]:
        h = h or await self.h.request("GET", "/api/system/health")
        try:
            services = await self.h.request("GET", "/api/system/services")
        except HERR:
            services = []
        try:
            version = await self.h.request("GET", "/api/system/version")
        except HERR:
            version = None
        return views.health_view(self.t, h, services, version)

    async def tool_health(self, i: dict, opts: dict) -> None:
        embed, rows = await self.health_message()
        await self.show(i, "", [embed], rows)

    async def on_health_seen(self, i: dict) -> None:
        m = HS.fullmatch(i["data"]["custom_id"])
        if not m:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        await self.rest.respond(i, 6)
        try:
            h = await self.h.request("POST", "/api/system/health/seen", json={"id": m[1], "seen": True})
        except HERR as e:
            self.done("health.seen", m[1], self.short_error(e))
            return await self.say(i, self.err(e))
        self.done("health.seen", m[1])
        embed, rows = await self.health_message(h if isinstance(h, dict) and "issues" in h else None)
        await self.show(i, "", [embed], rows)

    # --- search ------------------------------------------------------------------------------------------------------

    async def tool_search(self, i: dict, opts: dict) -> None:
        q = " ".join(str(opts.get("query") or "").split())[:200]
        if not q:
            return await self.show(i, self.t("se_empty"))
        d = await self.h.request("GET", "/api/search", params={"q": q, "kinds": "session", "limit": 25})
        text, rows = views.search_view(self.t, d.get("results") or [], q)
        await self.show(i, text, comps=rows)

    # --- backup ------------------------------------------------------------------------------------------------------

    async def tool_backup_status(self, i: dict, opts: dict) -> None:
        st = await self.h.request("GET", "/api/backup")
        try:
            hist = await self.h.request("GET", "/api/backup/history", params={"limit": 5})
        except HERR:
            hist = []
        await self.show(i, "", [views.backup_embed(self.t, st, hist if isinstance(hist, list) else [])])

    async def tool_backup_run(self, i: dict, opts: dict) -> None:
        await self.ask(i, "backup_run", {"target": "backup"}, self.t("bk_ask"))

    async def do_backup_run(self, a: dict) -> str:
        r = await self.h.request("POST", "/api/backup/run", timeout=180)
        if r.get("ok"):
            return self.t("bk_done", files=r.get("files", 0), commit=str(r.get("commit") or "")[:8])
        raise HarnessError(0, str(r.get("code") or "backup_failed"), str(r.get("error") or ""))

    # --- infra -------------------------------------------------------------------------------------------------------

    async def tool_infra(self, i: dict, opts: dict) -> None:
        embed, rows = views.infra_view(self.t, await self.h.request("GET", "/api/infra"))
        await self.show(i, "", [embed], rows)

    async def on_infra_refresh(self, i: dict) -> None:
        await self.rest.respond(i, 6)
        try:
            await self.h.request("POST", "/api/infra/refresh")
            self.done("infra.refresh", "")
            embed, rows = views.infra_view(self.t, await self.h.request("GET", "/api/infra"))
        except HERR as e:
            self.done("infra.refresh", "", self.short_error(e))
            return await self.say(i, self.err(e))
        await self.show(i, "", [embed], rows)

    # --- queue -------------------------------------------------------------------------------------------------------

    async def tool_queue(self, i: dict, opts: dict) -> None:
        sid = self.current_session(i)
        if not sid:
            return await self.show(i, self.t("no_session"))
        embed, rows = views.queue_view(self.t, await self.h.session(sid))
        await self.show(i, "", [embed], rows)

    async def on_queue_clear(self, i: dict) -> None:
        m = QC.fullmatch(i["data"]["custom_id"])
        if not m:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        await self.rest.respond(i, 6)
        try:
            n = int((await self.h.session(m[1])).get("queue") or 0)
        except HERR as e:
            return await self.say(i, self.err(e))
        if not n:
            return await self.say(i, self.t("qu_empty"))
        await self.ask(i, "queue_clear", {"sid": m[1], "target": m[1]}, self.t("qu_ask", n=n), follow=True)

    async def do_queue_clear(self, a: dict) -> str:
        await self.h.clear_queue(a["sid"])
        return self.t("qu_cleared")

    # --- files -------------------------------------------------------------------------------------------------------

    async def tool_files_ls(self, i: dict, opts: dict) -> None:
        root = self.workspace_root()
        try:
            p = filesafe.resolve(root, str(opts.get("path") or ""))
        except filesafe.Refused as e:
            return await self.show(i, self.t("fl_refused_" + ("secret" if e.args[0] == "secret" else "outside")))
        d = await self.h.request("GET", "/api/files/list", params={"path": p})
        entries = [e for e in d.get("entries") or [] if isinstance(e, dict) and not filesafe.hidden(str(e.get("name") or ""))]
        lines = [("📁 " + e["name"] + "/") if e.get("type") == "dir" else f"📄 {e['name']} · {self.size(e.get('size'))}" for e in entries[:40]]
        rel = os.path.relpath(p, os.path.normpath(root))
        embed = {"title": "🗂️ " + ("/" if rel == "." else "/" + rel), "color": views.BLUE,
                 "description": fmt.clip_block("\n".join(lines) or self.t("fl_empty"), 4000)}
        if len(entries) > 40:
            embed["footer"] = {"text": self.t("tk_more", n=len(entries) - 40)}
        await self.show(i, "", [embed])

    @staticmethod
    def size(n: Any) -> str:
        try:
            n = float(n)
        except (TypeError, ValueError):
            return "?"
        for div, unit in ((1 << 20, "Mo"), (1 << 10, "Ko")):
            if n >= div:
                return f"{n / div:.1f} {unit}"
        return f"{n:.0f} o"

    async def tool_files_get(self, i: dict, opts: dict) -> None:
        root = self.workspace_root()
        try:
            p = filesafe.resolve(root, str(opts.get("path") or ""))
        except filesafe.Refused as e:
            self.done("files.get", fmt.clip(str(opts.get("path") or ""), 100), "refused " + e.args[0])
            return await self.show(i, self.t("fl_refused_" + ("secret" if e.args[0] == "secret" else "outside")))
        rel = os.path.relpath(p, os.path.normpath(root))
        d = await self.h.request("GET", "/api/files/list", params={"path": os.path.dirname(p)})
        entry = next((e for e in d.get("entries") or [] if isinstance(e, dict) and e.get("name") == os.path.basename(p)), None)
        if not entry or entry.get("type") != "file":
            return await self.show(i, self.t("fl_notfile"))
        if int(entry.get("size") or 0) > filesafe.MAX_BYTES:
            self.done("files.get", rel, "too large")
            return await self.show(i, self.t("toolong", name=os.path.basename(p)))
        try:
            data = await self.h.fetch("/api/files/raw", {"path": p}, limit=filesafe.MAX_BYTES)
        except HarnessError as e:
            self.done("files.get", rel, self.short_error(e))
            return await self.show(i, self.t("toolong", name=os.path.basename(p)) if e.status == 413 else self.err(e))
        await self.show(i, self.t("fl_sending", name=os.path.basename(p)))
        try:
            await self.rest.followup_file(i, self.gw.app_id, "📄 " + os.path.basename(p), os.path.basename(p), data)
        except DiscordError as e:
            self.done("files.get", rel, f"discord {e.status}")
            return await self.show(i, self.t("toolong", name=os.path.basename(p)))
        self.done("files.get", rel)

    # --- mail --------------------------------------------------------------------------------------------------------

    async def tool_mail_unread(self, i: dict, opts: dict) -> None:
        accounts = await self.h.request("GET", "/api/mail/accounts")
        fields, options = [], []
        for a in (accounts if isinstance(accounts, list) else [])[:6]:
            aid = str(a.get("id") or "")
            if not ML.fullmatch(aid + ":0"):
                continue
            label = fmt.clip(str(a.get("label") or a.get("address") or aid), 60)
            try:
                d = await self.h.request("GET", f"/api/mail/{seg(aid)}/messages", params={"folder": "INBOX", "unread": "true", "size": 5})
            except HERR as e:
                fields.append({"name": label, "value": self.err(e), "inline": False})
                continue
            msgs = [m for m in d.get("messages") or [] if isinstance(m, dict) and isinstance(m.get("uid"), int)]
            lines = [f"• {fmt.clip(str((m.get('from') or {}).get('name') or (m.get('from') or {}).get('email') or '?'), 40)} — {fmt.clip(str(m.get('subject') or '…'), 80)}" for m in msgs]
            fields.append({"name": f"{label} · {self.t('ml_unread', n=d.get('total', len(msgs)))}", "value": "\n".join(lines) or self.t("ml_none"), "inline": False})
            for m in msgs:
                if len(options) < 25:
                    options.append({"label": fmt.clip(str(m.get("subject") or "…"), 100) or "…", "value": f"{aid}:{m['uid']}",
                                    "description": fmt.clip(f"{label} · {(m.get('from') or {}).get('email', '')}", 100)})
        embed = {"title": "📬 " + self.t("ml_title"), "color": views.BLUE, "fields": fields[:25],
                 "description": "" if fields else self.t("ml_no_account")}
        rows = [{"type": 1, "components": [{"type": 3, "custom_id": "ml:x", "placeholder": self.t("ml_ph"), "options": options}]}] if options else []
        await self.show(i, "", [embed], rows)

    async def on_mail_pick(self, i: dict) -> None:
        m = ML.fullmatch(str((i["data"].get("values") or [""])[0]))
        if not m:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        acc, uid = m[1], int(m[2])
        await self.rest.respond(i, 6)
        try:
            d = await self.h.request("GET", f"/api/mail/{seg(acc)}/message", params={"uid": uid, "folder": "INBOX", "seen": "false"})
        except HERR as e:
            return await self.say(i, self.err(e))
        frm = d.get("from") or {}
        embed = {"title": "✉️ " + fmt.clip(str(d.get("subject") or "…"), 200), "color": views.BLUE,
                 "description": fmt.clip_block(str(d.get("text") or ""), 900) or self.t("ml_notext"),
                 "fields": [{"name": self.t("ml_from"), "value": fmt.clip(f"{frm.get('name', '')} <{frm.get('email', '')}>", 200), "inline": True},
                            {"name": self.t("ml_date"), "value": str(d.get("date") or "–")[:40], "inline": True}]}
        if d.get("attachments"):
            embed["fields"].append({"name": "📎", "value": str(len(d["attachments"])), "inline": True})
        rows = [{"type": 1, "components": [
            {"type": 2, "style": 2, "label": "👁️ " + self.t("ml_mark_read"), "custom_id": f"ma:seen:{acc}:{uid}"},
            {"type": 2, "style": 2, "label": "📥 " + self.t("ml_archive"), "custom_id": f"ma:archive:{acc}:{uid}"}]}]
        await self.say(i, "", [embed], rows)

    async def on_mail_action(self, i: dict) -> None:
        m = MA.fullmatch(i["data"]["custom_id"])
        if not m:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        action, acc, uid = m[1], m[2], int(m[3])
        await self.rest.respond(i, 6)
        await self.ask(i, "mail_" + action, {"account": acc, "uid": uid, "target": f"{acc}:{uid}"},
                       self.t("ml_ask_" + action, acc=acc, uid=uid), follow=True)

    async def do_mail(self, a: dict, action: str) -> str:
        await self.h.request("POST", f"/api/mail/{seg(a['account'])}/actions", json={"folder": "INBOX", "uids": [int(a["uid"])], "action": action})
        return self.t("ml_done_" + action)

    async def do_mail_seen(self, a: dict) -> str:
        return await self.do_mail(a, "seen")

    async def do_mail_archive(self, a: dict) -> str:
        return await self.do_mail(a, "archive")

    async def tool_mail_outbox(self, i: dict, opts: dict) -> None:
        rows_in = await self.h.request("GET", "/api/mail/outbox")
        items = [r for r in rows_in if isinstance(r, dict) and OB.fullmatch(f"ob:send:{r.get('id')}")][:5] if isinstance(rows_in, list) else []
        if not items:
            return await self.show(i, self.t("ob_none"))
        fields, rows = [], []
        for n, r in enumerate(items, 1):
            to = ", ".join(str(x.get("email") if isinstance(x, dict) else x) for x in (r.get("to") or []))
            fields.append({"name": f"{n}. {fmt.clip(str(r.get('subject') or '…'), 120)}", "inline": False,
                           "value": fmt.clip_block(f"{self.t('ob_agent')}: {r.get('agent_name') or r.get('agent')}\n{self.t('ob_to')}: {to}\n{fmt.clip_block(str(r.get('text') or ''), 500)}", 1000)})
            rows.append({"type": 1, "components": [
                {"type": 2, "style": 1, "label": f"{n} · {self.t('ob_send')}", "custom_id": f"ob:send:{r['id']}"},
                {"type": 2, "style": 4, "label": f"{n} · {self.t('ob_deny')}", "custom_id": f"ob:deny:{r['id']}"}]})
        await self.show(i, "", [{"title": "📤 " + self.t("ob_title"), "color": views.BLUE, "fields": fields}], rows)

    async def on_outbox_button(self, i: dict) -> None:
        m = OB.fullmatch(i["data"]["custom_id"])
        if not m:
            return await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)
        action, oid = m[1], m[2]
        await self.rest.respond(i, 6)
        try:
            rows_in = await self.h.request("GET", "/api/mail/outbox")
        except HERR as e:
            return await self.say(i, self.err(e))
        r = next((x for x in rows_in if isinstance(x, dict) and x.get("id") == oid), None) if isinstance(rows_in, list) else None
        if not r:
            return await self.say(i, self.t("ob_gone"))
        to = ", ".join(str(x.get("email") if isinstance(x, dict) else x) for x in (r.get("to") or []))
        await self.ask(i, "outbox_" + action, {"oid": oid, "target": oid}, self.t("ob_ask_" + action, to=fmt.clip(to, 200), subject=fmt.clip(str(r.get("subject") or "…"), 150)), follow=True)

    async def do_outbox_send(self, a: dict) -> str:
        r = await self.h.request("POST", f"/api/mail/outbox/{seg(a['oid'])}/send")
        return self.t("ob_sent", status=str(r.get("status") or "?"))

    async def do_outbox_deny(self, a: dict) -> str:
        await self.h.request("POST", f"/api/mail/outbox/{seg(a['oid'])}/deny")
        return self.t("ob_denied")

    # --- audit -------------------------------------------------------------------------------------------------------

    async def tool_audit(self, i: dict, opts: dict) -> None:
        recs = self.audit.tail(10)
        lines = [f"<t:{int(r.get('at') or 0)}:T> `{fmt.clip(str(r.get('cmd')), 30)}` {fmt.clip(str(r.get('target')), 60)} → {fmt.clip(str(r.get('result')), 40)}" for r in reversed(recs)]
        await self.show(i, "", [{"title": "🧾 " + self.t("au_title"), "color": views.BLUE, "description": "\n".join(lines) or self.t("au_none")}])

    # --- component and modal routing ---------------------------------------------------------------------------------

    async def on_confirm(self, i: dict) -> None:
        m = CF.fullmatch(i["data"]["custom_id"])
        c = self.confirms.pop(m[2], None) if m else None
        if not m or not c or c.action not in CONFIRM_ACTIONS or time.time() - c.at > CONFIRM_TTL:
            return await self.rest.respond(i, 7, self.t("expired"), components=[])
        if m[1] == "n":
            return await self.rest.respond(i, 7, self.t("cancelled"), components=[])
        await self.rest.respond(i, 7, "⏳", components=[])
        try:
            msg = await getattr(self, "do_" + c.action)(c.args)
            result = "ok"
        except HERR as e:
            msg, result = self.err(e), self.short_error(e)
        self.done(c.action.replace("_", "."), str(c.args.get("target", "")), result)
        try:
            await self.show(i, msg)
        except DiscordError as e:
            log.warning("confirmation answer: %s", e)

    async def on_tool_component(self, i: dict, kind: str) -> bool:
        """Buttons and menus of the tools. False when `kind` is not one of them."""
        fn = {"cf": self.on_confirm, "tk": self.on_task_button, "hs": self.on_health_seen, "if": self.on_infra_refresh,
              "qc": self.on_queue_clear, "ml": self.on_mail_pick, "ma": self.on_mail_action, "ob": self.on_outbox_button}.get(kind)
        if not fn:
            return False
        try:
            await fn(i)
        except HERR as e:
            log.warning("component %s: %s", kind, e)
            try:
                await self.say(i, self.err(e))
            except DiscordError:
                pass
        except DiscordError as e:
            log.warning("component %s: %s", kind, e)
        return True

    # --- forms ---------------------------------------------------------------------------------------------------------

    async def modal_new(self, i: dict) -> None:
        """/new with the form: title, topic (typed; empty = a menu follows) and first message."""
        t = self.t
        hint = ""
        try:
            topics = await asyncio.wait_for(self.topics(), 1.5)
            hint = ", ".join(f"{x['project']}/{x['name']}" for x in topics[:3])
        except (asyncio.TimeoutError, *HERR):
            pass
        await self.rest.modal(i, CTX_NEW_FORM, t("nm_title"), [
            text_input("title", t("nm_f_title"), max_length=100),
            text_input("topic", t("nm_f_topic"), max_length=100, placeholder=(hint or t("nm_topic_ph"))[:100]),
            text_input("message", t("nm_f_message"), paragraph=True, max_length=4000)])

    async def on_modal(self, i: dict) -> None:
        cid = i["data"].get("custom_id")
        if cid == "ta":
            return await self.submit_task_add(i)
        if cid == CTX_NEW_FORM:
            ch, in_dm = i["channel_id"], not i.get("guild_id")
            if not self.allowed_here(ch, in_dm):
                return await self.rest.respond(i, 4, self.t("refused_here"), ephemeral=True)
            v = modal_values(i)
            return await self.cmd_new(i, ch, in_dm, v.get("title", "")[:100], v.get("topic", ""), v.get("message", ""))
        await self.rest.respond(i, 4, self.t("expired"), ephemeral=True)

    # --- autocomplete of the tools -------------------------------------------------------------------------------------

    async def tool_choices(self, path: list[str], focused: dict) -> list[dict]:
        q = " ".join(str(focused.get("value") or "").split()).lower()
        out: list[dict] = []
        if " ".join(path) in ("cron run", "cron toggle") and focused["name"] == "job":
            for j in await self.h.crons():
                if (not q or q in str(j.get("name", "")).lower()) and re.fullmatch(r"[\w.@-]{1,80}", str(j.get("id") or "")):
                    out.append({"name": f"{views.cron_icon(j)} {j.get('name')}", "value": j["id"]})
        elif path == ["agent", "run"] and focused["name"] == "agent":
            for a in await self.h.agents():
                label = str(a.get("display_name") or a.get("name") or "")
                if a.get("project") and (not q or q in label.lower() or q in agent_key(a).lower()) and len(agent_key(a)) <= 100:
                    out.append({"name": f"{label} ({a['project']}/{a['topic']})", "value": agent_key(a)})
        return out[:25]
