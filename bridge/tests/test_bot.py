"""Offline tests of the bridge logic with a fake Harness and a fake Discord."""
import asyncio

from harness_discord.bot import Bot
from harness_discord.harness import HarnessError
from harness_discord.store import Store

OWNER = "42"
TOPIC = "projects/dev/topics/blog"


class FakeRest:
    def __init__(self):
        self.sent, self.edits, self.threads, self.responses, self.deleted, self.renamed = [], [], [], [], [], []
        self.n = 100

    async def send(self, channel, content="", *, components=None, reply_to=None, silent=False):
        self.n += 1
        self.sent.append({"id": str(self.n), "channel": channel, "content": content, "components": components})
        return {"id": str(self.n)}

    async def edit(self, channel, message, content=None, components=None):
        self.edits.append((channel, message, content))
        return {}

    async def typing(self, channel):
        pass

    async def start_thread(self, channel, name, message=None):
        self.threads.append((channel, name, message))
        return {"id": f"T{len(self.threads)}"}

    async def rename(self, channel, name):
        self.renamed.append((channel, name))

    async def channel(self, channel):
        return {"type": 0}

    async def respond(self, i, kind, content=None, components=None, ephemeral=False):
        self.responses.append((kind, content, components))

    async def followup(self, i, app, content, ephemeral=False):
        self.responses.append(("follow", content, None))

    async def delete_channel(self, c):
        self.deleted.append(c)

    async def download(self, url, limit=0):
        return b"data"

    async def register_commands(self, app, cmds):
        self.cmds = cmds


class FakeGw:
    app_id = "app"
    user_id = "bot"


class FakeHarness:
    def __init__(self):
        self.created, self.messages, self.uploads, self.stopped = [], [], [], []
        self.events = []
        self.session_title = ""

    async def tree(self):
        return [{"name": "dev", "topics": [{"name": "blog", "path": TOPIC, "description": "Blog", "running": 0, "session_count": 3}]}]

    async def create_session(self, path, message="", attachments=None, voice=None, title=""):
        self.created.append((path, message, attachments, voice))
        return {"id": "S1", "title": "", "path": path}

    async def session(self, sid):
        return {"id": sid, "title": self.session_title, "path": TOPIC, "archived": False, "state": "idle", "queue": 0,
                "cost_usd": 0.1, "turns": 1, "model": ""}

    async def tail(self, sid):
        return 10

    async def send(self, sid, text, attachments=None, voice=None):
        self.messages.append((sid, text, attachments, voice))
        return {}

    async def upload(self, path, name, data, mime=""):
        self.uploads.append((path, name))
        return f"{path}/attachments/{name}"

    async def stop(self, sid):
        self.stopped.append(sid)

    async def archive(self, sid):
        return {}

    async def stream(self, sid, offset):
        yield "state", {"state": "running"}
        yield "events", {"events": self.events, "next": offset + 100}
        await asyncio.sleep(0.05)


def make(tmp_path, channel=""):
    rest, h = FakeRest(), FakeHarness()
    cfg = {"owner_id": OWNER, "channel_id": channel, "reply_mode": "live", "keep_days": 30}
    bot = Bot(h, rest, FakeGw(), Store(tmp_path / "state.json"), cfg, "fr", "http://x")
    return bot, rest, h


def msg(content, channel="D1", guild=None, author=OWNER, mid="m1", **kw):
    d = {"id": mid, "channel_id": channel, "content": content, "author": {"id": author}, "type": 0, "attachments": []}
    if guild:
        d["guild_id"] = guild
    d.update(kw)
    return d


def run(coro):
    return asyncio.run(coro)


async def settle(bot):
    for _ in range(100):
        if not bot.followers:
            return
        await asyncio.sleep(0.02)


TURN = [
    {"type": "user", "text": "salut"},
    {"type": "assistant", "text": "Je regarde."},
    {"type": "tool_use", "name": "Read", "summary": "Read a.py"},
    {"type": "assistant", "text": "Voici la réponse finale."},
    {"type": "result", "ok": True, "cost_usd": 0.02, "duration_ms": 3000},
]


