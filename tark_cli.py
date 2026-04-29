#!/usr/bin/env python3
"""
tark — CLI for Tark Platform C2.

Standalone Python script (stdlib only, no pip deps).
Authenticates via PAT token against the C2 API.

INVARIANT — every PAT-exposed API endpoint must have a CLI command.
    When you add or extend a resource in any `backend/*/api/pat_urls.py`,
    also add/update the matching `cmd_*` handler here. The generic
    `api <path>` command is the escape hatch for unreleased endpoints,
    not an excuse to skip named commands.

Usage (binary installed as `tark_cli` at ~/bin/tark_cli; examples below use that form):
    tark_cli status                         # Deployment overview
    tark_cli deployments                    # List deployments
    tark_cli deploy <id|domain>             # Deployment detail

    tark_cli tasks [--project=X] [--status=X]   # List tasks
    tark_cli task <id>                          # Task detail
    tark_cli create <project> <subject>         # Create task
    tark_cli projects                           # List PM projects
    tark_cli boards [--project=X]               # List boards
    tark_cli columns [--board=X]                # List board-columns
    tark_cli comments [--task=X]                # List task comments

    tark_cli timer                          # Active timer
    tark_cli start <task-id>                # Start timer
    tark_cli stop                           # Stop timer
    tark_cli discard                        # Discard timer

    tark_cli log <hours> <task-id> [desc]   # Log time entry
    tark_cli time [today|week|month]        # Time report

    tark_cli leads                          # Sales leads
    tark_cli offers                         # Sales offers
    tark_cli offer-lines [--offer=X]        # Offer line items
    tark_cli contracts                      # Sales contracts
    tark_cli pipelines                      # CRM pipelines
    tark_cli pipeline-stages [--pipeline=X] # Pipeline stages
    tark_cli contract-types                 # Contract types (system)
    tark_cli contract-templates             # Contract templates (system)
    tark_cli clients [--search=X]           # Tenant clients
    tark_cli ingest <project> <board> --tasks '[{"subject":"..."}]'   # Batch ingest PM tasks
    tark_cli wiki <task-id>                              # Fetch task wiki
    tark_cli wiki <task-id> set     --section <h> --body <md>  # Upsert (preferred)
    tark_cli wiki <task-id> append  --section <h> --body <md>  # Append (refuses if dup)
    tark_cli wiki <task-id> replace --section <h> --body <md>  # Replace (404 if missing)
    tark_cli stage <task-id> <stage>        # Advance task stage (gates on wiki)
    tark_cli update <task-id> [--priority X] [--column Y] [--assignee Z] [--name ...]  # Patch task fields
    tark_cli tokens                         # List PATs

    tark_cli api <path> [--filter k=v ...]  # Generic GET for any /pat/<path>/
    tark_cli api <path> --post <json>       # Generic POST
    tark_cli api <path> --patch <json>      # Generic PATCH
    tark_cli config                         # Show config
    tark_cli config set <key> <value>       # Persist a config value

Note: `--json` is a top-level flag and MUST precede the subcommand, e.g.
    tark_cli --json leads --pipeline Imports

Auth: C2_PAT env var, or ~/.config/tark/config.json
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / '.config' / 'tark'
CONFIG_FILE = CONFIG_DIR / 'config.json'
DEFAULT_URL = 'https://c2.tarktoostus.ee'


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def _get_pat() -> str:
    pat = os.environ.get('C2_PAT', '') or _load_config().get('pat', '')
    if not pat:
        _err('No PAT configured. Set C2_PAT env var or run: tark config set pat <token>')
    return pat


def _get_url() -> str:
    return os.environ.get('C2_URL', '') or _load_config().get('url', '') or DEFAULT_URL


def _get_user_id() -> int | None:
    val = os.environ.get('C2_USER_ID', '') or _load_config().get('user_id', '')
    return int(val) if val else None


# ---------------------------------------------------------------------------
# HTTP client (stdlib only)
# ---------------------------------------------------------------------------

def _request(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict | list:
    base = _get_url().rstrip('/')
    url = f'{base}{path}'

    if params:
        qs = '&'.join(f'{k}={urllib.request.quote(str(v))}' for k, v in params.items() if v is not None)
        if qs:
            url = f'{url}?{qs}'

    data = json.dumps(body).encode() if body else None
    headers = {
        'Authorization': f'Bearer {_get_pat()}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        body_text = ''
        try:
            body_text = e.read().decode()
        except Exception:
            pass
        if e.code == 401:
            _err('Authentication failed (401). Check your PAT token.')
        elif e.code == 403:
            # Determine required scope from path
            scope_hint = ''
            if '/c2/' in path:
                scope_hint = ' Add c2:read scope to your PAT.'
            elif '/pm/' in path:
                scope_hint = ' Add pm:write scope to your PAT.'
            _err(f'Permission denied (403).{scope_hint}')
        elif e.code == 404:
            try:
                payload = json.loads(body_text)
            except (json.JSONDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get('detail'):
                hint = ''
                if 'wiki' in path and payload.get('header'):
                    hint = '  Hint: use `wiki set` to upsert, or `wiki append` to add a new section.'
                _err(f'Not found (404): {payload["detail"]}{hint}')
            _err(f'Not found (404): {path}')
        else:
            # Surface structured DRF errors when present (e.g. stage-gate `missing_section`).
            try:
                payload = json.loads(body_text)
            except (json.JSONDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict) and (payload.get('detail') or payload.get('missing_section')):
                detail = payload.get('detail') or ''
                missing = payload.get('missing_section')
                msg = f'HTTP {e.code}: {detail}'.strip().rstrip(':')
                if missing:
                    msg += f'  [missing_section="{missing}"]'
                _err(msg)
            else:
                _err(f'HTTP {e.code}: {body_text[:500]}')
    except urllib.error.URLError as e:
        _err(f'Connection failed: {e.reason}')


def _get(path: str, **params) -> dict | list:
    return _request('GET', path, params=params if params else None)


def _post(path: str, body: dict | None = None) -> dict | list:
    return _request('POST', path, body=body)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _err(msg: str) -> None:
    print(f'Error: {msg}', file=sys.stderr)
    sys.exit(1)


def _json_out(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _table(headers: list[str], rows: list[list], widths: list[int] | None = None) -> None:
    if not widths:
        widths = []
        for i, h in enumerate(headers):
            col_max = len(h)
            for row in rows:
                if i < len(row):
                    col_max = max(col_max, len(str(row[i])))
            widths.append(min(col_max, 40))

    fmt = '  '.join(f'{{:<{w}}}' for w in widths)
    print(fmt.format(*[h[:w] for h, w in zip(headers, widths)]))
    print(fmt.format(*['-' * w for w in widths]))
    for row in rows:
        cells = [str(c)[:w] for c, w in zip(row, widths)]
        # Pad if row is shorter than headers
        while len(cells) < len(widths):
            cells.append('')
        print(fmt.format(*cells))


def _ago(iso_str: str | None) -> str:
    if not iso_str:
        return 'never'
    try:
        # Handle timezone-aware ISO strings
        clean = iso_str.replace('+00:00', '+0000').replace('Z', '+0000')
        if '+' in clean[10:]:
            dt_str = clean[:clean.rindex('+')]
        elif clean[10:].count('-') > 0:
            dt_str = clean[:clean.rindex('-')]
        else:
            dt_str = clean

        # Try multiple formats
        for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(dt_str, fmt)
                break
            except ValueError:
                continue
        else:
            return iso_str[:16]

        diff = datetime.utcnow() - dt
        secs = diff.total_seconds()
        if secs < 60:
            return 'just now'
        if secs < 3600:
            return f'{int(secs // 60)}m ago'
        if secs < 86400:
            return f'{secs / 3600:.1f}h ago'
        return f'{int(secs // 86400)}d ago'
    except Exception:
        return iso_str[:16] if iso_str else 'unknown'


def _resolve_project(name: str) -> int:
    """Resolve a project name (or substring) to its ID."""
    projects = _get('/api/v1/pat/pm/projects/')
    results = projects.get('results', projects) if isinstance(projects, dict) else projects
    match = [p for p in results if name.lower() in p.get('name', '').lower()]
    if not match:
        _err(f'No project matching "{name}"')
    if len(match) > 1:
        names = ', '.join(f'{p["name"]} (#{p["id"]})' for p in match[:5])
        _err(f'Ambiguous project "{name}": {names}')
    return match[0]['id']


def _monday() -> str:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def _month_start() -> str:
    return date.today().replace(day=1).isoformat()


# ---------------------------------------------------------------------------
# Commands: Deployments
# ---------------------------------------------------------------------------

def cmd_status(args):
    """Deployment grid summary."""
    data = _get('/api/v1/pat/c2/deployments/')
    results = data.get('results', data) if isinstance(data, dict) else data

    if args.json:
        _json_out(results)
        return

    print(f'\n  TARK DEPLOYMENTS ({len(results)})\n')
    rows = []
    for d in results:
        healthy = 'OK' if d.get('is_healthy') else 'DOWN'
        rows.append([
            d.get('id', ''),
            d.get('name', ''),
            d.get('domain', ''),
            d.get('environment_type', ''),
            healthy,
            _ago(d.get('last_seen')),
        ])
    _table(['ID', 'Name', 'Domain', 'Env', 'Health', 'Last Seen'], rows)
    print()


def cmd_deployments(args):
    """List deployments (alias for status)."""
    cmd_status(args)


def cmd_deploy(args):
    """Deployment detail."""
    ident = args.identifier

    # Try numeric ID first
    try:
        dep_id = int(ident)
        data = _get(f'/api/v1/pat/c2/deployments/{dep_id}/')
    except ValueError:
        # Search by domain
        all_deps = _get('/api/v1/pat/c2/deployments/')
        results = all_deps.get('results', all_deps) if isinstance(all_deps, dict) else all_deps
        match = [d for d in results if ident in d.get('domain', '')]
        if not match:
            _err(f'No deployment matching "{ident}"')
        data = match[0]

    if args.json:
        _json_out(data)
        return

    print(f'\n  {data.get("name", "?")} ({data.get("domain", "?")})')
    print(f'  ID: {data.get("id")}  Env: {data.get("environment_type")}  Health: {"OK" if data.get("is_healthy") else "DOWN"}')
    print(f'  Version: {data.get("deployed_version", "?")}  Last seen: {_ago(data.get("last_seen"))}')
    print(f'  Queue: {data.get("queue_depth", 0)} tasks, oldest {data.get("queue_oldest_seconds", 0)}s')
    if data.get('edge_device_count'):
        print(f'  Edge: {data.get("edge_devices_healthy", 0)}/{data.get("edge_device_count", 0)} healthy')
    print()


# ---------------------------------------------------------------------------
# Commands: Tasks
# ---------------------------------------------------------------------------

def cmd_tasks(args):
    """List tasks."""
    params = {'ordering': '-updated_at', 'limit': '50'}

    user_id = _get_user_id()
    if user_id and not args.all:
        params['assignee'] = str(user_id)

    if args.project:
        # Task → Board → Project; filter via board__project (task has no direct project FK)
        try:
            params['board__project'] = str(int(args.project))
        except ValueError:
            project_id = _resolve_project(args.project)
            params['board__project'] = str(project_id)

    if args.status:
        params['column__name'] = args.status

    data = _get('/api/v1/pat/pm/tasks/', **params)
    results = data.get('results', data) if isinstance(data, dict) else data

    if args.json:
        _json_out(results)
        return

    print(f'\n  TASKS ({len(results)})\n')
    rows = []
    for t in results:
        rows.append([
            t.get('id', ''),
            t.get('column_name', '-') or '-',
            t.get('name', '')[:50],
            t.get('project_name', '')[:20],
            t.get('priority', ''),
            f'{t.get("total_hours") or "-"}h' if t.get('total_hours') else '-',
        ])
    _table(['ID', 'Status', 'Name', 'Project', 'Pri', 'Hours'], rows)
    print()


def cmd_task(args):
    """Task detail."""
    data = _get(f'/api/v1/pat/pm/tasks/{args.id}/')

    if args.json:
        _json_out(data)
        return

    print(f'\n  #{data.get("id")} {data.get("name")}')
    print(f'  Project: {data.get("project_name")}  Column: {data.get("column_name") or "-"}')
    print(f'  Priority: {data.get("priority")}  Assignee: {data.get("assignee_name") or "-"}')
    if data.get('total_hours'):
        print(f'  Hours: {data.get("total_hours")}')
    if data.get('description'):
        print(f'\n  {data["description"][:500]}')
    print()


def _resolve_board(project_id: int, board_arg: str | None) -> int:
    """Resolve --board (ID or name) to a board ID. If omitted, pick the project's first board."""
    boards = _get('/api/v1/pat/pm/boards/', project=project_id)
    results = boards.get('results', boards) if isinstance(boards, dict) else boards
    if not results:
        _err(f'Project {project_id} has no boards. Create one in C2 first.')

    if board_arg is None:
        if len(results) > 1:
            names = ', '.join(f'{b["name"]} (#{b["id"]})' for b in results[:5])
            print(f'  Note: project has {len(results)} boards, using first. Pass --board to pick: {names}', file=sys.stderr)
        return results[0]['id']

    try:
        bid = int(board_arg)
        if any(b.get('id') == bid for b in results):
            return bid
        _err(f'Board #{bid} not in project {project_id}')
    except ValueError:
        match = [b for b in results if board_arg.lower() in (b.get('name') or '').lower()]
        if not match:
            _err(f'No board matching "{board_arg}" in project {project_id}')
        if len(match) > 1:
            names = ', '.join(f'{b["name"]} (#{b["id"]})' for b in match[:5])
            _err(f'Ambiguous board "{board_arg}": {names}')
        return match[0]['id']


