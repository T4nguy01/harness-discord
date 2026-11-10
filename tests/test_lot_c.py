"""Lot C: web mirror, project emoji, reactions, presence, autocomplete, answer buttons, context menus, edits,
forwarded messages, health buttons, pinned dashboard."""
import asyncio
import importlib.util
import time
from pathlib import Path

import pytest

import harness_discord.mirror as mirror_mod
from fakes import OWNER, TOPIC, until
from harness_discord.commands import resolve_model, resolve_topic
from harness_discord.discord_api import DiscordError
from harness_discord.relay import render
from test_integration_bot import GUILD, PROJECT_CH, inter, link, msg, scenario, settled, turn

pytestmark = pytest.mark.usefixtures("fast_sleep")
ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(mirror_mod, "CREATE_PAUSE", 0)


def web(e, sid, title="", updated=None, **kw):
    return e.fh.add_session(sid, TOPIC, title, updated=time.time() if updated is None else updated, **kw)


# === 1. web mirror ===================================================================================================

@scenario
async def test_mirror_creates_threads_for_recent_web_sessions(e):
    web(e, "W1", "Article")
    web(e, "W2", "Vieille", updated=time.time() - 31 * 86400)
    web(e, "W3", "Cron", cron=True)
    web(e, "W4", "Rangée", archived=True)
    web(e, "W5", "Agent")["path"] = TOPIC + "/agents/bob"
    await e.bot.mirror.sync()
    assert [t["name"] for t in e.fd.threads] == ["blog · Article"] and e.fd.threads[0]["parent"] == PROJECT_CH
    assert e.bot.store.session_of_channel("T1") == "W1"
    hello = e.fd.in_channel("T1")[0]
    assert "Article" in hello["content"] and "http://web/sessions/W1" in hello["content"]
    await e.bot.mirror.sync()
    assert len(e.fd.threads) == 1                                  # not twice


@scenario
async def test_mirror_is_capped_at_50_and_can_be_switched_off(e):
    for n in range(60):
        web(e, f"W{n}", f"t{n}", updated=time.time() - n)
    e.bot.cfg["mirror_web_sessions"] = False
    await e.bot.mirror.sync()
    assert e.fd.threads == []
    e.bot.cfg["mirror_web_sessions"] = True
    await e.bot.mirror.sync()
    assert len(e.fd.threads) == 50
    assert "W0" in e.bot.store.data["threads"] and "W59" not in e.bot.store.data["threads"]


@scenario
async def test_mirror_renames_archives_and_restores(e):
    s = web(e, "W1", "Un")
    await e.bot.mirror.sync()
    s["title"] = "Deux"
    await e.bot.mirror.sync()
    assert e.fd.renamed == [("T1", "blog · Deux")]
    await e.bot.mirror.sync()
    assert len(e.fd.renamed) == 1                                  # unchanged title: no rename
    s["archived"] = True
    await e.bot.mirror.sync()
    await e.bot.mirror.sync()
    assert e.fd.modified == [("T1", {"archived": True})]           # archived once
    s["archived"] = False
    await e.bot.mirror.sync()
    assert e.fd.modified[-1] == ("T1", {"archived": False}) and len(e.fd.threads) == 1


@scenario
async def test_mirror_recreates_a_thread_deleted_by_hand(e):
    web(e, "W1", "Un")
    await e.bot.mirror.sync()
    e.bot.store.data["threads"]["W1"]["archived"] = True
    e.fh.sessions["W1"]["archived"] = False
    e.fd.fail_next.append((404, {"code": 10003, "message": "Unknown Channel"}))
    await e.bot.mirror.sync()
    assert len(e.fd.threads) == 2 and e.bot.store.channel_of("W1") == "T2"


@scenario
async def test_mirror_failure_does_not_break_the_layout_sync(e):
    web(e, "W1", "Un")
    e.fd.fail_next.append((403, {"code": 50013}))                  # the thread cannot be created
    await e.bot.mirror.sync()
    assert e.bot.store.session_of_channel("T1") is None


@scenario
async def test_message_in_a_mirrored_thread_continues_the_session(e):
    web(e, "W1", "Un")
    await e.bot.mirror.sync()
    await e.fg.dispatch("MESSAGE_CREATE", msg("suite", channel="T1"))
    await until(lambda: e.fh.posted == [("W1", {"text": "suite"})])
    e.fh.cut()


