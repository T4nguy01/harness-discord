"""End to end: fake Discord gateway + fake Discord REST + fake Harness HTTP/SSE server, the real Gateway, Rest, Harness, Bot."""
import asyncio
import json

import pytest

from fakes import OWNER, PASSWORD, TOKEN, TOPIC, FakeDiscord, FakeGateway, FakeHarness, REAL_SLEEP, until
from harness_discord.bot import Bot
from harness_discord.discord_api import DiscordError, Gateway, Rest
from harness_discord.harness import Harness, HarnessError
from harness_discord.store import Store

pytestmark = pytest.mark.usefixtures("fast_sleep")

GUILD, PROJECT_CH = "G", "C1"


class Env:
    pass


def scenario(fn=None, *, topics=2, mode="live"):
    def deco(fn):
        def test(tmp_path, fast_sleep):
            async def main():
                e = Env()
                e.fg, e.fd, e.fh = FakeGateway(), FakeDiscord(), FakeHarness()
                await e.fg.start()
                await e.fh.start()
                if topics == 1:
                    e.fh.projects[0]["topics"] = e.fh.projects[0]["topics"][:1]
                e.rest = Rest(TOKEN, transport=e.fd.transport())
                e.h = Harness(e.fh.url, PASSWORD)
                holder = {}

                async def handler(t, d):
                    await holder["bot"].dispatch(t, d)

                e.gw = Gateway(TOKEN, handler, url=e.fg.url + "/?v=10&encoding=json")
                cfg = {"owner_id": OWNER, "reply_mode": mode, "guild_id": GUILD}
                e.bot = holder["bot"] = Bot(e.h, e.rest, e.gw, Store(tmp_path / "state.json"), cfg, "fr", "http://web")
                e.bot.lay["projects"] = {"dev": PROJECT_CH}
                e.bot.lay["alerts"] = "CA"
                e.bot.lay["agents"] = "CAG"
                e.task = asyncio.create_task(e.gw.run())
                try:
                    await until(e.gw.ready.is_set, what="gateway ready")
                    await fn(e)
                finally:
                    e.gw.stop()
                    e.task.cancel()
                    for f in list(e.bot.followers.values()):
                        f.task.cancel()
                    await asyncio.gather(e.task, *[f.task for f in e.bot.followers.values()], return_exceptions=True)
                    await e.rest.close()
                    await e.h.close()
                    await e.fg.stop()
                    await e.fh.stop()
            asyncio.run(main())
        test.__name__ = fn.__name__
        return test
    return deco(fn) if fn else deco


def msg(content="", channel=PROJECT_CH, guild=GUILD, author=OWNER, mid="m1", **kw):
    d = {"id": mid, "channel_id": channel, "content": content, "author": {"id": author}, "type": 0, "attachments": []}
    if guild:
        d["guild_id"] = guild
    d.update(kw)
    return d


def inter(kind, data, channel=PROJECT_CH, guild=GUILD, user=OWNER, iid="I1"):
    d = {"id": iid, "token": "tk-" + iid, "type": kind, "channel_id": channel, "data": data, "member": {"user": {"id": user}}}
    if guild:
        d["guild_id"] = guild
    return d


def link(e, sid="S1", thread="T1", title=""):
    e.fh.add_session(sid, TOPIC, title)
    e.bot.store.link(sid, thread, TOPIC, parent=PROJECT_CH)


def turn(text="Voici la réponse finale.", cost=0.02):
    return [{"type": "assistant", "text": "Je regarde."}, {"type": "tool_use", "name": "Read", "summary": "Read a.py"},
            {"type": "assistant", "text": text}, {"type": "result", "ok": True, "cost_usd": cost, "duration_ms": 3000}]


async def settled(e):
    await until(lambda: not e.bot.followers, what="followers done")


# --- the main flow --------------------------------------------------------------------------------------------------