def cmd_create(args):
    """Create a task. POST /api/v1/pat/pm/tasks/ requires `name` + `board`."""
    try:
        project_id = int(args.project)
    except ValueError:
        project_id = _resolve_project(args.project)

    board_id = _resolve_board(project_id, getattr(args, 'board', None))

    name = ' '.join(args.subject)
    body = {'name': name, 'board': board_id, 'priority': args.priority or 'medium'}

    user_id = _get_user_id()
    if user_id:
        body['assignee'] = user_id

    data = _post('/api/v1/pat/pm/tasks/', body)

    if args.json:
        _json_out(data)
        return

    print(f'  Created #{data.get("id")}: {data.get("name")}  (board #{board_id})')


# ---------------------------------------------------------------------------
# Commands: Timer
# ---------------------------------------------------------------------------

def cmd_timer(args):
    """Active timer state."""
    data = _get('/api/v1/pat/pm/tasks/timer/')

    if args.json:
        _json_out(data)
        return

    if not data.get('active'):
        print('  No active timer.')
        return

    task = data.get('task', {})
    started = data.get('started_at', '')
    print(f'  Timer: #{task.get("id")} {task.get("name", "?")}')
    print(f'  Started: {_ago(started)}')
    print(f'  Project: {task.get("project_name", "?")}')


