"""Audit fixes (lot A): reliability and security. Each test fails on the code before the fix."""
import asyncio
import importlib.util
import json
import logging
import re
import time
from pathlib import Path

import httpx
import pytest

import harness_discord.bot as botmod
import harness_discord.follow as followmod
from fakes import OWNER, PASSWORD, TOKEN, TOPIC, FakeGateway, FakeHarness, until
from harness_discord import discord_api, layout, relay
from harness_discord.bot import Bot, Pending
from harness_discord.discord_api import DiscordError, Gateway, Rest
from harness_discord.harness import Harness, HarnessError, check_sid
from harness_discord.relay import Relay, render
from harness_discord.store import Store
from test_bot import FakeGw, FakeHarness as UnitHarness, FakeRest, make, msg, run, settle
from test_integration_bot import inter, scenario
from test_relay import Feed, ev


# === 1. feed =========================================================================================================

class Flaky(FakeRest):
    def __init__(self, fails):
        super().__init__()
        self.fails = fails

    async def send(self, *a, **k):
        if self.fails:
            self.fails -= 1
            raise DiscordError(503, "down")
        return await super().send(*a, **k)


def feed_bot(tmp_path, rest=None):
    bot, r, h = make(tmp_path)
    if rest:
        bot.rest = rest
    bot.lay.update({"alerts": "A", "crons": "K", "notifications": "N", "agents": "G"})
    return bot


def test_button_label_falls_back_to_the_action_id():
    _, comps = render(ev(1, actions=[{"id": "seen"}, {"id": "open"}], url="/x"), "http://h")
    assert comps[0]["components"][0]["label"] == "seen"
    assert comps[0]["components"][1]["label"] == "open"


def test_malformed_event_is_skipped_and_feed_last_advances(tmp_path):
    async def go():
        bot = feed_bot(tmp_path)
        events = [ev(1)]
        bot.h = Feed(events)
        r = Relay(bot)
        await r.feed()
        events += [ev(2, count="x"), ev(3, "shop")]               # the first one cannot be rendered
        await r.feed()
        assert r.st["feed_last"] == 3 and len(bot.rest.sent) == 1 and "T3" in bot.rest.sent[0]["embeds"][0]["title"]
    run(go())


def test_transient_discord_error_retries_next_tick_then_gives_up(tmp_path):
    async def go():
        bot = feed_bot(tmp_path, Flaky(99))
        events = [ev(1)]
        bot.h = Feed(events)
        r = Relay(bot)
        await r.feed()
        events.append(ev(2, "shop"))
        await r.feed()
        assert r.st["feed_last"] == 1                             # tick 1: kept for a retry
        await r.feed()
        assert r.st["feed_last"] == 1                             # tick 2: kept again
        await r.feed()
        assert r.st["feed_last"] == 2                             # attempt 3: skipped for good
    run(go())


def test_transient_error_then_success_posts_once(tmp_path):
    async def go():
        bot = feed_bot(tmp_path, Flaky(1))
        events = [ev(1)]
        bot.h = Feed(events)
        r = Relay(bot)
        await r.feed()
        events.append(ev(2, "shop"))
        await r.feed()
        assert r.st["feed_last"] == 1 and bot.rest.sent == []
        await r.feed()
        assert r.st["feed_last"] == 2 and len(bot.rest.sent) == 1
    run(go())


def test_permanent_discord_error_is_skipped_at_once(tmp_path):
    class Forbidden(FakeRest):
        async def send(self, *a, **k):
            raise DiscordError(403, {"code": 50013})

    async def go():
        bot = feed_bot(tmp_path, Forbidden())
        events = [ev(1)]
        bot.h = Feed(events)
        r = Relay(bot)
        await r.feed()
        events.append(ev(2, "shop"))
        await r.feed()
        assert r.st["feed_last"] == 2
    run(go())


