---
name: discord-bridge
description: Set up, check or fix the Harness Discord bridge (talk to Harness sessions from a Discord server). Use when the user mentions the Discord bot, its channels, or it does not answer.
---

# Harness Discord bridge

The bridge is a service (`harness-discord`) that signs in to Harness like the owner, and a page extension `discord`
(settings, status) in `<workspace>/extensions/discord/`. Secrets live only in Settings -> Keys:
`DISCORD_BOT_TOKEN` and `HARNESS_PASSWORD` (never print them, never write them in a file or a message).
Settings (not secret): `<state>/extensions/discord/config.json` (`owner_id`, `guild_id`, `reply_mode`, `relay_feed`,
`relay_agents`, `relay_bell`, `mirror_web_sessions`, `reactions`, `answer_buttons`, `dashboard`, `proactive`, `tool_approvals`; all booleans, on by default; also `workspace_dir`, the folder `/harness files` may read, default `/root/maxxing`); status: `status.json` next to it. The Discord page edits them. The bridge re-reads the file every 10 s, but a setting takes effect when it is used:
`owner_id`, `reply_mode`, `relay_feed`, `relay_bell`, `reactions`, `answer_buttons`, `dashboard`, `proactive`, `tool_approvals`: within 10 s (`dashboard`: the message is refreshed within 10 minutes); `mirror_web_sessions`: at the next channel sync (Resync, or every 10 minutes); `relay_agents`: within 30 s (agents are polled every third
pass); `guild_id`: at the next channel sync (Resync button: within 10 s, otherwise every 10 minutes); keys
(`DISCORD_BOT_TOKEN`, `HARNESS_PASSWORD`): only after a restart of the service. The *Test message* and *Resync*
buttons leave a flag file that the bridge picks up within 10 s.

## Set up (the user does the Discord side)
1. Discord developer portal: new application -> Bot -> enable "Message Content Intent" -> copy the token.
2. Invite URL with scopes `bot applications.commands` and permissions integer `326686043216` (never Administrator):
   View Channels, Manage Channels, Manage Roles (private overwrites), Send Messages, Send Messages in Threads, Create
   Public Threads, Manage Threads, Read Message History, Add Reactions, Embed Links, Manage Messages (to pin the dashboard; optional). The server should be the user's own.
3. Keys: add `DISCORD_BOT_TOKEN` and `HARNESS_PASSWORD` (the Harness password). Two-step sign-in must be off
   (`maxxing 2fa status`): the bridge cannot answer it.
4. `sh <workspace>/extensions/discord/install.sh` (root): virtualenv + systemd service (restarts it if already running).
   Remove: `sh <workspace>/extensions/discord/uninstall.sh` (`--purge` also deletes the state folder).
   `maxxing` must be on the service PATH (`command -v maxxing`; the unit uses `/usr/local/bin:/usr/bin:/bin`).
5. The owner's Discord id in the Discord page. The bot then creates private categories: *Harness* (alerts,
   notifications, crons, agents) and *Projects* (one channel per Harness project, one thread per session).

## What the bridge does besides sessions
- Web mirror (`mirror_web_sessions`): a thread per recent web session (30 days, 50 max), named `<project emoji> topic · title`,
  renamed with the title, archived / restored with the session. Only a welcome message, no history.
- Reactions (`reactions`): ⏳ then ✅ / ❌ on the user's message; an edited message gets one ✏️ and is not sent again.
- Buttons under the final answer (`answer_buttons`): Regenerate, and Stop when a turn waits behind. Gone at the next turn.
- Commands with autocomplete: `/model` (free names like `opus`, `Sonnet 5.5`) and `/new [title] [topic]`; message menus
  "Send to Claude" and "New session from message"; forwarded messages are sent marked `[Forwarded]`.
- `/harness` tools (owner only, private answers): `tasks` (Done / In progress buttons), `task add` (form), `cron list|run|toggle`,
  `agent run`, `usage`, `health` (Mark seen), `search` (menu opening the session thread), `backup status|run`, `infra` (read only,
  Refresh), `queue` (Clear), `files ls|get` (workspace only, 25 MB, never secret files or folders), `mail unread|outbox`,
  `audit`. What runs or changes something asks for Confirm / Cancel buttons first (2 minutes, one use). Never offered: keys,
  restoring a backup, deletions, updates, restarting a service, direct mail sending. `/new form:true` opens a form. Commands are
  localized (French names) and registered only when their definition changed (hash in state.json: `commands_hash`).
- Audit trail: `audit.jsonl` in the state folder (mode 600, rotated at about 1 MB; time, command, target, result; never message
  text). `/harness audit` shows the last 10. Read it to answer "who did that from Discord".
- Tool-call approvals (`tool_approvals`): pending requests of `GET /api/approvals` are posted (polled every 3 s while a session is
  followed, else 10 s) in the session thread, else `alerts`, with Allow / Deny / Always allow (2 steps) / Deny with a reason (form).
  It is the same queue as the web UI and Telegram: the first decision wins, the card then shows how it ended (a late click gets a
  private "already answered or expired"). Owner only. The audit trail keeps tool, id and decision, never the request. Feed events
  with an `approval:<id>` ref are not posted again. Off = the feed alone relays them, as before.
- Proactive messages (`proactive`): 08:00 task summary (silent, `notifications`, once a day), plan above 80 % and failed backup
  (`alerts`, once each). Switch off with `proactive: false`.
- `/close` archives and locks the thread (no deletion); session threads auto-archive after 7 days; `alerts` has a 2 s slow mode;
  the dashboard and agent results carry an "Open in Harness" link button.
- Presence ("N sessions running" / "idle") and a pinned dashboard in `alerts` (`dashboard`; the pin needs Manage Messages,
  without it the dashboard is posted unpinned: look for "dashboard not pinned" in the journal).

## Check / fix
- `systemctl status harness-discord`, `journalctl -u harness-discord -n 50 --no-pager` (never paste a token).
- `missing DISCORD_BOT_TOKEN...` -> a key or the owner id is missing. `cannot sign in` -> wrong password or two-step on.
- No answer in a channel: only the owner id is heard; the channel must be a project channel or a session thread.
- A channel was deleted by hand: the Discord page -> Resync channels (or wait 10 minutes).
- After changing a key: `systemctl restart harness-discord` (say so first).

## Rules
Ask before deleting channels or messages on the user's server, or restarting the service. Do not add Discord commands that handle keys, restore, delete, update or restart. Never publish the
project (GitHub, community library) without the user's explicit agreement.
