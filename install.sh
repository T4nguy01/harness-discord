#!/bin/sh
# Install the Harness Discord bridge on a Harness Maxxing server: virtualenv + systemd unit. Run as root.
# Before: Settings -> Keys: DISCORD_BOT_TOKEN and HARNESS_PASSWORD; then put your Discord user id in
# <state>/config.json (see README).
set -e
DIR="$(cd "$(dirname "$0")/bridge" && pwd)"
command -v uv >/dev/null || { echo "uv is needed (it comes with Harness Maxxing)"; exit 1; }
cd "$DIR"
[ -d .venv ] || uv venv -q .venv
uv pip install -q -e . --python .venv/bin/python
sed "s#@DIR@#$DIR#g" harness-discord.service.in > /etc/systemd/system/harness-discord.service
systemctl daemon-reload
systemctl enable --now harness-discord
echo "harness-discord started: systemctl status harness-discord"
