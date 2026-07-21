#!/usr/bin/env python3
"""sales_followup.py — gate-safe drafting for the sales follow-up engine (Feat #4600).

The follow-up engine (tark-platform sales module) is a first-class EmailTask:

    DRAFT     (queued)        <- `tark_cli followups-check` creates these
    REVIEW    (draft ready)   <- THIS script moves emails here after writing the body
    CONFIRMED (human-armed)   <- ONLY a human confirms (sets the send time)
    SENT / FAILED (terminal)  <- ONLY the platform Q2 sender writes these

The body IS the verbatim email — there is no transform between confirm and send.
This script automates the assistant's half only: read a DRAFT, write its body,
move it to REVIEW. It HARD-REFUSES any move past REVIEW. Confirmation is the
human gate (off-PAT entirely), and SENT/FAILED belong to the sender. The gate is
enforced server-side too (the serializer blocks a PAT from setting
CONFIRMED/SENT/FAILED, and the sender only drains CONFIRMED); this script just
makes the safe transition easy and the unsafe one impossible from automation.

No safety bypass — it only ever drives the DRAFT -> REVIEW transition through
tark_cli and refuses to touch anything past REVIEW; it never passes --no-safety.

Subcommands:
    list                       DRAFT emails awaiting a body (table, or --json).
    draft <email_task_id>      Write the body + move DRAFT -> REVIEW.
        --subject <text>       Email subject.
        --body <text>          Email body (the verbatim email), or
        --body-file <path>     read the body from a file.
    summary <email_task_id>    Status + subject + customer.

Exit codes: 0 ok; 1 generic error; 3 refused (email in a non-draftable status).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TARK_CLI = os.environ.get('TARK_CLI', str(Path.home() / 'bin' / 'tark_cli'))
TARK_CLI_TIMEOUT = int(os.environ.get('SALES_FOLLOWUP_TARK_CLI_TIMEOUT', '60'))

# Statuses this script may draft FROM, and the one it moves TO. Everything past
# REVIEW is off-limits to automation (see the module docstring).
DRAFTABLE_STATUSES = {'DRAFT', 'REVIEW'}
TARGET_STATUS = 'REVIEW'
FORBIDDEN_STATUSES = {'CONFIRMED', 'SENT', 'FAILED', 'CANCELLED'}


def die(msg: str, rc: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(rc)


def run_cli(*args: object, json_mode: bool = False) -> tuple[int, str, str]:
    """Invoke tark_cli. Returns (rc, stdout, stderr). Mirrors c2_auto.run_cli."""
    cmd: list[str] = [TARK_CLI]
    if json_mode:
        cmd.append('--json')
    cmd.extend(str(a) for a in args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TARK_CLI_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, '', f'tark_cli timed out after {TARK_CLI_TIMEOUT}s: {" ".join(cmd[1:])}'
    return r.returncode, r.stdout, r.stderr


def _cli_json(*args: object):
    rc, out, err = run_cli(*args, json_mode=True)
    if rc != 0:
        die(f'tark_cli {" ".join(str(a) for a in args)} failed (rc={rc}): {err.strip() or out.strip()}', rc=1)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        die(f'tark_cli {" ".join(str(a) for a in args)} JSON parse failed: {exc}\nraw: {out[:500]}', rc=1)


def _results(data) -> list:
    if isinstance(data, dict):
        return data.get('results', []) if 'results' in data else [data]
    return data or []


def _get_email_task(email_task_id) -> dict:
    """Fetch one EmailTask via the generic PAT GET escape hatch."""
    data = _cli_json('api', f'sales/email-tasks/{email_task_id}')
    if isinstance(data, dict) and data.get('id'):
        return data
    die(f'REFUSE: EmailTask #{email_task_id} not found.', rc=3)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> None:
    data = _cli_json('email-tasks', '-f', 'DRAFT')
    tasks = _results(data)
    if args.json:
        print(json.dumps(tasks, indent=2, ensure_ascii=False))
        return

    print(f'\n  FOLLOW-UPS — DRAFT emails awaiting a body ({len(tasks)})\n')
    if not tasks:
        print('  (none — run `tark_cli followups-check` to enqueue due follow-ups)\n')
        return
    for t in tasks:
        summary = t.get('lead_summary') or {}
        who = summary.get('company_name') or summary.get('person_name') or t.get('to_email', '')
        print(f'  #{t.get("id"):<6} {t.get("subject", "(no subject)")}  [{who}]')
    print()


def cmd_draft(args: argparse.Namespace) -> None:
    # Forward --body-file as a path (don't slurp it into an argv string: keeps the
    # body out of the process table and off the ARG_MAX ceiling). Validate up front
    # so a missing file is a clean error, not a traceback from the child process.
    if args.body_file:
        if not Path(args.body_file).is_file():
            die(f'draft: --body-file not found: {args.body_file}', rc=1)
        body_args = ['--body-file', args.body_file]
    elif args.body is not None:
        body_args = ['--body', args.body]
    else:
        die('draft needs --body <text> or --body-file <path>', rc=1)

    task = _get_email_task(args.email_task_id)
    status = task.get('status', '')
    if status in FORBIDDEN_STATUSES:
        die(
            f'REFUSE: EmailTask #{args.email_task_id} is {status} — past the REVIEW gate. '
            f'CONFIRMED is the human confirm step; SENT/FAILED belong to the sender. '
            f'Automation never sets those.',
            rc=3,
        )
    # Fail CLOSED: only an explicitly DRAFT/REVIEW email is draftable. A missing or
    # unknown status refuses rather than proceeding — the gate-safe contract is that
    # automation can never act on a status it doesn't recognise.
    if status not in DRAFTABLE_STATUSES:
        die(f'REFUSE: EmailTask #{args.email_task_id} is {status!r}, not DRAFT/REVIEW — not draftable.', rc=3)

    # Write the body (+ subject) and move DRAFT -> REVIEW in one PATCH. The server
    # blocks any status past REVIEW, so this can never arm or send.
    cli_args = ['email-task-set', args.email_task_id, *body_args, '--status', TARGET_STATUS]
    if args.subject is not None:
        cli_args += ['--subject', args.subject]
    rc, out, err = run_cli(*cli_args)
    if rc != 0:
        die(f'failed to draft #{args.email_task_id} (rc={rc}): {err.strip() or out.strip()}', rc=1)

    print(f'OK: #{args.email_task_id} body written, moved DRAFT -> {TARGET_STATUS}. '
          f'A human confirms it (sets the send time) to send — nothing is emailed before that.')


def cmd_summary(args: argparse.Namespace) -> None:
    task = _get_email_task(args.email_task_id)
    summary = task.get('lead_summary') or {}
    who = summary.get('company_name') or summary.get('person_name') or task.get('to_email', '(no recipient)')
    print(f'#{task.get("id")} {task.get("subject", "")!r} — status={task.get("status")} '
          f'customer={who} send_at={task.get("send_at") or "-"}')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='sales_followup', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('list', help='DRAFT emails awaiting a body')
    p.add_argument('--json', action='store_true', help='Raw JSON output')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('draft', help='Write the body + move DRAFT -> REVIEW')
    p.add_argument('email_task_id', type=int)
    p.add_argument('--subject', help='Email subject')
    p.add_argument('--body', help='Email body (the verbatim email)')
    p.add_argument('--body-file', help='Read the email body from a file')
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser('summary', help='Status + subject + customer')
    p.add_argument('email_task_id', type=int)
    p.set_defaults(func=cmd_summary)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