@scenario
async def test_owner_message_menu_choice_session_thread_stream_status_and_answer(e):
    await e.fg.dispatch("MESSAGE_CREATE", msg("écris un article", mid="m1"))
    menu = await until(lambda: next((m for m in e.fd.in_channel(PROJECT_CH) if m["components"]), None), what="menu")
    assert menu["ref"] == "m1"
    sel = menu["components"][0]["components"][0]
    assert sel["custom_id"].startswith("pick:") and {o["value"] for o in sel["options"]} == {TOPIC, "projects/dev/topics/docs"}
    assert e.fh.created == []                                  # nothing started before the choice

    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": sel["custom_id"], "values": [TOPIC]}))
    await until(lambda: e.fh.created, what="session created")
    assert e.fh.created[0] == {"path": TOPIC, "message": "écris un article"}
    assert e.fd.responses[0]["type"] == 7 and "dev/blog" in e.fd.responses[0]["data"]["content"]
    await until(lambda: e.fd.threads, what="thread")
    assert e.fd.threads[0]["parent"] == PROJECT_CH and e.fd.threads[0]["message"] == "m1" and e.fd.threads[0]["name"].startswith("blog · ")
    assert e.bot.store.session_of_channel("T1") == "S1"
    await until(lambda: e.fh.stream_froms, what="stream opened")
    assert e.fh.stream_froms[0] == ("S1", 0)

    e.fh.sessions["S1"]["title"] = "Mon article"
    e.fh.emit("S1", *turn())
    await until(lambda: any(m["content"] == "Voici la réponse finale." for m in e.fd.in_channel("T1")), what="final answer")
    await settled(e)
    t1 = e.fd.in_channel("T1")
    status = t1[0]
    assert status["orig"].startswith("⏳") and "Read a.py" not in status["orig"] and status["flags"] == 1 << 12     # live status, silent
    assert any(ed["id"] == status["id"] and ed["content"].startswith("✅") and "$" in ed["content"] for ed in e.fd.edits)   # status edited into a summary
    finals = [m for m in t1 if m["content"] == "Voici la réponse finale."]
    assert len(finals) == 1 and t1.index(finals[0]) > 0
    assert e.fd.typing >= 1
    assert e.fd.renamed == [("T1", "blog · Mon article")]                          # thread renamed from the session title
    assert finals[0]["id"] in e.bot.msg_session

    # a message in the thread continues the same session
    await e.fg.dispatch("MESSAGE_CREATE", msg("encore", channel="T1", mid="m2"))
    await until(lambda: e.fh.posted, what="message forwarded")
    assert e.fh.posted == [("S1", {"text": "encore"})]
    assert len(e.fh.created) == 1
    await until(lambda: e.bot.followers.get("S1") and len(e.fh.stream_froms) >= 2, what="second stream")
    assert e.fh.stream_froms[-1][1] > 0                                        # follows from the end of the log, not 0
    e.fh.emit("S1", {"type": "assistant", "text": "Deuxième réponse."}, {"type": "result", "ok": True, "duration_ms": 10})
    await until(lambda: any(m["content"] == "Deuxième réponse." for m in e.fd.in_channel("T1")), what="second answer")
    assert len(e.fh.created) == 1
    await settled(e)


@scenario(topics=1)
async def test_single_topic_project_skips_the_menu(e):
    await e.fg.dispatch("MESSAGE_CREATE", msg("salut", mid="m9"))
    await until(lambda: e.fh.created, what="session")
    assert e.fh.created[0]["path"] == TOPIC
    assert not any(m["components"] for m in e.fd.messages)
    e.fh.emit("S1", {"type": "assistant", "text": "ok"}, {"type": "result", "ok": True, "duration_ms": 1})
    await until(lambda: any(m["content"] == "ok" for m in e.fd.in_channel("T1")))
    await settled(e)


@scenario(mode="final")
async def test_final_mode_sends_no_status_message(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1"))
    await until(lambda: e.fh.stream_froms)
    e.fh.emit("S1", *turn("fin"))
    await until(lambda: any(m["content"] == "fin" for m in e.fd.in_channel("T1")))
    await settled(e)
    assert [m["content"] for m in e.fd.in_channel("T1")] == ["fin"] and e.fd.edits == []


@scenario
async def test_menu_choice_expired_and_project_pick(e):
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "pick:dead", "values": [TOPIC]}))
    await until(lambda: e.fd.responses)
    assert e.fd.responses[0]["type"] == 7 and e.fd.responses[0]["data"]["components"] == [] and e.fh.created == []


