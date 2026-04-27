#!/usr/bin/env bash
#
# Bulk-create tasks in a project from a JSON file. Idempotent: re-running
# skips tasks whose subject already exists in the target board.
#
# Usage:
#   ./04_batch_ingest.sh <project-name> <board-name> <tasks.json>
#
# Example:
#   ./04_batch_ingest.sh "Sigma WorkMaster" "Backlog" sprint_tasks.json
#
# tasks.json format (minimum):
#   [
#     {"subject": "Wire up login endpoint", "priority": "HIGH"},
#     {"subject": "Add pagination to /api/v1/orders/", "priority": "NORMAL"},
#     {"subject": "Migrate to Tailwind v4", "priority": "LOW"}
#   ]
#
# Supported per-task fields: subject, priority, description, due_date, assignee_email.

set -euo pipefail

if [[ $# -lt 3 ]]; then
    echo "Usage: $0 <project-name> <board-name> <tasks.json>" >&2
    exit 1
fi

if command -v tark_cli >/dev/null 2>&1; then
    TARK="tark_cli"
else
    TARK="$(dirname "$0")/../tark_cli.py"
fi

PROJECT="$1"
BOARD="$2"
FILE="$3"

if [[ ! -f "$FILE" ]]; then
    echo "Error: file not found: $FILE" >&2
    exit 1
fi

# Validate JSON before sending. Cheap pre-flight check; saves a round trip on bad input.
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$FILE" 2>/dev/null; then
    echo "Error: $FILE is not valid JSON" >&2
    exit 1
fi

echo "Project: $PROJECT"
echo "Board:   $BOARD"
echo "File:    $FILE ($(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$FILE") tasks)"
echo

read -r -p "Ingest? [y/N] " confirm
case "$confirm" in
    [yY]|[yY][eE][sS])
        $TARK ingest "$PROJECT" "$BOARD" --tasks-file "$FILE"
        ;;
    *)
        echo "Cancelled."
        exit 1
        ;;
esac
