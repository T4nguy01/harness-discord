"""Discord REST (v10) with rate-limit handling, and the Gateway (websocket) with heartbeat, resume and reconnect."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable

import httpx
import websockets

log = logging.getLogger("harness_discord.discord")
API = "https://discord.com/api/v10"
NO_MENTIONS = {"parse": []}

# intents: guilds, guild messages, direct messages, message content
INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)


class DiscordError(Exception):
    def __init__(self, status: int, body: Any):
        super().__init__(f"discord {status}: {str(body)[:200]}")
        self.status, self.body = status, body


class Rest:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=API, timeout=30, transport=transport,
                                      headers={"Authorization": f"Bot {token}", "User-Agent": "HarnessDiscord (https://github.com, 0.1)"})

    async def close(self) -> None:
        await self.http.aclose()

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        for _ in range(4):
            r = await self.http.request(method, path, **kw)
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("retry_after", 1))
                except (ValueError, AttributeError):
                    wait = 1.0
                await asyncio.sleep(min(wait, 30) + 0.1)
                continue
            if r.status_code >= 500:
                await asyncio.sleep(1)
                continue
            break
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text
            raise DiscordError(r.status_code, body)
        return r.json() if r.content else {}

    # --- messages -------------------------------------------------------------------------------------------

    async def send(self, channel: str, content: str = "", *, components: list | None = None, reply_to: str | None = None,
                   silent: bool = False) -> dict:
        body: dict[str, Any] = {"content": content, "allowed_mentions": NO_MENTIONS}
        if components is not None:
            body["components"] = components
        if reply_to:
            body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
        if silent:
            body["flags"] = 1 << 12                      # SUPPRESS_NOTIFICATIONS
        return await self.call("POST", f"/channels/{channel}/messages", json=body)

    async def edit(self, channel: str, message: str, content: str | None = None, components: list | None = None) -> dict:
        body: dict[str, Any] = {"allowed_mentions": NO_MENTIONS}
        if content is not None:
            body["content"] = content
        if components is not None:
            body["components"] = components
        return await self.call("PATCH", f"/channels/{channel}/messages/{message}", json=body)

    async def delete(self, channel: str, message: str) -> None:
        try:
            await self.call("DELETE", f"/channels/{channel}/messages/{message}")
        except DiscordError:
            pass

    async def typing(self, channel: str) -> None:
        try:
            await self.call("POST", f"/channels/{channel}/typing")
        except DiscordError:
            pass

    async def react(self, channel: str, message: str, emoji: str) -> None:
        from urllib.parse import quote
        try:
            await self.call("PUT", f"/channels/{channel}/messages/{message}/reactions/{quote(emoji)}/@me")
        except DiscordError:
            pass

    # --- channels and threads -------------------------------------------------------------------------------

    async def start_thread(self, channel: str, name: str, message: str | None = None) -> dict:
        name = name[:100] or "session"
        if message:                                      # a thread under a message of the channel
            return await self.call("POST", f"/channels/{channel}/messages/{message}/threads",
                                   json={"name": name, "auto_archive_duration": 10080})
        return await self.call("POST", f"/channels/{channel}/threads",
                               json={"name": name, "auto_archive_duration": 10080, "type": 11})

    async def rename(self, channel: str, name: str) -> None:
        await self.call("PATCH", f"/channels/{channel}", json={"name": name[:100]})

    async def delete_channel(self, channel: str) -> None:
        try:
            await self.call("DELETE", f"/channels/{channel}")
        except DiscordError:
            pass

    async def channel(self, channel: str) -> dict:
        return await self.call("GET", f"/channels/{channel}")

    async def open_dm(self, user: str) -> dict:
        return await self.call("POST", "/users/@me/channels", json={"recipient_id": user})

    async def guilds(self) -> list[dict]:
        return await self.call("GET", "/users/@me/guilds")

    async def guild_channels(self, guild: str) -> list[dict]:
        return await self.call("GET", f"/guilds/{guild}/channels")

    async def create_channel(self, guild: str, name: str, kind: int = 0, parent: str | None = None, topic: str = "",
                             overwrites: list | None = None) -> dict:
        body: dict[str, Any] = {"name": name[:100], "type": kind}
        if parent:
            body["parent_id"] = parent
        if topic:
            body["topic"] = topic[:1024]
        if overwrites is not None:
            body["permission_overwrites"] = overwrites
        return await self.call("POST", f"/guilds/{guild}/channels", json=body)

    async def modify_channel(self, channel: str, **fields: Any) -> dict:
        return await self.call("PATCH", f"/channels/{channel}", json=fields)

    async def me(self) -> dict:
        return await self.call("GET", "/users/@me")

    # --- slash commands and interactions --------------------------------------------------------------------

    async def register_commands(self, app_id: str, commands: list[dict]) -> None:
        await self.call("PUT", f"/applications/{app_id}/commands", json=commands)

    async def respond(self, interaction: dict, kind: int, content: str | None = None, components: list | None = None,
                      ephemeral: bool = False) -> None:
        data: dict[str, Any] = {"allowed_mentions": NO_MENTIONS}
        if content is not None:
            data["content"] = content
        if components is not None:
            data["components"] = components
        if ephemeral:
            data["flags"] = 64
        await self.call("POST", f"/interactions/{interaction['id']}/{interaction['token']}/callback",
                        json={"type": kind, "data": data})

    async def followup(self, interaction: dict, app_id: str, content: str, ephemeral: bool = False) -> None:
        body: dict[str, Any] = {"content": content, "allowed_mentions": NO_MENTIONS}
        if ephemeral:
            body["flags"] = 64
        await self.call("POST", f"/webhooks/{app_id}/{interaction['token']}", json=body)

    async def download(self, url: str, limit: int = 25 * 1024 * 1024) -> bytes:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            if len(r.content) > limit:
                raise DiscordError(413, "attachment too large")
            return r.content


Handler = Callable[[str, dict], Awaitable[None]]


class Gateway:
    """Keeps one gateway connection alive and passes every dispatch (event name, data) to `handler`."""

    URL = "wss://gateway.discord.gg/?v=10&encoding=json"

    def __init__(self, token: str, handler: Handler, url: str | None = None):
        self.token, self.handler = token, handler
        self.url = url or self.URL
        self.session_id: str | None = None
        self.resume_url: str | None = None
        self.seq: int | None = None
        self.ready = asyncio.Event()
        self.app_id = ""
        self.user_id = ""
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        delay = 1.0
        while not self._stop:
            try:
                await self._connect()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("gateway: %s (reconnecting in %.0fs)", e, delay)
            self.ready.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _connect(self) -> None:
        url = (self.resume_url + "/?v=10&encoding=json") if self.resume_url and self.session_id else self.url
        async with websockets.connect(url, max_size=2 ** 24, ping_interval=None) as ws:
            hello = json.loads(await ws.recv())
            interval = hello["d"]["heartbeat_interval"] / 1000
            acked = {"ok": True}

            async def heartbeat() -> None:
                await asyncio.sleep(interval * random.random())
                while True:
                    if not acked["ok"]:
                        await ws.close(4000)
                        return
                    acked["ok"] = False
                    await ws.send(json.dumps({"op": 1, "d": self.seq}))
                    await asyncio.sleep(interval)

            hb = asyncio.create_task(heartbeat())
            try:
                if self.session_id and self.seq is not None:
                    await ws.send(json.dumps({"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.seq}}))
                else:
                    await ws.send(json.dumps({"op": 2, "d": {
                        "token": self.token, "intents": INTENTS,
                        "properties": {"os": "linux", "browser": "harness-discord", "device": "harness-discord"}}}))
                async for raw in ws:
                    m = json.loads(raw)
                    op = m.get("op")
                    if m.get("s") is not None:
                        self.seq = m["s"]
                    if op == 11:
                        acked["ok"] = True
                    elif op == 1:
                        await ws.send(json.dumps({"op": 1, "d": self.seq}))
                    elif op == 7:                              # reconnect asked by Discord
                        return
                    elif op == 9:                              # invalid session
                        if not m.get("d"):
                            self.session_id, self.seq, self.resume_url = None, None, None
                        await asyncio.sleep(random.uniform(1, 5))
                        return
                    elif op == 0:
                        t, d = m["t"], m["d"]
                        if t == "READY":
                            self.session_id, self.resume_url = d["session_id"], d["resume_gateway_url"]
                            self.app_id, self.user_id = d["application"]["id"], d["user"]["id"]
                            self.ready.set()
                        elif t == "RESUMED":
                            self.ready.set()
                        asyncio.create_task(self._dispatch(t, d))
            finally:
                hb.cancel()

    async def _dispatch(self, t: str, d: dict) -> None:
        try:
            await self.handler(t, d)
        except Exception:  # noqa: BLE001
            log.exception("handler %s failed", t)
