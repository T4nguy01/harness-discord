#!/bin/sh
# Install the Harness Discord bridge on a Harness Maxxing server (run as root): virtualenv + systemd service.
# Before: Settings -> Keys: DISCORD_BOT_TOKEN and HARNESS_PASSWORD; your Discord user id goes in the Discord page.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HERE/bridge"
VENV="${HD_VENV:-/var/lib/claude-maxxing/harness-discord-venv}"      # outside the extension folder: not part of the backup
command -v uv >/dev/null || { echo "uv is needed (it comes with Harness Maxxing)"; exit 1; }
[ -d "$VENV" ] || uv venv -q "$VENV"
uv pip install -q --python "$VENV/bin/python" "$DIR"
sed -e "s#@DIR@#$DIR#g" -e "s#@VENV@#$VENV#g" "$DIR/harness-discord.service.in" > /etc/systemd/system/harness-discord.service
systemctl daemon-reload
systemctl enable --now harness-discord
echo "harness-discord started: systemctl status harness-discord"
