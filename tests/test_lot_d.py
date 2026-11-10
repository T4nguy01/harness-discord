"""Lot D: /harness tools (tasks, jobs, agents, usage, health, search, backup, infra, queue, files, mail), confirmations,
forms (/new, task add), localized commands, proactive messages, audit trail, link buttons, locked threads, slow mode."""
import ast
import datetime
import json
import os
import re
import tempfile
import time
from pathlib import Path

import pytest

import harness_discord.notifier as nt
from fakes import OWNER, TOPIC, until
from harness_discord import filesafe, views
from harness_discord.audit import Audit
from harness_discord.commands import NAME_OK
from harness_discord.texts import T
from test_integration_bot import PROJECT_CH, inter, link, scenario

pytestmark = pytest.mark.usefixtures("fast_sleep")
SRC = Path(__file__).parent.parent / "discord" / "bridge" / "harness_discord"


def nest(path, opts=None):
    """Slash command options for `/harness <path...> <opts>`."""
    leaf = [{"name": k, "type": 3, "value": v} for k, v in (opts or {}).items()]
    if not path:
        return leaf
    node = {"type": 1, "name": path[-1], "options": leaf}
    for g in reversed(path[:-1]):
        node = {"type": 2, "name": g, "options": [node]}
    return [node]


async def hcmd(e, path, opts=None, iid="I1", **kw):
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "harness", "options": nest(path, opts)}, iid=iid, **kw))


def originals(e, iid):
    return [o for o in e.fd.originals if o["token"] == "tk-" + iid]


async def answer(e, iid, what="answer"):
    return (await until(lambda: originals(e, iid), what=what))[-1]


def followups(e, iid):
    return [o for o in e.fd.followups if o["token"] == "tk-" + iid]


def buttons(msg):
    return [c for row in msg.get("components") or [] for c in row["components"]]


def click(e, cid, iid, values=None):
    d = {"custom_id": cid}
    if values is not None:
        d["values"] = values
    return e.fg.dispatch("INTERACTION_CREATE", inter(3, d, iid=iid))


def audit_lines(e):
    p = Path(e.bot.store.path).parent / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# === 3. localised commands =================================================================================================

def walk(cmds):
    for c in cmds:
        yield c
        yield from walk(c.get("options") or [])


@scenario
async def test_command_definitions_follow_discord_rules(e):
    for lang in ("fr", "en"):
        e.bot.lang = lang
        cmds = await e.bot.commands()
        assert len(cmds) < 100
        for c in walk(cmds):
            if c.get("type") == 3 and "description" not in c and c.get("name") and "options" not in c and c in cmds:
                assert 1 <= len(c["name"]) <= 32                                    # context menu: any characters
                for v in (c.get("name_localizations") or {}).values():
                    assert 1 <= len(v) <= 32
                continue
            assert NAME_OK.match(c["name"]) and c["name"] == c["name"].lower(), c["name"]
            for v in (c.get("name_localizations") or {}).values():
                assert NAME_OK.match(v) and v == v.lower(), v
            assert 1 <= len(c.get("description", "x")) <= 100, c
            for v in (c.get("description_localizations") or {}).values():
                assert 1 <= len(v) <= 100
            if "choices" in c:
                assert len(c["choices"]) <= 25
        h = next(c for c in cmds if c["name"] == "harness")
        assert len(json.dumps(h, ensure_ascii=False)) < 7000                        # Discord: 8000 characters per command
        assert len(h["options"]) <= 25 and all(len(g.get("options") or []) <= 25 for g in h["options"])
        names = [c["name"] for c in cmds]
        assert len(names) == len(set(names))
    e.bot.lang = "en"
    by = {c["name"]: c for c in await e.bot.commands()}
    assert by["new"]["name_localizations"] == {"fr": "nouvelle"} and by["new"]["description_localizations"]["fr"]
    e.bot.lang = "fr"
    by = {c["name"]: c for c in await e.bot.commands()}
    assert by["new"]["description_localizations"]["en-US"] == T["en"]["d_new"]


@scenario
async def test_commands_are_registered_only_when_they_changed(e):
    await e.bot.register_commands()
    await e.bot.register_commands()
    puts = [c for c in e.fd.calls if c[0] == "PUT" and c[1].endswith("/commands")]
    assert len(puts) == 1 and "harness" in {c["name"] for c in e.fd.commands}
    assert e.bot.store.data["commands_hash"]
    e.bot.lang = "en"
    await e.bot.register_commands()
    assert len([c for c in e.fd.calls if c[0] == "PUT" and c[1].endswith("/commands")]) == 2


def test_texts_cover_every_key_used_in_the_code():
    keys = set()
    for f in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                if name in ("t", "ctx"):
                    keys.add(node.args[0].value)
                if name in ("opt", "sub") and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    keys.add(node.args[1].value)
                if name == "d":
                    keys.add(node.args[0].value)
                if name == "choice" and len(node.args) > 1:
                    keys.add(node.args[1].value)
    keys = {k for k in keys if isinstance(k, str) and re.fullmatch(r"[a-z0-9_]+", k)}
    for k in ("fl_refused_secret", "fl_refused_outside", "tk_title_open", "tk_title_todo", "tk_title_doing", "tk_title_done", "tk_title_all",
              "ml_ask_seen", "ml_ask_archive", "ml_done_seen", "ml_done_archive", "ob_ask_send", "ob_ask_deny", "cr_is_off",
              "f_open", "f_todo", "f_doing", "f_done", "f_all"):
        keys.add(k)
    for lang in ("en", "fr"):
        assert not [k for k in keys if k not in T[lang]], (lang, [k for k in keys if k not in T[lang]])
    assert set(T["en"]) == set(T["fr"])
    for k in T["en"]:                                                            # same placeholders in both languages
        assert sorted(re.findall(r"{(\w+)", T["en"][k])) == sorted(re.findall(r"{(\w+)", T["fr"][k])), k
        if k.startswith("d_"):
            assert len(T["en"][k]) <= 100 and len(T["fr"][k]) <= 100, k


