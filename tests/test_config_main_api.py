"""config.py (maxxing keys / status through subprocess), __main__ exit codes, discord/api.py (settings page API)."""
import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from harness_discord import __main__ as m
from harness_discord import config

ROOT = Path(__file__).resolve().parent.parent


def fake_run(monkeypatch, fn):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return fn(cmd, kw)
    monkeypatch.setattr(config.subprocess, "run", run)
    return calls


def done(out="", code=0):
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


# --- config.py --------------------------------------------------------------------------------------------------

def test_key_prefers_the_environment_and_never_calls_maxxing(monkeypatch):
    monkeypatch.setenv("X_KEY", "  abc \n")
    calls = fake_run(monkeypatch, lambda c, k: done("nope"))
    assert config.key("X_KEY") == "abc"
    assert calls == []


def test_key_reads_the_vault(monkeypatch):
    monkeypatch.delenv("X_KEY", raising=False)
    calls = fake_run(monkeypatch, lambda c, k: done("secret\n"))
    assert config.key("X_KEY") == "secret"
    assert calls[0][0] == ["maxxing", "keys", "get", "X_KEY"] and calls[0][1]["timeout"] == 10


@pytest.mark.parametrize("res", [done("x", 1), done("")])
def test_key_none_on_failure_or_empty(monkeypatch, res):
    monkeypatch.delenv("X_KEY", raising=False)
    fake_run(monkeypatch, lambda c, k: res)
    assert config.key("X_KEY") is None


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired("maxxing", 10), FileNotFoundError("maxxing")])
def test_key_none_on_timeout_or_missing_binary(monkeypatch, exc):
    monkeypatch.delenv("X_KEY", raising=False)

    def boom(c, k):
        raise exc
    fake_run(monkeypatch, boom)
    assert config.key("X_KEY") is None


def test_harness_url_env_status_and_fallback(monkeypatch):
    monkeypatch.setenv("HARNESS_URL", "http://h:1/")
    assert config.harness_url() == "http://h:1"
    monkeypatch.delenv("HARNESS_URL")
    calls = fake_run(monkeypatch, lambda c, k: done("Version  2\nURL      https://x.ts.net/\n"))
    assert config.harness_url() == "https://x.ts.net" and calls[0][1]["timeout"] == 20
    fake_run(monkeypatch, lambda c, k: done("nothing here"))
    assert config.harness_url() == "http://127.0.0.1:8787"

    def boom(c, k):
        raise subprocess.TimeoutExpired("maxxing", 20)
    fake_run(monkeypatch, boom)
    assert config.harness_url() == "http://127.0.0.1:8787"


def test_load_settings_defaults_file_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HD_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("DISCORD_OWNER_ID", raising=False)
    assert config.load_settings()["reply_mode"] == "live"
    (tmp_path / "config.json").write_text(json.dumps({"owner_id": 123, "guild_id": None, "relay_feed": False}))
    d = config.load_settings()
    assert d["owner_id"] == "123" and d["guild_id"] == "" and d["relay_feed"] is False and d["relay_bell"] is True
    monkeypatch.setenv("DISCORD_OWNER_ID", " 999 ")
    assert config.load_settings()["owner_id"] == "999"
    (tmp_path / "config.json").write_text("{broken")
    assert config.load_settings()["relay_agents"] is True


# --- __main__ exit codes ----------------------------------------------------------------------------------------

def set_env(monkeypatch, tmp_path, **kw):
    env = {"DISCORD_BOT_TOKEN": "t", "HARNESS_PASSWORD": "p", "DISCORD_OWNER_ID": "123456789012345678",
           "HARNESS_URL": "http://127.0.0.1:1", "HD_STATE_DIR": str(tmp_path)}
    env.update(kw)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: done("", 1))     # the vault has nothing


