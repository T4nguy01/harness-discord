"""Local fakes for the integration tests: a Discord gateway (websocket), a Discord REST (httpx.MockTransport) and a
Harness HTTP server (asyncio, real SSE). No real service, no secret: tokens are dummies."""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import websockets

from harness_discord.discord_api import INTENTS

REAL_SLEEP = asyncio.sleep
TOKEN = "fake-bot-token"
PASSWORD = "fake-password"
OWNER = "42"
TOPIC = "projects/dev/topics/blog"


async def until(cond, timeout: float = 5.0, what: str = "condition"):
    """Poll `cond()` until truthy (real sleeps, so it is not affected by the fast-sleep fixture)."""
    end = asyncio.get_running_loop().time() + timeout
    while True:
        v = cond()
        if v:
            return v
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timeout waiting for {what}")
        await REAL_SLEEP(0.01)


# --- Discord gateway ------------------------------------------------------------------------------------------

class FakeGateway:
    def __init__(self, hb_interval_ms: int = 50):
        self.hb_ms = hb_interval_ms
        self.ack = True                    # False: never answer heartbeats (zombie connection)
        self.identifies: list[dict] = []
        self.resumes: list[dict] = []
        self.heartbeats = 0
        self.connections = 0
        self.urls: list[str] = []
        self.log: list[tuple[int, str, dict]] = []     # every dispatch sent: (seq, t, d)
        self.seq = 0
        self.ws = None
        self.reject_resume = False         # answer the next RESUME with op 9 (not resumable)
        self.errors: list[str] = []
        self.server = None
        self.port = 0
        self.sessions = 0
        self.close_codes: list = []        # close code of each connection, as the server saw it

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0, ping_interval=None)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _send_dispatch(self, ws, t: str, d: dict, record: bool = True) -> None:
        self.seq += 1
        if record:
            self.log.append((self.seq, t, d))
        await ws.send(json.dumps({"op": 0, "s": self.seq, "t": t, "d": d}))

    async def dispatch(self, t: str, d: dict) -> None:
        """Send an event to the connected client (waits for the connection)."""
        await until(lambda: self.ws is not None, what="gateway client")
        await self._send_dispatch(self.ws, t, d)

    async def op(self, op: int, d=None) -> None:
        await until(lambda: self.ws is not None, what="gateway client")
        await self.ws.send(json.dumps({"op": op, "d": d}))

    async def drop(self, code: int = 1006) -> None:
        await until(lambda: self.ws is not None, what="gateway client")
        await self.ws.close(code if code != 1006 else 1001)

    def _ready(self) -> dict:
        self.sessions += 1
        return {"session_id": f"sess-{self.sessions}", "resume_gateway_url": self.url, "v": 10,
                "user": {"id": "bot", "username": "Harness_Agent"}, "application": {"id": "app"}}

    async def _handle(self, ws) -> None:
        self.connections += 1
        self.urls.append(ws.request.path)
        self.ws = ws
        try:
            await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": self.hb_ms}}))
            async for raw in ws:
                m = json.loads(raw)
                op = m["op"]
                if op == 1:
                    self.heartbeats += 1
                    if self.ack:
                        await ws.send(json.dumps({"op": 11}))
                elif op == 2:
                    d = m["d"]
                    if d.get("token") != TOKEN:
                        self.errors.append("bad token")
                        await ws.close(4004)
                        return
                    if d.get("intents") != INTENTS:
                        self.errors.append(f"bad intents {d.get('intents')}")
                        await ws.close(4013)
                        return
                    self.identifies.append(d)
                    await self._send_dispatch(ws, "READY", self._ready(), record=False)
                elif op == 6:
                    d = m["d"]
                    self.resumes.append(d)
                    if d.get("token") != TOKEN:
                        self.errors.append("bad token on resume")
                    if self.reject_resume:
                        self.reject_resume = False
                        await ws.send(json.dumps({"op": 9, "d": False}))
                        continue
                    for seq, t, data in [x for x in self.log if x[0] > d["seq"]]:      # replay what was missed
                        await ws.send(json.dumps({"op": 0, "s": seq, "t": t, "d": data}))
                    await self._send_dispatch(ws, "RESUMED", {}, record=False)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.close_codes.append(ws.close_code)
            if self.ws is ws:
                self.ws = None


# --- Discord REST -------------------------------------------------------------------------------------------------