# === 2. emoji in thread names ========================================================================================

@scenario
async def test_thread_name_carries_the_project_emoji(e):
    e.fh.projects[0]["emoji"] = "🛒"
    await e.bot.topics()
    assert e.bot.thread_name(TOPIC, "Titre") == "🛒 blog · Titre"
    web(e, "W1", "Article")
    await e.bot.mirror.sync()
    assert e.fd.threads[0]["name"] == "🛒 blog · Article"
    e.fh.projects[0]["emoji"] = ""
    await e.bot.topics()
    assert e.bot.thread_name(TOPIC, "Titre") == "blog · Titre"
    assert len(e.bot.thread_name(TOPIC, "x" * 200)) == 100


# === 3. reactions ====================================================================================================

@scenario
async def test_reactions_hourglass_then_check(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1", mid="m5"))
    await until(lambda: ("T1", "m5", "⏳") in e.fd.reactions, what="hourglass")
    await until(lambda: e.fh.stream_froms)
    e.fh.emit("S1", *turn())
    await until(lambda: ("T1", "m5", "✅") in e.fd.reactions, what="check")
    await settled(e)
    assert ("T1", "m5", "⏳") not in e.fd.reactions and ("T1", "m5", "⏳") in e.fd.unreactions


@scenario
async def test_reaction_cross_on_failed_turn_and_on_send_error(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1", mid="m5"))
    await until(lambda: e.fh.stream_froms)
    e.fh.emit("S1", {"type": "result", "ok": False, "duration_ms": 1})
    await until(lambda: ("T1", "m5", "❌") in e.fd.reactions)
    await settled(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("x", channel="T1", mid="m6"))
    e.fh.sessions["S1"]["archived"] = True
    await e.fg.dispatch("MESSAGE_CREATE", msg("y", channel="T1", mid="m7"))
    await until(lambda: ("T1", "m7", "❌") in e.fd.reactions)
    e.fh.cut()


@scenario
async def test_reactions_can_be_switched_off(e):
    e.bot.cfg["reactions"] = False
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1", mid="m5"))
    await until(lambda: e.fh.stream_froms)
    e.fh.emit("S1", *turn())
    await until(lambda: any(m["content"] == "Voici la réponse finale." for m in e.fd.in_channel("T1")))
    await settled(e)
    assert e.fd.reactions == [] and e.fd.unreactions == []


@scenario
async def test_first_message_of_a_new_session_gets_reactions(e):
    await e.fg.dispatch("MESSAGE_CREATE", msg("salut", mid="m1"))
    menu = await until(lambda: next((m for m in e.fd.in_channel(PROJECT_CH) if m["components"]), None))
    cid = menu["components"][0]["components"][0]["custom_id"]
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": cid, "values": [TOPIC]}))
    await until(lambda: (PROJECT_CH, "m1", "⏳") in e.fd.reactions or (PROJECT_CH, "m1", "⏳") in e.fd.unreactions)
    await until(lambda: e.fh.stream_froms)
    e.fh.emit("S1", *turn())
    await until(lambda: (PROJECT_CH, "m1", "✅") in e.fd.reactions)
    await settled(e)


# === 4. presence =====================================================================================================

@scenario
async def test_presence_follows_running_sessions(e):
    r = e.bot.relay
    await r.presence()
    await until(lambda: e.fg.presences, what="presence")
    assert e.fg.presences[-1]["status"] == "idle" and e.fg.presences[-1]["activities"][0]["state"] == "inactif"
    e.fh.add_session("S1", TOPIC, "a", state="running")
    e.fh.add_session("S2", TOPIC, "b", state="running")
    await r.presence()                                             # < 30 s since the last one: nothing
    assert len(e.fg.presences) == 1
    r.presence_at = 0
    r._sessions = (0.0, [])
    await r.presence()
    await until(lambda: len(e.fg.presences) == 2)
    assert e.fg.presences[-1]["status"] == "online" and e.fg.presences[-1]["activities"][0]["state"] == "2 sessions en cours"
    r.presence_at = 0
    r._sessions = (0.0, [])
    await r.presence()                                             # unchanged: nothing
    assert len(e.fg.presences) == 2
    e.fh.sessions.pop("S2")
    r.presence_at = 0
    r._sessions = (0.0, [])
    await r.presence()
    await until(lambda: len(e.fg.presences) == 3)
    assert e.fg.presences[-1]["activities"][0]["state"] == "1 session en cours"


@scenario
async def test_presence_is_part_of_the_relay_tick(e):
    e.bot.cfg.update({"relay_feed": False, "relay_bell": False, "relay_agents": False})
    await e.bot.relay.tick()
    await until(lambda: e.fg.presences)


def test_presence_is_sent_again_when_identifying():
    from harness_discord.discord_api import Gateway
    g = Gateway("t", None)
    assert asyncio.run(g.update_presence("x", "idle")) is False    # not connected: kept for the next identify
    assert g.presence["activities"][0]["state"] == "x" and g.presence["status"] == "idle"


# === 5. autocomplete =================================================================================================

def test_resolve_model_like_telegram():
    ms = [("claude-opus-5-5", "Opus 5.5"), ("claude-sonnet-5-5", "Sonnet 5.5"), ("claude-haiku-4-5-20251001", "Haiku 4.5")]
    assert resolve_model("opus", ms) == "claude-opus-5-5"
    assert resolve_model("Sonnet 5.5", ms) == "claude-sonnet-5-5"
    assert resolve_model("sonnet-5.5", ms) == "claude-sonnet-5-5"
    assert resolve_model("claude-haiku-4-5-20251001", ms) == "claude-haiku-4-5-20251001"
    assert resolve_model("default", ms) == "" and resolve_model("", ms) == ""
    assert resolve_model("gpt", ms) is None


def test_resolve_topic():
    ts = [{"path": "projects/a/topics/blog", "project": "a", "name": "blog"}, {"path": "projects/b/topics/blog", "project": "b", "name": "blog"},
          {"path": "projects/a/topics/docs", "project": "a", "name": "docs"}]
    assert resolve_topic("projects/a/topics/blog", ts) == "projects/a/topics/blog"
    assert resolve_topic("b/blog", ts) == "projects/b/topics/blog"
    assert resolve_topic("docs", ts) == "projects/a/topics/docs"
    assert resolve_topic("blog", ts) is None                       # two topics of that name
    assert resolve_topic("", ts) is None and resolve_topic("zzz", ts) is None


@scenario
async def test_autocomplete_models_and_topics(e):
    def ac(cmd, opt, value, iid, ch=PROJECT_CH, user=OWNER):
        return inter(4, {"name": cmd, "options": [{"name": opt, "value": value, "focused": True, "type": 3}]}, channel=ch, iid=iid, user=user)
    await e.fg.dispatch("INTERACTION_CREATE", ac("model", "id", "son", "I1", "T1"))
    await until(lambda: len(e.fd.autocompletes) == 1)
    assert e.fd.autocompletes[0] == [{"name": "Sonnet 5.5", "value": "claude-sonnet-5-5"}]
    assert e.fd.responses[0]["type"] == 8
    await e.fg.dispatch("INTERACTION_CREATE", ac("model", "id", "", "I2", "T1"))
    await until(lambda: len(e.fd.autocompletes) == 2)
    assert [c["name"] for c in e.fd.autocompletes[1]] == ["par défaut", "Opus 5.5", "Sonnet 5.5", "Haiku 4.5"]
    await e.fg.dispatch("INTERACTION_CREATE", ac("new", "topic", "do", "I3"))
    await until(lambda: len(e.fd.autocompletes) == 3)
    assert e.fd.autocompletes[2] == [{"name": "dev/docs", "value": "projects/dev/topics/docs"}]
    await e.fg.dispatch("INTERACTION_CREATE", ac("new", "topic", "", "I4"))
    await until(lambda: len(e.fd.autocompletes) == 4)
    assert len(e.fd.autocompletes[3]) == 2
    await e.fg.dispatch("INTERACTION_CREATE", ac("new", "topic", "", "I5", user="999"))     # a stranger gets nothing
    await until(lambda: len(e.fd.autocompletes) == 5)
    assert e.fd.autocompletes[4] == []


@scenario
async def test_autocomplete_is_limited_to_25_choices_of_100_characters(e):
    e.fh.projects[0]["topics"] = [{"name": f"t{n:02d}", "path": f"projects/dev/topics/t{n:02d}", "description": "", "running": 0, "session_count": 0}
                                  for n in range(40)]
    e.fh.models = [{"id": f"claude-opus-5-{n}", "label": "L" * 150} for n in range(40)]
    await e.fg.dispatch("INTERACTION_CREATE", inter(4, {"name": "new", "options": [{"name": "topic", "value": "", "focused": True}]}, iid="I1"))
    await e.fg.dispatch("INTERACTION_CREATE", inter(4, {"name": "model", "options": [{"name": "id", "value": "", "focused": True}]}, iid="I2", channel="T1"))
    await until(lambda: len(e.fd.autocompletes) == 2)
    assert all(len(c) == 25 for c in e.fd.autocompletes)
    assert all(len(x["name"]) <= 100 and len(x["value"]) <= 100 for c in e.fd.autocompletes for x in c)


@scenario
async def test_model_accepts_a_free_name_or_lists_choices(e):
    link(e)
    opt = lambda v: [{"name": "id", "value": v}]    # noqa: E731
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "model", "options": opt("Sonnet 5.5")}, channel="T1", iid="I1"))
    await until(lambda: e.fh.sessions["S1"]["model"] == "claude-sonnet-5-5")
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "model", "options": opt("gpt-9")}, channel="T1", iid="I2"))
    await until(lambda: len(e.fd.followups) == 2)
    assert "Modèle inconnu" in e.fd.followups[1]["content"] and "Opus 5.5" in e.fd.followups[1]["content"]
    assert e.fh.sessions["S1"]["model"] == "claude-sonnet-5-5"
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "model", "options": opt("default")}, channel="T1", iid="I3"))
    await until(lambda: e.fh.sessions["S1"]["model"] == "")


