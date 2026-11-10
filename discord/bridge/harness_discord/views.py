"""Embeds and button rows of the /harness tools. Pure functions: data from the Harness API in, Discord payloads out.
Only fields the Harness API really returns are read; anything missing is left out."""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from .fmt import clip

T_ = Callable[..., str]
TID = re.compile(r"[\w-]{1,40}", re.ASCII)
HEALTH_ID = re.compile(r"[0-9a-f]{12}")
FILTERS = {"o": ("todo", "doing"), "t": ("todo",), "d": ("doing",), "x": ("done",), "a": ("todo", "doing", "done")}
FILTER_OF = {"open": "o", "todo": "t", "doing": "d", "done": "x", "all": "a"}
STATUS_ICON = {"todo": "⬜", "doing": "🔶", "done": "✅"}
NEXT_STATUS = {"todo": ("done", "doing"), "doing": ("done", "todo"), "done": ("todo",)}
LEVEL_ICON = {"error": "🛑", "warn": "⚠️", "warning": "⚠️", "info": "ℹ️"}
RES_ICON = {"ok": "🟢", "warn": "🟠", "down": "🔴"}
BLUE, GREEN, ORANGE, RED = 0x5865F2, 0x57F287, 0xFEE75C, 0xED4245
MAX_TASKS, MAX_TASK_BUTTONS = 10, 5


def bar(pct: Any, width: int = 10) -> str:
    """A text progress bar: `████░░░░░░ 40 %`."""
    try:
        p = max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        p = 0.0
    full = round(width * p / 100)
    return "█" * full + "░" * (width - full) + f" {p:.0f} %"


def when(ts: Any) -> str:
    return f"<t:{int(ts)}:R>" if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 1e9 else "–"


def short_path(path: str) -> str:
    return path.split("projects/", 1)[-1].replace("/topics/", "/")


# --- tasks -----------------------------------------------------------------------------------------------------------

def tasks_view(t: T_, data: dict, f: str, today: str) -> tuple[dict, list[dict]]:
    wanted = FILTERS.get(f, FILTERS["o"])
    projects = {p.get("id"): p.get("name") for p in data.get("projects") or [] if isinstance(p, dict)}
    items = [x for x in data.get("tasks") or [] if isinstance(x, dict) and x.get("status") in wanted]
    if f == "x":
        items.sort(key=lambda x: -(x.get("done_at") or 0))
    else:
        items.sort(key=lambda x: (x.get("due") or "9999-99-99", -(x.get("priority") or 0), x.get("order") or 0))
    lines, rows = [], []
    for x in items[:MAX_TASKS]:
        due = x.get("due")
        bits = [f"{STATUS_ICON.get(x['status'], '')} **{clip(str(x.get('title') or '…'), 80)}**"]
        if due:
            bits.append(("⚠️ " if due < today and x["status"] != "done" else "📅 ") + due)
        if x.get("priority"):
            bits.append("!" * int(x["priority"]))
        if projects.get(x.get("project_id")):
            bits.append(clip(str(projects[x["project_id"]]), 30))
        lines.append(" · ".join(bits))
    for x in items[:MAX_TASK_BUTTONS]:
        tid = str(x.get("id") or "")
        if not TID.fullmatch(tid):
            continue
        btns = []
        for n, st in enumerate(NEXT_STATUS[x["status"]]):
            label = f"{STATUS_ICON[st]} {t('tk_' + st)}"
            if n == 0:
                label = clip(f"{label} · {x.get('title') or '…'}", 80)
            btns.append({"type": 2, "style": 3 if st == "done" else 2, "label": label, "custom_id": f"tk:{st}:{tid}:{f}"})
        rows.append({"type": 1, "components": btns})
    desc = "\n".join(lines) or t("tk_none")
    more = len(items) - len(lines)
    embed = {"title": "📋 " + t("tk_title_" + next(k for k, v in FILTER_OF.items() if v == f)), "description": desc[:4000], "color": BLUE}
    if more > 0:
        embed["footer"] = {"text": t("tk_more", n=more)}
    return embed, rows


