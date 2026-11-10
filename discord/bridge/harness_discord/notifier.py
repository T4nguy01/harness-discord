"""Proactive notifications (setting `proactive`): a morning summary of the tasks, a warning when the plan passes 80 %, and an
alert when the backup failed. Each one is sent once (what was sent is remembered in state.json). Information is silent
(Discord shows no notification); the two warnings are not."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import httpx

from . import fmt
from .harness import HarnessError

log = logging.getLogger("harness_discord.notifier")

HERR = (HarnessError, httpx.HTTPError)
MORNING_FROM, MORNING_UNTIL = 8, 12      # server time: not before 08:00, and no stale summary after noon
EVERY = 300                              # plan and backup are looked at every 5 minutes
PLAN_LIMIT = 80.0


def now() -> float:
    return time.time()


class Notifier:
    def __init__(self, bot: Any):
        self.bot = bot
        self.checked = 0.0

    @property
    def st(self) -> dict:
        return self.bot.store.data.setdefault("notifier", {})

    async def tick(self) -> None:
        b = self.bot
        if not b.cfg.get("proactive", True):
            return
        await self.morning()
        t = now()
        if t - self.checked < EVERY:
            return
        self.checked = t
        for part in (self.plan, self.backup):
            try:
                await part()
            except HERR as e:
                log.warning("notifier %s: %s", part.__name__, e)

    # --- morning summary ---------------------------------------------------------------------------------------------

    async def morning(self) -> None:
        b, st = self.bot, self.st
        local = datetime.fromtimestamp(now())
        today = local.date().isoformat()
        if st.get("morning") == today or not MORNING_FROM <= local.hour < MORNING_UNTIL:
            return
        ch = b.lay.get("notifications")
        if not ch:
            return
        try:
            data = await b.h.tasks()
        except HERR as e:
            log.warning("morning summary: %s", e)
            return
        st["morning"] = today                                  # once a day, whatever happens next
        b.store.save()
        late, due = [], []
        for x in data.get("tasks") or []:
            if not isinstance(x, dict) or x.get("status") == "done" or not x.get("due"):
                continue
            (late if x["due"] < today else due if x["due"] == today else []).append(x)
        if not late and not due:
            return
        t = b.t
        lines = []
        if late:
            lines.append(f"**⚠️ {t('mo_late', n=len(late))}**")
            lines += [f"- {fmt.clip(str(x.get('title') or '…'), 100)} ({x['due']})" for x in sorted(late, key=lambda x: x["due"])[:10]]
        if due:
            lines.append(f"**📅 {t('mo_today', n=len(due))}**")
            lines += [f"- {fmt.clip(str(x.get('title') or '…'), 100)}" for x in due[:10]]
        embed = {"title": "☀️ " + t("mo_title"), "description": "\n".join(lines)[:4000], "color": 0xFEE75C}
        await b.rest.send(ch, "", embeds=[embed], silent=True)

    # --- plan usage --------------------------------------------------------------------------------------------------

    async def plan(self) -> None:
        b, st = self.bot, self.st
        u = await b.h.request("GET", "/api/usage")
        w = u.get("five_hour") if isinstance(u, dict) else None
        if not isinstance(w, dict) or not isinstance(w.get("pct"), (int, float)) or w["pct"] < PLAN_LIMIT:
            return
        reset = w.get("resets_at")
        last = st.get("plan_window")
        if isinstance(reset, (int, float)) and isinstance(last, (int, float)) and abs(reset - last) < 1800:
            return                                              # already warned for this 5 hour window
        if not isinstance(reset, (int, float)) and last:
            return
        ch = b.lay.get("alerts")
        if not ch:
            return
        st["plan_window"] = reset if isinstance(reset, (int, float)) else now()
        b.store.save()
        when = f" <t:{int(reset)}:R>" if isinstance(reset, (int, float)) else ""
        await b.rest.send(ch, "", embeds=[{"title": "⚠️ " + b.t("pl_title", pct=int(w["pct"])), "color": 0xFEE75C,
                                           "description": b.t("pl_body") + (" " + b.t("pl_resets") + when if when else "")}])

    # --- backup ------------------------------------------------------------------------------------------------------

    async def backup(self) -> None:
        b, st = self.bot, self.st
        d = await b.h.request("GET", "/api/backup")
        last = (d.get("last") if isinstance(d, dict) else None) or {}
        at = last.get("at")
        if not at:
            return
        if "backup_seen" not in st:                             # first look: what already failed is not announced
            st["backup_seen"] = at
            b.store.save()
            return
        if at == st["backup_seen"] or last.get("ok") is not False:
            if at != st["backup_seen"]:
                st["backup_seen"] = at
                b.store.save()
            return
        ch = b.lay.get("alerts")
        if not ch:
            return
        st["backup_seen"] = at
        b.store.save()
        await b.rest.send(ch, "", embeds=[{"title": "🛑 " + b.t("bf_title"), "color": 0xED4245,
                                           "description": fmt.clip(str(last.get("error") or b.t("bf_body")), 500)}])