@scenario
async def test_new_with_a_topic_starts_without_a_menu(e):
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "new", "options": [{"name": "topic", "value": "projects/dev/topics/docs"}]}, iid="I1"))
    await until(lambda: e.fh.created)
    assert e.fh.created[0]["path"] == "projects/dev/topics/docs"
    await until(lambda: e.fd.threads)
    assert e.fd.threads[0]["name"].startswith("docs · ")


@scenario
async def test_new_with_an_unknown_topic_shows_the_filtered_menu(e):
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "new", "options": [{"name": "topic", "value": "doc"}]}, iid="I1"))
    await until(lambda: e.fd.originals)
    # "doc" matches only dev/docs: resolved straight to it
    await until(lambda: e.fh.created)
    assert e.fh.created[0]["path"] == "projects/dev/topics/docs"


@scenario
async def test_new_with_an_ambiguous_topic_shows_the_filtered_menu(e):
    e.fh.projects[0]["topics"].append({"name": "docs2", "path": "projects/dev/topics/docs2", "description": "", "running": 0, "session_count": 0})
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "new", "options": [{"name": "topic", "value": "doc"}]}, iid="I1"))
    await until(lambda: e.fd.originals)
    opts = e.fd.originals[0]["components"][0]["components"][0]["options"]
    assert {o["value"] for o in opts} == {"projects/dev/topics/docs", "projects/dev/topics/docs2"} and e.fh.created == []


