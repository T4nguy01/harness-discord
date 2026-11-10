"""Lot E: tool-call approvals with Discord buttons (poller, dedup with the feed, allow / deny / always / reason, external decisions)."""
import json
import time
from pathlib import Path

import pytest

from fakes import OWNER, TOPIC
from harness_discord import approvals, config
from harness_discord.approvals import excerpt, mask
from harness_discord.follow import Follower
from test_integration_bot import PROJECT_CH, inter, link, scenario

pytestmark = pytest.mark.usefixtures("fast_sleep")
RID = "0123456789abcdef"
RID2 = "fedcba9876543210"


def req(rid=RID, **kw):
    d = {"id": rid, "session": "S1", "agent_path": "", "always_rule": "Bash(git push:*)", "tool": "Bash",
         "input": {"command": "git push origin main"}, "summary": "Bash: git push origin main", "at": time.time(),
         "expires": time.time() + 1800, "status": "pending", "message": "", "decided_at": None, "clipped": False}
    d.update(kw)
    return d


def click(e, cid, iid, mid, user=OWNER, channel="T1"):
    d = inter(3, {"custom_id": cid}, channel=channel, user=user, iid=iid)
    d["message"] = {"id": mid}
    return e.fg.dispatch("INTERACTION_CREATE", d)


def card(e, ch="T1", n=-1):
    return e.fd.in_channel(ch)[n]


def audit(e):
    p = Path(e.bot.store.path).parent / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def ids(msg):
    return [c.get("custom_id") for row in msg["components"] for c in row["components"]]


async def wait_post(e, rid, status=None):
    from fakes import until
    await until(lambda: any(p[0] == rid for p in e.fh.approval_posts), what="POST approval")


async def last_edit(e, mid):
    from fakes import until
    await until(lambda: any(x["id"] == mid and x.get("embeds") for x in e.fd.edits), what="embed edit")
    return [x for x in e.fd.edits if x["id"] == mid and x.get("embeds")][-1]


# === posting ==================================================================================================================

@scenario
async def test_pending_request_is_posted_once_in_the_session_thread_with_buttons(e):
    link(e)
    e.fh.approvals = [req(input={"command": "curl -H 'Authorization: Bearer abcdef1234567890' -d token=hunter2 x.io"})]
    await e.bot.relay.approvals.tick()
    posts = e.fd.in_channel("T1")
    assert len(posts) == 1
    em = posts[0]["embeds"][0]
    assert em["color"] == approvals.ORANGE and "Bash" in em["title"] and "<#T1>" in em["fields"][0]["value"]
    assert "git push" not in em["description"] and "hunter2" not in em["description"] and "abcdef1234567890" not in em["description"]
    assert ids(posts[0]) == [f"ap:y:{RID}", f"ap:n:{RID}", f"ap:a:{RID}", f"ap:r:{RID}"]
    await e.bot.relay.approvals.tick()
    await e.bot.relay.approvals.tick()
    assert len(e.fd.in_channel("T1")) == 1                                       # no double post
    e.bot.store.save()
    assert RID in json.loads(Path(e.bot.store.path).read_text())["approvals"]


@scenario
async def test_request_of_an_unlinked_session_goes_to_alerts_and_long_ones_can_only_be_denied(e):
    e.fh.approvals = [req(session="OTHER", input={"file_path": "/tmp/a.py", "content": "print(1)"}),
                      req(RID2, session="OTHER", clipped=True)]
    await e.bot.relay.approvals.tick()
    a, b = e.fd.in_channel("CA")
    assert ids(a)[:2] == [f"ap:n:{RID}", f"ap:r:{RID}"] and "http://web" in json.dumps(a["components"])      # cut: no Allow, no Always
    assert f"ap:y:{RID2}" not in ids(b) and "ap:a:" + RID2 not in ids(b)


@scenario
async def test_no_channel_no_post_and_setting_off_does_nothing(e):
    e.bot.lay.pop("alerts")
    e.fh.approvals = [req(session="OTHER")]
    await e.bot.relay.approvals.tick()
    assert e.fd.messages == [] and RID not in e.bot.store.data.get("approvals", {})
    link(e)
    e.bot.cfg["tool_approvals"] = False
    n = len(e.fh.log)
    await e.bot.relay.approvals.tick()
    assert e.fd.messages == [] and len(e.fh.log) == n                              # not even a call to Harness


@scenario
async def test_feed_event_of_the_same_request_is_not_posted_twice_unless_the_setting_is_off(e):
    link(e)
    base = {"id": 1, "at": 1.0, "source": "agents", "level": "warning", "title": "waiting", "body": "b", "url": "", "count": 1,
            "read": False, "actions": [{"id": "allow", "label": "Allow"}], "ref": None, "agent": None, "resolved": None, "resolution": None}
    e.fh.feed = [dict(base)]
    await e.bot.relay.feed()                                                       # baseline
    e.fh.feed += [dict(base, id=2, ref=f"approval:{RID}")]
    await e.bot.relay.feed()
    assert e.fd.in_channel("CAG") == []                                            # the approvals poller owns it
    e.bot.cfg["tool_approvals"] = False
    e.fh.feed += [dict(base, id=3, ref=f"approval:{RID2}")]
    await e.bot.relay.feed()
    assert len(e.fd.in_channel("CAG")) == 1                                        # setting off: the feed keeps working as before