# --- attachments -------------------------------------------------------------------------------------------------------

@scenario
async def test_attachment_is_downloaded_uploaded_and_referenced(e):
    link(e)
    e.fh.files["a.txt"] = (b"hello file", "text/plain")
    e.fh.files["v.ogg"] = (b"OggS", "audio/ogg")
    att = [{"url": f"{e.fh.url}/cdn/a.txt", "filename": "a.txt", "size": 10, "content_type": "text/plain"}]
    await e.fg.dispatch("MESSAGE_CREATE", msg("voici", channel="T1", attachments=att))
    await until(lambda: e.fh.posted, what="forward")
    assert e.fh.uploads == [{"topic": TOPIC, "name": "a.txt", "mime": "text/plain", "data": b"hello file"}]
    assert e.fh.posted[0][1] == {"text": "voici", "attachments": [f"/root/maxxing/{TOPIC}/attachments/2026-11/a.txt"]}
    voice = [{"url": f"{e.fh.url}/cdn/v.ogg", "filename": "voice-message.ogg", "size": 4, "content_type": "audio/ogg", "flags": 1 << 13}]
    await e.fg.dispatch("MESSAGE_CREATE", msg("", channel="T1", mid="m2", attachments=voice))
    await until(lambda: len(e.fh.posted) == 2, what="voice forward")
    body = e.fh.posted[1][1]
    assert body["text"] == "(voice)" and body["voice"].endswith("voice-message.ogg") and "attachments" not in body
    e.fh.cut()


@scenario(topics=1)
async def test_attachment_on_new_session_and_download_failure(e):
    e.fh.files["a.txt"] = (b"x", "text/plain")
    att = [{"url": f"{e.fh.url}/cdn/missing", "filename": "bad.txt", "size": 1}, {"url": f"{e.fh.url}/cdn/a.txt", "filename": "a.txt", "size": 1}]
    await e.fg.dispatch("MESSAGE_CREATE", msg("fichiers", attachments=att))
    await until(lambda: e.fh.created, what="session")
    # missing file: user told, the rest still goes
    assert e.fh.created[0]["attachments"] == [f"/root/maxxing/{TOPIC}/attachments/2026-11/a.txt"]
    assert any("bad.txt" in m["content"] for m in e.fd.in_channel(PROJECT_CH))


# --- commands -----------------------------------------------------------------------------------------------------------

@scenario
async def test_slash_commands_status_stop_close(e):
    link(e, title="Titre")
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "status"}, channel="T1", iid="I1"))
    await until(lambda: e.fd.followups, what="status followup")
    assert e.fd.responses[0]["type"] == 5 and e.fd.responses[0]["data"]["flags"] == 64
    assert "http://web/sessions/S1" in e.fd.followups[0]["content"] and "Titre" in e.fd.followups[0]["content"]

    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "stop"}, channel="T1", iid="I2"))
    await until(lambda: e.fh.stopped == ["S1"], what="stop")
    await until(lambda: len(e.fd.followups) == 2)

    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "model", "options": [{"name": "id", "value": "sonnet"}]}, channel="T1", iid="I3"))
    await until(lambda: e.fh.sessions["S1"]["model"] == "sonnet")

    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "close"}, channel="T1", iid="I4"))
    await until(lambda: e.fd.deleted_channels == ["T1"], what="thread deleted")
    assert e.fh.sessions["S1"]["archived"] is True and e.bot.store.session_of_channel("T1") is None
    assert len(e.fd.followups) == 4


@scenario
async def test_stop_ends_the_follower(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("long job", channel="T1"))
    await until(lambda: "S1" in e.bot.followers and e.fh.stream_froms)
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "stop"}, channel="T1"))
    await until(lambda: e.fh.stopped)
    e.fh.cut()                                  # stream ends; expect is 0 so the follower stops instead of reconnecting
    await settled(e)


