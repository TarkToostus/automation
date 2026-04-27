#!/usr/bin/env bash
#
# Log a time entry to a specific task.
#
# Usage:
#   ./02_log_time.sh <hours> <task-id> [description...]
#
# Examples:
#   ./02_log_time.sh 1.5 123 fixed login bug
#   ./02_log_time.sh 0.25 456 "code review"
#
# Pass --date YYYY-MM-DD to backfill past days (defaults to today):
#   ./02_log_time.sh 2 123 "deployed staging" --date 2026-04-25

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <hours> <task-id> [description...]" >&2
    exit 1
fi

if command -v tark_cli >/dev/null 2>&1; then
    TARK="tark_cli"
else
    TARK="$(dirname "$0")/../tark_cli.py"
fi

HOURS="$1"
TASK_ID="$2"
shift 2
DESCRIPTION="${*:-}"

# Show the task first so you can confirm it's the right one.
echo "Target task:"
$TARK task "$TASK_ID"
echo

read -r -p "Log ${HOURS}h to task #${TASK_ID}? [y/N] " confirm
case "$confirm" in
    [yY]|[yY][eE][sS])
        $TARK log "$HOURS" "$TASK_ID" "$DESCRIPTION"
        ;;
    *)
        echo "Cancelled."
        exit 1
        ;;
esac