def cmd_start(args):
    """Start timer on task."""
    data = _post(f'/api/v1/pat/pm/tasks/{args.task_id}/start-timer/')

    if args.json:
        _json_out(data)
        return

    print(f'  Timer started on #{args.task_id}')


def cmd_stop(args):
    """Stop timer, save time entry."""
    data = _post('/api/v1/pat/pm/tasks/stop-timer/')

    if args.json:
        _json_out(data)
        return

    print(f'  Timer stopped. Time entry saved.')


def cmd_discard(args):
    """Discard timer without saving."""
    data = _post('/api/v1/pat/pm/tasks/discard-timer/')

    if args.json:
        _json_out(data)
        return

    print(f'  Timer discarded.')


# ---------------------------------------------------------------------------
# Commands: Time
# ---------------------------------------------------------------------------

def cmd_log(args):
    """Log a time entry."""
    body = {
        'task': args.task_id,
        'hours': str(args.hours),
        'description': ' '.join(args.description) if args.description else '',
        'date': args.date or date.today().isoformat(),
    }
    data = _post('/api/v1/pat/pm/time-entries/', body)

    if args.json:
        _json_out(data)
        return

    print(f'  Logged {args.hours}h to #{args.task_id}')


def cmd_time(args):
    """Time report."""
    period = args.period or 'week'
    params = {'ordering': '-date', 'limit': '100'}

    user_id = _get_user_id()
    if user_id:
        params['user'] = str(user_id)

    if period == 'today':
        params['date'] = date.today().isoformat()
    elif period == 'week':
        params['date__gte'] = _monday()
    elif period == 'month':
        params['date__gte'] = _month_start()

    data = _get('/api/v1/pat/pm/time-entries/', **params)
    results = data.get('results', data) if isinstance(data, dict) else data

    if args.json:
        _json_out(results)
        return

    total = sum(float(e.get('hours', 0)) for e in results)
    print(f'\n  TIME REPORT: {period} ({total:.1f}h total)\n')

    # Group by project
    by_project: dict[str, float] = {}
    for e in results:
        proj = e.get('project_name', '?')
        by_project[proj] = by_project.get(proj, 0) + float(e.get('hours', 0))

    if by_project:
        print('  By project:')
        for proj, hours in sorted(by_project.items(), key=lambda x: -x[1]):
            bar = '#' * int(hours)
            print(f'    {proj:<30} {hours:>5.1f}h  {bar}')
        print()

    # Detail
    rows = []
    for e in results:
        rows.append([
            e.get('date', ''),
            e.get('task_name', '')[:30],
            e.get('project_name', '')[:20],
            f'{float(e.get("hours", 0)):.1f}h',
            (e.get('description') or '')[:30],
        ])
    _table(['Date', 'Task', 'Project', 'Hours', 'Description'], rows)
    print()


# ---------------------------------------------------------------------------
# Commands: Leads
# ---------------------------------------------------------------------------