def test_tick_isolates_feed_bell_and_agents_and_logs_harness_down_briefly(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HD_STATE_DIR", str(tmp_path))

    class Down:
        async def request(self, *a, **k):
            raise httpx.ConnectError("refused")

    async def go():
        bot = feed_bot(tmp_path)
        bot.gw.ready = asyncio.Event()
        bot.h = Down()
        r = Relay(bot)
        polled = []

        async def poll():
            polled.append(1)
        r.bell.poll = poll
        await r.tick()
        assert polled == [1]                                       # the bell ran although the feed failed
    with caplog.at_level(logging.INFO, logger="harness_discord.relay"):
        run(go())
    assert not [x for x in caplog.records if x.exc_info]            # no traceback for Harness being down
    assert any("unreachable" in x.getMessage() for x in caplog.records)


# === 2. agents =======================================================================================================

APATH = "projects/p/topics/t/agents/a1"
BPATH = "projects/p/topics/t/agents/b1"


class AgentsH:
    def __init__(self, threads):
        self.threads = threads

    async def request(self, method, path, **kw):
        if path == "/api/agents":
            return {"agents": [{"path": p, "project": "p", "topic": "t", "name": p.split("/")[-1]} for p in self.threads]}
        name = re.fullmatch(r"/api/agents/p/t/(\w+)/thread", path)[1]
        v = self.threads[f"projects/p/topics/t/agents/{name}"]
        if isinstance(v, Exception):
            raise v
        return {"messages": v}


def res(at, text="ok"):
    return {"kind": "result", "at": at, "text": text, "ok": True, "cost": 0.1}


def agent_bot(tmp_path, threads, rest=None):
    bot = feed_bot(tmp_path, rest)
    bot.h = AgentsH(threads)
    r = Relay(bot)
    for p in threads:
        r.st["agents"][p] = {"seen": 0, "thread": "TH-" + p[-2:]}
    return bot, r


def test_one_failing_agent_does_not_block_the_others(tmp_path):
    async def go():
        bot, r = agent_bot(tmp_path, {APATH: HarnessError(500, "boom"), BPATH: [res(5)]})
        await r.agents()
        assert [s["channel"] for s in bot.rest.sent] == ["TH-b1"]
    run(go())


def test_deleted_agent_thread_is_cleared(tmp_path):
    class Gone(FakeRest):
        async def send(self, *a, **k):
            raise DiscordError(404, {"code": 10003, "message": "Unknown Channel"})

    async def go():
        bot, r = agent_bot(tmp_path, {APATH: [res(5)]}, Gone())
        await r.agents()
        assert r.st["agents"][APATH]["thread"] == ""
    run(go())


def test_thread_delete_event_resets_the_agent_thread(tmp_path):
    async def go():
        bot, r = agent_bot(tmp_path, {APATH: []})
        bot.relay = r
        await bot.dispatch("THREAD_DELETE", {"id": "TH-a1"})
        assert r.st["agents"][APATH]["thread"] == ""
    run(go())


def test_agent_result_is_saved_after_each_message(tmp_path):
    class Crash(BaseException):
        pass

    class Dies(FakeRest):
        async def send(self, *a, **k):
            if self.sent:
                raise Crash()
            return await super().send(*a, **k)

    async def go():
        bot, r = agent_bot(tmp_path, {APATH: [res(5, "one"), res(6, "two")]}, Dies())
        with pytest.raises(Crash):
            await r.agents()
        saved = json.loads((tmp_path / "state.json").read_text())
        assert saved["relay"]["agents"][APATH]["seen"] == 5          # "one" is never posted again after a restart
    run(go())


def test_long_agent_result_is_cut_to_five_parts_with_a_note(tmp_path):
    async def go():
        bot, r = agent_bot(tmp_path, {APATH: [res(5, ("mot " * 1000 + "\n") * 8)]})
        await r.agents()
        assert len(bot.rest.sent) == 5
        assert "tronqué" in bot.rest.sent[-1]["embeds"][0]["description"]
        assert all(len(s["embeds"][0]["description"]) <= 4096 for s in bot.rest.sent)
    run(go())


# === 3. gateway ======================================================================================================

def gw_run(fn, token=TOKEN, **kw):
    async def main():
        fg = FakeGateway()
        await fg.start()

        async def handler(t, d):
            pass
        gw = Gateway(token, handler, url=fg.url + "/?v=10&encoding=json")
        task = asyncio.create_task(gw.run())
        try:
            await fn(fg, gw, task)
        finally:
            gw.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await fg.stop()
    asyncio.run(main())


def test_invalid_token_is_fatal_and_not_retried(fast_sleep):
    async def fn(fg, gw, task):
        await asyncio.wait_for(task, 5)                              # run() returns instead of looping
        assert "4004" in gw.fatal and "token" in gw.fatal and fg.connections == 1 and not gw.ready.is_set()
    gw_run(fn, token="wrong")


def test_op7_and_op9_close_with_4000_so_resume_stays_possible(fast_sleep):
    async def fn(fg, gw, task):
        await until(gw.ready.is_set)
        await fg.op(7)
        await until(lambda: len(fg.resumes) == 1, what="RESUME")
        assert fg.close_codes[0] == 4000
        await until(gw.ready.is_set)
        await fg.op(9, True)
        await until(lambda: len(fg.resumes) == 2, what="second RESUME")
        assert fg.close_codes[1] == 4000 and len(fg.identifies) == 1
    gw_run(fn)


def test_reconnect_delay_resets_after_a_stable_connection(fast_sleep, monkeypatch, caplog):
    def delays():
        return [m for m in re.findall(r"reconnecting in (\d+)s", caplog.text)]

    async def drops(fg, gw, task):
        for n in (1, 2, 3):
            await until(lambda: fg.connections == n and gw.ready.is_set())
            await fg.drop(1011)
        await until(lambda: fg.connections == 4)

    with caplog.at_level(logging.WARNING, logger="harness_discord.discord"):
        gw_run(drops)
        assert delays()[:3] == ["1", "2", "4"]
        caplog.clear()
        monkeypatch.setattr(discord_api, "STABLE_AFTER", -1.0)         # every connection counts as stable
        gw_run(drops)
        assert delays()[:3] == ["1", "1", "1"]


def test_fatal_gateway_makes_the_process_exit_with_an_error_status(tmp_path, monkeypatch):
    from harness_discord import __main__ as m

    async def main():
        fg, fh = FakeGateway(), FakeHarness()
        await fg.start()
        await fh.start()
        monkeypatch.setattr(Gateway, "URL", fg.url + "/?v=10&encoding=json")
        for k, v in {"DISCORD_BOT_TOKEN": "bad", "HARNESS_PASSWORD": PASSWORD, "DISCORD_OWNER_ID": OWNER,
                     "HARNESS_URL": fh.url, "HD_STATE_DIR": str(tmp_path)}.items():
            monkeypatch.setenv(k, v)
        try:
            return await asyncio.wait_for(m.amain(), 10)
        finally:
            await fg.stop()
            await fh.stop()
    assert asyncio.run(main()) == 4
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["connected"] is False and "4004" in st["error"]


# === 4. followers ====================================================================================================

class StreamH(UnitHarness):
    def __init__(self, exc=None):
        super().__init__()
        self.exc, self.opened = exc, 0

    async def stream(self, sid, offset):
        self.opened += 1
        if self.exc:
            raise self.exc
        yield "state", {"state": "running"}
        await asyncio.sleep(100)


def test_follower_gives_up_at_once_on_404(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        bot.h = StreamH(HarnessError(404, "not_found"))
        await bot.follow("S1", "T1", 0, 1)
        await asyncio.wait_for(settle(bot), 3)
        assert bot.h.opened == 1 and bot.store.data["followers"] == {}
    run(go())


def test_follower_gives_up_after_consecutive_failures(tmp_path, fast_sleep):
    async def go():
        bot, rest, h = make(tmp_path)
        bot.h = StreamH(HarnessError(502, "bad_gateway"))
        await bot.follow("S1", "T1", 0, 1)
        await asyncio.wait_for(settle(bot), 5)
        assert bot.h.opened == followmod.FOLLOW_MAX_FAILS
    run(go())


def test_follower_gives_up_when_failing_for_too_long(tmp_path, fast_sleep, monkeypatch):
    monkeypatch.setattr(followmod, "FOLLOW_GIVE_UP", -1)
    async def go():
        bot, rest, h = make(tmp_path)
        bot.h = StreamH(httpx.ConnectError("refused"))
        await bot.follow("S1", "T1", 0, 1)
        await asyncio.wait_for(settle(bot), 5)
        assert bot.h.opened == 1
    run(go())


def test_result_with_null_cost_still_ends_the_turn(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        h.events = [{"type": "assistant", "text": "Salut"}, {"type": "result", "ok": True, "cost_usd": None, "duration_ms": None}]
        await bot.follow("S1", "T1", 0, 1)
        await asyncio.wait_for(settle(bot), 3)
        assert rest.sent[-1]["content"] == "Salut" and "0.00 $" in rest.edits[-1][2]
    run(go())


def test_a_failing_event_does_not_stop_the_next_ones(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        rest2 = Flaky(1)
        bot.rest = rest2
        h.events = [{"type": "note", "level": "error", "text": "oups"}, {"type": "assistant", "text": "Réponse"},
                    {"type": "result", "ok": True, "cost_usd": 0.1, "duration_ms": 1000}]
        await bot.follow("S1", "T1", 0, 1)
        await asyncio.wait_for(settle(bot), 3)
        assert rest2.sent and rest2.sent[-1]["content"] == "Réponse"
    run(go())


def test_active_followers_are_saved_and_resumed_after_restart(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        bot.h = StreamH()
        await bot.follow("S1", "T1", 5, 2)
        saved = json.loads((tmp_path / "state.json").read_text())["followers"]["S1"]
        assert saved["channel"] == "T1" and saved["offset"] == 5 and saved["expect"] == 2
        bot.followers["S1"].task.cancel()                              # the service stops here...
        data = json.loads((tmp_path / "state.json").read_text())
        data["followers"] = {"S1": saved, "OLD": {**saved, "at": time.time() - 3600}}
        (tmp_path / "state.json").write_text(json.dumps(data))
        bot2, _, _ = make(tmp_path)                                    # ...and starts again
        bot2.h = StreamH()
        bot2.resume_followers()
        assert set(bot2.followers) == {"S1"}                           # the stale one is not resumed
        f = bot2.followers["S1"]
        assert (f.channel, f.offset, f.expect) == ("T1", 5, 2)
        f.task.cancel()
        f.typing.cancel()
        await asyncio.gather(f.task, f.typing, return_exceptions=True)
    run(go())


def test_follow_end_is_purged(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        bot.follow_end["old"] = time.time() - 7200
        h.events = [{"type": "result", "ok": True, "cost_usd": 0, "duration_ms": 0}]
        await bot.follow("S1", "T1", 0, 1)
        await asyncio.wait_for(settle(bot), 3)
        assert "old" not in bot.follow_end and "S1" in bot.follow_end
    run(go())


# === 5. interactions answer first ====================================================================================

class Ordered:
    """Shared call log between a fake Discord and a fake Harness."""
    def __init__(self):
        self.log = []


def ordered_bot(tmp_path, channel="C1"):
    o = Ordered()
    bot, rest, h = make(tmp_path, channel=channel)

    orig_respond = rest.respond

    async def respond(*a, **k):
        o.log.append("respond")
        return await orig_respond(*a, **k)
    rest.respond = respond

    orig_tree = h.tree

    async def tree():
        o.log.append("tree")
        return await orig_tree()
    h.tree = tree
    return bot, rest, h, o


def ci(kind, data, channel="C1", guild="G"):
    return {"type": kind, "id": "i", "token": "t", "channel_id": channel, "guild_id": guild, "member": {"user": {"id": OWNER}}, "data": data}


def test_menu_choice_and_slash_new_answer_before_calling_harness(tmp_path):
    async def go():
        bot, rest, h, o = ordered_bot(tmp_path)
        await bot.on_interaction(ci(2, {"name": "new"}))
        assert o.log[0] == "respond" and "tree" in o.log
        pid = next(iter(bot.pending))
        o.log.clear()
        await bot.on_interaction(ci(3, {"custom_id": f"pick:{pid}", "values": [TOPIC]}))
        assert o.log[0] == "respond"
    run(go())


def test_alert_button_answers_before_the_slow_harness_call(tmp_path):
    async def go():
        o = Ordered()
        bot, rest, h = make(tmp_path)
        orig = rest.respond

        async def respond(*a, **k):
            o.log.append("respond")
            return await orig(*a, **k)
        rest.respond = respond

        class Slow(Feed):
            async def request(self, *a, **k):
                o.log.append("act")
                return await super().request(*a, **k)
        bot.h = Slow([])
        await bot.on_interaction(ci(3, {"custom_id": "fa:9:allow"}, channel="A"))
        assert o.log == ["respond", "act"]
    run(go())


def test_http_errors_are_caught_and_shown_short(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)

        async def boom(*a, **k):
            raise httpx.ConnectError("secret internal detail")
        h.create_session = boom
        await bot.start(Pending(text="x", files=[], channel="D1", message="m", in_dm=True), TOPIC)
        assert "unreachable" in rest.sent[-1]["content"] and "secret" not in rest.sent[-1]["content"]

        async def refuse(*a, **k):
            raise HarnessError(500, "server_error", "Traceback: /opt/secret/path")
        h.create_session = refuse
        await bot.start(Pending(text="x", files=[], channel="D1", message="m", in_dm=True), TOPIC)
        assert "500 server_error" in rest.sent[-1]["content"] and "Traceback" not in rest.sent[-1]["content"]

        async def down(sid):
            raise httpx.ReadTimeout("slow")
        h.session = down
        await bot.forward("S1", msg("hi"), "D1")
        assert "unreachable" in rest.sent[-1]["content"]
    run(go())


def test_harness_client_turns_network_errors_into_harness_errors():
    async def go():
        def handler(req):
            raise httpx.ConnectError("refused")
        h = Harness("http://127.0.0.1:1", "pw", transport=httpx.MockTransport(handler))
        with pytest.raises(HarnessError) as ei:
            await h.request("GET", "/api/sessions")
        assert ei.value.code == "unreachable"
        await h.close()
    run(go())


# === 6. store and layout =============================================================================================

def test_corrupt_state_is_kept_as_bak(tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    st = Store(tmp_path / "state.json")
    assert st.damaged and (tmp_path / "state.json.bak").read_text() == "{not json"


def test_save_fsyncs_and_skips_when_nothing_changed(tmp_path, monkeypatch):
    import os
    synced, replaced = [], []
    real_replace = os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append(b), real_replace(a, b)))
    st = Store(tmp_path / "state.json")
    st.save()
    st.save()
    assert len(synced) == 1 and len(replaced) == 1
    st.data["dm_current"] = "S9"
    st.save()
    assert len(replaced) == 2


def layout_fake(existing, fail_modify=False):
    from test_layout import R

    class R3(R):
        async def modify_channel(self, cid, **f):
            if fail_modify and "permission_overwrites" in f:
                raise DiscordError(403, "Missing Permissions")
            return await super().modify_channel(cid, **f)
    return R3(existing)


PUBLIC = {"id": "50", "name": "Harness", "type": 4, "permission_overwrites": []}
PRIVATE = {"id": "50", "name": "Harness", "type": 4,
           "permission_overwrites": [{"id": "G", "type": 0, "allow": "0", "deny": str(layout.VIEW)}]}


def test_reused_visible_category_is_made_private():
    r = layout_fake([dict(PUBLIC)])
    lay = asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [{"name": "dev"}], "fr"))
    assert lay["cat_system"] == "50"
    fixed = [f for c, f in r.moved if c == "50"]
    assert fixed and any(o["id"] == "G" for o in fixed[0]["permission_overwrites"])


def test_reused_private_category_is_left_alone():
    r = layout_fake([dict(PRIVATE)])
    asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [], "fr"))
    assert not [1 for c, f in r.moved if c == "50"]


def test_visible_category_that_cannot_be_locked_is_not_reused():
    r = layout_fake([dict(PUBLIC)], fail_modify=True)
    lay = asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [], "fr"))
    assert lay["cat_system"] != "50" and ("Harness", 4, None) in r.created


