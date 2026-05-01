# Tark Automation

Command-line client and example scripts for the **Tark Platform** API.

Authenticates against any Tark deployment using a Personal Access Token (PAT). Single-file Python script, stdlib only — no `pip install` required.

## Quickstart

```bash
# 1. Clone
git clone https://github.com/TarkToostus/automation.git
cd automation

# 2. Configure (replace with your deployment + PAT)
./tark_cli.py config set url https://your-deployment.tarkaeg.ee
./tark_cli.py config set pat tark_pat_xxxxxxxxxxxxx

# 3. First call
./tark_cli.py tasks
```

> Get a PAT from your deployment: **Profile → Security → API keys → Add token**.
> Treat it like a password. It inherits your user's permissions.

### Install as `tark_cli`

Optional. Symlink it onto your `PATH`:

```bash
ln -s "$PWD/tark_cli.py" ~/bin/tark_cli
tark_cli tasks
```

## Common Commands

| Command | What it does |
|---------|-------------|
| `tark_cli tasks` | My open tasks across all projects |
| `tark_cli tasks --project Sigma` | Tasks in a specific project |
| `tark_cli task 123` | Task detail |
| `tark_cli create <project> "Subject text"` | Create a task |
| `tark_cli timer` | Active timer state |
| `tark_cli start 123` / `stop` / `discard` | Timer control |
| `tark_cli log 1.5 123 "fixed bug"` | Log 1.5h to task #123 |
| `tark_cli time week` | Weekly time report grouped by project |
| `tark_cli projects` / `boards` / `columns` | Browse PM structure |
| `tark_cli leads` / `offers` / `contracts` | Browse CRM |
| `tark_cli wiki 123` | Fetch task wiki markdown |
| `tark_cli wiki 123 set --section Brief --body "..."` | Upsert a wiki section (preferred — dup-safe) |
| `tark_cli wiki 123 append --section Brief --body "..."` | Append; refuses if section already exists |
| `tark_cli wiki 123 replace --section Brief --body "..."` | Replace existing section's body |
| `tark_cli stage 123 work` | Advance task stage (server gates on wiki sections) |
| `tark_cli update 123 --priority high --assignee 38` | PATCH common task fields |
| `tark_cli ingest <project> <board> --tasks-file tasks.json` | Bulk-create tasks (dedupes by subject) |
| `tark_cli api <path>` | Generic GET against any `/api/v1/pat/<path>/` endpoint |
| `tark_cli api <path> --post '{...}'` / `--patch '{...}'` | Generic POST / PATCH escape hatch |

Run `tark_cli --help` for the full list, or `tark_cli <command> --help` for flags.

## Auth

Three ways to provide credentials, checked in order:

1. **Environment variable** — `C2_PAT=tark_pat_... C2_URL=https://...`
2. **Config file** — `~/.config/tark/config.json` (chmod 600), set via `tark_cli config set`
3. **Defaults** — falls back to `https://c2.tarktoostus.ee` if no URL configured

Run `tark_cli config` to see what's currently in effect.

## Output Formats

Pass `--json` *before* the subcommand to get raw JSON for piping into `jq`:

```bash
tark_cli --json tasks --project Sigma | jq '.[] | {id, name, hours: .total_hours}'
```

Without `--json`, output is human-readable tables.

## Examples

See [`examples/`](examples/) for runnable scripts:

- `01_dashboard.sh` — timer + open tasks + today's time, in one view
- `02_log_time.sh` — log time entries, with task picking
- `03_timer.sh` — start/stop a timer around a unit of work
- `04_batch_ingest.sh` — bulk-create tasks from a JSON file (idempotent)
- `05_weekly_report.sh` — time totals grouped by project, for invoicing

## Security

This CLI is open source. The security boundary lives at the API:

- **PATs are scoped to your user.** They cannot escalate privileges. Revoke them anytime in the UI.
- **Tenant isolation is enforced server-side.** A PAT for tenant A cannot read tenant B data.
- **Treat your PAT like a password.** Don't commit it. Don't paste it in screenshots. Rotate if exposed.
- **Audit log** — every PAT call is logged with timestamp + IP + endpoint.

If you find a security issue, email `security@tarktoostus.ee` rather than opening a public issue.

## Endpoints

PAT auth covers the workflow API surface:

- `pm/` — projects, boards, columns, tasks, comments, time entries, timer
- `sales/` — leads, offers, offer-lines, contracts, pipelines
- `system/` — clients, contract-types, contract-templates
- `c2/` — deployment status (read-only)

Some endpoints (analytics, token management, ingest webhooks) require JWT auth via the web UI rather than PAT. The CLI surfaces a hint when you hit one.

## License

MIT. See [LICENSE](LICENSE).