# === decisions ================================================================================================================

@scenario
async def test_allow_button_answers_harness_edits_the_card_green_and_audits_without_the_content(e):
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    mid = card(e)["id"]
    await click(e, f"ap:y:{RID}", "I1", mid)
    await wait_post(e, RID)
    assert e.fh.approval_posts == [(RID, {"decision": "allow"})]
    ed = await last_edit(e, mid)
    assert ed["embeds"][0]["color"] == approvals.GREEN and ed["components"] == []
    assert f"<@{OWNER}>" in json.dumps(ed["embeds"][0]["fields"]) and "git push" in ed["embeds"][0]["description"]
    rec = audit(e)[-1]
    assert rec["cmd"] == "approval" and rec["target"] == f"Bash {RID}" and rec["result"] == "allow"
    assert "git push" not in json.dumps(audit(e))
    await e.bot.relay.approvals.tick()                                             # decided by us: nothing more happens
    assert len([x for x in e.fd.edits if x["id"] == mid]) == 1


@scenario
async def test_deny_with_a_reason_opens_a_form_then_sends_the_message(e):
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    mid = card(e)["id"]
    await click(e, f"ap:r:{RID}", "I1", mid)
    from fakes import until
    r = (await until(lambda: [x for x in e.fd.responses if x["id"] == "I1"], what="modal"))[-1]
    assert r["type"] == 9 and r["data"]["custom_id"] == f"apm:{RID}" and r["data"]["components"][0]["components"][0]["custom_id"] == "reason"
    assert e.fh.approval_posts == []
    sub = inter(5, {"custom_id": f"apm:{RID}", "components": [{"type": 1, "components": [{"type": 4, "custom_id": "reason", "value": "pas sur main"}]}]},
                channel="T1", iid="I2")
    await e.fg.dispatch("INTERACTION_CREATE", sub)
    await wait_post(e, RID)
    assert e.fh.approval_posts == [(RID, {"decision": "deny", "message": "pas sur main"})]
    ed = await last_edit(e, mid)
    assert ed["embeds"][0]["color"] == approvals.RED and "pas sur main" in json.dumps(ed["embeds"][0]["fields"], ensure_ascii=False)
    assert audit(e)[-1]["result"] == "deny+reason" and "pas sur main" not in json.dumps(audit(e))


@scenario
async def test_deny_button(e):
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    mid = card(e)["id"]
    await click(e, f"ap:n:{RID}", "I1", mid)
    await wait_post(e, RID)
    assert e.fh.approval_posts == [(RID, {"decision": "deny"})]
    assert (await last_edit(e, mid))["embeds"][0]["color"] == approvals.RED


@scenario
async def test_always_allow_needs_a_second_click(e):
    from fakes import until
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    mid = card(e)["id"]
    await click(e, f"ap:a:{RID}", "I1", mid)
    r = (await until(lambda: [x for x in e.fd.responses if x["id"] == "I1"], what="confirmation"))[-1]
    assert r["type"] == 4 and r["data"]["flags"] == 64 and "Bash(git push:*)" in r["data"]["content"]
    assert r["data"]["components"][0]["components"][0]["custom_id"] == f"ap:c:{RID}"
    assert e.fh.approval_posts == [] and not any(x["id"] == mid for x in e.fd.edits)          # nothing permanent yet
    await click(e, f"ap:c:{RID}", "I2", "ephemeral-1")
    await wait_post(e, RID)
    assert e.fh.approval_posts == [(RID, {"decision": "allow", "always": True})]
    ed = await last_edit(e, mid)
    assert ed["embeds"][0]["color"] == approvals.GREEN and audit(e)[-1]["result"] == "always"


@scenario
async def test_always_is_refused_when_harness_has_no_rule_for_it(e):
    from fakes import until
    link(e)
    e.fh.approvals = [req(always_rule="")]
    await e.bot.relay.approvals.tick()
    assert ids(card(e)) == [f"ap:y:{RID}", f"ap:n:{RID}", f"ap:r:{RID}"]               # button not offered
    await click(e, f"ap:a:{RID}", "I1", card(e)["id"])                                  # forged / old click
    r = (await until(lambda: [x for x in e.fd.responses if x["id"] == "I1"], what="answer"))[-1]
    assert r["data"]["flags"] == 64 and e.fh.approval_posts == []