def test_nothing_is_archived_when_the_state_was_lost_or_channels_are_reused():
    old = [{"id": "1", "name": "général", "type": 0, "parent_id": "9"}, {"id": "9", "name": "Salons", "type": 4}]
    r = layout_fake(old)
    asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [], "fr", state_lost=True))
    assert r.moved == []
    r = layout_fake(old + [dict(PRIVATE)])                          # a category of the bridge already exists: a previous run
    asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [], "fr"))
    assert not [1 for c, f in r.moved if "parent_id" in f]


def test_text_channels_found_by_name_are_never_archived():
    old = [dict(PRIVATE), {"id": "51", "name": "dev", "type": 0, "parent_id": None},
           {"id": "1", "name": "général", "type": 0, "parent_id": "9"}]
    r = layout_fake(old)
    asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [{"name": "dev"}], "fr"))
    assert 51 != 0 and not [c for c, f in r.moved if c == "51" and "parent_id" in f]


def test_bot_passes_the_damaged_flag_to_the_layout(tmp_path):
    async def go():
        (tmp_path / "state.json").write_text("garbage")
        bot, rest, h = make(tmp_path)
        seen = {}

        async def ensure(*a, **k):
            seen.update(k)
            return a[1]

        class Rst(FakeRest):
            async def guilds(self):
                return [{"id": "G"}]
        bot.rest = Rst()
        orig = layout.ensure
        layout.ensure = ensure
        try:
            await bot.sync_layout()
        finally:
            layout.ensure = orig
        assert seen["state_lost"] is True and bot.store.damaged is False
    run(go())


