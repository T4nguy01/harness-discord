"""Run: python -m harness_discord  (needs DISCORD_BOT_TOKEN, DISCORD_OWNER_ID and HARNESS_PASSWORD, from the environment or the Keys vault)."""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time

from . import config
from .bot import Bot
from .discord_api import Gateway, Rest
from . import __version__
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
    try:
        h = Harness(url, password)
    except ValueError as e:                                  # plain http to a remote host: the password would travel in clear
        log.error("%s", e)
        return 2
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
    ready = asyncio.create_task(gw.ready.wait())
    await asyncio.wait({ready, task}, return_when=asyncio.FIRST_COMPLETED)     # the gateway task ends on a fatal close
    code = 0
    if gw.fatal:
        write_error(gw.fatal)
        code = 4
    elif ready.done():
        await bot.on_ready()
        stopper = asyncio.create_task(stop.wait())
        await asyncio.wait({stopper, task}, return_when=asyncio.FIRST_COMPLETED)
        if gw.fatal:
            write_error(gw.fatal)
            code = 4
    ready.cancel()
    gw.stop()
    task.cancel()
    await rest.close()
    await h.close()
    return code


def write_error(message: str) -> None:
    """status.json tells the Harness page why the bridge is not connected."""
    try:
        (config.state_dir() / "status.json").write_text(json.dumps(
            {"at": time.time(), "version": __version__, "connected": False, "error": message}))
    except OSError:
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