# === 6. answer buttons ===============================================================================================

def buttons(m):
    return [c for row in (m["components"] or []) for c in row["components"]]


@scenario
async def test_answer_buttons_regenerate_and_removed_at_next_turn(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("fais ça", channel="T1", mid="m1"))
    await until(lambda: e.fh.stream_froms)
    e.fh.emit("S1", *turn())
    final = await until(lambda: next((m for m in e.fd.in_channel("T1") if m["content"] == "Voici la réponse finale."), None))
    await settled(e)
    ids = [b["custom_id"] for b in buttons(final)]
    assert ids == ["ab:regen:S1"]                                  # no turn left behind this answer: no Stop
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "ab:regen:S1"}, channel="T1", iid="I1"))
    await until(lambda: len(e.fh.posted) == 2, what="resent")
    assert e.fh.posted[1] == ("S1", {"text": "fais ça"})
    await until(lambda: any(ed["id"] == final["id"] and ed.get("components") == [] for ed in e.fd.edits), what="buttons removed")
    assert e.fd.responses[0]["type"] == 6
    await until(lambda: e.fd.followups)
    assert e.fd.followups[0]["content"] == "Renvoyé."
    e.fh.emit("S1", *turn("Deuxième."))
    await until(lambda: any(m["content"] == "Deuxième." for m in e.fd.in_channel("T1")))
    await settled(e)


