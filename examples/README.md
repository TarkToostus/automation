# Examples

Runnable scripts that demonstrate common Tark API workflows. Each script assumes:

- `tark_cli.py` is on your `PATH` as `tark_cli` (or in the parent directory)
- `C2_PAT` and `C2_URL` are configured (see [main README](../README.md#auth))

Examples are deliberately short and **read like recipes** — copy, adapt, run.

| Script | Demonstrates |
|--------|-------------|
| [`01_dashboard.sh`](01_dashboard.sh) | Timer + my tasks + today's time, in a single dashboard view |
| [`02_log_time.sh`](02_log_time.sh) | Log a time entry to a task with a description |
| [`03_timer.sh`](03_timer.sh) | Start a timer, do work, stop it (saves a time entry automatically) |
| [`04_batch_ingest.sh`](04_batch_ingest.sh) | Bulk-create tasks from a JSON file (idempotent — safe to re-run) |
| [`05_weekly_report.sh`](05_weekly_report.sh) | Weekly time totals grouped by project (for invoicing) |

## Running

```bash
chmod +x examples/*.sh
./examples/01_dashboard.sh
```

Or invoke directly:

```bash
bash examples/01_dashboard.sh
```

## Adapting

These scripts are intentionally minimal. Real workflows usually combine commands and pipe through `jq`:

```bash
# All my "In Progress" tasks for Sigma, as JSON, just IDs and hours
tark_cli --json tasks --project Sigma --status "In Progress" \
  | jq '.[] | {id, name, total_hours}'
```

The `--json` flag goes **before** the subcommand. Every command supports it.

## Generic Escape Hatch

For any endpoint the CLI doesn't have a named command for:

```bash
tark_cli api pm/tasks --filter assignee=42 --filter ordering=-updated_at
tark_cli api pm/tasks --post '{"project": 18, "subject": "New task", "priority": "NORMAL"}'
tark_cli api pm/tasks/123 --patch '{"priority": "HIGH"}'
```

If you find yourself reaching for `api <path>` repeatedly, open an issue or PR
to add a named command for it.