@scenario
async def test_expired_request_gets_a_clear_private_message_and_the_card_is_closed(e):
    from fakes import until
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    mid = card(e)["id"]
    e.fh.approvals[0]["status"] = "timeout"                                             # the poller has not noticed yet
    await click(e, f"ap:y:{RID}", "I1", mid)
    f = (await until(lambda: [x for x in e.fd.followups if x["token"] == "tk-I1"], what="private message"))[-1]
    assert f["flags"] == 64 and "expiré" in f["content"]
    ed = await last_edit(e, mid)
    assert ed["embeds"][0]["color"] == approvals.GREY and ed["components"] == []
    await click(e, f"ap:y:{RID}", "I2", mid)                                            # clicking again: private message, no new call
    r = (await until(lambda: [x for x in e.fd.responses if x["id"] == "I2"], what="stale"))[-1]
    assert r["data"]["flags"] == 64 and len(e.fh.approval_posts) == 1
    await click(e, f"ap:y:{RID2}", "I3", mid)                                           # unknown request
    r = (await until(lambda: [x for x in e.fd.responses if x["id"] == "I3"], what="unknown"))[-1]
    assert r["data"]["flags"] == 64 and len(e.fh.approval_posts) == 1


@scenario
async def test_only_the_owner_can_decide(e):
    from fakes import until
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    await click(e, f"ap:y:{RID}", "I1", card(e)["id"], user="666")
    r = (await until(lambda: [x for x in e.fd.responses if x["id"] == "I1"], what="refusal"))[-1]
    assert r["data"]["flags"] == 64 and e.fh.approval_posts == []
    assert e.bot.relay.approvals.st[RID]["done"] is False


@scenario
async def test_a_button_of_another_message_is_ignored(e):
    from fakes import until
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    await click(e, f"ap:y:{RID}", "I1", "some-other-message", channel="D9")             # a DM or an unlinked thread
    r = (await until(lambda: [x for x in e.fd.responses if x["id"] == "I1"], what="ignored"))[-1]
    assert r["data"]["flags"] == 64 and e.fh.approval_posts == [] and not e.bot.relay.approvals.st[RID]["done"]


@scenario
async def test_decision_made_elsewhere_updates_the_card(e):
    link(e)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    mid = card(e)["id"]
    e.fh.approvals[0]["status"] = "allow"                                               # allowed in the web UI or on Telegram
    await e.bot.relay.approvals.tick()
    ed = await last_edit(e, mid)
    assert ed["embeds"][0]["color"] == approvals.GREY and ed["components"] == [] and e.fh.approval_posts == []
    assert "ailleurs" in json.dumps(ed["embeds"][0]["fields"], ensure_ascii=False)
    await e.bot.relay.approvals.tick()
    assert len([x for x in e.fd.edits if x["id"] == mid]) == 1


# === live status ==============================================================================================================

@scenario
async def test_live_status_says_it_waits_for_an_approval(e):
    link(e)
    f = e.bot.followers["S1"] = Follower("S1", "T1", 0)
    e.fh.approvals = [req()]
    await e.bot.relay.approvals.tick()
    status = [m for m in e.fd.in_channel("T1") if "approbation" in m["content"]]
    assert len(status) == 1 and e.bot.relay.approvals.waiting("S1") == 1
    await e.bot.status(f, "🔧 Bash")                                                     # an event arriving meanwhile keeps the line
    assert "approbation" in e.fd.in_channel("T1")[-1]["content"] or "approbation" in [m for m in e.fd.edits][-1].get("content", "")
    e.bot.followers.clear()


# === helpers ==================================================================================================================

def test_mask_hides_obvious_secrets_but_not_paths():
    assert "hunter2" not in mask("export DB_PASSWORD=hunter2 && run") and "DB_PASSWORD=***" in mask("export DB_PASSWORD=hunter2")
    assert "s3cr3t" not in mask("mycli --password s3cr3t --verbose") and "--verbose" in mask("mycli --password s3cr3t --verbose")
    assert "ghp_" not in mask("git clone https://ghp_abcdefghijklmnopqrstuvwxyz0123@github.com/x/y")
    assert "tok123456789" not in mask("curl -H 'Authorization: Bearer tok123456789' x")
    path = "/root/maxxing/projects/systeme/topics/harness/memory.md"
    assert mask(path) == path and mask("git push origin main") == "git push origin main"


def test_excerpt_whole_only_when_nothing_is_left_out():
    assert excerpt({"tool": "Bash", "input": {"command": "ls", "description": "list"}}) == ("ls", True)
    assert excerpt({"tool": "Write", "input": {"file_path": "/a", "content": "x"}})[1] is False
    assert excerpt({"tool": "Bash", "input": {"command": "x" * 3000}})[1] is False
    assert excerpt({"tool": "Bash", "input": {"command": "ls"}, "clipped": True})[1] is False


def test_setting_defaults_on_and_custom_ids_are_short():
    assert config.DEFAULTS["tool_approvals"] is True and "tool_approvals" in config.BOOLS
    for c in ("y", "n", "a", "r", "c"):
        assert len(f"ap:{c}:{RID}") <= 100 and approvals.CID.fullmatch(f"ap:{c}:{RID}")
    assert not approvals.CID.fullmatch("ap:y:../../x") and not approvals.CID.fullmatch(f"ap:z:{RID}")