# === 1. tasks ===========================================================================================================

def sample_tasks(e):
    e.fh.task_data = {"projects": [{"id": "p_1", "name": "apps"}], "tasks": [
        {"id": "t_a", "title": "En retard", "status": "todo", "due": "2020-01-01", "priority": 2, "project_id": "p_1", "order": 1},
        {"id": "t_b", "title": "Fini", "status": "done", "due": None, "order": 2, "done_at": 5},
        {"id": "t_c", "title": "En cours", "status": "doing", "due": None, "order": 3}]}


@scenario
async def test_tasks_lists_open_tasks_with_buttons_and_filters(e):
    sample_tasks(e)
    await hcmd(e, ["tasks"])
    await until(lambda: e.fd.responses)
    assert e.fd.responses[0]["type"] == 5 and e.fd.responses[0]["data"]["flags"] == 64        # private
    a = await answer(e, "I1")
    desc = a["embeds"][0]["description"]
    assert "En retard" in desc and "En cours" in desc and "Fini" not in desc and "⚠️ 2020-01-01" in desc and "apps" in desc
    ids = [b["custom_id"] for b in buttons(a)]
    assert "tk:done:t_a:o" in ids and "tk:doing:t_a:o" in ids and "tk:done:t_c:o" in ids and "tk:todo:t_c:o" in ids
    assert all(len(i) <= 100 for i in ids)
    await hcmd(e, ["tasks"], {"status": "done"}, iid="I2")
    d = (await answer(e, "I2"))["embeds"][0]["description"]
    assert "Fini" in d and "En retard" not in d


@scenario
async def test_task_button_changes_the_status_and_redraws(e):
    sample_tasks(e)
    await click(e, "tk:done:t_a:o", "I1")
    await until(lambda: e.fh.task_patches, what="patch")
    assert e.fh.task_patches == [("t_a", {"status": "done"})]
    a = await answer(e, "I1")
    assert "En retard" not in a["embeds"][0]["description"]
    assert ("task.status", "t_a", "done") in [(x["cmd"], x["target"], x["result"]) for x in audit_lines(e)]
    await click(e, "tk:done:t_zzz:o", "I2")                                      # unknown task: an error, nothing else
    await until(lambda: followups(e, "I2"), what="error")
    assert "404" in followups(e, "I2")[0]["content"]
    await click(e, "tk:bogus:t_a:o", "I3")
    await until(lambda: any(r["id"] == "I3" for r in e.fd.responses))
    assert len(e.fh.task_patches) == 1


@scenario
async def test_task_add_form_then_submit(e):
    await hcmd(e, ["task", "add"])
    r = await until(lambda: e.fd.responses and e.fd.responses[0], what="modal")
    assert r["type"] == 9 and r["data"]["custom_id"] == "ta"
    ids = [c["components"][0]["custom_id"] for c in r["data"]["components"]]
    assert ids == ["title", "notes", "due", "priority"] and len(r["data"]["title"]) <= 45

    def submit(iid, **v):
        comps = [{"type": 1, "components": [{"type": 4, "custom_id": k, "value": x}]} for k, x in v.items()]
        return e.fg.dispatch("INTERACTION_CREATE", inter(5, {"custom_id": "ta", "components": comps}, iid=iid))

    await submit("I2", title="Appeler Paul", notes="urgent", due="2026-12-31", priority="2")
    await until(lambda: e.fh.task_posts, what="post")
    assert e.fh.task_posts[0] == {"title": "Appeler Paul", "notes": "urgent", "due": "2026-12-31", "priority": 2}
    assert "Appeler Paul" in (await answer(e, "I2"))["content"]
    await submit("I3", title="x", due="31/12/2026")
    await submit("I4", title="x", priority="9")
    await submit("I5", title="")
    await until(lambda: len([r for r in e.fd.responses if r["id"] in ("I3", "I4", "I5")]) == 3)
    assert len(e.fh.task_posts) == 1                                              # nothing created
    assert all(r["data"]["flags"] == 64 for r in e.fd.responses if r["id"] in ("I3", "I4", "I5"))
    await e.fg.dispatch("INTERACTION_CREATE", inter(5, {"custom_id": "ta", "components": []}, user="999", iid="I6"))
    await until(lambda: any(r["id"] == "I6" for r in e.fd.responses))
    assert len(e.fh.task_posts) == 1 and next(r for r in e.fd.responses if r["id"] == "I6")["data"]["content"] == T["fr"]["not_allowed"]


# === confirmations / jobs / agents ===========================================================================================

def job(**kw):
    return {"id": "c_1", "name": "Memory compaction", "enabled": True, "human": "every day at 05:00", "running": False,
            "next_run": 1794369600, "last_run": {"at": 1794283201.0, "ok": True}, **kw}