class FakeDiscord:
    """httpx.MockTransport handler for https://discord.com/api/v10/..."""

    def __init__(self):
        self.messages: list[dict] = []
        self.threads: list[dict] = []
        self.responses: list[dict] = []        # interaction callbacks
        self.followups: list[dict] = []
        self.originals: list[dict] = []        # edits of an interaction's own answer (deferred / placeholder)
        self.edits: list[dict] = []
        self.deleted_channels: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.commands: list = []
        self.typing = 0
        self.calls: list[tuple[str, str]] = []
        self.channels: dict[str, dict] = {}
        self.fail_next: list[tuple[int, dict]] = []     # queued error responses (status, json body)
        self.n = 1000
        self.auth: set[str] = set()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def nid(self) -> str:
        self.n += 1
        return str(self.n)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.auth.add(req.headers.get("authorization", ""))
        path = req.url.path.removeprefix("/api/v10")
        self.calls.append((req.method, path))
        if self.fail_next:
            st, body = self.fail_next.pop(0)
            return httpx.Response(st, json=body)
        body = json.loads(req.content) if req.content else {}
        m = req.method
        if (r := re.fullmatch(r"/channels/(\w+)/messages", path)) and m == "POST":
            msg = {"id": self.nid(), "channel": r[1], "content": body.get("content", ""), "orig": body.get("content", ""), "components": body.get("components"), "embeds": body.get("embeds"),
                   "flags": body.get("flags", 0), "ref": (body.get("message_reference") or {}).get("message_id")}
            self.messages.append(msg)
            return httpx.Response(200, json={"id": msg["id"]})
        if (r := re.fullmatch(r"/channels/(\w+)/messages/(\w+)", path)) and m == "PATCH":
            self.edits.append({"channel": r[1], "id": r[2], **body})
            for x in self.messages:
                if x["id"] == r[2]:
                    x["content"] = body.get("content", x["content"])
                    if "components" in body:
                        x["components"] = body["components"]
            return httpx.Response(200, json={"id": r[2]})
        if (r := re.fullmatch(r"/channels/(\w+)/typing", path)):
            self.typing += 1
            return httpx.Response(204)
        if (r := re.fullmatch(r"/channels/(\w+)/messages/(\w+)/threads", path)):
            t = {"id": f"T{len(self.threads) + 1}", "parent": r[1], "message": r[2], "name": body["name"]}
            self.threads.append(t)
            self.channels[t["id"]] = {"id": t["id"], "type": 11, "parent_id": r[1]}
            return httpx.Response(200, json={"id": t["id"]})
        if (r := re.fullmatch(r"/channels/(\w+)/threads", path)):
            t = {"id": f"T{len(self.threads) + 1}", "parent": r[1], "message": None, "name": body["name"]}
            self.threads.append(t)
            self.channels[t["id"]] = {"id": t["id"], "type": 11, "parent_id": r[1]}
            return httpx.Response(200, json={"id": t["id"]})
        if (r := re.fullmatch(r"/channels/(\w+)/messages/(\w+)/reactions/(.+)/@me", path)):
            self.reactions.append((r[1], r[2], unquote(r[3])))
            return httpx.Response(204)
        if (r := re.fullmatch(r"/channels/(\w+)", path)):
            if m == "GET":
                return httpx.Response(200, json=self.channels.get(r[1], {"id": r[1], "type": 0}))
            if m == "PATCH":
                self.renamed.append((r[1], body["name"]))
                return httpx.Response(200, json={"id": r[1]})
            if m == "DELETE":
                self.deleted_channels.append(r[1])
                return httpx.Response(200, json={"id": r[1]})
        if (r := re.fullmatch(r"/applications/(\w+)/commands", path)) and m == "PUT":
            self.commands = body
            return httpx.Response(200, json=body)
        if (r := re.fullmatch(r"/interactions/([\w-]+)/([\w-]+)/callback", path)):
            self.responses.append({"id": r[1], "token": r[2], **body})
            return httpx.Response(204)
        if (r := re.fullmatch(r"/webhooks/(\w+)/([\w-]+)/messages/@original", path)) and m == "PATCH":
            self.originals.append({"token": r[2], **body})
            return httpx.Response(200, json={"id": "orig"})
        if (r := re.fullmatch(r"/webhooks/(\w+)/([\w-]+)", path)):
            self.followups.append({"token": r[2], **body})
            return httpx.Response(200, json={"id": self.nid()})
        return httpx.Response(404, json={"message": f"unknown route {m} {path}"})

    # helpers for assertions
    def in_channel(self, ch: str) -> list[dict]:
        return [x for x in self.messages if x["channel"] == ch]


