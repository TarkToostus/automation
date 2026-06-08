#!/usr/bin/env bash
#
# Sales follow-up assistant loop: enqueue due follow-ups, then draft each DRAFT
# EmailTask and move it to REVIEW for a human to confirm. Nothing is emailed by
# this script — a human confirms (sets the send time) in the UI, and only then,
# once send_at passes, does the platform sender send it verbatim.
#
# Usage:
#   ./06_followup_draft.sh                 # list DRAFT emails awaiting a body
#   ./06_followup_draft.sh <id> "Subject line" "Body text"   # draft one email
#
# Needs a PAT with sales:write scope held by a user with sales.change_salesconfig.

set -euo pipefail

if command -v tark_cli >/dev/null 2>&1; then
    TARK="tark_cli"
else
    TARK="$(dirname "$0")/../tark_cli.py"
fi
FOLLOWUP="$(dirname "$0")/../sales_followup.py"

# sales_followup.py shells out to tark_cli (defaulting to ~/bin/tark_cli) — point it
# at the same CLI this script resolved, so the checkout-local fallback works too.
export TARK_CLI="$TARK"

# 1. Pull in any due follow-ups (creates DRAFT EmailTasks).
$TARK followups-check

# 2. No args -> just show what is waiting for a body.
if [[ $# -eq 0 ]]; then
    "$FOLLOWUP" list
    echo "To draft one:  $0 <id> \"Subject\" \"Body\""
    exit 0
fi

if [[ $# -lt 3 ]]; then
    echo "Usage: $0 <id> \"Subject line\" \"Body text\"" >&2
    exit 1
fi

ID="$1"
SUBJECT="$2"
BODY="$3"

# 3. Write the body and move the email DRAFT -> REVIEW (gate-safe — the helper
#    refuses to touch CONFIRMED/SENT/FAILED). A human confirms it in the UI.
"$FOLLOWUP" draft "$ID" --subject "$SUBJECT" --body "$BODY"