def cmd_leads(args):
    """Sales leads (CRM `/sales/leads/`). Supports filters."""
    params = {}
    if getattr(args, 'pipeline', None):
        params['pipeline__name'] = args.pipeline
    if getattr(args, 'status', None):
        params['status'] = args.status
    if getattr(args, 'limit', None):
        params['limit'] = args.limit
    if getattr(args, 'ordering', None):
        params['ordering'] = args.ordering
    qs = ('?' + urllib.parse.urlencode(params)) if params else ''
    data = _get(f'/api/v1/pat/sales/leads/{qs}')
    results = data.get('results', data) if isinstance(data, dict) else data

    if args.json:
        _json_out(results)
        return

    print(f'\n  SALES LEADS ({len(results)})\n')
    rows = []
    for l in results:
        rows.append([
            l.get('id', ''),
            l.get('company_name', ''),
            l.get('stage') or l.get('status', ''),
            l.get('source', ''),
            l.get('contact_name', ''),
            f'{l.get("estimated_mrr") or "-"}',
        ])
    _table(['ID', 'Company', 'Stage', 'Source', 'Contact', 'MRR'], rows)
    print()


def cmd_offers(args):
    """Sales offers (CRM `/sales/offers/`)."""
    params = {}
    if getattr(args, 'limit', None):
        params['limit'] = args.limit
    if getattr(args, 'ordering', None):
        params['ordering'] = args.ordering
    qs = ('?' + urllib.parse.urlencode(params)) if params else ''
    data = _get(f'/api/v1/pat/sales/offers/{qs}')
    results = data.get('results', data) if isinstance(data, dict) else data

    if args.json:
        _json_out(results)
        return

    print(f'\n  SALES OFFERS ({len(results)})\n')
    rows = []
    for o in results:
        rows.append([
            o.get('id', ''),
            o.get('company_name') or (o.get('lead', {}) or {}).get('company_name', ''),
            o.get('status', ''),
            f'{o.get("total") or "-"}',
            o.get('created_at', '')[:10],
        ])
    _table(['ID', 'Company', 'Status', 'Total', 'Created'], rows)
    print()


# ---------------------------------------------------------------------------
# Commands: PM — projects, boards, columns, comments
# ---------------------------------------------------------------------------

def _simple_list(path: str, label: str, headers: list, row_fn, args, params: dict | None = None):
    """Shared list helper: GET /api/v1/pat/<path>/, print table or JSON."""
    qs = ('?' + urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v})) if params else ''
    data = _get(f'/api/v1/pat/{path}/{qs}')
    results = data.get('results', data) if isinstance(data, dict) else data
    if args.json:
        _json_out(results)
        return
    print(f'\n  {label.upper()} ({len(results)})\n')
    _table(headers, [row_fn(r) for r in results])
    print()


def cmd_projects(args):
    """List PM projects."""
    _simple_list(
        'pm/projects', 'projects',
        ['ID', 'Name', 'Status', 'Owner'],
        lambda p: [p.get('id'), p.get('name', ''), p.get('status', ''), p.get('owner_name') or p.get('owner', '')],
        args,
    )


_PROJECT_UPDATE_FIELDS = ('name', 'description', 'status', 'owner',
                          'start_date', 'end_date', 'client')


def cmd_projects_update(args):
    """PATCH PM project fields on /api/v1/pat/pm/projects/{id}/.

    NOTE: backend `pat_urls.py` must register `partial_update: pm:write` for
    `pm-project`; otherwise the server returns 405 Method Not Allowed.
    """
    body = {}
    for fld in _PROJECT_UPDATE_FIELDS:
        val = getattr(args, fld, None)
        if val is not None:
            body[fld] = val

    if not body:
        _err(f'No fields to update. Pass one of: --{", --".join(f.replace("_", "-") for f in _PROJECT_UPDATE_FIELDS)}')

    data = _request('PATCH', f'/api/v1/pat/pm/projects/{args.id}/', body=body)
    if args.json:
        _json_out(data)
        return
    changes = ', '.join(f'{k}={v}' for k, v in body.items())
    print(f'  Updated project #{args.id}: {changes}')


def cmd_boards(args):
    """List PM boards. Optional --project filter."""
    _simple_list(
        'pm/boards', 'boards',
        ['ID', 'Name', 'Project', 'Type', 'Tasks'],
        lambda b: [b.get('id'), b.get('name', ''), b.get('project_name') or b.get('project', ''), b.get('board_type', ''), b.get('task_count', '')],
        args,
        params={'project': args.project} if getattr(args, 'project', None) else None,
    )


def cmd_columns(args):
    """List board columns. Optional --board filter."""
    _simple_list(
        'pm/board-columns', 'board columns',
        ['ID', 'Name', 'Board', 'Order', 'Done'],
        lambda c: [c.get('id'), c.get('name', ''), c.get('board', ''), c.get('order', ''), 'yes' if c.get('is_done') else ''],
        args,
        params={'board': args.board, 'ordering': 'order'} if getattr(args, 'board', None) else {'ordering': 'order'},
    )


def cmd_comments(args):
    """List task comments. Optional --task filter."""
    _simple_list(
        'pm/task-comments', 'task comments',
        ['ID', 'Task', 'Author', 'Created', 'Body'],
        lambda c: [c.get('id'), c.get('task'), c.get('author_name') or c.get('author', ''), (c.get('created_at') or '')[:10], (c.get('body') or '')[:60]],
        args,
        params={'task': args.task} if getattr(args, 'task', None) else None,
    )


# ---------------------------------------------------------------------------
# Commands: Sales — offer-lines, contracts, pipelines
# ---------------------------------------------------------------------------

def cmd_offer_lines(args):
    """List offer lines. Optional --offer filter."""
    _simple_list(
        'sales/offer-lines', 'offer lines',
        ['ID', 'Offer', 'Description', 'Qty', 'Unit', 'Total'],
        lambda l: [l.get('id'), l.get('offer'), (l.get('description') or '')[:40], l.get('quantity', ''), l.get('unit_price', ''), l.get('total', '')],
        args,
        params={'offer': args.offer} if getattr(args, 'offer', None) else None,
    )


def cmd_contracts(args):
    """List sales contracts."""
    _simple_list(
        'sales/contracts', 'contracts',
        ['ID', 'Title', 'Client', 'Status', 'Signed'],
        lambda c: [c.get('id'), (c.get('title') or '')[:40], c.get('client_name') or c.get('client', ''), c.get('status', ''), (c.get('signed_at') or '')[:10]],
        args,
    )