@scenario
async def test_cron_list_and_run_needs_a_confirmation_click(e):
    e.fh.crons = [job(), job(id="c_2", name="Check updates", enabled=False)]
    await hcmd(e, ["cron", "list"])
    d = (await answer(e, "I1"))["embeds"][0]["description"]
    assert "Memory compaction" in d and "⏸️" in d and "✅" in d
    await hcmd(e, ["cron", "run"], {"job": "c_1"}, iid="I2")
    a = await answer(e, "I2")
    assert e.fh.cron_runs == []                                                    # asked, not done
    yes = next(b for b in buttons(a) if b["custom_id"].startswith("cf:y:"))
    assert re.fullmatch(r"cf:y:[0-9a-f]{8}", yes["custom_id"])
    await click(e, yes["custom_id"], "I3")
    await until(lambda: e.fh.cron_runs == ["c_1"], what="run")
    assert e.fd.responses[-1]["type"] == 7
    done = await answer(e, "I3")
    assert "Memory compaction" in done["content"]
    await click(e, yes["custom_id"], "I4")                                         # one use only
    await until(lambda: originals(e, "I4") or [r for r in e.fd.responses if r["id"] == "I4"])
    assert e.fh.cron_runs == ["c_1"]
    assert ("cron.run", "c_1", "ok") in [(x["cmd"], x["target"], x["result"]) for x in audit_lines(e)]


@scenario
async def test_confirmation_can_be_cancelled_and_expires(e):
    e.fh.crons = [job()]
    await hcmd(e, ["cron", "run"], {"job": "Memory"})                            # a part of the name is enough when it is unique
    a = await answer(e, "I1")
    no = next(b for b in buttons(a) if b["custom_id"].startswith("cf:n:"))
    await click(e, no["custom_id"], "I2")
    await until(lambda: any(r["id"] == "I2" for r in e.fd.responses))
    assert e.fh.cron_runs == []
    await hcmd(e, ["cron", "run"], {"job": "c_1"}, iid="I3")
    yes = next(b for b in buttons(await answer(e, "I3")) if b["custom_id"].startswith("cf:y:"))
    for c in e.bot.confirms.values():
        c.at -= 200                                                                # older than 2 minutes
    await click(e, yes["custom_id"], "I4")
    await until(lambda: any(r["id"] == "I4" for r in e.fd.responses))
    assert e.fh.cron_runs == [] and "expir" in [r for r in e.fd.responses if r["id"] == "I4"][0]["data"]["content"]
    await click(e, "cf:y:zzzzzzzz", "I5")                                           # malformed id
    await until(lambda: any(r["id"] == "I5" for r in e.fd.responses))
    assert e.fh.cron_runs == []


@scenario
async def test_confirm_buttons_of_someone_else_do_nothing(e):
    e.fh.crons = [job()]
    await hcmd(e, ["cron", "run"], {"job": "c_1"})
    yes = next(b for b in buttons(await answer(e, "I1")) if b["custom_id"].startswith("cf:y:"))
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": yes["custom_id"]}, user="999", iid="I2"))
    await until(lambda: any(r["id"] == "I2" for r in e.fd.responses))
    assert e.fh.cron_runs == [] and e.bot.confirms


@scenario
async def test_cron_toggle_and_unknown_job(e):
    e.fh.crons = [job()]
    await hcmd(e, ["cron", "toggle"], {"job": "c_1"})
    yes = next(b for b in buttons(await answer(e, "I1")) if b["custom_id"].startswith("cf:y:"))
    await click(e, yes["custom_id"], "I2")
    await until(lambda: e.fh.crons[0]["enabled"] is False, what="toggle")
    await hcmd(e, ["cron", "run"], {"job": "nope"}, iid="I3")
    assert "Introuvable" in (await answer(e, "I3"))["content"] and e.fh.cron_runs == []


@scenario
async def test_job_and_agent_autocomplete(e):
    e.fh.crons = [job(), job(id="c_2", name="Check updates")]
    e.fh.agents = [{"path": "projects/dev/topics/chat/agents/bob", "project": "dev", "topic": "chat", "name": "bob", "display_name": "Bob"}]
    opts = [{"type": 2, "name": "cron", "options": [{"type": 1, "name": "run", "options": [{"name": "job", "type": 3, "value": "upd", "focused": True}]}]}]
    await e.fg.dispatch("INTERACTION_CREATE", inter(4, {"name": "harness", "options": opts}, iid="A1"))
    await until(lambda: e.fd.autocompletes)
    assert [c["value"] for c in e.fd.autocompletes[-1]] == ["c_2"]
    opts = [{"type": 2, "name": "agent", "options": [{"type": 1, "name": "run", "options": [{"name": "agent", "type": 3, "value": "bo", "focused": True}]}]}]
    await e.fg.dispatch("INTERACTION_CREATE", inter(4, {"name": "harness", "options": opts}, iid="A2"))
    await until(lambda: len(e.fd.autocompletes) == 2)
    assert e.fd.autocompletes[-1] == [{"name": "Bob (dev/chat)", "value": "dev/chat/bob"}]
    await e.fg.dispatch("INTERACTION_CREATE", inter(4, {"name": "harness", "options": opts}, user="999", iid="A3"))
    await until(lambda: len(e.fd.autocompletes) == 3)
    assert e.fd.autocompletes[-1] == []                                              # nothing for strangers


@scenario
async def test_agent_run_confirms_then_runs_with_the_text(e):
    e.fh.agents = [{"path": "projects/dev/topics/chat/agents/bob", "project": "dev", "topic": "chat", "name": "bob", "display_name": "Bob"}]
    await hcmd(e, ["agent", "run"], {"agent": "dev/chat/bob", "text": "fais le point"})
    a = await answer(e, "I1")
    assert e.fh.agent_runs == [] and "Bob" in a["content"]
    yes = next(b for b in buttons(a) if b["custom_id"].startswith("cf:y:"))
    await click(e, yes["custom_id"], "I2")
    await until(lambda: e.fh.agent_runs, what="agent run")
    assert e.fh.agent_runs == [("dev/chat/bob", {"text": "fais le point"})]
    assert ("agent.run", "dev/chat/bob", "ok") in [(x["cmd"], x["target"], x["result"]) for x in audit_lines(e)]
    assert "fais le point" not in json.dumps(audit_lines(e))                          # never the content


