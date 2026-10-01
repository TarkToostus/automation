#!/usr/bin/env bash
#
# Weekly time report — totals grouped by project, suitable for invoicing
# or weekly check-ins.
#
# Usage:
#   ./05_weekly_report.sh                    # this week (Mon → today)
#   ./05_weekly_report.sh today              # just today
#   ./05_weekly_report.sh month              # this month
#
# Outputs both the human-readable breakdown and a JSON copy you can pipe
# into another tool (e.g. an invoicing script).

set -euo pipefail

if command -v tark_cli >/dev/null 2>&1; then
    TARK="tark_cli"
else
    TARK="$(dirname "$0")/../tark_cli.py"
fi

PERIOD="${1:-week}"

echo "▸ Time report — $PERIOD"
echo "───────────────────────────────────────────────────────"
$TARK time "$PERIOD"

echo
echo "▸ JSON for downstream tooling"
echo "───────────────────────────────────────────────────────"
$TARK --json time "$PERIOD" \
    | python3 -c "
import json, sys
from collections import defaultdict

entries = json.load(sys.stdin)
by_project = defaultdict(float)
by_day = defaultdict(float)

for e in entries:
    by_project[e.get('project_name', '?')] += float(e.get('hours', 0))
    by_day[e.get('date', '?')] += float(e.get('hours', 0))

print(json.dumps({
    'period': '$PERIOD',
    'total_hours': sum(by_project.values()),
    'by_project': dict(by_project),
    'by_day': dict(sorted(by_day.items())),
}, indent=2))
"
