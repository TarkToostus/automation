# Command reference

Everything `tark_cli` can do, for people who prefer the terminal. Most users never need this
page — just ask the assistant in plain words (see the [README](../README.md)).

`tark_cli.py` is a single Python 3 file (stdlib only, no `pip install`) that authenticates
against any Tark deployment with a Personal Access Token (PAT).

## Quickstart

You only need **Python 3** (stdlib only — no `pip install`). `git` is optional.

```bash
# 1a. Get the CLI WITHOUT git (one file, public repo):
curl -fsSL https://raw.githubusercontent.com/TarkToostus/automation/main/tark_cli.py -o tark_cli.py
chmod +x tark_cli.py
# 1b. ...or with git, if you have it:
#     git clone https://github.com/TarkToostus/automation.git && cd automation

# 2. Configure (replace with your deployment + PAT)
./tark_cli.py config set url https://your-deployment.example.com
./tark_cli.py config set pat tark_pat_xxxxxxxxxxxxx

# 3. First call
./tark_cli.py tasks
```

> One-shot installer (Claude Code + CLI + config, no git/Node/Xcode):
> `curl -fsSL https://raw.githubusercontent.com/TarkToostus/automation/main/setup.sh | bash`
> (Windows: `irm https://raw.githubusercontent.com/TarkToostus/automation/main/setup.ps1 | iex`)

> Get a PAT from your deployment: **Profile → Security → API keys → Add token**.
> Treat it like a password. It inherits your user's permissions.

### Install as `tark_cli`

Optional. Symlink it onto your `PATH`:

```bash
mkdir -p ~/bin
ln -sf "$PWD/tark_cli.py" ~/bin/tark_cli
# ~/bin must be on PATH — if `tark_cli` isn't found, add this to ~/.zshrc and reopen the shell:
#   export PATH="$HOME/bin:$PATH"
tark_cli tasks
```

## Common Commands

| Command | What it does |
|---------|-------------|
| `tark_cli tasks` | My open tasks across all projects |
| `tark_cli tasks --project Website` | Tasks in a specific project |
| `tark_cli tasks --board 100 --status WORK --all` | Board-scoped column listing (follows pagination; a project can hold several boards) |
| `tark_cli task 123` | Task detail |
| `tark_cli deps 200` | Task dependencies, both directions (blocked by / blocks) |
| `tark_cli deps 200 add --blocker 201` | Record a blocker (needs `pm:write`) |
| `tark_cli deps 200 remove --blocker 201` | Drop a blocker (needs `pm:delete`) |
| `tark_cli create <project> "Subject text"` | Create a task |
| `tark_cli timer` | Active timer state |
| `tark_cli start 123` / `stop` / `discard` | Timer control |
| `tark_cli log 1.5 123 "fixed bug"` | Log 1.5h to task #123 |
| `tark_cli time week` | Weekly time report grouped by project |
| `tark_cli projects` / `boards` / `columns` | Browse PM structure |
| `tark_cli boards-create <project_id> "Board name"` | Create a board (needs `pm:write`) |
| `tark_cli comment <task_id> "text"` | Add a task comment (needs `pm:write`) |
| `tark_cli task-comment <id>` / `time-entry <id>` | Retrieve one comment / time entry |
| `tark_cli time-update <id> --hours 2.5 --description "..."` | Patch a time entry (sparse, needs `pm:write`) |
| `tark_cli task-delete <id> [--yes]` | Delete a task (needs `pm:delete`, **destructive** - confirms unless `--yes`) |
| `tark_cli time-delete <id> [--yes]` | Delete a time entry (needs `pm:write`, **destructive**) |
| `tark_cli contract-blocks` | List contract blocks (system, `sales:read`) |
| `tark_cli <lead\|offer\|offer-line\|contract\|pipeline\|pipeline-stage\|email-task> <id>` | Retrieve one sales record by ID |
| `tark_cli <client\|user\|contract-type\|contract-block\|contract-template\|column> <id>` | Retrieve one system/PM record by ID |
| `tark_cli offer-line-delete <id> [--yes]` | Delete an offer line (needs `sales:write`, **destructive**) |

## Common Commands — Sales, Wiki & API