# === usage, health, search, backup, infra, queue =================================================================================

@scenario
async def test_usage_shows_bars_and_handles_a_plan_without_window(e):
    await hcmd(e, ["usage"])
    f = (await answer(e, "I1"))["embeds"][0]["fields"]
    assert any("███" in x["value"] and "37 %" in x["value"] for x in f) and any("64 %" in x["value"] for x in f)
    assert any("45.90 $" in x["value"] for x in f)
    e.fh.usage = {"available": True, "five_hour": None, "seven_day": {"pct": 10, "resets_at": 1794369600}, "plan": "Ultra"}
    await hcmd(e, ["usage"], iid="I2")
    emb = (await answer(e, "I2"))["embeds"][0]
    assert "Ultra" in emb["title"] and any("5" in x["value"] and "fen" in x["value"].lower() for x in emb["fields"])
    e.fh.usage = {"available": False, "reason": "disabled", "five_hour": None, "seven_day": None}
    await hcmd(e, ["usage"], iid="I3")
    assert "disabled" in json.dumps((await answer(e, "I3"))["embeds"][0], ensure_ascii=False)


def test_progress_bar():
    assert views.bar(0) == "░░░░░░░░░░ 0 %" and views.bar(100) == "██████████ 100 %" and views.bar(37).startswith("████░")
    assert views.bar("x") == "░░░░░░░░░░ 0 %" and views.bar(250).endswith("100 %")


@scenario
async def test_health_with_seen_button(e):
    e.fh.health["issues"] = [{"level": "warn", "code": "backup_old", "text": "Last memory backup is 31 h old", "id": "0123456789ab", "seen": False},
                             {"level": "error", "code": "x", "text": "vu déjà", "id": "ba9876543210", "seen": True}]
    e.fh.health["ok"] = False
    await hcmd(e, ["health"])
    a = await answer(e, "I1")
    emb = a["embeds"][0]
    assert "31 h" in emb["description"] and "Harness Maxxing" in json.dumps(emb["fields"], ensure_ascii=False) and "2.0.0a135" in json.dumps(emb["fields"])
    assert [b["custom_id"] for b in buttons(a)] == ["hs:0123456789ab"]
    await click(e, "hs:0123456789ab", "I2")
    await until(lambda: e.fh.health_seen, what="seen")
    assert e.fh.health_seen == [{"id": "0123456789ab", "seen": True}]
    after = await answer(e, "I2")
    assert buttons(after) == []
    await click(e, "hs:nothex", "I3")
    await until(lambda: any(r["id"] == "I3" for r in e.fd.responses))
    assert len(e.fh.health_seen) == 1


@scenario
async def test_search_gives_a_menu_that_opens_the_session_thread(e):
    e.fh.add_session("S9", TOPIC, "Article sur le café")
    e.fh.search_results = [{"kind": "session", "id": "S9", "path": TOPIC, "title": "Article sur le café", "snippet": "du \x02café\x03 noir"},
                           {"kind": "file", "id": "/x", "path": "/x", "title": "f", "snippet": ""}]
    await hcmd(e, ["search"], {"query": "café"})
    a = await answer(e, "I1")
    menu = buttons(a)[0]
    assert menu["custom_id"] == "sess:x" and [o["value"] for o in menu["options"]] == ["S9"] and "café noir" in menu["options"][0]["description"]
    assert "\x02" not in json.dumps(menu)
    assert ("GET", "/api/search?q=caf%C3%A9&kinds=session&limit=25") in e.fh.log
    await click(e, "sess:x", "I2", values=["S9"])
    await until(lambda: e.bot.store.channel_of("S9"), what="thread")
    assert e.fd.threads and e.fd.threads[0]["parent"] == PROJECT_CH
    e.fh.search_results = []
    await hcmd(e, ["search"], {"query": "rien"}, iid="I3")
    assert "rien" in (await answer(e, "I3"))["content"]
    await hcmd(e, ["search"], {"query": "   "}, iid="I4")
    assert (await answer(e, "I4"))["content"]


@scenario
async def test_backup_status_and_run_with_confirmation(e):
    await hcmd(e, ["backup", "status"])
    emb = (await answer(e, "I1"))["embeds"][0]
    assert "me/mem" in json.dumps(emb["fields"]) and "95" in json.dumps(emb["fields"])
    await hcmd(e, ["backup", "run"], iid="I2")
    a = await answer(e, "I2")
    assert e.fh.backup_runs == 0
    await click(e, next(b for b in buttons(a) if b["custom_id"].startswith("cf:y:"))["custom_id"], "I3")
    await until(lambda: e.fh.backup_runs == 1, what="backup")
    assert "abcdef12" in (await answer(e, "I3"))["content"]
    e.fh.backup_state = {"configured": False}
    await hcmd(e, ["backup", "status"], iid="I4")
    assert (await answer(e, "I4"))["embeds"][0]["description"]


@scenario
async def test_infra_read_only_with_refresh_button(e):
    await hcmd(e, ["infra"])
    a = await answer(e, "I1")
    d = a["embeds"][0]["description"]
    assert d.index("**shop**") < d.index("shop.example.com") and "🔴" in d and "🟢" in d                 # down first
    assert [b["custom_id"] for b in buttons(a)] == ["if:r"]
    await click(e, "if:r", "I2")
    await until(lambda: e.fh.infra_refreshes == 1, what="refresh")
    e.fh.infra = {"enabled": False, "resources": []}
    await hcmd(e, ["infra"], iid="I3")
    a3 = await answer(e, "I3")
    assert buttons(a3) == [] and a3["embeds"][0]["description"]


