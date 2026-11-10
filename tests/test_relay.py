import asyncio

from harness_discord import relay
from harness_discord.relay import Relay, render, route
from test_bot import FakeRest, make, run


def ev(i, source="system", level="info", **kw):
    d = {"id": i, "at": 1.0, "source": source, "level": level, "title": f"T{i}", "body": "b", "url": "", "count": 1,
         "read": False, "actions": [], "ref": None, "agent": None, "resolved": None, "resolution": None}
    d.update(kw)
    return d


def test_routes():
    assert route(ev(1, "crons")) == "crons"
    assert route(ev(1, "security")) == "alerts" and route(ev(1, "health")) == "alerts"
    assert route(ev(1, "agents", agent="projects/a/topics/b/agents/c")) == "agents"
    assert route(ev(1, "shop")) == "notifications"
    assert route(ev(1, "shop", actions=[{"id": "allow", "label": "Allow"}])) == "alerts"


def test_render_buttons_and_resolution():
    e = ev(7, "agents", "warning", actions=[{"id": "allow", "label": "Allow", "style": "primary"},
                                            {"id": "deny", "label": "Deny", "style": "danger"},
                                            {"id": "open", "label": "Open"}], url="/agents/x")
    embed, comps = render(e, "http://h")
    assert "⚠️" in embed["title"] and "T7" in embed["title"] and embed["description"] == "b" and embed["footer"]["text"] == "agents"
    b = comps[0]["components"]
    assert b[0]["custom_id"] == "fa:7:allow" and b[0]["style"] == 1 and b[1]["style"] == 4
    assert b[2]["style"] == 5 and b[2]["url"] == "http://h/agents/x"
    embed, comps = render(ev(7, resolved=2.0, resolution="Allowed"), "http://h")
    assert embed["fields"][0]["value"] == "Allowed" and comps == [] and embed["color"] == relay.DONE_COLOR


class Feed:
    def __init__(self, events):
        self.events = events

    async def request(self, method, path, **kw):
        if path == "/api/feed":
            return {"events": self.events}
        if path.startswith("/api/feed/"):
            return {"event": ev(int(path.split("/")[3]), resolved=3.0, resolution="Allowed")}
        return {}


def test_feed_starts_from_now_then_posts_new_events_in_the_right_channels(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path, channel="C1")
        bot.lay.update({"alerts": "A", "crons": "K", "notifications": "N", "agents": "G"})
        old = [ev(1), ev(2, "crons")]
        bot.h = Feed(old)
        r = Relay(bot)
        await r.feed()
        assert rest.sent == []                                   # history is not replayed
        old += [ev(3, "crons", "error"), ev(4, "security"), ev(5, "shop")]
        await r.feed()
        assert [(s["channel"]) for s in rest.sent] == ["K", "A", "N"]
        assert "T3" in rest.sent[0]["embeds"][0]["title"]
        old[4:4] = []
        await r.feed()
        assert len(rest.sent) == 3                               # nothing twice
        # an alert resolved elsewhere gets its message edited
        old[2] = ev(3, "crons", "error", resolved=9.0, resolution="Fixed")
        await r.feed()
        assert rest.edits and "Fixed" in rest.edits[0][3][0]["fields"][0]["value"]
    run(go())


def test_button_press_calls_the_act_endpoint(tmp_path):
    async def go():
        bot, rest, h = make(tmp_path)
        bot.h = Feed([])
        await bot.on_interaction({"type": 3, "id": "i", "token": "t", "channel_id": "A", "user": {"id": "42"},
                                  "data": {"custom_id": "fa:9:allow"}})
        assert rest.responses[0][0] == 6
        kind, content, comps, embeds = rest.responses[1]
        assert kind == "orig" and "Allowed" in embeds[0]["fields"][0]["value"] and comps == []
    run(go())


def test_agent_embed_parts():
    from harness_discord.relay import agent_embed
    first = agent_embed("a", True, "Bot", 0, 2, {"cost": 0.5, "at": 1.8e9})
    last = agent_embed("b", True, "Bot", 1, 2, {"cost": 0.5, "at": 1.8e9})
    assert first["author"]["name"] == "✅ Bot" and first["footer"]["text"] == "1/2" and "timestamp" not in first
    assert "author" not in last and last["footer"]["text"] == "2/2 · 0.50 $" and "timestamp" in last
    one = agent_embed("x", False, "Bot", 0, 1, {})
    assert one["author"]["name"].startswith("🛑") and "footer" not in one


def test_render_buttons_valid():
    from harness_discord.relay import render
    ev = {"id": 7, "title": "t", "url": "/x", "actions": [
        {"id": "ok", "label": "  "}, {"id": "ok", "label": "dup"}, {"id": "bad id!", "label": "x"},
        {"id": "open", "label": "Open", "url": "/x"}]}
    _, comps = render(ev, "https://h.example")
    btns = comps[0]["components"]
    assert [b.get("custom_id") or b["url"] for b in btns] == ["fa:7:ok", "https://h.example/x"] and btns[0]["label"] == "ok"
    _, comps = render(ev, "")                                      # no web address: no link button
    assert all("url" not in b for b in comps[0]["components"])
    _, comps = render({**ev, "id": "abc"}, "https://h.example")      # non-numeric id: no clickable action
    assert [b["style"] for b in comps[0]["components"]] == [5]