# --- jobs ------------------------------------------------------------------------------------------------------------

def cron_icon(j: dict) -> str:
    if j.get("running"):
        return "🔄"
    if not j.get("enabled"):
        return "⏸️"
    return "❌" if (j.get("last_run") or {}).get("ok") is False else "✅"


def crons_embed(t: T_, jobs: list[dict]) -> dict:
    lines = []
    for j in jobs[:25]:
        bits = [f"{cron_icon(j)} **{clip(str(j.get('name') or j.get('id')), 60)}**", clip(str(j.get("human") or j.get("schedule") or ""), 50)]
        if j.get("enabled") and j.get("next_run"):
            bits.append(t("cr_next") + " " + when(j["next_run"]))
        last = j.get("last_run")
        if last:
            bits.append(t("cr_last") + " " + when(last.get("at")))
        lines.append(" · ".join(b for b in bits if b))
    return {"title": "⏰ " + t("cr_title"), "description": ("\n".join(lines) or t("cr_none"))[:4000], "color": BLUE}


# --- usage -----------------------------------------------------------------------------------------------------------

def usage_embed(t: T_, usage: dict | None, stats: dict | None, today: str) -> dict:
    fields: list[dict] = []
    u = usage or {}
    if u.get("available") is False or not u:
        fields.append({"name": t("us_plan"), "value": t("us_unavailable", why=str(u.get("reason") or "")), "inline": False})
    else:
        for key, label in (("five_hour", t("us_5h")), ("seven_day", t("us_7d"))):
            w = u.get(key)
            if isinstance(w, dict) and w.get("pct") is not None:
                fields.append({"name": label, "value": f"`{bar(w['pct'])}`\n{t('us_resets')} {when(w.get('resets_at'))}", "inline": False})
            elif key == "five_hour":
                fields.append({"name": label, "value": t("us_no5h"), "inline": False})
    tot = (stats or {}).get("totals") or {}
    if tot:
        fields.append({"name": t("us_totals"), "value": t("us_totals_v", cost=tot.get("cost_usd", 0), turns=tot.get("turns", 0),
                                                         sessions=tot.get("sessions", 0), tokens=_tokens(tot.get("total", 0))), "inline": False})
    days = (stats or {}).get("days") or []
    day = next((d for d in days if d.get("date") == today), None)
    if day:
        fields.append({"name": t("us_today"), "value": f"{day.get('cost_usd', 0):.2f} $ · {_tokens(day.get('tokens', 0))}", "inline": True})
    projects = sorted((p for p in (stats or {}).get("projects") or [] if isinstance(p, dict)), key=lambda p: -(p.get("cost_usd") or 0))[:3]
    if projects:
        fields.append({"name": t("us_top"), "value": "\n".join(f"{clip(str(p.get('name')), 30)} · {p.get('cost_usd', 0):.2f} $" for p in projects), "inline": True})
    title = "📈 " + t("us_title") + (f" · {u['plan']}" if u.get("plan") else "")
    return {"title": title, "color": BLUE, "fields": fields}


