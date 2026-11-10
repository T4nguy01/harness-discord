"""Discord REST (v10) with rate-limit handling, and the Gateway (websocket) with heartbeat, resume and reconnect."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlparse

import httpx
import websockets

from . import __version__

log = logging.getLogger("harness_discord.discord")
API = "https://discord.com/api/v10"
NO_MENTIONS = {"parse": []}
USER_AGENT = f"DiscordBot (https://github.com/T4nguy01/harness-discord, {__version__})"

STABLE_AFTER = 30.0           # a connection that lasted this long resets the reconnect delay
# gateway close codes after which retrying is pointless: authentication failed, bad shard, bad / disallowed intents
FATAL_CLOSE = {4004: "invalid bot token", 4010: "invalid shard", 4011: "sharding required", 4012: "invalid API version",
               4013: "invalid intents", 4014: "disallowed intents (enable MESSAGE CONTENT in the Developer Portal)"}

# intents: guilds, guild messages, direct messages, message content
INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)


class DiscordError(Exception):
    def __init__(self, status: int, body: Any):
        super().__init__(f"discord {status}: {str(body)[:200]}")
        self.status, self.body = status, body

    @property
    def transient(self) -> bool:
        """Worth retrying later: network error (0), rate limit, Discord server error."""
        return self.status in (0, 429) or self.status >= 500


class Rest:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=API, timeout=30, transport=transport,
                                      headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT})

    async def close(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _idempotent(method: str, path: str) -> bool:
        """A message POST is never replayed after a 5xx or a network error: it may already be posted (duplicate)."""
        return not (method == "POST" and (path.endswith("/messages") or path.startswith("/webhooks/")))

    @staticmethod
    def _retry_after(r: httpx.Response) -> float:
        """Seconds to wait after a 429: JSON `retry_after`, else the Retry-After header, else 1."""
        for get in (lambda: r.json()["retry_after"], lambda: r.headers["retry-after"]):
            try:
                return max(0.0, float(get()))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return 1.0

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        safe = self._idempotent(method, path)
        tries = 4
        for n in range(tries):
            last = n == tries - 1
            try:
                r = await self.http.request(method, path, **kw)
            except httpx.TransportError as e:                  # includes timeouts
                if not safe or last:
                    raise DiscordError(0, f"network: {type(e).__name__}") from e
                await asyncio.sleep(min(2 ** n, 8))
                continue
            if r.status_code == 429:
                wait = self._retry_after(r)
                if last:
                    break
                await asyncio.sleep(min(wait, 30) + 0.1)
                continue
            if r.status_code >= 500 and safe and not last:
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
                   silent: bool = False, embeds: list | None = None) -> dict:
        body: dict[str, Any] = {"content": content, "allowed_mentions": NO_MENTIONS}
        if embeds:
            body["embeds"] = embeds
        if components is not None:
            body["components"] = components
        if reply_to:
            body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
        if silent:
            body["flags"] = 1 << 12                      # SUPPRESS_NOTIFICATIONS
        return await self.call("POST", f"/channels/{channel}/messages", json=body)

    async def edit(self, channel: str, message: str, content: str | None = None, components: list | None = None,
                   embeds: list | None = None) -> dict:
        body: dict[str, Any] = {"allowed_mentions": NO_MENTIONS}
        if embeds is not None:
            body["embeds"] = embeds
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
        try:
            await self.call("PUT", f"/channels/{channel}/messages/{message}/reactions/{quote(emoji)}/@me")
        except DiscordError:
            pass

    async def unreact(self, channel: str, message: str, emoji: str) -> None:
        """Remove the bot's own reaction (nothing happens when it is not there)."""
        try:
            await self.call("DELETE", f"/channels/{channel}/messages/{message}/reactions/{quote(emoji)}/@me")
        except DiscordError:
            pass

    async def pin(self, channel: str, message: str) -> None:
        """Pin a message (needs Manage Messages, or Pin Messages on recent servers). Raises DiscordError when refused."""
        await self.call("PUT", f"/channels/{channel}/messages/pins/{message}")

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
                      ephemeral: bool = False, embeds: list | None = None) -> None:
        data: dict[str, Any] = {"allowed_mentions": NO_MENTIONS}
        if embeds is not None:
            data["embeds"] = embeds
        if content is not None:
            data["content"] = content
        if components is not None:
            data["components"] = components
        if ephemeral:
            data["flags"] = 64
        await self.call("POST", f"/interactions/{interaction['id']}/{interaction['token']}/callback",
                        json={"type": kind, "data": data})

    async def autocomplete(self, interaction: dict, choices: list[dict]) -> None:
        """Answer an autocomplete interaction (type 4): at most 25 choices {name, value}, each <= 100 characters."""
        out = [{"name": str(c["name"])[:100], "value": str(c["value"])[:100]} for c in choices[:25]]
        await self.call("POST", f"/interactions/{interaction['id']}/{interaction['token']}/callback",
                        json={"type": 8, "data": {"choices": out}})

    async def followup(self, interaction: dict, app_id: str, content: str, ephemeral: bool = False) -> None:
        body: dict[str, Any] = {"content": content, "allowed_mentions": NO_MENTIONS}
        if ephemeral:
            body["flags"] = 64
        await self.call("POST", f"/webhooks/{app_id}/{interaction['token']}", json=body)

    async def edit_original(self, interaction: dict, app_id: str, content: str | None = None, components: list | None = None,
                            embeds: list | None = None) -> None:
        """Fill in the answer of an interaction already acknowledged (deferred, or a type 7 placeholder)."""
        body: dict[str, Any] = {"allowed_mentions": NO_MENTIONS}
        if content is not None:
            body["content"] = content
        if components is not None:
            body["components"] = components
        if embeds is not None:
            body["embeds"] = embeds
        await self.call("PATCH", f"/webhooks/{app_id}/{interaction['token']}/messages/@original", json=body)

    CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
    CDN_ONLY = True                                            # tests switch it off to reach a local fake CDN

    async def download(self, url: str, limit: int = 25 * 1024 * 1024) -> bytes:
        """Fetch an attachment from Discord's CDN only (no other host, no redirect), stopping at `limit` bytes."""
        u = urlparse(url)
        if self.CDN_ONLY and (u.scheme != "https" or u.hostname not in self.CDN_HOSTS):
            raise DiscordError(400, "attachment host not allowed")
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        raise DiscordError(413, "attachment too large")
                return bytes(buf)


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
        self.fatal = ""                              # set when Discord closes with a code retrying cannot fix
        self._tasks: set[asyncio.Task] = set()      # keep references: a task nobody holds can be collected early
        self.presence: dict | None = None            # last status asked for (sent again when identifying)
        self._ws: Any = None

    def stop(self) -> None:
        self._stop = True

    @staticmethod
    def presence_payload(text: str, status: str = "online") -> dict:
        """Gateway op 3 payload: a custom status line (activity type 4)."""
        return {"since": None, "status": status, "afk": False,
                "activities": [{"name": "Custom Status", "type": 4, "state": text[:128]}]}

    async def update_presence(self, text: str, status: str = "online") -> bool:
        """Set the bot's status line (op 3). Kept for the next identify; True when sent on the live connection."""
        self.presence = self.presence_payload(text, status)
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps({"op": 3, "d": self.presence}))
        except websockets.ConnectionClosed:
            return False
        return True

    async def run(self) -> None:
        delay = 1.0
        while not self._stop:
            t0 = time.monotonic()
            err: Exception | None = None
            try:
                await self._connect()
            except asyncio.CancelledError:
                raise
            except websockets.ConnectionClosed as e:
                rcvd = getattr(e, "rcvd", None)
                code = rcvd.code if rcvd is not None else getattr(e, "code", None)
                if code in FATAL_CLOSE:
                    self.fatal = f"gateway closed with {code}: {FATAL_CLOSE[code]}"
                    log.error("%s - not retrying", self.fatal)
                    self.ready.clear()
                    return
                err = e
            except Exception as e:  # noqa: BLE001
                err = e
            self.ready.clear()
            if err is None or time.monotonic() - t0 > STABLE_AFTER:      # a connection that held for a while: start over at 1 s
                delay = 1.0
            if err is not None:
                log.warning("gateway: %s (reconnecting in %.0fs)", err, delay)
            if self._stop:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _connect(self) -> None:
        url = (self.resume_url + "/?v=10&encoding=json") if self.resume_url and self.session_id else self.url
        async with websockets.connect(url, max_size=2 ** 24, ping_interval=None) as ws:
            hello = json.loads(await ws.recv())
            acked = {"ok": True}
            hb = asyncio.create_task(self._heartbeat(ws, hello["d"]["heartbeat_interval"] / 1000, acked))
            self._ws = ws
            try:
                await self._identify(ws)
                async for raw in ws:
                    if await self._on_frame(ws, json.loads(raw), acked):
                        return
            finally:
                hb.cancel()
                self._ws = None

    async def _heartbeat(self, ws: Any, interval: float, acked: dict) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not acked["ok"]:
                await ws.close(4000)
                return
            acked["ok"] = False
            await ws.send(json.dumps({"op": 1, "d": self.seq}))
            await asyncio.sleep(interval)

    async def _identify(self, ws: Any) -> None:
        if self.session_id and self.seq is not None:
            await ws.send(json.dumps({"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.seq}}))
        else:
            d = {"token": self.token, "intents": INTENTS,
                 "properties": {"os": "linux", "browser": "harness-discord", "device": "harness-discord"}}
            if self.presence:
                d["presence"] = self.presence
            await ws.send(json.dumps({"op": 2, "d": d}))

    async def _on_frame(self, ws: Any, m: dict, acked: dict) -> bool:
        """One gateway frame. True when this connection must end (Discord asked for a reconnect)."""
        op = m.get("op")
        if m.get("s") is not None:
            self.seq = m["s"]
        if op == 11:
            acked["ok"] = True
        elif op == 1:
            await ws.send(json.dumps({"op": 1, "d": self.seq}))
        elif op == 7:                                         # reconnect asked by Discord
            await ws.close(4000)                              # not 1000: that would invalidate the session (RESUME stays possible)
            return True
        elif op == 9:                                         # invalid session
            if not m.get("d"):
                self.session_id, self.seq, self.resume_url = None, None, None
            await asyncio.sleep(random.uniform(1, 5))
            await ws.close(4000)
            return True
        elif op == 0:
            t, d = m["t"], m["d"]
            if t == "READY":
                self.session_id, self.resume_url = d["session_id"], d["resume_gateway_url"]
                self.app_id, self.user_id = d["application"]["id"], d["user"]["id"]
                self.ready.set()
            elif t == "RESUMED":
                self.ready.set()
            task = asyncio.create_task(self._dispatch(t, d))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return False

    async def _dispatch(self, t: str, d: dict) -> None:
        try:
            await self.handler(t, d)
        except Exception:  # noqa: BLE001
            log.exception("handler %s failed", t)