# === 7. Rest.call ====================================================================================================

def rest_with(handler, monkeypatch):
    sleeps = []

    async def sleep(d, result=None):
        sleeps.append(round(d, 2))
    monkeypatch.setattr(asyncio, "sleep", sleep)
    return Rest("t", transport=httpx.MockTransport(handler)), sleeps


def seq(*responses):
    calls = []
    it = iter(responses)

    def handler(req):
        calls.append(req.method)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return handler, calls


def test_429_uses_retry_after_from_json_then_header_then_default(monkeypatch):
    async def go():
        h, calls = seq(httpx.Response(429, json={"retry_after": 0.5}), httpx.Response(200, json={"ok": 1}))
        rest, sleeps = rest_with(h, monkeypatch)
        assert await rest.call("GET", "/x") == {"ok": 1} and sleeps == [0.6]
        h, calls = seq(httpx.Response(429, text="slow down", headers={"retry-after": "2"}), httpx.Response(200, json={}))
        rest, sleeps = rest_with(h, monkeypatch)
        await rest.call("GET", "/x")
        assert sleeps == [2.1]
        h, calls = seq(httpx.Response(429, text="no json"), httpx.Response(200, json={}))
        rest, sleeps = rest_with(h, monkeypatch)
        await rest.call("GET", "/x")
        assert sleeps == [1.1]
    run(go())