@scenario
async def test_command_without_session_and_help_and_commands_registered(e):
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "stop"}, channel=PROJECT_CH, iid="I1"))
    await until(lambda: e.fd.followups)
    assert e.fh.stopped == []
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "help"}, iid="I2"))
    await until(lambda: len(e.fd.responses) == 2)
    assert e.fd.responses[1]["type"] == 4
    await e.rest.register_commands(e.gw.app_id, await e.bot.commands())
    assert {c["name"] for c in e.fd.commands} == {"new", "sessions", "stop", "close", "model", "status", "help"}


@scenario
async def test_slash_new_in_project_channel_then_pick(e):
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "new", "options": [{"name": "title", "value": "Idée"}]}))
    await until(lambda: e.fd.responses)
    r = e.fd.responses[0]
    assert r["type"] == 4
    cid = r["data"]["components"][0]["components"][0]["custom_id"]
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": cid, "values": ["projects/dev/topics/docs"]}, iid="I2"))
    await until(lambda: e.fh.created)
    assert e.fh.created[0] == {"path": "projects/dev/topics/docs", "title": "Idée"}
    await until(lambda: any("started" in m["content"] or "dev/docs" in m["content"] for m in e.fd.messages) or e.fd.threads)


# --- security --------------------------------------------------------------------------------------------------------

@scenario
async def test_unknown_user_and_bots_are_ignored(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("hack", author="99", mid="s1"))
    await e.fg.dispatch("MESSAGE_CREATE", msg("hack", author="99", channel="T1", mid="s2"))
    bot_msg = msg("echo", mid="s3")
    bot_msg["author"]["bot"] = True
    await e.fg.dispatch("MESSAGE_CREATE", bot_msg)
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "stop"}, channel="T1", user="99", iid="I9"))
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "pick:xx", "values": [TOPIC]}, user="99", iid="I8"))
    await until(lambda: len(e.fd.responses) == 2)
    await REAL_SLEEP(0.2)
    assert all(r["type"] == 4 and r["data"]["flags"] == 64 for r in e.fd.responses)
    assert e.fd.messages == [] and e.fh.created == [] and e.fh.posted == [] and e.fh.stopped == []
    assert e.fh.log == []                                       # Harness was never called


# --- Harness errors and recovery ------------------------------------------------------------------------------------

@scenario
async def test_harness_401_logs_in_again_and_retries(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("un", channel="T1", mid="m1"))
    await until(lambda: len(e.fh.posted) == 1)
    assert e.fh.logins == 1
    e.fh.emit("S1", {"type": "assistant", "text": "r1"}, {"type": "result", "ok": True, "duration_ms": 1})
    await until(lambda: any(m["content"] == "r1" for m in e.fd.in_channel("T1")))
    await settled(e)
    e.fh.revoke()                                               # cookie lost (restart, password change...)
    await e.fg.dispatch("MESSAGE_CREATE", msg("deux", channel="T1", mid="m2"))
    await until(lambda: len(e.fh.posted) == 2, what="retry after 401")
    assert e.fh.logins == 2
    assert not any("401" in m["content"] or "err" in m["content"].lower() for m in e.fd.in_channel("T1"))
    e.fh.emit("S1", {"type": "assistant", "text": "r2"}, {"type": "result", "ok": True, "duration_ms": 1})
    await until(lambda: any(m["content"] == "r2" for m in e.fd.in_channel("T1")))
    await settled(e)


