#!/bin/sh
# Remove the Harness Discord bridge: stops and disables the service, deletes its systemd unit and its virtualenv.
# Kept: the state folder (config, state, status) and the keys in Settings -> Keys, unless you pass --purge.
# Never touched: the channels and categories on your Discord server (delete them there yourself).
# Usage: sh uninstall.sh [--purge] [--yes]
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
PURGE=no; YES=no
for a in "$@"; do
  case "$a" in
    --purge) PURGE=yes ;;
    --yes|-y) YES=yes ;;
    *) echo "usage: sh uninstall.sh [--purge] [--yes]"; exit 2 ;;
  esac
done
VENV="${HD_VENV:-/var/lib/claude-maxxing/harness-discord-venv}"
STATE="${HD_STATE_DIR:-/var/lib/claude-maxxing/extensions/discord}"
UNIT="${HD_UNIT:-/etc/systemd/system/harness-discord.service}"
echo "This will stop and disable harness-discord, delete $UNIT and $VENV."
[ "$PURGE" = yes ] && echo "--purge: it will also delete the state folder $STATE."
echo "Discord channels and the keys in Settings -> Keys are not touched."
if [ "$YES" != yes ] && [ -t 0 ]; then
  printf "Continue? [y/N] "
  read -r ans
  case "$ans" in y|Y|yes|YES) ;; *) echo "cancelled"; exit 1 ;; esac
fi
if command -v systemctl >/dev/null; then
  systemctl disable --now harness-discord 2>/dev/null || true
fi
rm -f "$UNIT"
command -v systemctl >/dev/null && systemctl daemon-reload
rm -rf "$VENV"
[ "$PURGE" = yes ] && rm -rf "$STATE"
echo "Removed the service and the virtualenv."
[ "$PURGE" = yes ] || echo "Kept: $STATE (delete it by hand or rerun with --purge)."
echo "Still yours to do: delete <workspace>/extensions/discord/ and the skill link <workspace>/.claude/skills/discord-bridge,"
echo "the Discord categories (Harness, Projects, Archives), the bot in the developer portal, and the two keys in Settings -> Keys."