def test_5xx_then_200_is_retried_for_idempotent_requests(monkeypatch):
    async def go():
        h, calls = seq(httpx.Response(502, json={}), httpx.Response(200, json={"id": "1"}))
        rest, sleeps = rest_with(h, monkeypatch)
        assert await rest.call("PATCH", "/channels/1", json={}) == {"id": "1"} and len(calls) == 2
    run(go())


def test_message_post_is_not_retried_on_5xx_or_network_error(monkeypatch):
    async def go():
        h, calls = seq(httpx.Response(502, json={}), httpx.Response(200, json={"id": "1"}))
        rest, sleeps = rest_with(h, monkeypatch)
        with pytest.raises(DiscordError) as ei:
            await rest.send("C1", "hi")
        assert ei.value.status == 502 and len(calls) == 1 and ei.value.transient
        h, calls = seq(httpx.ReadTimeout("t"), httpx.Response(200, json={"id": "1"}))
        rest, sleeps = rest_with(h, monkeypatch)
        with pytest.raises(DiscordError) as ei:
            await rest.send("C1", "hi")
        assert ei.value.status == 0 and len(calls) == 1
        h, calls = seq(httpx.Response(429, json={"retry_after": 0.1}), httpx.Response(200, json={"id": "1"}))     # a 429 was not processed: safe
        rest, sleeps = rest_with(h, monkeypatch)
        assert (await rest.send("C1", "hi"))["id"] == "1"
    run(go())