@scenario
async def test_stream_401_during_a_turn_recovers(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1"))
    await until(lambda: e.fh.stream_froms)
    e.fh.revoke()
    e.fh.cut()                                                  # the open stream dies, the reconnect gets 401, then login
    await until(lambda: e.fh.logins == 2, what="second login")
    await until(lambda: len(e.fh.stream_froms) >= 2, what="stream reopened")
    e.fh.emit("S1", {"type": "assistant", "text": "revenu"}, {"type": "result", "ok": True, "duration_ms": 1})
    await until(lambda: any(m["content"] == "revenu" for m in e.fd.in_channel("T1")), what="answer after 401")
    await settled(e)


@scenario
async def test_wrong_password_reports_error_to_the_user(e):
    link(e)
    e.h.password = "nope"
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1"))
    await until(lambda: e.fd.in_channel("T1"), what="error message")
    assert "bad_password" in e.fd.in_channel("T1")[0]["content"] and e.fh.posted == []


@scenario
async def test_session_gone_unlinks_the_thread(e):
    e.bot.store.link("S404", "T1", TOPIC, parent=PROJECT_CH)     # the session does not exist in Harness
    await e.fg.dispatch("MESSAGE_CREATE", msg("hello?", channel="T1"))
    await until(lambda: e.fd.in_channel("T1"))
    assert e.bot.store.session_of_channel("T1") is None


@scenario
async def test_archived_session_is_not_continued(e):
    link(e)
    e.fh.sessions["S1"]["archived"] = True
    await e.fg.dispatch("MESSAGE_CREATE", msg("hello?", channel="T1"))
    await until(lambda: e.fd.in_channel("T1"))
    assert e.fh.posted == []


# --- SSE cut ----------------------------------------------------------------------------------------------------------

@scenario
async def test_sse_cut_resumes_from_offset_without_duplicate_answer(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1"))
    await until(lambda: e.fh.stream_froms)
    start = e.fh.stream_froms[0][1]
    e.fh.emit("S1", {"type": "assistant", "text": "Partie A"})
    await until(lambda: e.bot.followers["S1"].cur == ["Partie A"], what="first event consumed")
    off = e.bot.followers["S1"].offset
    assert off > start
    e.fh.cut()
    await until(lambda: len(e.fh.stream_froms) == 2, what="reconnect")
    assert e.fh.stream_froms[1] == ("S1", off)                  # resumed where it stopped, not from the beginning
    e.fh.emit("S1", {"type": "result", "ok": True, "duration_ms": 5})
    await until(lambda: any("Partie A" in m["content"] for m in e.fd.in_channel("T1") if not m["content"].startswith(("⏳", "✅ Terminé"))), what="answer")
    await settled(e)
    answers = [m["content"] for m in e.fd.in_channel("T1") if not m["content"].startswith(("⏳", "✅ Terminé"))]
    assert answers == ["Partie A"]                              # sent once, not repeated after the cut


@scenario
async def test_sse_cut_twice_still_one_answer(e):
    link(e)
    await e.fg.dispatch("MESSAGE_CREATE", msg("go", channel="T1"))
    await until(lambda: e.fh.stream_froms)
    for i, txt in enumerate(["un", "deux"]):
        e.fh.emit("S1", {"type": "assistant", "text": txt})
        await until(lambda: e.bot.followers["S1"].last_text == txt)
        e.fh.cut()
        await until(lambda: len(e.fh.stream_froms) == i + 2)
    e.fh.emit("S1", {"type": "result", "ok": True, "duration_ms": 5})
    await settled(e)
    answers = [m["content"] for m in e.fd.in_channel("T1") if not m["content"].startswith(("⏳", "✅ Terminé"))]
    assert answers == ["un\n\ndeux"]


# --- feed relay: alerts with Allow / Deny buttons -------------------------------------------------------------------

def alert(eid=2, **kw):
    d = {"id": eid, "at": 1.0, "source": "security", "level": "warning", "title": "Autoriser ?", "body": "rm -rf /tmp/x", "url": "", "count": 1,
         "read": False, "actions": [{"id": "allow", "label": "Autoriser", "style": "primary"}, {"id": "deny", "label": "Refuser", "style": "danger"}],
         "ref": "approval:7", "agent": None, "resolved": None, "resolution": None}
    d.update(kw)
    return d


@scenario
async def test_feed_alert_buttons_allow_and_deny(e):
    e.fh.feed = [alert(1, actions=[], title="ancien")]
    await e.bot.relay.feed()                                    # first run: baseline, history is not replayed
    assert e.fd.messages == []
    e.fh.feed += [alert(2), alert(3, title="Deuxième")]
    await e.bot.relay.feed()
    posted = e.fd.in_channel("CA")
    assert len(posted) == 2 and "Autoriser ?" in posted[0]["content"]
    btns = posted[0]["components"][0]["components"]
    assert [b["custom_id"] for b in btns] == ["fa:2:allow", "fa:2:deny"] and [b["style"] for b in btns] == [1, 4]
    await e.bot.relay.feed()
    assert len(e.fd.in_channel("CA")) == 2                      # no repost

    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "fa:2:allow"}, channel="CA", iid="IA"))
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "fa:3:deny"}, channel="CA", iid="IB"))
    await until(lambda: len(e.fd.responses) == 2)
    assert sorted(e.fh.acts) == [(2, "allow"), (3, "deny")]
    by = {r["id"]: r for r in e.fd.responses}
    assert by["IA"]["type"] == 7 and "Allowed" in by["IA"]["data"]["content"] and by["IA"]["data"].get("components") in ([], None)
    assert "Denied" in by["IB"]["data"]["content"]