@scenario
async def test_stop_button_only_when_a_turn_waits_behind_and_it_stops(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("un", channel="T1", mid="m1"))
    await until(lambda: e.fh.stream_froms)
    await e.fg.dispatch("MESSAGE_CREATE", msg("deux", channel="T1", mid="m2"))
    await until(lambda: len(e.fh.posted) == 2)
    e.fh.emit("S1", {"type": "assistant", "text": "réponse un"}, {"type": "result", "ok": True, "duration_ms": 1})
    final = await until(lambda: next((m for m in e.fd.in_channel("T1") if m["content"] == "réponse un"), None))
    assert [b["custom_id"] for b in buttons(final)] == ["ab:stop:S1", "ab:regen:S1"]
    e.fh.sessions["S1"]["state"] = "running"
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "ab:stop:S1"}, channel="T1", iid="I1"))
    await until(lambda: e.fh.stopped == ["S1"], what="stopped")
    e.fh.cut()
    await settled(e)


@scenario
async def test_stop_button_with_nothing_running_and_bad_ids(e):
    link(e)
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "ab:stop:S1"}, channel="T1", iid="I1"))
    await until(lambda: e.fd.followups)
    assert e.fh.stopped == [] and e.fd.followups[0]["content"] == "Aucun tour en cours."
    for n, cid in enumerate(["ab:stop:../x", "ab:regen:" + "a" * 70, "ab:boom:S1", "ab:regen:"]):
        await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": cid}, channel="T1", iid=f"B{n}"))
    await until(lambda: len(e.fd.responses) == 5)
    assert all(r["type"] == 4 and r["data"]["flags"] == 64 for r in e.fd.responses[1:])
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "ab:regen:S1"}, channel="T1", iid="R1", user="999"))
    await until(lambda: len(e.fd.responses) == 6)
    assert e.fd.responses[5]["data"]["content"].startswith("Ce bot") and e.fh.posted == []
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "ab:regen:S1"}, channel="T1", iid="R2"))     # nothing to resend
    await until(lambda: len(e.fd.followups) == 2)
    assert "plus ce message" in e.fd.followups[1]["content"] and e.fh.posted == []


@scenario
async def test_answer_buttons_can_be_switched_off(e):
    e.bot.cfg["answer_buttons"] = False
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1"))
    await until(lambda: e.fh.stream_froms)
    e.fh.emit("S1", *turn())
    final = await until(lambda: next((m for m in e.fd.in_channel("T1") if m["content"] == "Voici la réponse finale."), None))
    await settled(e)
    assert final["components"] is None


# === 7. context menus ================================================================================================

def ctx(name, target, ch, **kw):
    m = {"id": target, "content": "texte cité", "attachments": [], "author": {"id": "7"}, **kw}
    return inter(2, {"name": name, "type": 3, "target_id": target, "resolved": {"messages": {target: m}}}, channel=ch, iid="X" + target)


@scenario
async def test_send_to_claude_in_a_session_thread(e):
    link(e)
    await e.fg.dispatch("INTERACTION_CREATE", ctx("Envoyer à Claude", "t1", "T1"))
    await until(lambda: e.fh.posted == [("S1", {"text": "texte cité"})])
    await until(lambda: e.fd.followups)
    assert e.fd.responses[0]["type"] == 5 and e.fd.responses[0]["data"]["flags"] == 64
    assert e.fd.followups[0]["content"] == "Envoyé à Claude." and e.fd.followups[0]["flags"] == 64
    e.fh.cut()


@scenario
async def test_send_to_claude_in_a_project_channel_asks_the_topic(e):
    await e.fg.dispatch("INTERACTION_CREATE", ctx("Envoyer à Claude", "t2", PROJECT_CH))
    await until(lambda: e.fd.originals)
    sel = e.fd.originals[0]["components"][0]["components"][0]
    assert sel["custom_id"].startswith("pick:") and e.fh.created == []
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": sel["custom_id"], "values": [TOPIC]}, iid="P1"))
    await until(lambda: e.fh.created)
    assert e.fh.created[0] == {"path": TOPIC, "message": "texte cité"}
    await until(lambda: e.fd.threads)
    assert e.fd.threads[0]["message"] == "t2"                      # the thread hangs under the chosen message
    e.fh.cut()