def _tokens(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    for div, unit in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"


# --- health ----------------------------------------------------------------------------------------------------------

def health_view(t: T_, h: dict, services: Any, version: dict | None) -> tuple[dict, list[dict]]:
    issues = [i for i in h.get("issues") or [] if isinstance(i, dict)]
    lines = [f"{LEVEL_ICON.get(i.get('level'), '•')} {clip(str(i.get('text') or ''), 200)}" + (f" ({t('hl_seen_tag')})" if i.get("seen") else "") for i in issues]
    open_issues = [i for i in issues if not i.get("seen")]
    color = GREEN if h.get("ok") and not issues else RED if any(i.get("level") == "error" for i in open_issues) else ORANGE if open_issues else GREEN
    fields: list[dict] = []
    bits = []
    if h.get("cpu_pct") is not None:
        bits.append(f"CPU {h['cpu_pct']:.0f} %")
    mem = h.get("mem") or {}
    if mem.get("total"):
        bits.append(f"RAM {100 - 100 * (mem.get('avail') or 0) / mem['total']:.0f} %")
    disk = h.get("disk") or {}
    if disk.get("pct") is not None:
        bits.append(f"{t('hl_disk')} {disk['pct']:.0f} %")
    if h.get("uptime_s"):
        bits.append(t("hl_up", d=h["uptime_s"] / 86400))
    if bits:
        fields.append({"name": t("hl_machine"), "value": " · ".join(b for b in bits if b), "inline": False})
    svc = [s for s in services if isinstance(s, dict)] if isinstance(services, list) else []
    if svc:
        fields.append({"name": t("hl_services"), "value": "\n".join(f"{'🟢' if s.get('active') else '🔴'} {clip(str(s.get('label') or s.get('name')), 40)} · {s.get('state', '')}" for s in svc)[:1000], "inline": False})
    extra = []
    if h.get("claude_auth") is not None:
        extra.append(("✅ " if h["claude_auth"] else "❌ ") + "Claude")
    if h.get("backup_age_h") is not None:
        extra.append(f"{t('hl_backup')} {h['backup_age_h']:.0f} h")
    if extra:
        fields.append({"name": t("hl_other"), "value": " · ".join(extra), "inline": False})
    if version and version.get("version"):
        behind = version.get("behind") or 0
        fields.append({"name": t("hl_version"), "value": str(version["version"]) + (" · " + t("hl_behind", n=behind) if behind else " · " + t("hl_current")), "inline": False})
    embed = {"title": "🩺 " + t("hl_title"), "description": ("\n".join(lines) or t("hl_ok"))[:4000], "color": color, "fields": fields}
    rows = []
    for i in open_issues[:5]:
        iid = str(i.get("id") or "")
        if HEALTH_ID.fullmatch(iid):
            rows.append({"type": 1, "components": [{"type": 2, "style": 2, "label": clip(t("hl_mark") + " · " + str(i.get("text") or ""), 80), "custom_id": f"hs:{iid}"}]})
    return embed, rows


# --- backup ----------------------------------------------------------------------------------------------------------

def backup_embed(t: T_, st: dict, history: list[dict]) -> dict:
    if not st.get("configured"):
        return {"title": "💾 " + t("bk_title"), "description": t("bk_none"), "color": ORANGE}
    last = st.get("last") or {}
    fields = [{"name": t("bk_repo"), "value": clip(str(st.get("repo") or "–"), 100), "inline": True},
              {"name": t("bk_schedule"), "value": f"`{clip(str(st.get('schedule') or '–'), 40)}`", "inline": True}]
    if last:
        v = ("✅ " if last.get("ok") else "❌ ") + when(last.get("at")) + (f" · {last.get('files')} {t('bk_files')}" if last.get("files") is not None else "")
        if last.get("error"):
            v += "\n" + clip(str(last["error"]), 300)
        fields.append({"name": t("bk_last"), "value": v, "inline": False})
    hist = [f"{'✅' if r.get('ok') else '❌'} {when(r.get('at'))}" + (f" · {str(r['commit'])[:8]}" if r.get("commit") else "") +
            ("" if r.get("changed", True) or not r.get("ok") else " · " + t("bk_nochange")) for r in history[:5] if isinstance(r, dict)]
    if hist:
        fields.append({"name": t("bk_history"), "value": "\n".join(hist), "inline": False})
    ok = last.get("ok", True)
    return {"title": "💾 " + t("bk_title"), "color": GREEN if ok else RED, "fields": fields}


# --- infra -----------------------------------------------------------------------------------------------------------

def infra_view(t: T_, v: dict) -> tuple[dict, list[dict]]:
    refresh = [{"type": 1, "components": [{"type": 2, "style": 2, "label": "🔄 " + t("if_refresh"), "custom_id": "if:r"}]}]
    if not v.get("enabled"):
        return {"title": "🏗️ Infra", "description": t("if_off"), "color": ORANGE}, []
    res = [r for r in v.get("resources") or [] if isinstance(r, dict) and not r.get("hidden")]
    order = {"down": 0, "warn": 1}
    res.sort(key=lambda r: (order.get(r.get("status"), 2), str(r.get("kind")), str(r.get("name"))))
    counts = {s: sum(1 for r in res if r.get("status") == s) for s in ("ok", "warn", "down")}
    lines = [f"{RES_ICON.get(r.get('status'), '⚪')} {clip(str(r.get('kind') or ''), 12)} **{clip(str(r.get('name') or r.get('id')), 50)}**"
             + (f" · {clip(str(r['location']), 30)}" if r.get("location") else "") + (f" · {clip(str(r['status_detail']), 40)}" if r.get("status_detail") else "")
             for r in res[:30]]
    desc = "\n".join(lines) or t("if_empty")
    embed = {"title": "🏗️ Infra", "description": desc[:4000], "color": RED if counts["down"] else ORANGE if counts["warn"] else GREEN,
             "fields": [{"name": t("if_counts"), "value": f"🟢 {counts['ok']} · 🟠 {counts['warn']} · 🔴 {counts['down']}", "inline": False}]}
    if len(res) > 30:
        embed["footer"] = {"text": t("tk_more", n=len(res) - 30)}
    if v.get("generated_at"):
        embed["fields"].append({"name": t("if_at"), "value": when(v["generated_at"]), "inline": True})
    if v.get("refreshing"):
        embed["fields"].append({"name": "🔄", "value": t("if_refreshing"), "inline": True})
    return embed, refresh


# --- queue -----------------------------------------------------------------------------------------------------------

def queue_view(t: T_, s: dict) -> tuple[dict, list[dict]]:
    items = [x for x in s.get("queue_items") or [] if isinstance(x, dict)]
    n = int(s.get("queue") or 0)
    lines = [f"{k}. {clip(str(x.get('text') or '…'), 150) or '…'}" + (f" 📎{x['attachments']}" if x.get("attachments") else "") + f" · {x.get('source', 'web')}"
             for k, x in enumerate(items, 1)]
    embed = {"title": "🧾 " + t("qu_title", title=clip(str(s.get("title") or "…"), 60)), "color": BLUE,
             "description": ("\n".join(lines) or t("qu_empty")) if n else t("qu_empty")}
    rows = [{"type": 1, "components": [{"type": 2, "style": 4, "label": t("qu_clear", n=n), "custom_id": "qc:" + str(s.get("id"))[:64]}]}] if n else []
    return embed, rows


# --- search ----------------------------------------------------------------------------------------------------------

MARKS = re.compile(r"[\x00-\x08\x0b-\x1f]")


def search_view(t: T_, results: list[dict], q: str) -> tuple[str, list[dict]]:
    sess = [r for r in results if isinstance(r, dict) and r.get("kind") == "session" and re.fullmatch(r"[\w-]{1,64}", str(r.get("id") or ""), re.ASCII)][:25]
    if not sess:
        return t("se_none", q=clip(q, 60)), []
    opts = []
    for r in sess:
        o = {"label": clip(str(r.get("title") or "…"), 100) or "…", "value": r["id"]}
        desc = " · ".join(x for x in (short_path(str(r.get("path") or "")), clip(MARKS.sub("", str(r.get("snippet") or "")), 60)) if x)
        if desc:
            o["description"] = clip(desc, 100)
        opts.append(o)
    return t("se_pick", n=len(sess), q=clip(q, 60)), [{"type": 1, "components": [{"type": 3, "custom_id": "sess:x", "placeholder": t("sessions_ph"), "options": opts}]}]