def test_exit_2_when_a_key_is_missing(tmp_path, monkeypatch):
    set_env(monkeypatch, tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    assert asyncio.run(m.amain()) == 2


def test_exit_2_when_the_url_is_refused(tmp_path, monkeypatch):
    set_env(monkeypatch, tmp_path, HARNESS_URL="http://example.com")
    assert asyncio.run(m.amain()) == 2


def test_exit_3_when_harness_cannot_be_reached(tmp_path, monkeypatch):
    set_env(monkeypatch, tmp_path)
    assert asyncio.run(m.amain()) == 3


def test_write_error_goes_to_status_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HD_STATE_DIR", str(tmp_path))
    m.write_error("boom")
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["error"] == "boom" and st["connected"] is False and st["version"]


# --- discord/api.py (stdlib only: called with a fake ctx) ---------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("hd_page_api", ROOT / "discord" / "api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="active\n", stderr="")
    monkeypatch.setattr(mod.subprocess, "run", run)
    mod.calls = calls
    mod.ctx = {"state_dir": str(tmp_path), "secret": lambda n: "x" if n == "DISCORD_BOT_TOKEN" else None}
    mod.dir = tmp_path
    return mod


def test_api_status_never_returns_secrets(api):
    st = api.handle(api.ctx, "GET", "status", {}, None)
    assert st["service"] == "active" and st["keys"] == {"token": True, "password": False}
    assert st["config"]["reply_mode"] == "live" and st["alive"] is False and st["error"] == ""


def test_api_status_shows_the_fatal_error_and_liveness(api):
    import time
    (api.dir / "status.json").write_text(json.dumps({"at": time.time(), "connected": True, "running": 2, "error": "e" * 500}))
    st = api.handle(api.ctx, "GET", "", {}, None)
    assert st["alive"] and st["connected"] and st["running"] == 2 and len(st["error"]) == 300
    (api.dir / "status.json").write_text(json.dumps({"at": time.time() - 500, "connected": True}))
    assert api.handle(api.ctx, "GET", "status", {}, None)["connected"] is False


def test_api_config_validation(api):
    bad = [{"owner_id": "abc"}, {"guild_id": "12"}, {"reply_mode": "loud"}, {"relay_feed": "yes"}, {"relay_bell": 1}]
    for b in bad:
        code, out = api.handle(api.ctx, "POST", "config", {}, b)
        assert code == 400 and out["error"], b
    assert not (api.dir / "config.json").exists()


def test_api_config_saves_valid_values(api):
    for mode in api.MODES:
        r = api.handle(api.ctx, "POST", "config", {}, {"reply_mode": mode})
        assert r["config"]["reply_mode"] == mode
    r = api.handle(api.ctx, "POST", "config", {}, {"owner_id": " 123456789012345678 ", "guild_id": "", "relay_feed": False})
    assert r["config"]["owner_id"] == "123456789012345678" and r["config"]["relay_feed"] is False
    saved = json.loads((api.dir / "config.json").read_text())
    assert saved["relay_feed"] is False and saved["reply_mode"] == "final"
    assert api.MODES == ("live", "final")


def test_api_actions(api):
    for act in ("sync", "test"):
        assert api.handle(api.ctx, "POST", "action", {}, {"action": act}) == {"ok": True}
        assert (api.dir / f"{act}.flag").exists()
    r = api.handle(api.ctx, "POST", "action", {}, {"action": "restart"})
    assert r["ok"] is True and ["systemctl", "restart", "harness-discord"] in api.calls
    code, out = api.handle(api.ctx, "POST", "action", {}, {"action": "rm -rf"})
    assert code == 400 and out["error"] == "bad_action"
    assert api.handle(api.ctx, "GET", "nope", {}, None)[0] == 404


def test_proactive_setting_defaults_on_and_is_a_strict_boolean(api, tmp_path, monkeypatch):
    assert "proactive" in config.BOOLS and config.DEFAULTS["proactive"] is True
    assert api.handle(api.ctx, "GET", "status", {}, None)["config"]["proactive"] is True
    for bad in ("false", 0, 1, None, "yes", []):
        code, out = api.handle(api.ctx, "POST", "config", {}, {"proactive": bad})
        assert code == 400 and out["error"] == "bad_flag", bad
    assert not (api.dir / "config.json").exists()
    r = api.handle(api.ctx, "POST", "config", {}, {"proactive": False})
    assert r["config"]["proactive"] is False and json.loads((api.dir / "config.json").read_text())["proactive"] is False
    monkeypatch.setenv("HD_STATE_DIR", str(api.dir))
    assert config.load_settings()["proactive"] is False


def test_tool_approvals_setting_defaults_on_and_is_a_strict_boolean(api):
    assert "tool_approvals" in config.BOOLS and config.DEFAULTS["tool_approvals"] is True
    assert api.handle(api.ctx, "GET", "status", {}, None)["config"]["tool_approvals"] is True
    for bad in ("false", 0, None, []):
        code, out = api.handle(api.ctx, "POST", "config", {}, {"tool_approvals": bad})
        assert code == 400 and out["error"] == "bad_flag", bad
    r = api.handle(api.ctx, "POST", "config", {}, {"tool_approvals": False})
    assert r["config"]["tool_approvals"] is False