def cmd_pipelines(args):
    """List CRM pipelines."""
    _simple_list(
        'sales/pipelines', 'pipelines',
        ['ID', 'Name', 'Module', 'Default'],
        lambda p: [p.get('id'), p.get('name', ''), p.get('module', ''), 'yes' if p.get('is_default') else ''],
        args,
    )


def cmd_pipeline_stages(args):
    """List pipeline stages. Optional --pipeline filter."""
    _simple_list(
        'sales/pipeline-stages', 'pipeline stages',
        ['ID', 'Name', 'Pipeline', 'Order'],
        lambda s: [s.get('id'), s.get('name', ''), s.get('pipeline_name') or s.get('pipeline', ''), s.get('order', '')],
        args,
        params={'pipeline': args.pipeline} if getattr(args, 'pipeline', None) else None,
    )


# ---------------------------------------------------------------------------
# Commands: Clients (core — mounted at /pat/system/)
# ---------------------------------------------------------------------------

def cmd_clients(args):
    """List tenant clients. Tenant-scoped server-side."""
    params = {}
    if getattr(args, 'search', None):
        params['search'] = args.search
    if getattr(args, 'limit', None):
        params['limit'] = args.limit
    _simple_list(
        'system/clients', 'clients',
        ['ID', 'Name', 'Account Mgr', 'Offers', 'Created'],
        lambda c: [
            c.get('id'),
            (c.get('name') or '')[:40],
            c.get('account_manager_name') or c.get('account_manager', ''),
            c.get('offer_count', ''),
            (c.get('created_at') or '')[:10],
        ],
        args,
        params=params or None,
    )


_CLIENT_WRITABLE_FIELDS = (
    'name', 'address', 'contact_info', 'registry_code',
    'representative_name', 'representative_basis',
    'email', 'billing_info', 'notes',
)


def _build_client_body(args, *, include_name: bool) -> dict:
    body: dict = {}
    if include_name:
        body['name'] = args.name
    for flag in _CLIENT_WRITABLE_FIELDS:
        if flag == 'name' and not include_name:
            continue
        val = getattr(args, flag, None)
        if val is not None:
            body[flag] = val
    contact = getattr(args, 'contact', None)
    if contact is not None:
        body['contact'] = contact
    return body


def cmd_clients_create(args):
    """Create a tenant client (Company). POST /api/v1/pat/system/clients/ — needs sales:write."""
    body = _build_client_body(args, include_name=True)
    resp = _request('POST', '/api/v1/pat/system/clients/', body=body)
    if args.json:
        print(json.dumps(resp, indent=2))
        return
    print(f"  Created client #{resp.get('id')}: {resp.get('name', '')}")


def cmd_clients_update(args):
    """Update a tenant client. PATCH /api/v1/pat/system/clients/<id>/ — needs sales:write."""
    body = _build_client_body(args, include_name=False)
    if getattr(args, 'name', None) is not None:
        body['name'] = args.name
    if not body:
        _err('clients-update requires at least one field flag (e.g. --notes)')
    resp = _request('PATCH', f'/api/v1/pat/system/clients/{args.id}/', body=body)
    if args.json:
        print(json.dumps(resp, indent=2))
        return
    print(f"  Updated client #{resp.get('id')}: {resp.get('name', '')}")


# ---------------------------------------------------------------------------
# Commands: PM batch ingest
# ---------------------------------------------------------------------------

def cmd_ingest(args):
    """Batch-create PM tasks via /pat/pm/tasks/ingest/ (dedupes by subject per board).

    Usage:
        tark_cli ingest <project> <board> --tasks '[{"subject":"...","priority":"NORMAL"}]'
        tark_cli ingest <project> <board> --tasks-file tasks.json
    """
    try:
        if args.tasks_file:
            with open(args.tasks_file) as f:
                tasks = json.load(f)
        elif args.tasks:
            tasks = json.loads(args.tasks)
        else:
            _err('Provide --tasks <json> or --tasks-file <path>')
            return
    except (OSError, json.JSONDecodeError) as e:
        _err(f'Cannot read tasks: {e}')
        return

    if not isinstance(tasks, list) or not tasks:
        _err('tasks must be a non-empty JSON array of {subject, ...} objects')
        return

    body = {'project': args.project, 'board': args.board, 'tasks': tasks}
    result = _request('POST', '/api/v1/pat/pm/tasks/ingest/', body=body)
    if args.json:
        _json_out(result)
        return
    # Backend returns {created: int, skipped: int, details: [{subject, status, id?, reason?}, ...]}
    created = int(result.get('created', 0) or 0)
    skipped = int(result.get('skipped', 0) or 0)
    details = result.get('details') or []
    print(f'\n  INGEST: {created} created, {skipped} skipped (duplicate subjects)\n')
    for d in details:
        s = d.get('status')
        subj = (d.get('subject') or '')[:70]
        if s == 'created':
            print(f'  [+] #{d.get("id")} {subj}')
        elif s == 'skipped':
            print(f'  [=] {subj} — {d.get("reason", "exists")}')
        else:
            print(f'  [!] {subj} — {d.get("reason", s)}')
    print()


# ---------------------------------------------------------------------------
# Commands: PM wiki + stage
# ---------------------------------------------------------------------------

_WIKI_HEADER_RE = re.compile(r'^##\s+(?P<title>.+?)\s*$', re.MULTILINE)


def _wiki_section_exists(wiki_text: str, header: str) -> bool:
    """Mirror of backend `_wiki_has_section`: anchored prefix match on `## {header}`."""
    pattern = re.compile(rf'^{re.escape(header)}(:| Phase |$)')
    for m in _WIKI_HEADER_RE.finditer(wiki_text or ''):
        if pattern.match(m.group('title').strip()):
            return True
    return False


