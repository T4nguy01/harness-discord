"""The bell: turns that did not come from Discord ring in the session's thread."""
import time

from harness_discord.bell import Bell
from harness_discord.harness import HarnessError

from test_bot import make, run


def sess(sid="S1", turns=1, state="idle", notify=True, **kw):
    return {"id": sid, "title": "Mon billet", "turns": turns, "state": state, "notify": notify, "last_text": "Voilà   le résultat", **kw}


def setup(tmp_path):
    bot, rest, h = make(tmp_path)
    bot.store.link("S1", "T9", "projects/dev/topics/blog")
    box = {"list": [sess()]}

    async def request(method, path, **kw):
        assert (method, path) == ("GET", "/api/sessions")
        return box["list"]

    h.request = request
    bot.bell = Bell(bot)
    return bot, rest, box


async def tick(bell):
    bell.clock = getattr(bell, "clock", time.time()) + 20      # 20 s between two polls
    await bell.poll(bell.clock)


def test_first_pass_replays_nothing_then_web_turn_rings(tmp_path):
    async def go():
        bot, rest, box = setup(tmp_path)
        await tick(bot.bell)
        assert rest.sent == []
        box["list"] = [sess(turns=2)]
        await tick(bot.bell)
        assert [m["channel"] for m in rest.sent] == ["T9"]
        assert rest.sent[0]["content"] == "✅ **Mon billet** - Voilà le résultat"
        await tick(bot.bell)
        assert len(rest.sent) == 1                       # no repeat
    run(go())


def test_running_turn_waits_for_its_end(tmp_path):
    async def go():
        bot, rest, box = setup(tmp_path)
        await tick(bot.bell)
        box["list"] = [sess(turns=1, state="running")]
        await tick(bot.bell)
        assert rest.sent == []
        box["list"] = [sess(turns=2)]
        await tick(bot.bell)
        assert len(rest.sent) == 1
    run(go())


def test_no_bell_unlinked_or_discord_turns_are_silent(tmp_path):
    async def go():
        bot, rest, box = setup(tmp_path)
        await tick(bot.bell)
        box["list"] = [sess(turns=2, notify=False), sess("S2", turns=5)]   # bell off; S2 has no thread
        await tick(bot.bell)
        box["list"] = [sess(turns=3)]
        bot.followers["S1"] = object()                   # a Discord turn is running
        await tick(bot.bell)
        del bot.followers["S1"]
        bot.follow_end["S1"] = bot.bell.clock + 5              # one just ended between two polls
        box["list"] = [sess(turns=4)]
        await tick(bot.bell)
        assert rest.sent == []
        box["list"] = [sess(turns=5)]                    # then a web turn rings again
        await tick(bot.bell)
        assert len(rest.sent) == 1
    run(go())


def test_interval_and_harness_error(tmp_path):
    async def go():
        bot, rest, box = setup(tmp_path)
        await tick(bot.bell)
        box["list"] = [sess(turns=2)]
        await bot.bell.poll()                            # < 15 s since the last poll: nothing
        assert rest.sent == []

        async def boom(*a, **k):
            raise HarnessError(500, "x")
        bot.h.request = boom
        try:
            await tick(bot.bell)
        except HarnessError:
            pass                                         # the relay loop logs it and goes on
    run(go())