@scenario(topics=1)
async def test_new_session_from_message_in_a_thread_and_single_topic(e):
    link(e)
    await e.fg.dispatch("INTERACTION_CREATE", ctx("Nouvelle session (message)", "t3", "T1"))
    await until(lambda: e.fh.created)
    assert e.fh.created[0] == {"path": TOPIC, "message": "texte cité"} and e.fh.posted == []
    await until(lambda: e.fd.threads)
    assert e.fd.threads[0]["parent"] == PROJECT_CH and e.fd.threads[0]["message"] is None
    e.fh.cut()


@scenario
async def test_context_menu_owner_only_empty_and_unknown(e):
    await e.fg.dispatch("INTERACTION_CREATE", {**ctx("Envoyer à Claude", "t4", PROJECT_CH), "member": {"user": {"id": "999"}}})
    await until(lambda: e.fd.responses)
    assert e.fd.responses[0]["data"]["content"].startswith("Ce bot") and e.fd.responses[0]["data"]["flags"] == 64
    await e.fg.dispatch("INTERACTION_CREATE", ctx("Envoyer à Claude", "t5", PROJECT_CH, content=""))
    await until(lambda: len(e.fd.responses) == 2)
    assert e.fd.responses[1]["data"]["content"] == "Ce message n'a ni texte ni fichier."
    await e.fg.dispatch("INTERACTION_CREATE", ctx("Envoyer à Claude", "t6", "ELSEWHERE"))
    await until(lambda: len(e.fd.responses) == 3)
    assert e.fd.responses[2]["data"]["content"].startswith("Cette commande")
    assert e.fh.created == [] and e.fh.posted == []


# === 8. edited messages ==============================================================================================

@scenario
async def test_edited_forwarded_message_gets_one_pencil(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("avant", channel="T1", mid="m1"))
    await until(lambda: e.fh.posted)
    edited = {"id": "m1", "channel_id": "T1", "content": "après", "author": {"id": OWNER}, "edited_timestamp": "2026-11-10T10:00:00+00:00"}
    await e.fg.dispatch("MESSAGE_UPDATE", edited)
    await until(lambda: ("T1", "m1", "✏️") in e.fd.reactions)
    n = len(e.fd.calls)
    await e.fg.dispatch("MESSAGE_UPDATE", edited)
    await e.fg.dispatch("MESSAGE_UPDATE", {"id": "unknown", "channel_id": "T1", "content": "x", "author": {"id": OWNER}, "edited_timestamp": "t"})
    await e.fg.dispatch("MESSAGE_UPDATE", {"id": "m1", "channel_id": "T1", "embeds": [{}]})            # an embed unfurl, not an edit
    await asyncio.sleep(0.2)
    assert e.fd.reactions.count(("T1", "m1", "✏️")) == 1
    assert [c for c in e.fd.calls[n:] if "reactions" in c[1]] == []
    assert len(e.fh.posted) == 1                                   # the edit is not sent again
    e.fh.cut()


# === 9. forwarded messages ===========================================================================================

@scenario
async def test_forwarded_message_snapshots_are_sent_marked(e):
    link(e)
    e.fh.files["a.txt"] = (b"zzz", "text/plain")
    snap = [{"message": {"content": "contenu d'origine", "attachments": [{"url": f"{e.fh.url}/cdn/a.txt", "filename": "a.txt", "size": 3, "content_type": "text/plain"}]}}]
    await e.fg.dispatch("MESSAGE_CREATE", msg("", channel="T1", message_snapshots=snap, message_reference={"type": 1, "message_id": "z"}))
    await until(lambda: e.fh.posted)
    body = e.fh.posted[0][1]
    assert body["text"] == "[Transféré]\n> contenu d'origine" and len(body["attachments"]) == 1
    await e.fg.dispatch("MESSAGE_CREATE", msg("vois ça", channel="T1", mid="m2", message_snapshots=snap))
    await until(lambda: len(e.fh.posted) == 2)
    assert e.fh.posted[1][1]["text"].startswith("vois ça\n\n[Transféré]\n> contenu d'origine")
    e.fh.cut()