def cmd_wiki(args):
    """Fetch / set / append / replace task wiki sections.

    Actions:
        get      — fetch the markdown body (default)
        set      — upsert: replace if section exists, else append (preferred for /brief)
        append   — add a new `## Section` block; refuses if header already exists (use --force to override)
        replace  — overwrite an existing section's body; 404 if header missing

    NOTE: POST payload uses field name `body` (not `content`).
    """
    path = f'/api/v1/pat/pm/tasks/{args.task_id}/wiki/'

    if args.action in (None, 'get'):
        data = _get(path)
        if args.json:
            _json_out(data)
            return
        print(data.get('wiki', '') if isinstance(data, dict) else data)
        return

    if args.action not in ('set', 'append', 'replace'):
        _err(f'Unknown wiki action "{args.action}". Use: get, set, append, replace')
        return

    if not args.section or args.body is None:
        _err('--section and --body are required for set/append/replace')
        return

    op = args.action

    # Pre-flight: GET current wiki for set/append safety checks.
    # (replace doesn't need this — server returns 404 with detail if missing.)
    if op in ('set', 'append'):
        cur = _get(path)
        wiki_text = cur.get('wiki', '') if isinstance(cur, dict) else ''
        exists = _wiki_section_exists(wiki_text, args.section)
        if op == 'set':
            op = 'replace' if exists else 'append'
        elif op == 'append' and exists and not getattr(args, 'force', False):
            _err(
                f'Section "## {args.section}" already exists on task #{args.task_id}. '
                f'Use `wiki set` to upsert, `wiki replace` to overwrite, or pass --force to add a duplicate.'
            )

    payload = {'action': op, 'section': args.section, 'body': args.body}
    data = _post(path, payload)
    if args.json:
        _json_out(data)
        return
    suffix = f' (via {op})' if args.action == 'set' else ''
    print(f'  wiki {args.action} OK on task #{args.task_id} section "{args.section}"{suffix}')


_UPDATE_FIELDS = ('priority', 'column', 'assignee', 'name', 'description',
                  'estimate_hours', 'start_date', 'due_date', 'parent', 'board')


def cmd_update(args):
    """PATCH task fields on /api/v1/pat/pm/tasks/{id}/.

    Curated set of common fields; for anything else use the `api` escape hatch:
        tark_cli api pm/tasks/<id> --patch '{"field":"value"}'
    """
    body = {}
    for fld in _UPDATE_FIELDS:
        val = getattr(args, fld, None)
        if val is not None:
            body[fld] = val

    if not body:
        _err(f'No fields to update. Pass one of: --{", --".join(f.replace("_", "-") for f in _UPDATE_FIELDS)}')

    data = _request('PATCH', f'/api/v1/pat/pm/tasks/{args.task_id}/', body=body)
    if args.json:
        _json_out(data)
        return
    changes = ', '.join(f'{k}={v}' for k, v in body.items())
    print(f'  Updated #{args.task_id}: {changes}')


def cmd_stage(args):
    """Advance task to next stage. Server gates on wiki section presence."""
    data = _post(f'/api/v1/pat/pm/tasks/{args.task_id}/stage/', {'stage': args.stage})
    if args.json:
        _json_out(data)
        return
    if isinstance(data, dict) and data.get('stage'):
        prev = data.get('previous_stage', '?')
        print(f'  Task #{args.task_id}: {prev} → {data["stage"]}')
    else:
        _json_out(data)


# ---------------------------------------------------------------------------
# Commands: System — contract types, blocks, templates
# ---------------------------------------------------------------------------

def cmd_contract_types(args):
    """List contract types (core/system)."""
    _simple_list(
        'system/contract-types', 'contract types',
        ['ID', 'Name', 'Key'],
        lambda c: [c.get('id'), c.get('name', ''), c.get('key', '')],
        args,
    )


def cmd_contract_templates(args):
    """List contract templates (core/system)."""
    _simple_list(
        'system/contract-templates', 'contract templates',
        ['ID', 'Name', 'Type', 'Version'],
        lambda t: [t.get('id'), t.get('name', ''), t.get('contract_type_name') or t.get('contract_type', ''), t.get('version', '')],
        args,
    )


# ---------------------------------------------------------------------------
# Commands: Generic — `api` escape hatch for any PAT endpoint
# ---------------------------------------------------------------------------

def cmd_api(args):
    """Generic GET/POST/PATCH against /api/v1/pat/<path>/.

    Escape hatch for endpoints that don't yet have a named command. When you
    reach for this repeatedly for the same endpoint, add a named command.
    """
    path = args.path.strip('/')
    params = {}
    for kv in (args.filter or []):
        if '=' in kv:
            k, v = kv.split('=', 1)
            params[k] = v

    body = None
    method = 'GET'
    raw_body = args.post or args.patch
    if args.post:
        method = 'POST'
    elif args.patch:
        method = 'PATCH'
    if raw_body:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as e:
            _err(f'Invalid JSON for --{method.lower()}: {e}')
            return

    qs = ('?' + urllib.parse.urlencode(params)) if params else ''
    result = _request(method, f'/api/v1/pat/{path}/{qs}', body=body)
    _json_out(result)


# ---------------------------------------------------------------------------
# Commands: Tokens
# ---------------------------------------------------------------------------

def cmd_tokens(args):
    """List PATs. Note: token management requires JWT auth, not PAT.
    This command may fail with 401 if called with PAT auth only.
    Use the C2 web UI to manage tokens: {url}/c2/tokens
    """
    try:
        data = _get('/api/v1/pat/tokens/')
    except SystemExit:
        print('  Token management requires web login (JWT auth), not PAT auth.')
        print(f'  Manage tokens at: {_get_url()}/c2/tokens')
        return

    results = data.get('results', data) if isinstance(data, dict) else data

    if args.json:
        _json_out(results)
        return

    print(f'\n  PERSONAL ACCESS TOKENS ({len(results)})\n')
    rows = []
    for t in results:
        rows.append([
            t.get('id', ''),
            t.get('prefix', ''),
            t.get('name', ''),
            _ago(t.get('last_used')),
            'active' if t.get('is_active') else 'revoked',
        ])
    _table(['ID', 'Prefix', 'Name', 'Last Used', 'Status'], rows)
    print()


# ---------------------------------------------------------------------------
# Commands: Config
# ---------------------------------------------------------------------------

