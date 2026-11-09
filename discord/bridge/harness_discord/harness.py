"""Client of the Harness Maxxing HTTP API (docs/dev/API.md): sign in once, keep the cookie, sign in again on a 401."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

import httpx

log = logging.getLogger("harness_discord.harness")


class HarnessError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}".strip())
        self.status, self.code, self.message = status, code, message


class Harness:
    def __init__(self, base_url: str, password: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base = base_url.rstrip("/")
        self.password = password
        self.http = httpx.AsyncClient(base_url=self.base, headers={"X-Maxxing": "1"}, timeout=30, transport=transport)
        self._signed = False

    async def close(self) -> None:
        await self.http.aclose()

    async def login(self) -> None:
        r = await self.http.post("/api/auth/login", json={"password": self.password})
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not d.get("ok"):
            if d.get("two_factor"):
                raise HarnessError(r.status_code, "two_factor", "two-step sign-in is on: the bridge cannot sign in")
            raise HarnessError(r.status_code, d.get("error", "login_failed"), d.get("message", ""))
        self._signed = True

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        for attempt in (1, 2):
            if not self._signed:
                await self.login()
            r = await self.http.request(method, path, **kw)
            if r.status_code == 401 and attempt == 1:
                self._signed = False
                continue
            break
        if r.status_code >= 400:
            try:
                d = r.json()
            except ValueError:
                d = {}
            raise HarnessError(r.status_code, d.get("error", "http_error"), d.get("message", r.text[:200]))
        return r.json() if r.content else {}

    # --- workspace and sessions -------------------------------------------------------------------------------

    async def language(self) -> str:
        r = await self.http.get("/api/auth/state")
        return (r.json().get("language") or "en")[:2]

    async def tree(self) -> list[dict]:
        return (await self.request("GET", "/api/workspace/tree"))["projects"]

    async def sessions(self, path: str = "") -> list[dict]:
        return await self.request("GET", "/api/sessions", params={"path": path} if path else None)

    async def session(self, sid: str) -> dict:
        return await self.request("GET", f"/api/sessions/{sid}")

    async def create_session(self, path: str, message: str = "", attachments: list[str] | None = None,
                             voice: str | None = None, title: str = "") -> dict:
        body: dict[str, Any] = {"path": path}
        if message:
            body["message"] = message
        if attachments:
            body["attachments"] = attachments
        if voice:
            body["voice"] = voice
        if title:
            body["title"] = title
        return await self.request("POST", "/api/sessions", json=body)

    async def send(self, sid: str, text: str, attachments: list[str] | None = None, voice: str | None = None) -> dict:
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if voice:
            body["voice"] = voice
        return await self.request("POST", f"/api/sessions/{sid}/messages", json=body)

    async def stop(self, sid: str) -> None:
        await self.request("POST", f"/api/sessions/{sid}/stop")

    async def archive(self, sid: str) -> dict:
        return await self.request("PATCH", f"/api/sessions/{sid}", json={"archived": True})

    async def set_model(self, sid: str, model: str) -> dict:
        return await self.request("PATCH", f"/api/sessions/{sid}", json={"model": model})

    async def upload(self, path: str, name: str, data: bytes, mime: str = "application/octet-stream") -> str:
        d = await self.request("POST", "/api/uploads", params={"path": path}, files={"file": (os.path.basename(name) or "file", data, mime)})
        return d["files"][0]["path"]

    async def tail(self, sid: str) -> int:
        """Offset of the end of the session log (where to start following)."""
        d = await self.request("GET", f"/api/sessions/{sid}/events", params={"tail": 1})
        return int(d.get("next", 0))

    async def stream(self, sid: str, offset: int) -> AsyncIterator[tuple[str, Any]]:
        """SSE of one session: yields ("events", {events, next}) / ("state", Session) / ("reset", {})."""
        if not self._signed:
            await self.login()
        async with self.http.stream("GET", f"/api/sessions/{sid}/stream", params={"from": offset},
                                    timeout=httpx.Timeout(30, read=None)) as r:
            if r.status_code == 401:
                self._signed = False
                raise HarnessError(401, "auth_required")
            if r.status_code >= 400:
                raise HarnessError(r.status_code, "stream_failed")
            ev = "message"
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        yield ev, json.loads(line[5:].strip())
                    except ValueError:
                        pass
                    ev = "message"