def test_network_errors_are_retried_for_idempotent_requests(monkeypatch):
    async def go():
        h, calls = seq(httpx.ConnectError("x"), httpx.ReadTimeout("y"), httpx.Response(200, json={"id": "1"}))
        rest, sleeps = rest_with(h, monkeypatch)
        assert await rest.call("GET", "/channels/1") == {"id": "1"} and len(calls) == 3
    run(go())


def test_repeated_failures_raise_and_the_last_try_does_not_sleep(monkeypatch):
    async def go():
        for resp in (httpx.Response(500, json={}), httpx.Response(429, json={"retry_after": 0.1}), httpx.ConnectError("x")):
            h, calls = seq(*[resp] * 4)
            rest, sleeps = rest_with(h, monkeypatch)
            with pytest.raises(DiscordError) as ei:
                await rest.call("GET", "/channels/1")
            assert len(calls) == 4 and len(sleeps) == 3 and ei.value.transient
    run(go())


# === 8. misc =========================================================================================================

def test_thread_creation_failure_still_answers_and_does_not_block(tmp_path):
    class NoThread(FakeRest):
        async def start_thread(self, *a, **k):
            raise DiscordError(403, "Missing Access")

    async def go():
        bot, rest, h = make(tmp_path, channel="C1")
        bot.rest = rest = NoThread()
        h.events = [{"type": "assistant", "text": "Réponse"}, {"type": "result", "ok": True, "cost_usd": 0, "duration_ms": 0}]
        ok = await bot.start(Pending(text="salut", files=[], channel="C1", message="m1", in_dm=False), TOPIC)
        await settle(bot)
        assert ok is False and bot.store.data["threads"] == {}
        texts = [s["content"] for s in rest.sent]
        assert any("fil" in t for t in texts) and texts[-1] == "Réponse"      # told, and still answered in the channel
    run(go())


