"""Run: python -m harness_discord  (needs DISCORD_BOT_TOKEN, DISCORD_OWNER_ID and HARNESS_PASSWORD, from the environment or the Keys vault)."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import config
from .bot import Bot
from .discord_api import Gateway, Rest
from .harness import Harness, HarnessError
from .store import Store


async def amain() -> int:
    log = logging.getLogger("harness_discord")
    token, password = config.key("DISCORD_BOT_TOKEN"), config.key("HARNESS_PASSWORD")
    cfg = config.load_settings()
    if not cfg["owner_id"]:
        cfg["owner_id"] = config.key("DISCORD_OWNER_ID") or ""
    if not (token and password and cfg["owner_id"]):
        log.error("missing DISCORD_BOT_TOKEN, HARNESS_PASSWORD or the owner id (owner_id in config.json or DISCORD_OWNER_ID)")
        return 2
    url = config.harness_url()
    h = Harness(url, password)
    try:
        await h.login()
    except (HarnessError, Exception) as e:  # noqa: BLE001
        log.error("cannot sign in to Harness at %s: %s", url, e)
        return 3
    rest = Rest(token)
    holder: dict = {}

    async def handler(name: str, d: dict) -> None:
        if "bot" in holder:
            await holder["bot"].dispatch(name, d)

    gw = Gateway(token, handler)
    bot = Bot(h, rest, gw, Store(config.state_dir() / "state.json"), cfg, await h.language(), url)
    holder["bot"] = bot
    task = asyncio.create_task(gw.run())
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await gw.ready.wait()
    await bot.on_ready()
    await stop.wait()
    gw.stop()
    task.cancel()
    await rest.close()
    await h.close()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
