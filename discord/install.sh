#!/bin/sh
# Install the Harness Discord bridge on a Harness Maxxing server (run as root): virtualenv + systemd service.
# Before: Settings -> Keys: DISCORD_BOT_TOKEN and HARNESS_PASSWORD; your Discord user id goes in the Discord page.
# Remove with uninstall.sh (same folder).
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HERE/bridge"
VENV="${HD_VENV:-/var/lib/claude-maxxing/harness-discord-venv}"      # outside the extension folder: not part of the backup
STATE="${HD_STATE_DIR:-/var/lib/claude-maxxing/extensions/discord}"  # the only folder the service may write
UNIT="${HD_UNIT:-/etc/systemd/system/harness-discord.service}"
command -v uv >/dev/null || { echo "uv is needed (it comes with Harness Maxxing)"; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl is needed (systemd)"; exit 1; }
command -v maxxing >/dev/null || { echo "maxxing is not on PATH: the service reads its keys with 'maxxing keys get'"; exit 1; }
case "$(dirname "$(command -v maxxing)")" in
  /usr/local/bin|/usr/bin|/bin) ;;
  *) echo "warning: maxxing is in $(dirname "$(command -v maxxing)"), outside the service PATH (/usr/local/bin:/usr/bin:/bin): add it to Environment=PATH in $UNIT" ;;
esac
WAS_ACTIVE=no
systemctl is-active --quiet harness-discord 2>/dev/null && WAS_ACTIVE=yes
mkdir -p "$STATE"
[ -d "$VENV" ] || uv venv -q "$VENV"
uv pip install -q --python "$VENV/bin/python" "$DIR"
rm -rf "$DIR/build" "$DIR"/*.egg-info
python3 -c 'import sys; t = open(sys.argv[1]).read(); print(t.replace("@DIR@", sys.argv[2]).replace("@VENV@", sys.argv[3]).replace("@STATE@", sys.argv[4]), end="")' \
  "$DIR/harness-discord.service.in" "$DIR" "$VENV" "$STATE" > "$UNIT"
systemctl daemon-reload
systemctl enable harness-discord
if [ "$WAS_ACTIVE" = yes ]; then
  systemctl restart harness-discord
  echo "harness-discord was running: restarted with the new code."
else
  systemctl start harness-discord
  echo "harness-discord started."
fi
echo "Done. Check: systemctl status harness-discord  (logs: journalctl -u harness-discord -n 50 --no-pager)"
echo "Next: open Discord in the Harness sidebar and enter your Discord user id."
echo "Remove later: sh $HERE/uninstall.sh"
