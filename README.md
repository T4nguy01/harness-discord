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
- **Commands**: `/new [title] [topic] [form]`, `/sessions`, `/stop`, `/close`, `/model [name]`, `/status`, `/help` (French or English,
  following the Harness language). `/model` and the `topic` of `/new` have **autocomplete** (up to 25 choices, filtered as you
  type); `/model` also takes a free name like `opus` or `Sonnet 5.5` (same resolution as Telegram), otherwise it lists the choices.
- **Forms**: `/new form:true` opens a form (title, topic typed or left empty for the menu, first message); `/harness task add`
  opens one too (title, notes, due date, priority). Only the owner's submissions count.
- **Tools, `/harness ...`** (owner only, the answers are private and nothing is posted in the channel):
  `tasks [status]` (with **Done / In progress** buttons under each task), `task add`, `cron list|run|toggle`, `agent run`,
  `usage` (5 h and 7 day windows as text bars, spending), `health` (issues with a **Mark seen** button, services, version),
  `search <words>` (a menu that opens or creates the session's thread), `backup status|run`, `infra` (read only, **Refresh** button),
  `queue` (messages waiting in this session, **Clear**), `files ls|get` (see below), `mail unread|outbox`, `audit`.
  French Discords see them translated (`/harness tâches`, `/nouvelle`...): the command names and descriptions are localized.
  Everything that **runs or changes** something (`cron run|toggle`, `agent run`, `backup run`, clearing a queue, marking mail read
  or archiving it, sending or refusing a mail an agent wrote) first shows what it will do with **Confirm / Cancel** buttons
  (short random ids, valid for 2 minutes, one use). One-click buttons that are the confirmation themselves: task status,
  mark a health issue seen, refresh infra. Mail: unread counts and subjects per mailbox, reading does not mark a message read;
  an agent's waiting mail is sent only after you confirm, with its recipient and subject in the question. There is no way to send a
  mail directly.
- **Files** (`/harness files ls|get`): the workspace folder only (default `/root/maxxing`, `workspace_dir` in `config.json` or
  `HD_WORKSPACE`), 25 MB at most, sent as a private attachment. Never listed or sent: `.env*`, keys and certificates (`*.pem`,
  `*.key`...), `id_rsa`-like files, anything with `secret`, `token`, `password` or `credential` in its name, and the folders `.git`,
  `.ssh`, `.gnupg`, `.aws`, `.config`, `.claude`... A symbolic link leading outside the workspace is refused.
- **Never offered from Discord**: keys and secrets, restoring a backup, deleting anything, updating Harness, restarting a service.
- **Audit** (`/harness audit`, private, last 10): every action run from Discord (new session, stop, close, model, task, job, agent,
  backup, queue, mail, health, infra, file sent) is written to `audit.jsonl` in the state folder (mode 600, rotated at about 1 MB):
  time, command, target (an id or a path), result. Never the text of a message, never a secret.
- **Proactive messages** (`proactive`, on by default): at 08:00 server time (until noon) a silent summary of late and due tasks in
  `notifications`, once a day; a warning in `alerts` when the plan passes 80 % of its 5 hour window (once per window); an alert in
  `alerts` when the memory backup fails (once per failure; a failure already there at start-up is not announced).
- **Tool-call approvals** (`tool_approvals`, on by default): when Claude or an agent waits for your OK on a tool call, the bridge posts an
  orange card in the session thread (the `alerts` channel when the session has no thread): tool, an excerpt of the request in a code
  block (obvious secrets such as `token=...` or `Bearer ...` are masked), and the buttons **Allow**, **Deny**, **Always allow** (two
  steps: a private confirmation shows the permanent rule it creates) and **Deny with a reason** (a form; Claude reads the reason).
  It is the same queue as the web UI and Telegram: the first answer wins. The card turns green or red (who decided) or grey (decided
  elsewhere, timed out, session stopped) and loses its buttons; a late click gets a private "already answered or expired". A request
  too long to be shown whole can only be denied. While one waits, the live status line says "waiting for your approval". The audit
  trail records tool, id and decision, never the request. The feed no longer posts its own copy of the same request.
- **Open in Harness**: a link button (the plain address of Harness, no token) under the dashboard and under agent results.
- **Threads**: `/close` archives **and locks** the thread (it is no longer deleted); session threads auto-archive after 7 days;
  `alerts` gets a 2 second slow mode (once, when the bridge can set it).
- **Message menus** (right-click a message -> Apps): **Send to Claude** (the text and files of that message go to the
  current session, or you pick the topic in a project channel) and **New session from message**. Owner only, answers are private.
- **Forwarded and quoted messages**: a reply passes the quoted text; a message forwarded with Discord's Forward is sent
  with a `[Forwarded]` mark (`[Transféré]` in French) and its attachments.
- **Status reactions** (`reactions`): ⏳ on your message when Claude starts, replaced by ✅ at the end (❌ on an error).
  If you edit a message already sent to Claude, the edit is **not** sent again: the bot adds one ✏️ reaction (a message edit has no
  interaction to answer with) and logs it.
- **Answer buttons** (`answer_buttons`): under the final answer, 🔁 **Regenerate** (sends your last message again) and, when
  another turn waits behind it, ⏹ **Stop**. They disappear when the next turn starts. Owner only.
- **Web mirror** (`mirror_web_sessions`): every 10 minutes (and on **Resync channels**) a thread is created in the project channel
  for each session opened in the web UI in the last 30 days (50 at most, like Telegram), named `<project emoji> topic · title`.
  Only a welcome message (title and link) is posted, not the history; writing in the thread continues the session, and new
  turns started on the web ring the bell. The thread is renamed when the title changes, archived when the session is
  archived in Harness, and brought back (or recreated if you deleted it) when it is un-archived.
- **Presence**: the bot's status reads "N sessions running" or "idle", refreshed at most every 30 s and only when it changes.
- **Dashboard** (`dashboard`): a message pinned in `alerts` (sessions running, threads, jobs, agents, last relay), edited at most
  every 10 minutes and only when something changed. Pinning needs **Manage Messages**; without it the message is still posted, just not pinned.
- **Alerts and notifications**: the Harness feed is posted in `alerts` (server problems, updates, sign-ins),
  `crons` (job reports), `agents` (requests with **Allow / Deny** buttons, failures) and `notifications` (everything else).
  An alert handled in Harness is updated in Discord too. They are posted as **embeds**: alerts and notifications are
  coloured by level (info, warning, error...) with a footer and a timestamp; agent results are green (success) or red
  (failure) and show the cost of the run.
- **Agents**: each agent gets a thread in `agents` with its results; reply in the thread to start it again.
- **Bell**: when a turn started elsewhere (web UI) ends in a session with its bell on, a short "✅ title - preview" rings in its thread.
- **Settings page** in Harness (sidebar): status, owner, reply mode, what to relay, resync, test message, restart.

Not possible from outside the server: the two-step sign-in approval (it needs the product's internals).

**Opt-in.** The relays and the extras are settings on the Discord page (`mirror_web_sessions`, `reactions`, `answer_buttons`, `dashboard`, `proactive` and `tool_approvals` are on by default): `relay_feed` (alerts, approvals, job reports), `relay_agents`
(agent results) and `relay_bell` (bell in session threads) can each be switched off there, as can the web mirror, the reactions, the answer buttons and the dashboard. The **Send a test message** button
and the relays (and the proactive messages, switch `proactive` off to stop them) are the only things that post on their own: with them off, and as long as you write nothing, nothing is sent
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
  Threads, Create Public Threads, Manage Threads, Read Message History, Add Reactions, Embed Links, Manage Messages
  (only to pin the dashboard; the bridge works without it) (permissions integer `326686043216`). Without Manage Roles, create the *Harness* and *Projects* categories yourself,
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

- The bridge creates and moves **its own** categories and channels (Harness, Projects) on the server you give it, and can archive and lock session threads. Use a **personal server** you own; only the owner id set in the page can talk to it.
- It signs in to the Harness API as you with `HARNESS_PASSWORD`, so it has owner rights: **2FA must stay off**, and anyone who controls the Discord owner account or the bot token controls your sessions.
- `install.sh` must run as root: it writes `/etc/systemd/system/harness-discord.service` (hardened unit) and runs `systemctl`. The page's "Restart the bridge" button asks for confirmation.
- `reply_mode`: `live` (the answer is edited as it streams) or `final` (one message at the end).
- Attachments are fetched only from Discord's CDN, capped at 25 MB.
- The skill link (`ln -s`) points to this clone: keep the clone where it is.

## Develop

`discord/bridge/` is a small Python package (`httpx`, `websockets`); tests: from the repo root, `pip install -e "discord/bridge[dev]" && pytest` (the async tests need no pytest plugin).

MIT licence.