| Command | What it does |
|---------|-------------|
| `tark_cli leads` / `offers` / `contracts` | Browse CRM |
| `tark_cli leads create --title "Acme retrofit" --company "Acme OÜ" --pipeline Outbound --source COLD` | Create a lead (needs `sales:write` PAT) |
| `tark_cli leads-update <id> --status QUALIFIED --pipeline-stage 4` | Patch a lead (sparse, only sends given flags) |
| `tark_cli leads-ingest --pipeline Outbound --leads '[{"title":"..."}]'` | Batch-create leads (dedupes by title) |
| `tark_cli offers-create --title "..." --client 5 --amount 1500` / `offers-update <id> ...` | Create / patch an offer |
| `tark_cli offer-lines-create --offer 3 --description "..." --quantity 2 --unit-price 99` / `offer-lines-update <id> ...` | Create / patch an offer line |
| `tark_cli contracts-create --title "..." --client 2 --template 1` / `contracts-update <id> ...` | Create / patch a contract (content-JSON via `api`) |
| `tark_cli email-tasks-create --lead 12 --subject "..." --body "..." --status REVIEW` | Draft a sales email (never confirms/sends) |
| `tark_cli followups-check` | Run the due-follow-up check now — creates DRAFT EmailTasks for due leads |
| `tark_cli email-tasks -f DRAFT` | List scheduled sales emails (the follow-up engine), filter by status |
| `tark_cli email-task-set <id> --body "..." --status REVIEW` | Edit a draft email's body/subject/status |
| `tark_cli wiki 123` | Fetch task wiki markdown |
| `tark_cli wiki 123 set --section Brief --body "..."` | Upsert a wiki section (preferred — dup-safe) |
| `tark_cli wiki 123 append --section Brief --body "..."` | Append; if the section exists, MERGES onto the end of its own body — above any `## ` sub-block it holds (`--force` duplicates the block instead) |
| `tark_cli wiki 123 replace --section Brief --body "..."` | Replace existing section's body |
| `tark_cli wiki 123 delete --section Brief` | Dry run: show what a delete would remove (exits non-zero) |
| `tark_cli wiki 123 delete --section Brief --yes` | Remove the section — DESTRUCTIVE, needs a `pm:delete` PAT scope |
| `tark_cli stage 123 work` | Advance task stage (server gates on wiki sections) |
| `tark_cli update 123 --priority high --assignee 7` | PATCH common task fields |
| `tark_cli ingest <project> <board> --tasks-file tasks.json` | Bulk-create tasks (dedupes by subject) |
| `tark_cli api <path>` | Generic GET against any `/api/v1/pat/<path>/` endpoint |
| `tark_cli api <path> --post '{...}'` / `--patch '{...}'` | Generic POST / PATCH escape hatch |
| `tark_cli api "pm/tasks/?board=12&page=2"` | Inline query string; merges with `--filter` (flag wins on key clash) |

## Common Commands — Tark Aeg schedules (`aeg`)

Needs a seat that holds a schedule capability (manage all / location / team schedules) — the
CLI can do exactly what that person can in the schedule editor, for the employees in their scope.
Two ways in (`aeg --auth auto|pat|login`, default `auto`):

- **PAT** — a `workforce:read` / `workforce:write` token on `/api/v1/pat/workforce/`, used when
  the server has that API. **Not yet available on servers** (the PAT schedule API is parked):
  today every server answers 404 and `auto` goes to seat login — don't mint `workforce:*` tokens.