def cmd_config(args):
    """Show or set config."""
    if args.action == 'set' and args.key and args.value:
        cfg = _load_config()
        # Convert user_id to int
        val = args.value
        if args.key == 'user_id':
            val = int(val)
        cfg[args.key] = val
        _save_config(cfg)
        print(f'  Saved {args.key} to {CONFIG_FILE}')
        return

    cfg = _load_config()
    if args.json:
        _json_out(cfg)
        return

    print(f'\n  CONFIG ({CONFIG_FILE})\n')
    if not cfg:
        print('  (empty)')
        print()
        print('  Quick setup:')
        print('    tark_cli config set pat tark_pat_...')
        print('    tark_cli config set url https://c2.tarktoostus.ee')
        print('    tark_cli config set user_id 38')
    else:
        for k, v in cfg.items():
            display = f'{str(v)[:8]}...' if k == 'pat' and len(str(v)) > 12 else v
            print(f'  {k}: {display}')

    # Show effective values
    print()
    print('  Effective:')
    print(f'    URL:     {_get_url()}')
    pat = os.environ.get('C2_PAT', '') or cfg.get('pat', '')
    print(f'    PAT:     {"***" + pat[-6:] if pat else "(not set)"}')
    print(f'    User ID: {_get_user_id() or "(not set)"}')
    print()


