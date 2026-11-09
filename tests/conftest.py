import asyncio

import pytest


@pytest.fixture
def fast_sleep(monkeypatch):
    """Long waits of the bridge (reconnect backoff, 3 s retry, typing loop, invalid-session jitter) last at most 50 ms."""
    real = asyncio.sleep

    async def quick(delay, result=None):
        return await real(min(delay, 0.05), result)

    monkeypatch.setattr(asyncio, "sleep", quick)
    monkeypatch.setattr("random.uniform", lambda a, b: 0.01)


@pytest.fixture(autouse=True)
def local_cdn(request, monkeypatch):
    """Integration tests download from a local fake CDN; the host allow-list has its own test."""
    if "allowlist" not in request.node.name:
        from harness_discord.discord_api import Rest
        monkeypatch.setattr(Rest, "CDN_ONLY", False)
