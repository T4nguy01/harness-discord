"""Gateway against a local fake Discord websocket: IDENTIFY, READY, dispatch, heartbeat, RESUME, op 7, op 9."""
import asyncio

import pytest

from fakes import TOKEN, FakeGateway, until
from harness_discord.discord_api import INTENTS, Gateway

pytestmark = pytest.mark.usefixtures("fast_sleep")


def scenario(fn):
    def test(fast_sleep):
        async def main():
            fg = FakeGateway()
            await fg.start()
            got: list[tuple[str, dict]] = []

            async def handler(t, d):
                got.append((t, d))

            gw = Gateway(TOKEN, handler, url=fg.url + "/?v=10&encoding=json")
            task = asyncio.create_task(gw.run())
            try:
                await fn(fg, gw, got)
                assert fg.errors == []
            finally:
                gw.stop()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await fg.stop()
        asyncio.run(main())
    test.__name__ = fn.__name__
    return test


@scenario
async def test_identify_ready_dispatch_heartbeat(fg, gw, got):
    await until(gw.ready.is_set, what="READY")
    assert len(fg.identifies) == 1 and fg.identifies[0]["intents"] == INTENTS
    assert fg.identifies[0]["token"] == TOKEN
    assert gw.app_id == "app" and gw.user_id == "bot" and gw.session_id == "sess-1"
    await fg.dispatch("MESSAGE_CREATE", {"id": "1", "content": "hi"})
    await fg.dispatch("INTERACTION_CREATE", {"id": "2", "type": 3})
    await until(lambda: len(got) >= 3, what="dispatches")
    assert [t for t, _ in got] == ["READY", "MESSAGE_CREATE", "INTERACTION_CREATE"]
    assert gw.seq == 3
    await until(lambda: fg.heartbeats >= 3, what="heartbeats")
    assert fg.connections == 1


@scenario
async def test_resume_after_connection_loss_replays_missed_events(fg, gw, got):
    await until(gw.ready.is_set)
    await fg.dispatch("MESSAGE_CREATE", {"id": "1"})
    await until(lambda: gw.seq == 2)
    ws = fg.ws
    await fg.drop()
    await until(lambda: fg.ws is None or fg.ws is not ws)
    fg.seq += 1                                   # an event sent by Discord while we were away
    fg.log.append((fg.seq, "MESSAGE_CREATE", {"id": "missed"}))
    await until(lambda: len(fg.resumes) == 1, what="RESUME")
    assert fg.resumes[0]["session_id"] == "sess-1" and fg.resumes[0]["seq"] == 2
    assert len(fg.identifies) == 1                # no new IDENTIFY
    await until(lambda: any(d.get("id") == "missed" for _, d in got), what="replayed event")
    await until(gw.ready.is_set)
    assert [d["id"] for t, d in got if t == "MESSAGE_CREATE"] == ["1", "missed"]


@scenario
async def test_op7_reconnect_resumes(fg, gw, got):
    await until(gw.ready.is_set)
    await fg.op(7)
    await until(lambda: len(fg.resumes) == 1, what="RESUME after op 7")
    assert fg.connections == 2 and len(fg.identifies) == 1
    await until(gw.ready.is_set)


@scenario
async def test_op9_not_resumable_identifies_again(fg, gw, got):
    await until(gw.ready.is_set)
    await fg.dispatch("MESSAGE_CREATE", {"id": "1"})
    await fg.op(9, False)
    await until(lambda: len(fg.identifies) == 2, what="second IDENTIFY")
    assert fg.resumes == [] and gw.session_id == "sess-2"
    await until(gw.ready.is_set)


@scenario
async def test_op9_resumable_resumes(fg, gw, got):
    await until(gw.ready.is_set)
    await fg.op(9, True)
    await until(lambda: len(fg.resumes) == 1, what="RESUME after op 9 true")
    assert len(fg.identifies) == 1


@scenario
async def test_resume_rejected_falls_back_to_identify(fg, gw, got):
    await until(gw.ready.is_set)
    fg.reject_resume = True
    await fg.op(7)
    await until(lambda: len(fg.identifies) == 2, what="IDENTIFY after rejected RESUME")
    assert len(fg.resumes) == 1 and gw.session_id == "sess-2"


@scenario
async def test_missing_heartbeat_ack_closes_and_resumes(fg, gw, got):
    await until(gw.ready.is_set)
    fg.ack = False
    await until(lambda: len(fg.resumes) >= 1, timeout=8, what="reconnect after zombie connection")
    fg.ack = True
    await until(gw.ready.is_set)
    assert len(fg.identifies) == 1


@scenario
async def test_handler_error_does_not_stop_gateway(fg, gw, got):
    async def boom(t, d):
        raise RuntimeError("boom")
    gw.handler = boom
    await until(gw.ready.is_set)
    await fg.dispatch("MESSAGE_CREATE", {"id": "1"})
    await fg.dispatch("MESSAGE_CREATE", {"id": "2"})
    await until(lambda: gw.seq == 3)
    assert fg.connections == 1


def test_wrong_token_is_rejected_by_the_fake(fast_sleep):
    async def main():
        fg = FakeGateway()
        await fg.start()
        gw = Gateway("wrong", lambda t, d: asyncio.sleep(0), url=fg.url)
        task = asyncio.create_task(gw.run())
        await until(lambda: "bad token" in fg.errors)
        gw.stop(); task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await fg.stop()
    asyncio.run(main())