# ---------------------------------------------------------------------------
# Arg parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Match prog to the binary name (supports both the canonical ~/bin/tark_cli symlink
    # and a direct invocation of ./cli/tark). Falls back to "tark_cli" to match docs.
    prog_name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else 'tark_cli'
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description='Tark Platform C2 CLI',
    )
    parser.add_argument('--json', action='store_true', help='Output raw JSON')
    sub = parser.add_subparsers(dest='command')

    # status
    sub.add_parser('status', help='Deployment grid summary')

    # deployments
    sub.add_parser('deployments', help='List deployments')

    # deploy <id|domain>
    p = sub.add_parser('deploy', help='Deployment detail')
    p.add_argument('identifier', help='Deployment ID or domain substring')

    # tasks
    p = sub.add_parser('tasks', help='List tasks')
    p.add_argument('--project', '-p', help='Filter by project name or ID')
    p.add_argument('--status', '-s', help='Filter by column name (e.g. "In Progress")')
    p.add_argument('--all', '-a', action='store_true', help='Show all tasks (not just mine)')

    # task <id>
    p = sub.add_parser('task', help='Task detail')
    p.add_argument('id', type=int, help='Task ID')

    # create <project> <subject>
    p = sub.add_parser('create', help='Create task')
    p.add_argument('project', help='Project name or ID')
    p.add_argument('subject', nargs='+', help='Task name (sent as `name` to API)')
    p.add_argument('--board', '-b', help='Board name or ID (auto-picks project\'s first board if omitted)')
    p.add_argument('--priority', choices=['low', 'medium', 'high', 'urgent'], default='medium')

    # timer
    sub.add_parser('timer', help='Active timer state')

    # start <task-id>
    p = sub.add_parser('start', help='Start timer')
    p.add_argument('task_id', type=int, help='Task ID')

    # stop
    sub.add_parser('stop', help='Stop timer')

    # discard
    sub.add_parser('discard', help='Discard timer')

    # log <hours> <task-id> [desc]
    p = sub.add_parser('log', help='Log time entry')
    p.add_argument('hours', type=float, help='Hours to log')
    p.add_argument('task_id', type=int, help='Task ID')
    p.add_argument('description', nargs='*', help='Description')
    p.add_argument('--date', '-d', help='Date (YYYY-MM-DD, default: today)')

    # time [period]
    p = sub.add_parser('time', help='Time report')
    p.add_argument('period', nargs='?', choices=['today', 'week', 'month'], default='week')

    # leads
    p = sub.add_parser('leads', help='Sales leads')
    p.add_argument('--pipeline', help='Filter by pipeline name (e.g. Imports, Hiring)')
    p.add_argument('--status', help='Filter by status (e.g. NEW)')
    p.add_argument('--limit', type=int, help='Max results')
    p.add_argument('--ordering', help='Ordering field (e.g. -created_at)')

    # offers
    p = sub.add_parser('offers', help='Sales offers')
    p.add_argument('--limit', type=int, help='Max results')
    p.add_argument('--ordering', help='Ordering field (e.g. -created_at)')

    # offer-lines
    p = sub.add_parser('offer-lines', help='Sales offer lines')
    p.add_argument('--offer', help='Filter by offer ID')

    # contracts
    sub.add_parser('contracts', help='Sales contracts')

    # pipelines
    sub.add_parser('pipelines', help='CRM pipelines')

    # pipeline-stages
    p = sub.add_parser('pipeline-stages', help='Pipeline stages')
    p.add_argument('--pipeline', help='Filter by pipeline ID')

    # projects
    sub.add_parser('projects', help='PM projects')

    # projects-update — needs pm:write + backend `partial_update` registration in pat_urls.py
    p = sub.add_parser('projects-update', help='PATCH a PM project (needs pm:write + backend partial_update)')
    p.add_argument('id', type=int, help='Project ID')
    p.add_argument('--name', help='Project name')
    p.add_argument('--description', help='Description')
    p.add_argument('--status', help='Status')
    p.add_argument('--owner', type=int, help='Owner user ID (use api --patch \'{"owner":null}\' to unassign)')
    p.add_argument('--start-date', dest='start_date', help='Start date (YYYY-MM-DD)')
    p.add_argument('--end-date', dest='end_date', help='End date (YYYY-MM-DD)')
    p.add_argument('--client', type=int, help='Client ID')

    # boards
    p = sub.add_parser('boards', help='PM boards')
    p.add_argument('--project', help='Filter by project ID')

    # board-columns
    p = sub.add_parser('columns', help='PM board columns')
    p.add_argument('--board', help='Filter by board ID')

    # task-comments
    p = sub.add_parser('comments', help='Task comments')
    p.add_argument('--task', help='Filter by task ID')

    # clients (core)
    p = sub.add_parser('clients', help='Tenant clients')
    p.add_argument('--search', '-s', help='Search by name or address')
    p.add_argument('--limit', type=int, help='Max results')

    # clients-create — needs sales:write
    p = sub.add_parser('clients-create', help='Create a tenant client (Company) — needs sales:write')
    p.add_argument('name', help='Company name (required)')
    p.add_argument('--registry-code', dest='registry_code', help='Registry code (e.g. 11483740)')
    p.add_argument('--email', help='Primary contact email')
    p.add_argument('--address', help='Address')
    p.add_argument('--contact-info', dest='contact_info', help='Free-form contact info')
    p.add_argument('--contact', type=int, help='Contact (User) ID — must belong to your tenant')
    p.add_argument('--representative-name', dest='representative_name', help='Legal representative name')
    p.add_argument('--representative-basis', dest='representative_basis', help='Representative legal basis')
    p.add_argument('--billing-info', dest='billing_info', help='Billing details')
    p.add_argument('--notes', help='Notes')

    # clients-update — needs sales:write
    p = sub.add_parser('clients-update', help='Update a tenant client (PATCH) — needs sales:write')
    p.add_argument('id', type=int, help='Client ID')
    p.add_argument('--name', help='Company name')
    p.add_argument('--registry-code', dest='registry_code', help='Registry code')
    p.add_argument('--email', help='Primary contact email')
    p.add_argument('--address', help='Address')
    p.add_argument('--contact-info', dest='contact_info', help='Free-form contact info')
    p.add_argument('--contact', type=int, help='Contact (User) ID — must belong to your tenant')
    p.add_argument('--representative-name', dest='representative_name', help='Legal representative name')
    p.add_argument('--representative-basis', dest='representative_basis', help='Representative legal basis')
    p.add_argument('--billing-info', dest='billing_info', help='Billing details')
    p.add_argument('--notes', help='Notes')

    # ingest (PM batch)
    p = sub.add_parser('ingest', help='Batch-create PM tasks (dedupes by subject)')
    p.add_argument('project', help='Project name or ID')
    p.add_argument('board', help='Board name or ID')
    p.add_argument('--tasks', help='Tasks as JSON array string')
    p.add_argument('--tasks-file', help='Path to JSON file containing the tasks array')

    # wiki <task-id> [get|set|append|replace] [--section H] [--body MD] [--force]
    p = sub.add_parser('wiki', help='Task wiki get/set/append/replace (POST uses `body` field, not `content`)')
    p.add_argument('task_id', type=int, help='Task ID')
    p.add_argument('action', nargs='?', choices=['get', 'set', 'append', 'replace'], help='Default: get. `set` upserts.')
    p.add_argument('--section', help='Section header (without leading "## ")')
    p.add_argument('--body', help='Section markdown body')
    p.add_argument('--force', action='store_true', help='Allow `append` to create a duplicate of an existing section')

    # stage <task-id> <stage>
    p = sub.add_parser('stage', help='Advance task stage (gates on wiki section)')
    p.add_argument('task_id', type=int, help='Task ID')
    p.add_argument('stage', help='Target stage: brief|plan|review_plan|work|verify|review_impl|document|commit|deploy')

    # update <task-id> [field flags]
    p = sub.add_parser('update', help='PATCH task fields (priority, column, assignee, name, ...)')
    p.add_argument('task_id', type=int, help='Task ID')
    p.add_argument('--priority', choices=['low', 'medium', 'high', 'urgent'], help='Task priority')
    p.add_argument('--column', type=int, help='Board column ID')
    p.add_argument('--assignee', type=int, help='Assignee user ID (use 0 to unassign — server may reject; prefer api --patch \'{"assignee":null}\')')
    p.add_argument('--name', help='Task name/subject')
    p.add_argument('--description', help='Task description')
    p.add_argument('--estimate-hours', dest='estimate_hours', type=float, help='Estimated hours')
    p.add_argument('--start-date', dest='start_date', help='Start date (YYYY-MM-DD)')
    p.add_argument('--due-date', dest='due_date', help='Due date (YYYY-MM-DD)')
    p.add_argument('--parent', type=int, help='Parent task ID')
    p.add_argument('--board', type=int, help='Board ID (move task to a different board)')

    # contract-types, contract-templates
    sub.add_parser('contract-types', help='Contract types (system)')
    sub.add_parser('contract-templates', help='Contract templates (system)')

    # generic api escape hatch
    p = sub.add_parser('api', help='Generic request to /api/v1/pat/<path>/')
    p.add_argument('path', help='Path suffix after /api/v1/pat/ (e.g. sales/leads)')
    p.add_argument('--filter', '-f', action='append', help='Query filter k=v (repeatable)')
    p.add_argument('--post', help='POST body (JSON string)')
    p.add_argument('--patch', help='PATCH body (JSON string). Use path like "pm/tasks/123"')

    # tokens
    sub.add_parser('tokens', help='List PATs')

    # config [set <key> <value>]
    p = sub.add_parser('config', help='Show/set config')
    p.add_argument('action', nargs='?', default='show', help='"set" to save a value')
    p.add_argument('key', nargs='?', help='Config key (pat, url, user_id)')
    p.add_argument('value', nargs='?', help='Config value')

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COMMANDS = {
    'status': cmd_status,
    'deployments': cmd_deployments,
    'deploy': cmd_deploy,
    'tasks': cmd_tasks,
    'task': cmd_task,
    'create': cmd_create,
    'timer': cmd_timer,
    'start': cmd_start,
    'stop': cmd_stop,
    'discard': cmd_discard,
    'log': cmd_log,
    'time': cmd_time,
    'leads': cmd_leads,
    'offers': cmd_offers,
    'offer-lines': cmd_offer_lines,
    'contracts': cmd_contracts,
    'pipelines': cmd_pipelines,
    'pipeline-stages': cmd_pipeline_stages,
    'projects': cmd_projects,
    'projects-update': cmd_projects_update,
    'boards': cmd_boards,
    'columns': cmd_columns,
    'comments': cmd_comments,
    'contract-types': cmd_contract_types,
    'contract-templates': cmd_contract_templates,
    'clients': cmd_clients,
    'clients-create': cmd_clients_create,
    'clients-update': cmd_clients_update,
    'ingest': cmd_ingest,
    'wiki': cmd_wiki,
    'stage': cmd_stage,
    'update': cmd_update,
    'api': cmd_api,
    'tokens': cmd_tokens,
    'config': cmd_config,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        # Default: show status
        args.command = 'status'
        cmd_status(args)
        return

    handler = COMMANDS.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