@scenario
async def test_queue_lists_waiting_messages_and_clears_after_confirmation(e):
    link(e)
    e.fh.sessions["S1"].update({"queue": 2, "title": "Mon fil", "queue_items": [{"text": "première", "attachments": 0, "source": "web"}, {"text": "deuxième", "attachments": 1, "source": "telegram"}]})
    await hcmd(e, ["queue"], channel="T1")
    a = await answer(e, "I1")
    assert "première" in a["embeds"][0]["description"] and "deuxième" in a["embeds"][0]["description"]
    clear = buttons(a)[0]["custom_id"]
    assert clear == "qc:S1"
    await click(e, clear, "I2")
    ask = await until(lambda: followups(e, "I2"), what="confirm")
    assert e.fh.queue_cleared == []
    await click(e, next(b for b in buttons(ask[0]) if b["custom_id"].startswith("cf:y:"))["custom_id"], "I3")
    await until(lambda: e.fh.queue_cleared == ["S1"], what="cleared")
    await hcmd(e, ["queue"], iid="I4")                                                 # no session in this channel
    assert (await answer(e, "I4"))["content"]


# === files =====================================================================================================================

def workspace(e, tmp):
    root = Path(tmp) / "ws"
    (root / "docs").mkdir(parents=True)
    (root / ".ssh").mkdir()
    (root / "docs" / "note.txt").write_text("hello")
    (root / ".env").write_text("K=1")
    outside = Path(tmp) / "outside.txt"
    outside.write_text("private")
    os.symlink(outside, root / "docs" / "link.txt")
    e.bot.cfg["workspace_dir"] = str(root)

    def ent(p, kind, size=0):
        return {"name": os.path.basename(p), "path": p, "type": kind, "size": size}
    e.fh.fs = {str(root): [ent(f"{root}/docs", "dir"), ent(f"{root}/.ssh", "dir"), ent(f"{root}/.env", "file", 3), ent(f"{root}/CLAUDE.md", "file", 5)],
               str(root / "docs"): [ent(f"{root}/docs/note.txt", "file", 5), ent(f"{root}/docs/link.txt", "file", 7), ent(f"{root}/docs/big.bin", "file", 26 * 1024 * 1024)]}
    e.fh.fs_data = {f"{root}/docs/note.txt": b"hello", f"{root}/docs/link.txt": b"private"}
    return root


@scenario
async def test_files_ls_hides_secrets_and_stays_in_the_workspace(e):
    with tempfile.TemporaryDirectory() as tmp:
        root = workspace(e, tmp)
        await hcmd(e, ["files", "ls"])
        d = (await answer(e, "I1"))["embeds"][0]["description"]
        assert "docs/" in d and "CLAUDE.md" in d and ".env" not in d and ".ssh" not in d
        await hcmd(e, ["files", "ls"], {"path": "docs"}, iid="I2")
        assert "note.txt" in (await answer(e, "I2"))["embeds"][0]["description"]
        for n, bad in enumerate(["../outside.txt", "/etc", ".ssh", "/root/.ssh/id_rsa"], 3):
            await hcmd(e, ["files", "ls"], {"path": bad}, iid=f"I{n}")
            a = await answer(e, f"I{n}")
            assert "embeds" not in a or not a["embeds"]
        assert not [x for x in e.fh.log if "/etc" in x[1] or ".ssh" in x[1]]


@scenario
async def test_files_get_sends_an_attachment_with_the_rules(e):
    with tempfile.TemporaryDirectory() as tmp:
        root = workspace(e, tmp)
        await hcmd(e, ["files", "get"], {"path": "docs/note.txt"})
        await until(lambda: [f for f in e.fd.followups if f.get("_file")], what="file")
        f = next(f for f in e.fd.followups if f.get("_file"))
        assert f["_file"] == ("note.txt", b"hello") and f["flags"] == 64                      # private
        assert ("files.get", "docs/note.txt", "ok") in [(x["cmd"], x["target"], x["result"]) for x in audit_lines(e)]
        for n, bad in enumerate([".env", "docs/link.txt", "../outside.txt", "docs/big.bin", "docs/missing.txt", "docs"], 2):
            await hcmd(e, ["files", "get"], {"path": bad}, iid=f"I{n}")
            await answer(e, f"I{n}")
        assert len([f for f in e.fd.followups if f.get("_file")]) == 1                          # nothing else sent
        assert not [x for x in e.fh.log if "raw" in x[1] and ("env" in x[1] or "link" in x[1] or "big" in x[1])]


def test_filesafe_rules(tmp_path):
    root = tmp_path / "ws"
    (root / "a").mkdir(parents=True)
    (root / "a" / "ok.md").write_text("x")
    os.symlink(tmp_path, root / "a" / "up")
    r = str(root)
    assert filesafe.resolve(r, "a/ok.md") == f"{r}/a/ok.md" and filesafe.resolve(r, f"{r}/a") == f"{r}/a" and filesafe.resolve(r, "") == r
    for bad in ["../x", "/etc/passwd", "a/../../x", ".env", "a/.env.local", "x/id_rsa", "k.pem", "my_token.txt", ".git/config", ".claude/settings.json",
                "a/up", "x" * 500]:
        with pytest.raises(filesafe.Refused):
            filesafe.resolve(r, bad)
    assert filesafe.hidden(".env") and filesafe.hidden("secrets.yaml") and not filesafe.hidden("README.md")


# === mail ======================================================================================================================

