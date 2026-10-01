#!/usr/bin/env bash
#
# Personal dashboard — what /c2 shows in one screen:
#   - Active timer (if any)
#   - My open tasks
#   - Time logged today + this week
#
# Mirrors the workflow at https://github.com/TarkToostus/automation
#
# Usage:
#   ./01_dashboard.sh

set -euo pipefail

# Locate tark_cli — prefer PATH, fall back to the script in the parent directory.
if command -v tark_cli >/dev/null 2>&1; then
    TARK="tark_cli"
else
    TARK="$(dirname "$0")/../tark_cli.py"
fi

echo
echo "═══════════════════════════════════════════════════════"
echo "  TARK DASHBOARD — $(date '+%a %Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════════════════════"

echo
echo "▸ Active Timer"
echo "───────────────────────────────────────────────────────"
$TARK timer

echo
echo "▸ My Open Tasks"
echo "───────────────────────────────────────────────────────"
$TARK tasks --status "In Progress"

echo
echo "▸ Time Today"
echo "───────────────────────────────────────────────────────"
$TARK time today

echo
echo "▸ Time This Week"
echo "───────────────────────────────────────────────────────"
$TARK time week
