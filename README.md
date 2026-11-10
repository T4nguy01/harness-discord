# Harness Discord

![Harness Discord](assets/banner.png)

Talk to your [Harness Maxxing](https://harness-maxxing.com) sessions from your own Discord server: the same thing the
built-in Telegram gateway does, for Discord. One private category per purpose, one channel per Harness project, one
thread per session.

## What it does

- **Sessions**: write in a project channel, pick the topic (skipped when the project has only one), a thread is created
  and Claude answers in it. Write in the thread to go on. Messages sent while Claude works wait their turn.
- **Live status**: a message updated while Claude works (current step), folded into a summary (steps, time, cost)
  when the turn ends; the answer arrives as a new message. Long answers are split in several messages, tables become lists.
- **Files and voice**: attachments are saved in the topic (`attachments/`) and passed to Claude; Discord voice messages
  are transcribed by Harness.
- **Commands**: `/new [title]`, `/sessions`, `/stop`, `/close`, `/model [id]`, `/status`, `/help` (French or English,
  following the Harness language).
- **Alerts and notifications**: the Harness feed is posted in `alerts` (server problems, updates, sign-ins),
  `crons` (job reports), `agents` (requests with **Allow / Deny** buttons, failures) and `notifications` (everything else).
  An alert handled in Harness is updated in Discord too. They are posted as **embeds**: alerts and notifications are
  coloured by level (info, warning, error...) with a footer and a timestamp; agent results are green (success) or red
  (failure) and show the cost of the run.
- **Agents**: each agent gets a thread in `agents` with its results; reply in the thread to start it again.
- **Bell**: when a turn started elsewhere (web UI) ends in a session with its bell on, a short "✅ title - preview" rings in its thread.
- **Settings page** in Harness (sidebar): status, owner, reply mode, what to relay, resync, test message, restart.

Not possible from outside the server: the two-step sign-in approval (it needs the product's internals).

**Opt-in.** The relays are settings on the Discord page: `relay_feed` (alerts, approvals, job reports), `relay_agents`
(agent results) and `relay_bell` (bell in session threads) can each be switched off there. The **Send a test message** button
and the relays are the only things that post on their own: with them off, and as long as you write nothing, nothing is sent
to Discord. The bridge only ever answers the owner id you set.

## What the `discord/` folder contains

`discord/` is a page extension (`manifest.json`, `api.py`, `page.js`) **and also** the bridge itself (`bridge/`, a Python
service) with `install.sh` / `uninstall.sh`. Installing it therefore **runs code on your server**, as a systemd service that
signs in to Harness with your password. Read it first; the community library will label it "Runs code on the server".

## Requirements

- A Harness Maxxing server (the bridge talks to its HTTP API), `uv` (shipped with Harness), root access for the service.
- A Discord application with a bot, **Message Content Intent** on, invited with the scopes `bot` and
  `applications.commands`. Do **not** give Administrator. The bot needs exactly: View Channels, Manage Channels,
  Manage Roles (only to set the private permission overwrites on the channels it creates), Send Messages, Send Messages in
  Threads, Create Public Threads, Manage Threads, Read Message History, Add Reactions, Embed Links
  (permissions integer `326686035024`). Without Manage Roles, create the *Harness* and *Projects* categories yourself,
  private to you and the bot: the bridge reuses them.
- In Harness **Settings -> Keys**: `DISCORD_BOT_TOKEN` and `HARNESS_PASSWORD`. The bridge signs in to Harness like you do,
  so keep two-step sign-in off. Neither key is ever stored in this repository or in the extension's files.

## Install

1. Copy the `discord/` folder to `<workspace>/extensions/discord/` (or let Harness install it from the community library).
2. As root: `sh <workspace>/extensions/discord/install.sh` (creates a virtualenv outside the folder and the `harness-discord`
   systemd service; if the service already runs it is restarted). The service reads the keys with `maxxing keys get`, so
   `maxxing` must be on its PATH (`command -v maxxing`; the unit uses `/usr/local/bin:/usr/bin:/bin`, the installer checks it).
   The unit can write only the state folder `/var/lib/claude-maxxing/extensions/discord/`.
3. Open **Discord** in the Harness sidebar, enter your Discord user id (Discord: Settings -> Advanced -> Developer mode,
   right-click your name -> Copy user ID). The bot builds the categories and channels by itself.
4. Optional, the skill: `ln -s <this repo>/skill/discord-bridge <workspace>/.claude/skills/discord-bridge`.

Test: write in a project channel, or press **Send a test message** on the Discord page.

## What it sends, and where

- To **Discord**: your messages and files from the owner's channels, and Claude's answers, alerts and agent results.
- To **Harness**: your messages and files (its HTTP API, on the server itself).
- Nothing else leaves the server. Only the Discord user id you set can talk to the bot; mentions are never parsed.

## Remove

As root: `sh <workspace>/extensions/discord/uninstall.sh` (asks for confirmation; `--yes` skips it). It stops and disables
the service and deletes the unit and the virtualenv. It keeps the state folder `/var/lib/claude-maxxing/extensions/discord/`
and your keys, unless you pass `--purge` (deletes the state folder). It never touches your Discord server: delete the
categories (Harness, Projects, Archives) and the bot yourself, then the folder `<workspace>/extensions/discord/`, the skill
link `<workspace>/.claude/skills/discord-bridge` and the two keys in Settings -> Keys.

## Limits and security

- The bridge creates, moves and deletes **its own** categories and channels (Harness, Projects) on the server you give it, and can archive session threads. Use a **personal server** you own; only the owner id set in the page can talk to it.
- It signs in to the Harness API as you with `HARNESS_PASSWORD`, so it has owner rights: **2FA must stay off**, and anyone who controls the Discord owner account or the bot token controls your sessions.
- `install.sh` must run as root: it writes `/etc/systemd/system/harness-discord.service` (hardened unit) and runs `systemctl`. The page's "Restart the bridge" button asks for confirmation.
- `reply_mode`: `live` (the answer is edited as it streams) or `final` (one message at the end).
- Attachments are fetched only from Discord's CDN, capped at 25 MB.
- The skill link (`ln -s`) points to this clone: keep the clone where it is.

## Develop

`discord/bridge/` is a small Python package (`httpx`, `websockets`); tests: from the repo root, `pip install -e "discord/bridge[dev]" && pytest` (the async tests need no pytest plugin).

MIT licence.