@scenario
async def test_mail_unread_pick_and_action_with_confirmation(e):
    e.fh.mail_accounts = [{"id": "me-example-com", "address": "me@example.com", "label": "Perso"}]
    e.fh.mail_unread = {"me-example-com": [{"uid": 7, "from": {"name": "Paul", "email": "p@x.org"}, "subject": "Facture", "date": "2026-11-10"}]}
    await hcmd(e, ["mail", "unread"])
    a = await answer(e, "I1")
    assert "Perso" in a["embeds"][0]["fields"][0]["name"] and "Facture" in a["embeds"][0]["fields"][0]["value"]
    menu = buttons(a)[0]
    assert menu["options"][0]["value"] == "me-example-com:7"
    assert ("GET", "/api/mail/me-example-com/messages?folder=INBOX&unread=true&size=5") in e.fh.log
    await click(e, "ml:x", "I2", values=["me-example-com:7"])
    msg = (await until(lambda: followups(e, "I2"), what="message"))[0]
    assert ("GET", "/api/mail/me-example-com/message?uid=7&folder=INBOX&seen=false") in e.fh.log          # reading does not mark it read
    assert {b["custom_id"] for b in buttons(msg)} == {"ma:seen:me-example-com:7", "ma:archive:me-example-com:7"}
    await click(e, "ma:archive:me-example-com:7", "I3")
    ask = (await until(lambda: followups(e, "I3"), what="confirm"))[0]
    assert e.fh.mail_actions == []
    await click(e, next(b for b in buttons(ask) if b["custom_id"].startswith("cf:y:"))["custom_id"], "I4")
    await until(lambda: e.fh.mail_actions, what="action")
    assert e.fh.mail_actions == [("me-example-com", {"folder": "INBOX", "uids": [7], "action": "archive"})]
    await click(e, "ml:x", "I5", values=["../x:1"])                                       # malformed value
    await click(e, "ma:delete:me-example-com:7", "I6")
    await until(lambda: len([r for r in e.fd.responses if r["id"] in ("I5", "I6")]) == 2)
    assert len(e.fh.mail_actions) == 1


@scenario
async def test_mail_outbox_send_needs_a_confirmation_that_names_the_recipient(e):
    e.fh.outbox = [{"id": "o_abc123", "account": "a", "agent": "x/bob", "agent_name": "Bob", "to": [{"email": "client@x.org"}], "subject": "Devis", "text": "Bonjour"}]
    await hcmd(e, ["mail", "outbox"])
    a = await answer(e, "I1")
    ids = [b["custom_id"] for b in buttons(a)]
    assert ids == ["ob:send:o_abc123", "ob:deny:o_abc123"] and "Devis" in json.dumps(a["embeds"], ensure_ascii=False)
    await click(e, "ob:send:o_abc123", "I2")
    ask = (await until(lambda: followups(e, "I2"), what="confirm"))[0]
    assert "client@x.org" in ask["content"] and "Devis" in ask["content"] and e.fh.outbox_acts == []
    await click(e, next(b for b in buttons(ask) if b["custom_id"].startswith("cf:y:"))["custom_id"], "I3")
    await until(lambda: e.fh.outbox_acts == [("o_abc123", "send")], what="send")
    assert ("outbox.send", "o_abc123", "ok") in [(x["cmd"], x["target"], x["result"]) for x in audit_lines(e)]
    e.fh.outbox = []
    await hcmd(e, ["mail", "outbox"], iid="I4")
    assert (await answer(e, "I4"))["content"]


@scenario
async def test_mail_feature_off_is_a_short_error(e):
    e.fh.mail_off = True
    await hcmd(e, ["mail", "unread"])
    assert "409" in (await answer(e, "I1"))["content"]


# === 2. /new form ==============================================================================================================

@scenario
async def test_new_form_opens_a_modal_and_starts_the_session_with_its_message(e):
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "new", "options": [{"name": "form", "type": 5, "value": True}]}))
    r = await until(lambda: e.fd.responses and e.fd.responses[0], what="modal")
    assert r["type"] == 9 and r["data"]["custom_id"] == "nm"
    assert [c["components"][0]["custom_id"] for c in r["data"]["components"]] == ["title", "topic", "message"]
    assert r["data"]["components"][2]["components"][0]["style"] == 2                          # paragraph

    def submit(iid, topic, user=OWNER):
        comps = [{"type": 1, "components": [{"type": 4, "custom_id": k, "value": v}]} for k, v in (("title", "Mon sujet"), ("topic", topic), ("message", "écris le plan"))]
        return e.fg.dispatch("INTERACTION_CREATE", inter(5, {"custom_id": "nm", "components": comps}, iid=iid, user=user))

    await submit("I2", "dev/blog", user="999")
    await until(lambda: any(x["id"] == "I2" for x in e.fd.responses))
    assert e.fh.created == []                                                                 # a stranger starts nothing
    await submit("I3", "dev/blog")
    await until(lambda: e.fh.created, what="session")
    assert e.fh.created[0]["path"] == TOPIC and e.fh.created[0]["message"] == "écris le plan" and e.fh.created[0]["title"] == "Mon sujet"
    await submit("I4", "")                                                                    # no topic: a menu, the text waits
    a = await answer(e, "I4")
    menu = buttons(a)[0]
    assert menu["custom_id"].startswith("pick:") and len(e.fh.created) == 1
    await click(e, menu["custom_id"], "I5", values=["projects/dev/topics/docs"])
    await until(lambda: len(e.fh.created) == 2, what="second session")
    assert e.fh.created[1]["message"] == "écris le plan" and e.fh.created[1]["path"] == "projects/dev/topics/docs"


# === 7. audit ==================================================================================================================