@scenario
async def test_forwarded_message_without_session_goes_through_the_menu(e):
    snap = [{"message": {"content": "partagé", "attachments": []}}]
    await e.fg.dispatch("MESSAGE_CREATE", msg("", message_snapshots=snap))
    menu = await until(lambda: next((m for m in e.fd.in_channel(PROJECT_CH) if m["components"]), None))
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": menu["components"][0]["components"][0]["custom_id"], "values": [TOPIC]}))
    await until(lambda: e.fh.created)
    assert e.fh.created[0]["message"] == "[Transféré]\n> partagé"
    e.fh.cut()


# === 10. health alerts buttons =======================================================================================

def health(i=7, **kw):
    d = {"id": i, "at": 1.0, "source": "health", "level": "warning", "title": "Disque plein", "body": "", "url": "/settings/system", "count": 1,
         "read": False, "actions": [{"id": "seen", "label": "Seen", "style": "default"}], "ref": f"health:{i}", "agent": None, "resolved": None, "resolution": None}
    d.update(kw)
    return d


def test_health_alert_renders_a_working_seen_button():
    embed, comps = render(health(), "http://web")
    assert comps[0]["components"][0]["custom_id"] == "fa:7:seen" and comps[0]["components"][0]["label"] == "Seen"


@scenario
async def test_health_alert_goes_to_alerts_and_seen_button_closes_it(e):
    e.fh.feed.append(health(6, read=True, actions=[]))
    await e.bot.relay.feed()                                       # first pass: start from now
    e.fh.feed.append(health(7))
    await e.bot.relay.feed()
    m = e.fd.in_channel("CA")[0]
    assert buttons(m)[0]["custom_id"] == "fa:7:seen"
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "fa:7:seen"}, channel="CA", iid="S1"))
    await until(lambda: e.fd.originals)
    assert e.fh.acts == [(7, "seen")]
    assert e.fd.originals[0]["components"] == [] and e.fd.originals[0]["embeds"][0]["fields"][0]["value"] == "Vu"


@scenario
async def test_health_alert_seen_on_the_web_removes_the_button(e):
    await e.bot.relay.feed()
    e.fh.feed.append(health(8))
    await e.bot.relay.feed()
    e.fh.feed[0]["read"] = True                                    # seen in the web UI
    await e.bot.relay.feed()
    assert e.fd.edits and e.fd.edits[-1]["components"] == []
    n = len(e.fd.edits)
    await e.bot.relay.feed()
    assert len(e.fd.edits) == n
    e.fh.feed.append(health(9, ref="approval:abc", actions=[{"id": "allow", "label": "Allow"}], read=False))
    await e.bot.relay.feed()
    e.fh.feed[1]["read"] = True                                    # an approval stays decidable even if read
    await e.bot.relay.feed()
    assert len(e.fd.edits) == n


# === 11. dashboard ===================================================================================================

@scenario
async def test_dashboard_is_posted_pinned_and_edited_only_on_change(e):
    web_ = e.bot.relay
    e.fh.add_session("S1", TOPIC, "a", state="running")
    e.fh.crons.append({"enabled": True, "last_run": {"ok": False}})
    e.fh.crons.append({"enabled": True, "last_run": {"ok": True}})
    e.fh.agents.append({"path": "projects/dev/topics/blog/agents/bob", "running_now": 1, "waiting": 2})
    await web_.dashboard.tick()
    m = e.fd.in_channel("CA")
    assert len(m) == 1 and e.fd.pins == [("CA", m[0]["id"])]
    f = {x["name"]: x["value"] for x in m[0]["embeds"][0]["fields"]}
    assert f["Sessions en cours"] == "1" and f["Fils"] == "0" and f["Jobs planifiés"] == "2 actifs, 1 en échec"
    assert f["Agents"] == "1 (en cours 1, en attente 2)" and f["Dernier relais"] == "rien pour l'instant"
    await web_.dashboard.tick()                                    # < 10 min: nothing
    assert e.fd.edits == []
    st = e.bot.store.data["dashboard"]
    st["at"] -= 700
    await web_.dashboard.tick()                                    # 10 min, nothing changed: no edit
    assert e.fd.edits == []
    st["at"] -= 700
    e.fh.sessions["S1"]["state"] = "idle"
    web_._sessions = (0.0, [])
    await web_.dashboard.tick()
    assert len(e.fd.edits) == 1 and e.fd.edits[0]["id"] == m[0]["id"]
    assert len(e.fd.in_channel("CA")) == 1