def test_stranger_is_ignored(tmp_path):
    bot, rest, _ = make(tmp_path)
    run(bot.on_message(msg("hi", author="999")))
    assert rest.sent == []


def test_dm_message_asks_the_topic_then_runs_the_turn(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        h.events = TURN
        await bot.on_message(msg("salut"))
        menu = rest.sent[0]
        assert menu["content"] == "Dans quel topic ?"
        opt = menu["components"][0]["components"][0]["options"][0]
        assert opt["value"] == TOPIC and opt["label"] == "dev/blog"
        pid = menu["components"][0]["components"][0]["custom_id"].split(":")[1]
        await bot.on_interaction({"type": 3, "id": "i", "token": "t", "channel_id": "D1", "user": {"id": OWNER},
                                  "data": {"custom_id": f"pick:{pid}", "values": [TOPIC]}})
        await settle(bot)
        assert h.created == [(TOPIC, "salut", [], None)]
        assert bot.store.data["dm_current"] == "S1"
        answers = [s["content"] for s in rest.sent[1:]]
        assert answers[-1] == "Voici la réponse finale."           # only the text after the last tool call
        assert any("Terminé" in (e[2] or "") for e in rest.edits)   # status folded into a summary
    run(go())


def test_main_channel_gets_a_thread_and_the_thread_continues_the_session(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path, channel="C1")
        h.events = TURN
        await bot.on_message(msg("salut", channel="C1", guild="G"))
        pid = rest.sent[0]["components"][0]["components"][0]["custom_id"].split(":")[1]
        await bot.on_interaction({"type": 3, "id": "i", "token": "t", "channel_id": "C1", "guild_id": "G", "member": {"user": {"id": OWNER}},
                                  "data": {"custom_id": f"pick:{pid}", "values": [TOPIC]}})
        await settle(bot)
        assert rest.threads[0][0] == "C1" and rest.threads[0][2] == "m1"
        assert bot.store.session_of_channel("T1") == "S1"
        assert rest.renamed == []                                    # no title yet
        h.session_title = "Mon titre"
        await bot.on_message(msg("et ensuite ?", channel="T1", guild="G", mid="m2"))
        await settle(bot)
        assert h.messages[0][:2] == ("S1", "et ensuite ?")
        assert rest.renamed and "Mon titre" in rest.renamed[0][1]
    run(go())


def test_attachments_are_uploaded_and_voice_is_passed_apart(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        h.events = TURN
        bot.store.set_dm("S1", "D1")
        files = [{"filename": "a.png", "url": "u", "size": 10, "content_type": "image/png", "flags": 0},
                 {"filename": "v.ogg", "url": "u", "size": 10, "content_type": "audio/ogg", "flags": 1 << 13}]
        # a reply to one of the bot's answers continues that session
        bot.remember("R1", "S1")
        await bot.on_message(msg("regarde", attachments=files, message_reference={"message_id": "R1"}))
        await settle(bot)
        assert h.messages[0][0] == "S1" and h.messages[0][2] == [f"{TOPIC}/attachments/a.png"]
        assert h.messages[0][3] == f"{TOPIC}/attachments/v.ogg"
    run(go())


def test_stop_command_uses_the_thread_session(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path, channel="C1")
        bot.store.link("S1", "T9", TOPIC)
        await bot.on_interaction({"type": 2, "id": "i", "token": "t", "channel_id": "T9", "guild_id": "G",
                                  "member": {"user": {"id": OWNER}}, "data": {"name": "stop"}})
        assert h.stopped == ["S1"]
    run(go())


def test_gone_session_is_unlinked(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path, channel="C1")
        bot.store.link("S1", "T9", TOPIC)

        async def boom(sid):
            raise HarnessError(404, "not_found")
        h.session = boom
        await bot.on_message(msg("hello", channel="T9", guild="G"))
        assert bot.store.session_of_channel("T9") is None
        assert "n'existe plus" in rest.sent[0]["content"]
    run(go())