def test_audit_file_is_private_rotated_and_never_holds_content(tmp_path):
    a = Audit(tmp_path / "audit.jsonl", max_bytes=400)
    for n in range(12):
        a.log("cron.run", f"c_{n}", "ok")
    assert (tmp_path / "audit.jsonl").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "audit.jsonl.1").exists() and (tmp_path / "audit.jsonl.1").stat().st_mode & 0o777 == 0o600
    last = a.tail(10)
    assert 5 <= len(last) <= 10 and last[-1]["target"] == "c_11" and set(last[0]) == {"at", "cmd", "target", "result"}
    assert a.tail(3)[0]["target"] == "c_9" and a.tail(3)[2]["target"] == "c_11"
    Audit(tmp_path / "none.jsonl").log("x", "line\nbreak", "ok")
    assert len((tmp_path / "none.jsonl").read_text().splitlines()) == 1


@scenario
async def test_audit_command_is_private_and_lists_the_last_actions(e):
    for n in range(12):
        e.bot.audit.log("cron.run", f"c_{n}", "ok")
    await hcmd(e, ["audit"])
    await until(lambda: e.fd.responses)
    assert e.fd.responses[0]["data"]["flags"] == 64
    d = (await answer(e, "I1"))["embeds"][0]["description"]
    assert d.count("`cron.run`") == 10 and "c_11" in d and "c_2 " in d and "c_1 " not in d and "c_0 " not in d


@scenario
async def test_stop_close_model_new_are_audited_without_content(e):
    link(e)
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "stop"}, channel="T1", iid="I1"))
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "close"}, channel="T1", iid="I2"))
    await until(lambda: len(audit_lines(e)) >= 2, what="audit")
    assert [x["cmd"] for x in audit_lines(e)][:2] == ["stop", "close"]
    assert audit_lines(e)[0]["target"] == "S1"


# === 4. link buttons, 5. locked threads, slow mode, archive duration =============================================================

@scenario
async def test_dashboard_and_agent_results_carry_an_open_in_harness_button(e):
    e.fh.agents = [{"path": "projects/dev/topics/chat/agents/bob", "project": "dev", "topic": "chat", "name": "bob", "display_name": "Bob"}]
    e.fh.agent_msgs["dev/chat/bob"] = []
    await e.bot.relay.agents()
    e.fh.agent_msgs["dev/chat/bob"] = [{"kind": "result", "at": time.time() + 5, "text": "Fait.", "ok": True}]
    await e.bot.relay.agents()
    msg = e.fd.in_channel("T1")[-1]
    link_btn = buttons(msg)[0]
    assert link_btn["style"] == 5 and link_btn["url"] == "http://web" and "token" not in link_btn["url"]
    await e.bot.relay.dashboard.tick()
    dash = e.fd.in_channel("CA")[-1]
    assert buttons(dash)[0]["url"] == "http://web" and buttons(dash)[0]["style"] == 5


@scenario
async def test_close_locks_and_archives_the_thread_instead_of_deleting_it(e):
    link(e)
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "close"}, channel="T1"))
    await until(lambda: ("T1", {"archived": True, "locked": True}) in e.fd.modified, what="lock")
    assert e.fd.deleted_channels == [] and e.bot.store.session_of_channel("T1") is None


@scenario
async def test_session_threads_archive_after_seven_days_and_alerts_gets_slow_mode_once(e):
    from harness_discord.discord_api import ARCHIVE_AFTER
    assert ARCHIVE_AFTER == 10080 and ARCHIVE_AFTER in (60, 1440, 4320, 10080)
    await e.rest.start_thread(PROJECT_CH, "x")
    await e.bot.rest.start_thread(PROJECT_CH, "y", "m9")
    from harness_discord import layout
    await layout.slow_alerts(e.rest, e.bot.lay)
    await layout.slow_alerts(e.rest, e.bot.lay)
    assert [m for m in e.fd.modified if m[0] == "CA"] == [("CA", {"rate_limit_per_user": 2})]
    e.fd.fail_next.append((403, {"code": 50013}))
    e.bot.lay["alerts"] = "CB"
    await layout.slow_alerts(e.rest, e.bot.lay)                                                 # refused: no crash, not retried
    await layout.slow_alerts(e.rest, e.bot.lay)
    assert e.bot.lay["alerts_slow"] == "CB"


# === 6. proactive messages ======================================================================================================

def at(h, m=0, day=10):
    return datetime.datetime(2026, 11, day, h, m).timestamp()


@scenario
async def test_morning_summary_once_a_day_silent_and_only_in_the_morning(e):
    e.bot.lay["notifications"] = "CN"
    e.fh.task_data = {"projects": [], "tasks": [
        {"id": "t1", "title": "Rendre le rapport", "status": "todo", "due": "2026-11-09"},
        {"id": "t2", "title": "Appeler", "status": "doing", "due": "2026-11-10"},
        {"id": "t3", "title": "Fini hier", "status": "done", "due": "2026-11-09"},
        {"id": "t4", "title": "Plus tard", "status": "todo", "due": "2026-12-01"}]}
    n = e.bot.relay.notifier
    nt.now = lambda: at(7, 59)
    await n.morning()
    assert e.fd.in_channel("CN") == []                                                        # not yet
    nt.now = lambda: at(8, 0)
    await n.morning()
    await n.morning()
    msgs = e.fd.in_channel("CN")
    assert len(msgs) == 1 and msgs[0]["flags"] == 1 << 12                                    # once, silent
    d = msgs[0]["embeds"][0]["description"]
    assert "Rendre le rapport" in d and "Appeler" in d and "Fini hier" not in d and "Plus tard" not in d
    nt.now = lambda: at(9, 0, day=11)
    await n.morning()
    assert len(e.fd.in_channel("CN")) == 2                                                    # the next day again
    nt.now = lambda: at(15, 0, day=12)
    await n.morning()
    assert len(e.fd.in_channel("CN")) == 2                                                    # afternoon: skipped
    assert e.bot.store.data["notifier"]["morning"] == "2026-11-11"