@scenario
async def test_feed_double_click_and_stranger_click(e):
    e.fh.feed = [alert(1)]
    await e.bot.relay.feed()
    e.fh.feed.append(alert(2))
    await e.bot.relay.feed()
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "fa:2:allow"}, channel="CA", user="99", iid="I0"))
    await until(lambda: e.fd.responses)
    assert e.fh.acts == [] and e.fd.responses[0]["data"]["flags"] == 64
    for n in (1, 2):
        await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "fa:2:allow"}, channel="CA", iid=f"I{n}"))
    await until(lambda: len(e.fd.responses) == 3)
    assert all("Allowed" in r["data"]["content"] for r in e.fd.responses[1:])   # idempotent on the server


@scenario
async def test_feed_alert_resolved_elsewhere_is_edited_and_act_error(e):
    e.fh.feed = [alert(1, actions=[])]
    await e.bot.relay.feed()
    e.fh.feed.append(alert(2))
    await e.bot.relay.feed()
    mid = e.fd.in_channel("CA")[0]["id"]
    e.fh.feed[1].update(resolved=5.0, resolution="Fixed", actions=[])      # handled in the web UI
    await e.bot.relay.feed()
    assert e.fd.edits[-1]["id"] == mid and "Fixed" in e.fd.edits[-1]["content"] and e.fd.edits[-1]["components"] == []
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "fa:99:allow"}, channel="CA"))     # unknown event -> 404
    await until(lambda: e.fd.responses)
    assert e.fd.responses[0]["type"] == 4 and e.fd.responses[0]["data"]["flags"] == 64


@scenario
async def test_feed_routing_and_quiet_levels(e):
    e.bot.lay.update(crons="CC", notifications="CN")
    e.fh.feed = [alert(1, actions=[])]
    await e.bot.relay.feed()
    e.fh.feed += [alert(2, source="crons", actions=[], level="info", title="job"), alert(3, source="shop", actions=[], level="success", title="vente")]
    await e.bot.relay.feed()
    assert [m["channel"] for m in e.fd.messages] == ["CC", "CN"] and all(m["flags"] == 1 << 12 for m in e.fd.messages)


# --- agents relay ------------------------------------------------------------------------------------------------------

@scenario
async def test_agent_result_relayed_and_owner_reply_runs_agent(e):
    path = "projects/dev/topics/blog/agents/writer"
    e.fh.agents = [{"path": path, "project": "dev", "topic": "blog", "name": "writer", "display_name": "Writer"}]
    e.fh.agent_msgs["dev/blog/writer"] = [{"kind": "run", "at": 10.0, "text": "old"}]
    await e.bot.relay.agents()                                  # baseline
    assert e.fd.messages == []
    e.fh.agent_msgs["dev/blog/writer"].append({"kind": "result", "at": 20.0, "text": "Article prêt", "ok": True, "cost": 0.05})
    await e.bot.relay.agents()
    assert e.fd.threads[0]["parent"] == "CAG" and "Writer" in e.fd.threads[0]["name"]
    out = e.fd.in_channel("T1")
    assert out[0]["content"].startswith("Article prêt") and "0.05" in out[0]["content"]
    await e.bot.relay.agents()
    assert len(e.fd.in_channel("T1")) == 1                      # not repeated
    await e.fg.dispatch("MESSAGE_CREATE", msg("fais une v2", channel="T1", mid="m5"))
    await until(lambda: e.fh.agent_runs, what="agent run")
    assert e.fh.agent_runs == [("dev/blog/writer", {"text": "fais une v2"})]
    await until(lambda: e.fd.reactions)
    assert e.fd.reactions[0][:2] == ("T1", "m5") and e.fh.created == []