# --- Harness HTTP server ---------------------------------------------------------------------------------------------

class FakeHarness:
    """A tiny HTTP/1.1 server (one request per connection) following docs/dev/API.md for the routes the bridge uses."""

    def __init__(self):
        self.server = None
        self.port = 0
        self.valid: set[str] = set()       # valid cookies
        self.logins = 0
        self.log: list[tuple[str, str]] = []     # (method, path?query) of every authenticated call
        self.projects = [{"name": "dev", "emoji": "", "topics": [
            {"name": "blog", "path": TOPIC, "description": "Blog", "running": 0, "session_count": 3},
            {"name": "docs", "path": "projects/dev/topics/docs", "description": "", "running": 0, "session_count": 1}]}]
        self.sessions: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}
        self.posted: list[tuple[str, dict]] = []      # (session id, body of POST /messages)
        self.created: list[dict] = []
        self.stopped: list[str] = []
        self.uploads: list[dict] = []
        self.stream_froms: list[tuple[str, int]] = []
        self.cut_gen = 0
        self.feed: list[dict] = []
        self.acts: list[tuple[int, str]] = []
        self.agents: list[dict] = []
        self.agent_msgs: dict[str, list[dict]] = {}
        self.agent_runs: list[tuple[str, dict]] = []
        self.files: dict[str, tuple[bytes, str]] = {}      # served at /cdn/<name> (Discord attachments)
        self.n = 0
        self.tasks: set[asyncio.Task] = set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._conn, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.server.close()
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    # scenario controls
    def revoke(self) -> None:
        """Forget every cookie: the next request gets 401."""
        self.valid.clear()

    def cut(self) -> None:
        """Close every open SSE stream (network cut)."""
        self.cut_gen += 1

    def emit(self, sid: str, *evs: dict) -> None:
        self.events.setdefault(sid, []).extend(evs)

    def add_session(self, sid: str = "S1", path: str = TOPIC, title: str = "", **kw) -> dict:
        s = {"id": sid, "title": title, "path": path, "archived": False, "state": "idle", "queue": 0,
             "cost_usd": 0.1, "turns": 1, "model": "", "real_model": "opus"}
        s.update(kw)
        self.sessions[sid] = s
        self.events.setdefault(sid, [])
        return s

    # HTTP plumbing
    async def _conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        t = asyncio.current_task()
        self.tasks.add(t)
        try:
            await self._serve(reader, writer)
        except (asyncio.CancelledError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.tasks.discard(t)
            writer.close()

    async def _serve(self, reader, writer) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        hdr = {k.lower(): v.strip() for k, v in (x.split(":", 1) for x in lines[1:] if ":" in x)}
        body = await reader.readexactly(int(hdr.get("content-length", 0)))
        u = urlsplit(target)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        cookie = re.search(r"sid=(\w+)", hdr.get("cookie", ""))
        authed = bool(cookie and cookie[1] in self.valid)
        path = u.path

        def send(status: int, obj=None, raw: bytes | None = None, ctype="application/json", extra=""):
            data = raw if raw is not None else json.dumps(obj).encode()
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n{extra}Connection: close\r\n\r\n".encode() + data)

        if path.startswith("/cdn/"):
            data, mime = self.files[path[5:]]
            return send(200, raw=data, ctype=mime)
        if path == "/api/auth/state":
            return send(200, {"language": "fr"})
        if path == "/api/auth/login":
            if hdr.get("x-maxxing") != "1":
                return send(403, {"error": "csrf"})
            self.logins += 1
            if json.loads(body).get("password") != PASSWORD:
                return send(401, {"ok": False, "error": "bad_password"})
            tok = f"tok{self.logins}"
            self.valid.add(tok)
            return send(200, {"ok": True}, extra=f"Set-Cookie: sid={tok}; Path=/; HttpOnly\r\n")
        if not authed:
            return send(401, {"error": "auth_required"})
        self.log.append((method, target))
        if path.endswith("/stream"):
            return await self._stream(writer, path.split("/")[3], int(q.get("from", 0)), cookie[1])
        code, obj = self.route(method, path, q, body, hdr)
        send(code, obj)

    def route(self, method: str, path: str, q: dict, body: bytes, hdr: dict) -> tuple[int, object]:
        j = json.loads(body) if body and "json" in hdr.get("content-type", "") else {}
        if path == "/api/workspace/tree":
            return 200, {"projects": self.projects}
        if path == "/api/sessions" and method == "GET":
            return 200, list(self.sessions.values())
        if path == "/api/sessions" and method == "POST":
            self.created.append(j)
            self.n += 1
            s = self.add_session(f"S{self.n}", j["path"], j.get("title", ""))
            if j.get("message"):
                s["state"] = "running"
                self.emit(s["id"], {"type": "user", "text": j["message"]})
            return 200, s
        if (r := re.fullmatch(r"/api/sessions/(\w+)", path)):
            s = self.sessions.get(r[1])
            if not s:
                return 404, {"error": "not_found"}
            if method == "PATCH":
                s.update({k: v for k, v in j.items() if k in ("archived", "model")})
            return 200, s
        if (r := re.fullmatch(r"/api/sessions/(\w+)/messages", path)):
            if r[1] not in self.sessions:
                return 404, {"error": "not_found"}
            self.posted.append((r[1], j))
            self.emit(r[1], {"type": "user", "text": j["text"]})
            self.sessions[r[1]]["state"] = "running"
            return 200, {"queued": False, "session": self.sessions[r[1]]}
        if (r := re.fullmatch(r"/api/sessions/(\w+)/stop", path)):
            self.stopped.append(r[1])
            return 200, {"ok": True}
        if (r := re.fullmatch(r"/api/sessions/(\w+)/events", path)):
            ev = self.events.get(r[1], [])
            return 200, {"events": ev[-1:], "start": max(0, len(ev) - 1), "next": len(ev), "more_before": False, "state": self.sessions[r[1]]["state"]}
        if path == "/api/uploads":
            ct = hdr["content-type"]
            bnd = ct.split("boundary=")[1].encode()
            files = []
            for part in body.split(b"--" + bnd):
                if b"filename=" not in part:
                    continue
                h, _, data = part.partition(b"\r\n\r\n")
                name = re.search(rb'filename="([^"]*)"', h)[1].decode()
                mime = (re.search(rb"Content-Type: ([^\r\n]+)", h) or [None, b""])[1].decode()
                data = data.removesuffix(b"\r\n")
                self.uploads.append({"topic": q.get("path"), "name": name, "mime": mime, "data": data})
                files.append({"path": f"/workspace/{q.get('path')}/attachments/2026-11/{name}", "name": name, "size": len(data), "mime": mime})
            return 200, {"files": files}
        if path == "/api/feed":
            return 200, {"events": sorted(self.feed, key=lambda e: -e["id"])[: int(q.get("limit", 50))], "more": False, "unread": 0, "pending": 0}
        if (r := re.fullmatch(r"/api/feed/(\d+)/act/(\w+)", path)):
            eid, action = int(r[1]), r[2]
            self.acts.append((eid, action))
            e = next((x for x in self.feed if x["id"] == eid), None)
            if not e:
                return 404, {"error": "not_found"}
            if not e.get("resolved"):
                e["resolved"] = True
                e["resolution"] = {"allow": "Allowed", "deny": "Denied"}.get(action, "Seen")
                e["actions"] = []
            return 200, {"event": e}
        if path == "/api/agents":
            return 200, {"agents": self.agents}
        if (r := re.fullmatch(r"/api/agents/(\w+)/(\w+)/(\w+)/thread", path)):
            return 200, {"messages": self.agent_msgs.get(f"{r[1]}/{r[2]}/{r[3]}", [])}
        if (r := re.fullmatch(r"/api/agents/(\w+)/(\w+)/(\w+)/run", path)):
            self.agent_runs.append((f"{r[1]}/{r[2]}/{r[3]}", j))
            return 200, {"started": True}
        return 404, {"error": "not_found", "message": f"{method} {path}"}

    async def _stream(self, writer, sid: str, frm: int, cookie: str) -> None:
        self.stream_froms.append((sid, frm))
        gen = self.cut_gen
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
        pos, last_state = frm, None
        n = 0
        while self.cut_gen == gen and cookie in self.valid:
            s = self.sessions[sid]
            if s["state"] != last_state:
                last_state = s["state"]
                writer.write(f"event: state\ndata: {json.dumps(s)}\n\n".encode())
            ev = self.events.get(sid, [])
            if pos < len(ev):
                writer.write(f"event: events\ndata: {json.dumps({'events': ev[pos:], 'next': len(ev)})}\n\n".encode())
                pos = len(ev)
            n += 1
            if n % 50 == 0:
                writer.write(b": hb\n\n")
            await writer.drain()
            await REAL_SLEEP(0.02)