@scenario
async def test_morning_summary_says_nothing_when_nothing_is_due_and_obeys_the_setting(e):
    e.bot.lay["notifications"] = "CN"
    n = e.bot.relay.notifier
    nt.now = lambda: at(8, 30)
    await n.tick()
    assert e.fd.in_channel("CN") == [] and e.bot.store.data["notifier"]["morning"] == "2026-11-10"
    e.bot.cfg["proactive"] = False
    e.fh.task_data = {"projects": [], "tasks": [{"id": "t1", "title": "x", "status": "todo", "due": "2026-11-01"}]}
    nt.now = lambda: at(8, 30, day=11)
    await n.tick()
    assert e.fd.in_channel("CN") == [] and e.bot.store.data["notifier"]["morning"] == "2026-11-10"


@scenario
async def test_plan_warning_once_per_window(e):
    n = e.bot.relay.notifier
    nt.now = lambda: at(14)
    e.fh.usage = {"available": True, "five_hour": {"pct": 79.0, "resets_at": 1794297000}, "seven_day": None}
    await n.plan()
    assert e.fd.in_channel("CA") == []
    e.fh.usage["five_hour"]["pct"] = 85.0
    await n.plan()
    await n.plan()
    msgs = e.fd.in_channel("CA")
    assert len(msgs) == 1 and "85" in msgs[0]["embeds"][0]["title"] and msgs[0]["flags"] == 0       # a warning rings
    e.fh.usage["five_hour"]["resets_at"] += 5 * 3600                                               # the next window
    await n.plan()
    assert len(e.fd.in_channel("CA")) == 2
    e.fh.usage = {"available": True, "five_hour": None, "seven_day": {"pct": 99, "resets_at": 1}}  # a plan without a 5 hour window
    await n.plan()
    assert len(e.fd.in_channel("CA")) == 2


@scenario
async def test_backup_failure_is_announced_once_per_failure_and_not_for_old_ones(e):
    n = e.bot.relay.notifier
    e.fh.backup_state["last"] = {"at": 100.0, "ok": False, "error": "push refused"}
    await n.backup()
    assert e.fd.in_channel("CA") == []                                                              # failed before we looked: not announced
    e.fh.backup_state["last"] = {"at": 200.0, "ok": False, "error": "push refused"}
    await n.backup()
    await n.backup()
    msgs = e.fd.in_channel("CA")
    assert len(msgs) == 1 and "push refused" in msgs[0]["embeds"][0]["description"]
    e.fh.backup_state["last"] = {"at": 300.0, "ok": True, "error": None}
    await n.backup()
    e.fh.backup_state["last"] = {"at": 400.0, "ok": False, "error": "again"}
    await n.backup()
    assert len(e.fd.in_channel("CA")) == 2


@scenario
async def test_notifier_tick_checks_plan_and_backup_every_five_minutes(e):
    n = e.bot.relay.notifier
    nt.now = lambda: at(14)
    await n.tick()
    first = len(e.fh.log)
    await n.tick()
    assert len(e.fh.log) == first                                                                  # not again before 5 minutes
    nt.now = lambda: at(14, 6)
    await n.tick()
    assert len(e.fh.log) > first


# === security: owner only, nothing dangerous offered ===============================================================================

@scenario
async def test_a_stranger_gets_nothing_from_harness_commands(e):
    e.fh.crons = [job()]
    before = len(e.fh.log)
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "harness", "options": nest(["cron", "run"], {"job": "c_1"})}, user="999"))
    await until(lambda: e.fd.responses)
    r = e.fd.responses[0]
    assert r["type"] == 4 and r["data"]["flags"] == 64 and r["data"]["content"] == T["fr"]["not_allowed"]
    assert len(e.fh.log) == before and e.fh.cron_runs == []


def test_no_route_of_the_forbidden_kind_is_called_by_the_code():
    code = "\n".join(p.read_text() for p in SRC.glob("*.py"))
    for forbidden in ("/api/secrets", "/api/secret-files", "/api/backup/restore", "/api/system/update", "/api/system/services/", "/api/mail/send"):
        assert forbidden not in code, forbidden
    assert not re.search(r'"(DELETE)",\s*f?"/api/(?!sessions/\{check_sid\(sid\)\}/queue)', code)       # the only DELETE is the queue


def test_all_confirm_buttons_use_short_validated_ids():
    from harness_discord.tools import CF, CONFIRM_ACTIONS
    assert CF.fullmatch("cf:y:0123abcd") and not CF.fullmatch("cf:y:0123ABCD") and not CF.fullmatch("cf:y:0123abcde")
    for action in CONFIRM_ACTIONS:
        assert action in {"cron_run", "cron_toggle", "agent_run", "backup_run", "queue_clear", "mail_seen", "mail_archive", "outbox_send", "outbox_deny"}


@scenario
async def test_new_sessions_and_agent_replies_are_audited_without_their_text(e):
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "new", "options": [{"name": "topic", "type": 3, "value": "dev/blog"}]}))
    await until(lambda: e.fh.created, what="session")
    await until(lambda: audit_lines(e), what="audit")
    assert audit_lines(e)[0] == {**audit_lines(e)[0], "cmd": "session.new", "target": TOPIC}
    assert "secret-text" not in json.dumps(audit_lines(e))
