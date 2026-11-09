---
name: discord-bridge
description: Set up, check or fix the Harness Discord bridge (talk to Harness sessions from a Discord server). Use when the user mentions the Discord bot, its channels, or it does not answer.
---

# Harness Discord bridge

The bridge is a service (`harness-discord`) that signs in to Harness like the owner, and a page extension `discord`
(settings, status) in `<workspace>/extensions/discord/`. Secrets live only in Settings -> Keys:
`DISCORD_BOT_TOKEN` and `HARNESS_PASSWORD` (never print them, never write them in a file or a message).
Settings (not secret): `<state>/extensions/discord/config.json` (`owner_id`, `guild_id`, `reply_mode`, `relay_feed`,
`relay_agents`, `relay_bell`); status: `status.json` next to it. The Discord page edits them; a change applies within 10 s.

## Set up (the user does the Discord side)
1. Discord developer portal: new application -> Bot -> enable "Message Content Intent" -> copy the token.
2. Invite URL with scopes `bot applications.commands` and permissions integer `326686035024` (never Administrator):
   View Channels, Manage Channels, Manage Roles (private overwrites), Send Messages, Send Messages in Threads, Create
   Public Threads, Manage Threads, Read Message History, Add Reactions, Embed Links. The server should be the user's own.
3. Keys: add `DISCORD_BOT_TOKEN` and `HARNESS_PASSWORD` (the Harness password). Two-step sign-in must be off
   (`maxxing 2fa status`): the bridge cannot answer it.
4. `sh <workspace>/extensions/discord/install.sh` (root): virtualenv + systemd service.
5. The owner's Discord id in the Discord page. The bot then creates private categories: *Harness* (alerts,
   notifications, crons, agents) and *Projects* (one channel per Harness project, one thread per session).

## Check / fix
- `systemctl status harness-discord`, `journalctl -u harness-discord -n 50 --no-pager` (never paste a token).
- `missing DISCORD_BOT_TOKEN...` -> a key or the owner id is missing. `cannot sign in` -> wrong password or two-step on.
- No answer in a channel: only the owner id is heard; the channel must be a project channel or a session thread.
- A channel was deleted by hand: the Discord page -> Resync channels (or wait 10 minutes).
- After changing a key: `systemctl restart harness-discord` (say so first).

## Rules
Ask before deleting channels or messages on the user's server, or restarting the service. Never publish the
project (GitHub, community library) without the user's explicit agreement.