def test_messages_of_a_session_reach_harness_in_order(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        calls = []
        orig = h.session

        async def slow(sid):
            calls.append(1)
            if len(calls) == 1:
                await asyncio.sleep(0.1)                               # the first message is slow to check
            return await orig(sid)
        h.session = slow
        await asyncio.gather(bot.forward("S1", msg("un"), "D1"), bot.forward("S1", msg("deux", mid="m2"), "D1"))
        assert [m[1] for m in h.messages] == ["un", "deux"]
        await settle(bot)
    run(go())


def test_menus_over_25_entries_are_cut_with_a_note(tmp_path):
    bot, _, _ = make(tmp_path)
    topics = [{"project": "dev", "name": f"t{i}", "path": f"projects/dev/topics/t{i}", "desc": ""} for i in range(30)]
    opts = bot.menu("x", topics)[0]["components"][0]["options"]
    assert len(opts) == 25 and "25" in bot.where(topics)
    many = [{"project": f"p{i}", "name": "t", "path": f"projects/p{i}/topics/t", "desc": ""} for i in range(30)]
    assert len(bot.menu("x", many, projects=True)[0]["components"][0]["options"]) == 25 and "25" in bot.where(many, True)
    assert "25" not in bot.where(topics[:3])


@scenario
async def test_sessions_command_is_ephemeral_and_refused_outside_known_channels(e):
    e.fh.add_session("S1", TOPIC, "Mon article")
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "sessions"}, channel="C777", iid="I1"))
    await until(lambda: e.fd.responses)
    r = e.fd.responses[0]
    assert r["type"] == 4 and r["data"]["flags"] == 64 and e.fh.log == []
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "new"}, channel="C777", iid="I2"))
    await until(lambda: len(e.fd.responses) == 2)
    assert e.fd.responses[1]["type"] == 4 and e.fd.responses[1]["data"]["flags"] == 64
    await e.fg.dispatch("INTERACTION_CREATE", inter(2, {"name": "sessions"}, iid="I3"))     # in the project channel
    await until(lambda: e.fd.originals)
    assert e.fd.responses[2]["type"] == 5 and e.fd.responses[2]["data"]["flags"] == 64
    assert e.fd.originals[0]["components"][0]["components"][0]["options"][0]["value"] == "S1"
    assert e.fd.messages == []                                      # nothing public in the channel


