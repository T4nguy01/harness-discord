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


@pytest.fixture(autouse=True)
def night(monkeypatch):
    """The morning summary depends on the time of day: tests run at 03:00 unless they set their own clock."""
    import datetime
    from harness_discord import notifier
    at = datetime.datetime(2026, 11, 10, 3, 0).timestamp()
    monkeypatch.setattr(notifier, "now", lambda: at)


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """No test may touch the real state folder of the running bridge (status.json, audit.jsonl...)."""
    monkeypatch.setenv("HD_STATE_DIR", str(tmp_path / "hd-state"))