# --- Rest ---------------------------------------------------------------------------------------------------------------

def test_rest_retries_429_and_5xx_and_raises_on_4xx(fast_sleep):
    async def main():
        fd = FakeDiscord()
        rest = Rest(TOKEN, transport=fd.transport())
        fd.fail_next = [(429, {"retry_after": 0.01}), (502, {})]
        m = await rest.send("C1", "hello")
        assert m["id"] and len(fd.messages) == 1
        assert [c for c in fd.calls if c[0] == "POST"].__len__() == 3          # 429, 502, then success
        fd.fail_next = [(403, {"message": "Missing Access", "code": 50001})]
        with pytest.raises(DiscordError) as ei:
            await rest.send("C1", "x")
        assert ei.value.status == 403 and ei.value.body["code"] == 50001
        await rest.react("C1", "5", "✅")
        assert fd.reactions == [("C1", "5", "✅")]
        fd.fail_next = [(403, {})]
        await rest.typing("C1")                                                  # errors swallowed
        await rest.close()
    asyncio.run(main())


def test_harness_client_sse_parsing_and_two_factor(fast_sleep):
    async def main():
        fh = FakeHarness()
        await fh.start()
        fh.add_session()
        fh.emit("S1", {"type": "assistant", "text": "a"})
        h = Harness(fh.url, PASSWORD)
        seen = []
        async for kind, data in h.stream("S1", 0):
            seen.append((kind, data))
            if kind == "events":
                break
        assert seen[0][0] == "state" and seen[0][1]["id"] == "S1"
        assert seen[1] == ("events", {"events": [{"type": "assistant", "text": "a"}], "next": 1})
        assert await h.tail("S1") == 1 and await h.language() == "fr"
        assert (await h.upload(TOPIC, "f.txt", b"abc")).endswith("/f.txt") and fh.uploads[0]["data"] == b"abc"
        with pytest.raises(HarnessError) as ei:
            await h.request("GET", "/api/nothing")
        assert ei.value.status == 404 and ei.value.code == "not_found"
        await h.close()
        await fh.stop()
    asyncio.run(main())


# --- direct messages ------------------------------------------------------------------------------------------------------

@scenario
async def test_dm_flow_menu_then_reply_to_bot_message_continues(e):
    await e.fg.dispatch("MESSAGE_CREATE", msg("bonjour", channel="D1", guild=None, mid="d1"))
    menu = await until(lambda: next((m for m in e.fd.in_channel("D1") if m["components"]), None), what="DM menu")
    cid = menu["components"][0]["components"][0]["custom_id"]
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": cid, "values": [TOPIC]}, channel="D1", guild=None))
    await until(lambda: e.fh.created, what="session")
    assert e.fd.threads == [] and e.bot.store.data["dm_current"] == "S1"          # no thread in a DM
    e.fh.emit("S1", {"type": "assistant", "text": "Salut !"}, {"type": "result", "ok": True, "duration_ms": 1})
    await until(lambda: any(m["content"] == "Salut !" for m in e.fd.in_channel("D1")), what="DM answer")
    await settled(e)
    answer = next(m for m in e.fd.in_channel("D1") if m["content"] == "Salut !")
    await e.fg.dispatch("MESSAGE_CREATE", msg("et ensuite", channel="D1", guild=None, mid="d2",
                                               message_reference={"message_id": answer["id"]}))
    await until(lambda: e.fh.posted, what="reply forwarded")
    assert e.fh.posted[0][0] == "S1" and len(e.fh.created) == 1
    e.fh.cut()
