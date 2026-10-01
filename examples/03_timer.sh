#!/usr/bin/env bash
#
# Wrap a unit of work with a timer. Starts the timer, runs your command,
# stops the timer (saves a time entry automatically), or discards on Ctrl-C.
#
# Usage:
#   ./03_timer.sh <task-id> -- <command> [args...]
#
# Example — time a test run against task #123:
#   ./03_timer.sh 123 -- pnpm test
#
# Example — time an editor session:
#   ./03_timer.sh 456 -- $EDITOR notes.md

set -euo pipefail

if [[ $# -lt 3 || "$2" != "--" ]]; then
    echo "Usage: $0 <task-id> -- <command> [args...]" >&2
    exit 1
fi

if command -v tark_cli >/dev/null 2>&1; then
    TARK="tark_cli"
else
    TARK="$(dirname "$0")/../tark_cli.py"
fi

TASK_ID="$1"
shift 2  # drop task-id and "--"

# Discard timer if the user aborts mid-work (Ctrl-C, command crashes).
on_abort() {
    echo
    echo "Aborted — discarding timer."
    $TARK discard || true
    exit 130
}
trap on_abort INT TERM

$TARK start "$TASK_ID"
echo
echo "Running: $*"
echo

# Run the command. If it fails, still stop the timer (work happened).
if "$@"; then
    echo
    echo "Command finished successfully."
else
    rc=$?
    echo
    echo "Command exited with status $rc."
fi

trap - INT TERM
$TARK stop