- **Seat login** — otherwise (or `--auth login`): logs in like the web app as `aeg --user <name>`
  (or profile `user` / `$TARK_AEG_USER`) and calls the web schedule-editor endpoints. The
  password is read from the environment, never stored, and only sent to the host it belongs to:
  `--password-env VAR` wins for any host; a `--url` host that is not the target's own host
  (the profile's url, else the default url) gets **no** env password — pass `--password-env` or
  type it at the prompt; under `--profile` the env var named by the profile's `password_env`;
  a profile without one uses `$TARK_AEG_PASSWORD` only when it is on the default target's host,
  a profile on any other host must set its own `password_env`; on the default target
  `$TARK_AEG_PASSWORD`, then top-level `password_env`, then `$TARK_PASSWORD` (never sent to a
  profile's or a `--url` host); else a prompt. The JWT pair is cached in
  `~/.config/tark/aeg-sessions.json` (0600; login is rate-limited); `aeg --relogin` drops it.

`--url` overrides the host for one run; only an explicit `--pat`/`--pat-env` is sent there (a
stored or `$TARK_PAT` token never is) — without one, `aeg` goes straight to seat login (password:
`--password-env` or the prompt).

Writes go through the Plan/Actual save (no "schedule published" event). A seat without a
Plan/Actual capability gets a 403 there and the write **aborts** (exit 1, nothing more written).
`--legacy-save` (on `set` / `apply` / `delete`) opts into the Graafik save for that case instead —
it writes something other than the location-scoped diff: one location-less shift per day, located
rows are NOT deleted, and one "schedule published" notification per month goes to the employees.
`--legacy-save` is shown in the `--dry-run` output and the confirm prompt, and always asks for
confirmation (or `--yes`). `--json` on a write prints one JSON document (ops, issues, written
counts, verified cells) on stdout; the human tables go to stderr.
`--resolve-ip IP` (or profile key `resolve_ip`) pins the profile's host to an IP while DNS
propagates; the profile key is ignored when `--url` points at another host.

| Command | What it does |
|---------|-------------|
| `tark_cli aeg employees --department Vastuvõtt` | Employees in scope (filters: `--department/--location/--role/--search`) |
| `tark_cli aeg shifts --location Vastuvõtt` / `aeg locations` / `aeg departments` | Shift catalogue, locations, role groups |
| `tark_cli aeg schedule get --from 2026-10-05 --to 2026-10-11 -d Vastuvõtt` | Planned shifts (grid ≤ 14 days, else list; repeated `--date` = only those days, ≤ 92-day span; `--json` before `aeg` for raw) |
| `tark_cli aeg schedule set -e "Mari Maasikas" --date 2026-10-05 --shift "Vastuvõtt päev"` | Set one shift (replaces that day's shift at the same location — shifts at other locations are kept; `--dry-run`) |
| `tark_cli aeg schedule apply plan.json --dry-run` | Diff + rule check a JSON/CSV plan; drop `--dry-run` to write (one request per month; exit 3 on violations without `--force`). `--prune` also deletes the plan people's other shifts in range, only at the plan's location(s). `"shift": null` (empty CSV cell) clears that person's day at the entry's `location`, else the plan's `location`, else their home location — never elsewhere |
| `tark_cli aeg schedule delete --date 2026-12-31 -e "Mari Maasikas" --yes` | Delete planned shifts (needs a selector or `--all-in-scope`). Repeated `--date` = exactly those days; `-l` = rows at that location; `-d` = its people's rows at its location(s) (location-less rows of its people included) |
| `tark_cli aeg schedule check --week 2026-W41 -d Vastuvõtt --require "Vastuvõtt päev=2"` | Rest (11 h), days in a row (5), h/week (48), headcount, `--senior` checks; exit 3 on violations |

## Profiles (second server / tenant)

`--profile NAME` (or `$TARK_PROFILE`) selects `profiles.NAME` in `config.json`; its url + PAT
never fall back to the default ones, so the default (C2) setup keeps working unchanged.

```bash
tark_cli --profile demo config set url https://demo.example.com
tark_cli --profile demo config set pat_env TARK_DEMO_PAT   # PAT read from this env var
tark_cli --profile demo aeg employees
# no PAT API on that server? log in as the seat instead (password only from the env):
tark_cli --profile demo config set user demo.manager
tark_cli --profile demo config set password_env TARK_DEMO_PASSWORD   # required off the default host
tark_cli --profile demo aeg employees
```

`config set password …` (any `*password*` key but `password_env`) is refused — passwords
live only in env vars.

Run `tark_cli --help` for the full list, or `tark_cli <command> --help` for flags.

## Token management (`tokens`)

PAT create/list/revoke need **web login (JWT)**, not a PAT — a token can never mint
or revoke tokens (privilege escalation). These mirror the web UI:

```bash
tark_cli tokens                       # list your PATs (prompts for login)
tark_cli tokens scopes                # scope -> capability map (works offline)
tark_cli tokens create --name ci-bot --scope pm:write --scope sales:read [--expires 2026-12-31]
tark_cli tokens revoke <id> [--yes]   # soft-revoke (is_active=False), destructive
```

- **Username** — `--user`, else config `user` (`tark_cli config set user <name>`), else prompt.
- **Password** — `getpass` prompt, or from the environment for automation: `$TARK_PASSWORD` on
  the default target only; under `--profile` / `--url` the same rules as `aeg` seat login
  (`--password-env`, profile `password_env`, `$TARK_AEG_PASSWORD` on the default host only).
  **Never stored**, never
  written to config; the JWT lives in memory for the one request. Keep secrets in a
  private (chmod 600) env file you source, never inline.
- `tokens create` prints the token **once** — store it immediately.

## Sales follow-up engine

The platform runs an in-product sales follow-up cadence. A due lead becomes a
**DRAFT** `EmailTask` — a first-class scheduled email whose **body IS the verbatim
email** (no transform between confirm and send), pre-filled with the lead's
details (`{company}`, `{name}`). Two independent send gates protect it:

```
DRAFT  →  REVIEW  →  CONFIRMED  →  SENT / FAILED
        (you draft)  (human       (platform sender:
                      confirms +   status==CONFIRMED
                      sets time)   AND send_at<=now)
```

Automation drives the **DRAFT → REVIEW** half only. **Confirmation is a human
gate — not available over a PAT at all** (the serializer blocks a PAT from
setting CONFIRMED/SENT/FAILED), and only the platform sender writes SENT/FAILED.
Two surfaces:

```bash
tark_cli followups-check          # create DRAFT EmailTasks for every due lead now
tark_cli email-tasks -f DRAFT     # list scheduled emails by status

# The gate-safe assistant helper — writes the body and moves DRAFT -> REVIEW.
# It HARD-REFUSES any move into CONFIRMED / SENT / FAILED (the human + sender own those).
./sales_followup.py list
./sales_followup.py draft 123 --subject "Acme + Tark — next step" --body "Tere, Anna! ..."
```

`followups-check` needs a PAT with `sales:write` scope held by a user with the
`sales.change_salesconfig` permission; the `email-tasks` / `email-task-set`
commands need `sales:write`.

## Auth

Three ways to provide credentials, checked in order:

1. **Environment variable** — `TARK_PAT=tark_pat_... TARK_URL=https://...` (legacy `C2_PAT`/`C2_URL` still work)
2. **Config file** — `~/.config/tark/config.json` (chmod 600), set via `tark_cli config set`
3. **No default** — set the URL via `tark_cli config set url ...` or `TARK_URL` (legacy `C2_URL`); the CLI errors with guidance if none is configured

Run `tark_cli config` to see what's currently in effect.

## Output Formats

Pass `--json` *before* the subcommand to get raw JSON for piping into `jq`:

```bash
tark_cli --json tasks --project Website | jq '.[] | {id, name, hours: .total_hours}'
```

Without `--json`, output is human-readable tables.

## Examples

See [`examples/`](../examples/) for runnable scripts:

- `01_dashboard.sh` — timer + open tasks + today's time, in one view
- `02_log_time.sh` — log time entries, with task picking
- `03_timer.sh` — start/stop a timer around a unit of work
- `04_batch_ingest.sh` — bulk-create tasks from a JSON file (idempotent)
- `05_weekly_report.sh` — time totals grouped by project, for invoicing
- `06_followup_draft.sh` — draft a sales follow-up email for a due lead

## Tests

`python3 -m pytest tests` (needs `pip install pytest`; no network, every API call is stubbed).

## Security

This CLI is open source. The security boundary lives at the API:

- **PATs are scoped to your user.** They cannot escalate privileges. Revoke them anytime in the UI.
- **Tenant isolation is enforced server-side.** A PAT for tenant A cannot read tenant B data.
- **Treat your PAT like a password.** Don't commit it. Don't paste it in screenshots. Rotate if exposed.
- **Audit log** — every PAT call is logged with timestamp + IP + endpoint.

If you find a security issue, email `security@tarktoostus.ee` rather than opening a public issue.

## Endpoints

PAT auth covers the workflow API surface:

- `pm/` — projects, boards, columns, tasks, task dependencies, comments, time entries, timer
- `sales/` — leads, offers, offer-lines, contracts, pipelines, email-tasks (the follow-up engine), enqueue trigger (`config/enqueue-followups`)
- `system/` — clients, contract-types, contract-templates

Some endpoints (analytics, token management, ingest webhooks) require JWT auth via the web UI rather than PAT. The CLI surfaces a hint when you hit one.

## License

MIT. See [LICENSE](../LICENSE).
