import asyncio

from harness_discord import layout


class R:
    def __init__(self, existing):
        self.ch = {c["id"]: c for c in existing}
        self.created, self.moved = [], []
        self.n = 1000

    async def guilds(self):
        return [{"id": "G"}]

    async def guild_channels(self, g):
        return list(self.ch.values())

    async def create_channel(self, guild, name, kind=0, parent=None, topic="", overwrites=None):
        self.n += 1
        c = {"id": str(self.n), "name": name, "type": kind, "parent_id": parent, "overwrites": overwrites}
        self.ch[c["id"]] = c
        self.created.append((name, kind, parent))
        return c

    async def modify_channel(self, cid, **f):
        self.moved.append((cid, f))
        self.ch[cid]["parent_id"] = f.get("parent_id")
        return {}


OLD = [{"id": "1", "name": "général", "type": 0, "parent_id": "9"}, {"id": "9", "name": "Salons", "type": 4}]


def test_slug():
    assert layout.slug("Home Assistant") == "home-assistant"
    assert layout.slug("my_proj") == "my-proj" and layout.slug("***") == "projet"


def test_first_run_builds_everything_private_and_archives_old_channels():
    r = R(OLD)
    lay = asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [{"name": "dev"}, {"name": "home-assistant"}], "fr"))
    names = [c[0] for c in r.created]
    assert names[:2] == ["Harness", "Projets"] and "alertes" in names and "dev" in names and "home-assistant" in names
    cat = next(c for c in r.ch.values() if c["name"] == "Harness")
    assert any(o["id"] == "OWNER" for o in cat["overwrites"]) and any(o["id"] == "G" and int(o["deny"]) for o in cat["overwrites"])
    assert r.moved and r.moved[0][0] == "1"                         # the old channel moved to Archives
    assert set(lay["projects"]) == {"dev", "home-assistant"}


def test_second_run_creates_nothing_and_new_project_gets_a_channel():
    r = R(OLD)
    lay = asyncio.run(layout.ensure(r, {}, "G", "OWNER", "BOT", [{"name": "dev"}], "fr"))
    before = len(r.created)
    asyncio.run(layout.ensure(r, lay, "G", "OWNER", "BOT", [{"name": "dev"}], "fr"))
    assert len(r.created) == before
    asyncio.run(layout.ensure(r, lay, "G", "OWNER", "BOT", [{"name": "dev"}, {"name": "blog"}], "fr"))
    assert len(r.created) == before + 1 and "blog" in lay["projects"]