@scenario
async def test_dashboard_skips_unavailable_elements_and_survives_a_refused_pin(e):
    e.fd.pin_status = 403
    e.bot.relay.st["last_post"] = 1_800_000_000
    await e.bot.relay.dashboard.tick()
    m = e.fd.in_channel("CA")[0]
    names = [x["name"] for x in m["embeds"][0]["fields"]]
    assert "Agents" in names                                       # the fake offers them; now the ones it does not
    assert [x["value"] for x in m["embeds"][0]["fields"] if x["name"] == "Dernier relais"] == ["<t:1800000000:R>"]
    assert e.fd.pins == [] and e.bot.store.data["dashboard"]["pin"] is False and e.bot.store.data["dashboard"]["pin_warned"]
    e.fd.pin_status = 204
    e.bot.store.data["dashboard"]["at"] -= 700
    await e.bot.relay.dashboard.tick()
    assert e.fd.pins and e.bot.store.data["dashboard"]["pin"] is True


@scenario
async def test_dashboard_recreated_when_deleted_and_switchable(e):
    e.bot.cfg["dashboard"] = False
    await e.bot.relay.dashboard.tick()
    assert e.fd.in_channel("CA") == []
    e.bot.cfg["dashboard"] = True
    await e.bot.relay.dashboard.tick()
    first = e.fd.in_channel("CA")[0]["id"]
    st = e.bot.store.data["dashboard"]
    st["at"] -= 700
    st["hash"] = "other"
    e.fd.fail_next.append((404, {"code": 10008, "message": "Unknown Message"}))
    await e.bot.relay.dashboard.tick()
    ids = [m["id"] for m in e.fd.in_channel("CA")]
    assert len(ids) == 2 and ids[1] != first and st["message"] == ids[1]


@scenario
async def test_dashboard_runs_in_the_relay_tick(e):
    e.bot.cfg.update({"relay_feed": False, "relay_bell": False, "relay_agents": False})
    await e.bot.relay.tick()
    assert len(e.fd.in_channel("CA")) == 1


# === settings ========================================================================================================

def load_api():
    spec = importlib.util.spec_from_file_location("hd_api_c", ROOT / "discord" / "api.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


NEW_FLAGS = ("mirror_web_sessions", "reactions", "answer_buttons", "dashboard")


def test_new_settings_default_true_and_are_strict_booleans(tmp_path, monkeypatch):
    from harness_discord import config
    monkeypatch.setenv("HD_STATE_DIR", str(tmp_path))
    assert all(config.load_settings()[k] is True for k in NEW_FLAGS)
    api = load_api()
    ctx = {"state_dir": str(tmp_path), "secret": lambda n: None}
    assert all(api.handle(ctx, "GET", "", "", None)["config"][k] is True for k in NEW_FLAGS)
    for k in NEW_FLAGS:
        for bad in ("false", 0, None, "yes"):
            status, body = api.handle(ctx, "POST", "config", "", {k: bad})
            assert status == 400 and body["error"] == "bad_flag"
    out = api.handle(ctx, "POST", "config", "", {k: False for k in NEW_FLAGS})
    assert all(out["config"][k] is False for k in NEW_FLAGS)
    assert all(config.load_settings()[k] is False for k in NEW_FLAGS)


def test_page_and_docs_mention_the_new_features():
    page = (ROOT / "discord" / "page.js").read_text()
    for k in NEW_FLAGS:
        assert f'name="{k}"' in page and k in page
    for f in ("discord/page.js", "README.md", "skill/discord-bridge/SKILL.md"):
        text = (ROOT / f).read_text()
        assert "Manage Messages" in text or "Gérer les messages" in text, f
    for f in ("README.md", "skill/discord-bridge/SKILL.md"):
        text = (ROOT / f).read_text()
        for k in NEW_FLAGS:
            assert k in text, (f, k)


def test_texts_are_complete_in_both_languages():
    from harness_discord.texts import T
    assert set(T["fr"]) == set(T["en"])
    assert len(T["fr"]["ctx_new"]) <= 32 and len(T["en"]["ctx_new"]) <= 32 and len(T["fr"]["ctx_send"]) <= 32