@scenario
async def test_open_session_replies_privately_and_rejects_bad_ids(e):
    e.fh.add_session("S1", TOPIC, "Mon article")
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "sess:x", "values": ["S1"]}, iid="I1"))
    await until(lambda: e.fd.followups)
    assert e.fd.followups[0]["flags"] == 64 and e.fd.messages == []
    n = len(e.fh.log)
    await e.fg.dispatch("INTERACTION_CREATE", inter(3, {"custom_id": "sess:x", "values": ["../../admin"]}, iid="I2"))
    await until(lambda: len(e.fd.followups) == 2)
    assert len(e.fh.log) == n                                       # Harness was not asked about it


# === 12. validation ==================================================================================================

def test_session_ids_are_validated():
    assert check_sid("abc-123_DEF") == "abc-123_DEF"
    for bad in ("", "../x", "a/b", "a b", "é", "x" * 65, "a?b=1"):
        with pytest.raises(HarnessError):
            check_sid(bad)


def test_event_id_accepts_ascii_digits_only(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        acted = []

        class F(Feed):
            async def request(self, method, path, **kw):
                acted.append(path)
                return await super().request(method, path, **kw)
        bot.h = F([])
        await bot.on_interaction(ci(3, {"custom_id": "fa:٣:allow"}, channel="A"))     # an Arabic-Indic digit
        await bot.on_interaction(ci(3, {"custom_id": "fa:" + "9" * 21 + ":allow"}, channel="A"))
        assert acted == [] and [r[0] for r in rest.responses] == [4, 4]
    run(go())


def test_password_never_travels_over_plain_http_outside_local_and_tailnet():
    from harness_discord.harness import check_url
    for ok in ("http://127.0.0.1:8787", "http://localhost:8787", "http://100.77.130.11:8787", "http://100.64.0.1",
               "http://100.127.255.254", "http://harness.tail8de083.ts.net", "https://example.com", "http://[::1]:8787"):
        check_url(ok)
    for bad in ("http://example.com", "http://192.168.1.5:8787", "http://100.128.0.1", "http://100.63.255.255", "ftp://127.0.0.1"):
        with pytest.raises(ValueError):
            check_url(bad)
    with pytest.raises(ValueError):
        Harness("http://example.com", "pw")


# === 13. page API ====================================================================================================

def load_api():
    spec = importlib.util.spec_from_file_location("hd_api", Path(__file__).parent.parent / "discord" / "api.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_relay_flags_must_be_real_booleans(tmp_path):
    api = load_api()
    ctx = {"state_dir": str(tmp_path), "secret": lambda n: None}
    for bad in ("false", 0, "", None, "no"):
        status, body = api.handle(ctx, "POST", "config", "", {"relay_feed": bad})
        assert status == 400 and body["error"] == "bad_flag", bad
    assert not (tmp_path / "config.json").exists()
    out = api.handle(ctx, "POST", "config", "", {"relay_feed": False, "relay_bell": True})
    assert out["config"]["relay_feed"] is False and out["config"]["relay_bell"] is True


# === 10, 14. docs and unit ===========================================================================================

def test_unit_is_hardened():
    unit = (Path(__file__).parent.parent / "discord" / "bridge" / "harness-discord.service.in").read_text()
    for line in ("UMask=0077", "CapabilityBoundingSet=", "RestrictSUIDSGID=true", "ProtectClock=true", "ProtectHostname=true",
                 "ProtectProc=invisible", "MemoryMax=512M"):
        assert re.search(rf"^{re.escape(line)}$", unit, re.M), line


def test_setup_docs_no_longer_recommend_administrator():
    root = Path(__file__).parent.parent
    for f in ("discord/page.js", "README.md", "skill/discord-bridge/SKILL.md"):
        text = (root / f).read_text()
        assert not re.search(r"(Administrator|Administrateur)\s*,\s*(or|ou)\b|permissions?\s*:?\s*Administrat", text), f
        assert "Manage Roles" in text or "Gérer les rôles" in text, f
