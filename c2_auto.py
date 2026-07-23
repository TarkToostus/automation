#!/usr/bin/env python3
"""c2_auto.py — deterministic ops for the /c2-auto skill.

The /c2-auto.md skill is high-level guidance for Claude (when to refuse,
what shape Brief/Plan take, how to handle PLAN_REVIEW overwrite).
This script holds the mechanical pieces: column lookups, stage-advance
ordering, column-race preflight, section validation, wiki + column writes.

Skill calls this; this calls tark_cli. No safety bypass — tark_cli's
safety checks stay on (per memory: feedback_never_propose_cutting_safety).

Exit codes:
    0 — success
    1 — generic error (printed reason on stderr)
    2 — needs explicit user confirmation (currently: PLAN_REVIEW overwrite)
    3 — refused — task in unsupported state for the requested verb

Subcommands:
    create [--todo] <subject>...    Create on Autopilot/Sidecar (IDEA by default).
    preflight <task_id>             Check column for promote eligibility.
    promote <task_id>               Write Brief+Plan+Review:Plan + advance + WORK.
        --brief-file <path>         Brief markdown (must contain required sections).
        --plan-file <path>          Plan markdown (must contain required sections).
        --force-overwrite           Allow PLAN_REVIEW overwrite (skill must confirm).
        --wiki-only                 Write the wiki sections but DON'T advance/move
                                    (wiki-fill is free; the WORK move is the gate).
    status <task_id> <column>       Move to a human-owned column by name.
    summary <task_id>               Print column, stage, last wiki section.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

TARK_CLI = os.environ.get("TARK_CLI", str(Path.home() / "bin" / "tark_cli"))
TARK_CLI_TIMEOUT = int(os.environ.get("C2_AUTO_TARK_CLI_TIMEOUT", "60"))


def _base_url() -> str:
    """Deployment base URL from $TARK_URL/$C2_URL or ~/.config/tark/config.json (no
    hardcoded domain). Empty when unset, so callers print a relative path instead."""
    url = os.environ.get("TARK_URL") or os.environ.get("C2_URL", "")
    if not url:
        cfg = Path.home() / ".config" / "tark" / "config.json"
        if cfg.exists():
            try:
                url = json.loads(cfg.read_text()).get("url", "")
            except (json.JSONDecodeError, OSError):
                url = ""
    return url.rstrip("/")
SIDECAR_PROJECT_ID = 28
SIDECAR_BOARD_ID = 48

# Hardcoded column IDs (board 48, defaults from orchestrator/runners/proof/c2_poller.py).
# Stable in production. If the board is ever rebuilt these need bumping in lockstep
# with the daemon's c2_poller.py constants.
COLUMN_IDS = {
    "IDEA": 226,
    "TODO": 227,
    "IN_PLAN": 228,
    "PLAN_REVIEW": 229,
    "WORK": 230,
    "IN_PROGRESS": 231,
    "REVIEW": 232,
    "TEST_PRELIVE": 233,
    "TEST_SUCCESS": 234,
    "DONE": 235,
    "FAIL": 236,
    "REJECTED": 237,
}

DAEMON_OWNED = {"IN_PLAN", "IN_PROGRESS", "TEST_PRELIVE"}
# Columns humans set as destinations. REVIEW and TEST_SUCCESS are read-only
# from the human side — daemon (WorkEngine.on_done, PreviewVerifyEngine)
# enters them. Humans ACT on items in those columns (merge / accept) but
# don't move tasks INTO them.
HUMAN_SETTABLE = {"IDEA", "TODO", "WORK", "DONE", "FAIL", "REJECTED"}
PROMOTE_OK = {"IDEA", "TODO"}
PROMOTE_CONFIRM = {"PLAN_REVIEW"}

STAGE_ORDER = [
    "brief", "plan", "review_plan", "work",
    "verify", "review_impl", "document", "commit", "deploy",
]

REQUIRED_BRIEF_SECTIONS = (
    "## Problem",
    "## Goals",
    "## Non-Goals",
    "## Acceptance Stories",
    "## Constraints",
)
REQUIRED_PLAN_SECTIONS = (
    "## Architecture",
    "## Pattern Reference",
    "## Migration Order",
    "## Risks",
)


# ---------------------------------------------------------------------------
# tark_cli wrapper (kept thin so tests can monkeypatch)
# ---------------------------------------------------------------------------

def run_cli(*args: object, json_mode: bool = False) -> tuple[int, str, str]:
    """Invoke tark_cli. Returns (rc, stdout, stderr).

    Times out at TARK_CLI_TIMEOUT seconds (default 60) — gemini safety can be
    slow, but indefinite hangs leave the calling chat session stuck. On timeout
    returns (124, "", "tark_cli timed out after Ns") — caller surfaces it like
    any other CLI failure. Per memory feedback_never_propose_cutting_safety:
    the answer to slowness is timeout/retry, not skipping the safety check.
    """
    cmd: list[str] = [TARK_CLI]
    if json_mode:
        cmd.append("--json")
    cmd.extend(str(a) for a in args)
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TARK_CLI_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"tark_cli timed out after {TARK_CLI_TIMEOUT}s: {' '.join(cmd[1:])}"
    return r.returncode, r.stdout, r.stderr


def fetch_task(task_id: int) -> dict:
    rc, out, err = run_cli("task", task_id, json_mode=True)
    if rc != 0:
        die(f"tark_cli task {task_id} failed (rc={rc}): {err.strip() or out.strip()}", rc=1)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        die(f"task JSON parse failed: {exc}\nraw: {out[:500]}", rc=1)


def die(msg: str, rc: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(rc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def column_name_of(task: dict) -> str:
    name = task.get("column_name") or ""
    if not name and isinstance(task.get("column"), dict):
        name = task["column"].get("name", "") or ""
    return name


def stage_of(task: dict) -> str:
    s = task.get("stage") or "brief"
    return s if s in STAGE_ORDER else "brief"


def assert_on_sidecar_board(task: dict) -> None:
    """Refuse if the task lives on a board other than Sidecar (48).

    /c2-auto is opinionated about board 48. If a task got created on
    board 47 "Main" or elsewhere, the human-settable column IDs are
    different and column moves would silently mistarget. Better to
    refuse than to corrupt state.

    The task-detail API emits `board_id` (with `board_name` alongside);
    older shapes used `board`. Accept either. Missing/null in both keys is
    treated as an error — a task without a board is malformed and we can't
    safely target columns.
    """
    board = task.get("board")
    if board is None:
        board = task.get("board_id")
    if board != SIDECAR_BOARD_ID:
        die(
            f"REFUSE: task #{task.get('id')} is on board {board!r} "
            f"(not Sidecar={SIDECAR_BOARD_ID}). /c2-auto only targets the Sidecar board.",
            rc=3,
        )


def validate_required_sections(label: str, body: str, required: tuple[str, ...]) -> list[str]:
    return [s for s in required if s not in body]


def demote_inner_headings(body: str) -> str:
    """Demote top-level (## ) headings in a Brief/Plan body to ### .

    The daemon WorkEngine reads a section as the text between '## <Section>' and
    the NEXT '## ' line (daemon/cli_tools/c2.py:_extract_section, regex
    ``^## Brief\\s*\\n(.*?)(?=^## |\\Z)``). A brief/plan body whose sub-sections
    are '## Problem', '## Goals', ... renders them as SIBLING top-level h2 in the
    wiki, so '## Brief' captures an empty body and the worker builds blind from
    the task title (the #4455 failure). Demoting the inner headings to '### '
    keeps them nested inside the section so the whole body is captured.

    Fenced code blocks are skipped — a '## ' inside a ``` / ~~~ block is content,
    not a heading. h1 ('# ') is left alone (it does not terminate the regex).
    """
    out: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            line = "#" + line  # '## X' -> '### X'
        out.append(line)
    result = "\n".join(out)
    if body.endswith("\n"):
        result += "\n"
    return result


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_create(args: argparse.Namespace) -> None:
    subject = " ".join(args.subject).strip()
    if not subject:
        die("create: subject required", rc=1)

    # Pass project + board by ID (stable) rather than name. The "Sidecar" board
    # was renamed to "c2-auto" 2026-05-13; the "Autopilot" project could be
    # renamed too. IDs stay. If either ever moves, bump the constants at the
    # top of this file (SIDECAR_PROJECT_ID / SIDECAR_BOARD_ID).
    rc, out, err = run_cli(
        "create", str(SIDECAR_PROJECT_ID), subject,
        "--board", str(SIDECAR_BOARD_ID), json_mode=True
    )
    if rc != 0:
        die(f"tark_cli create failed (rc={rc}): {err.strip() or out.strip()}", rc=1)

    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        die(f"create JSON parse failed: {exc}\nraw: {out[:500]}", rc=1)

    task_id = data["id"]
    landed = "IDEA"

    if args.todo:
        rc2, _, err2 = run_cli("update", task_id, "--column", COLUMN_IDS["TODO"])
        if rc2 != 0:
            die(
                f"created #{task_id} on IDEA but TODO move failed (rc={rc2}): {err2.strip()}",
                rc=1,
            )
        landed = "TODO"

    print(f"OK: #{task_id} on {landed}")
    base = _base_url()
    path = (
        f"/project-management/plan/pm-projects/"
        f"{SIDECAR_PROJECT_ID}/board/{SIDECAR_BOARD_ID}/tasks/{task_id}"
    )
    print(f"{base}{path}" if base else path)
    if landed == "TODO":
        print("Daemon PlanEngine claims within 15s (PROOF_POLL_INTERVAL_SEC).")


def cmd_preflight(args: argparse.Namespace) -> None:
    """Print column eligibility. Exit codes communicate the verdict."""
    task = fetch_task(args.task_id)
    assert_on_sidecar_board(task)
    col = column_name_of(task)

    if col in PROMOTE_OK:
        print(f"OK: column={col} — safe to promote")
        sys.exit(0)
    if col in PROMOTE_CONFIRM:
        print(
            f"CONFIRM_REQUIRED: column={col} — daemon plan exists; "
            f"promote will OVERWRITE ## Brief / ## Plan written by PlanEngine"
        )
        sys.exit(2)
    if col in DAEMON_OWNED:
        die(
            f"REFUSE: column={col} — daemon owns this task. "
            f"Wait for the engine to release it, or move to REJECTED first.",
            rc=3,
        )
    die(
        f"REFUSE: column={col} — promote only allowed from IDEA / TODO "
        f"(or PLAN_REVIEW with --force-overwrite). Use status to re-position first.",
        rc=3,
    )


def cmd_promote(args: argparse.Namespace) -> None:
    task = fetch_task(args.task_id)
    assert_on_sidecar_board(task)
    col = column_name_of(task)

    # Re-check column eligibility (don't trust caller's preflight).
    if col in DAEMON_OWNED:
        die(f"REFUSE: column={col} — daemon owns this task", rc=3)
    if col in PROMOTE_CONFIRM and not args.force_overwrite:
        die(
            f"REFUSE: column={col} — pass --force-overwrite to overwrite daemon plan",
            rc=2,
        )
    if col not in PROMOTE_OK | PROMOTE_CONFIRM:
        die(
            f"REFUSE: column={col} — promote allowed from IDEA / TODO / PLAN_REVIEW only",
            rc=3,
        )

    # Validate content files exist + contain required sections.
    try:
        brief = Path(args.brief_file).read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        die(f"REFUSE: cannot read --brief-file {args.brief_file!r}: {exc}", rc=1)
    try:
        plan = Path(args.plan_file).read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        die(f"REFUSE: cannot read --plan-file {args.plan_file!r}: {exc}", rc=1)

    missing_brief = validate_required_sections("Brief", brief, REQUIRED_BRIEF_SECTIONS)
    if missing_brief:
        die(
            f"REFUSE: brief is missing required sections: {missing_brief}. "
            f"See /brief.md template.",
            rc=1,
        )
    missing_plan = validate_required_sections("Plan", plan, REQUIRED_PLAN_SECTIONS)
    if missing_plan:
        die(
            f"REFUSE: plan is missing required sections: {missing_plan}. "
            f"See /plan.md template.",
            rc=1,
        )

    review_body = (
        f"Skipped — planned in chat session by Martin on {date.today().isoformat()}.\n\n"
        "Plan confirmed at chat-write time; no separate adversarial review pass."
    )

    # Demote inner '## ' sub-headings to '### ' so the daemon WorkEngine can read
    # the ## Brief / ## Plan bodies (see demote_inner_headings — the #4455 fix).
    # Validation above ran on the original h2 content, so this is safe here.
    brief = demote_inner_headings(brief)
    plan = demote_inner_headings(plan)

    # Write three wiki sections (set = upsert).
    for section, body in (("Brief", brief), ("Plan", plan), ("Review: Plan", review_body)):
        rc, out, err = run_cli(
            "wiki", args.task_id, "set", "--section", section, "--body", body
        )
        if rc != 0:
            die(
                f"wiki set {section!r} failed (rc={rc}): {err.strip() or out.strip()}",
                rc=1,
            )

    # --wiki-only: the wiki fill is "free" (the analysis already lives in the
    # chat — write it down), but the move to WORK is the human plan-confirm gate.
    # Stop here without advancing the stage or moving the column. Re-running
    # promote without --wiki-only later is idempotent on the wiki (upsert) and
    # performs the canonical advance + move. See /c2-auto "wiki-fill is free".
    if getattr(args, "wiki_only", False):
        brief_lead = brief.lstrip().splitlines()[0][:80]
        plan_lead = plan.lstrip().splitlines()[0][:80]
        print(f"OK: #{args.task_id} wiki filled (Brief + Plan + Review: Plan); stays on {col} — NOT promoted")
        print(f"Brief: {brief_lead}")
        print(f"Plan:  {plan_lead}")
        print("Promote to WORK after confirm (same args, drop --wiki-only).")
        return

    # Forward-only stage advance: jump from current stage up to 'work'.
    cur_stage = stage_of(task)
    target_idx = STAGE_ORDER.index("work")
    cur_idx = STAGE_ORDER.index(cur_stage)
    if cur_idx >= target_idx:
        print(f"INFO: stage already at {cur_stage} (>= work) — skipping stage advance")
    else:
        for stage_name in STAGE_ORDER[cur_idx + 1 : target_idx + 1]:
            rc, out, err = run_cli("stage", args.task_id, stage_name)
            if rc != 0:
                die(
                    f"stage advance {cur_stage} → {stage_name} failed (rc={rc}): "
                    f"{err.strip() or out.strip()}",
                    rc=1,
                )

    # Move column to WORK.
    rc, out, err = run_cli("update", args.task_id, "--column", COLUMN_IDS["WORK"])
    if rc != 0:
        die(
            f"column move to WORK failed (rc={rc}): {err.strip() or out.strip()}",
            rc=1,
        )

    brief_lead = brief.lstrip().splitlines()[0][:80]
    plan_lead = plan.lstrip().splitlines()[0][:80]
    print(f"OK: #{args.task_id} promoted to WORK (column={COLUMN_IDS['WORK']})")
    print(f"Brief: {brief_lead}")
    print(f"Plan:  {plan_lead}")
    print("Daemon WorkEngine claims within 15s.")


def cmd_status(args: argparse.Namespace) -> None:
    name = args.column.upper()
    if name not in COLUMN_IDS:
        die(f"unknown column: {args.column!r}", rc=1)
    if name not in HUMAN_SETTABLE:
        die(
            f"REFUSE: {name} is daemon-owned — humans cannot set this manually. "
            f"Human-settable: {sorted(HUMAN_SETTABLE)}",
            rc=3,
        )
    task = fetch_task(args.task_id)
    assert_on_sidecar_board(task)
    rc, out, err = run_cli("update", args.task_id, "--column", COLUMN_IDS[name])
    if rc != 0:
        die(f"column move failed (rc={rc}): {err.strip() or out.strip()}", rc=1)
    print(f"OK: #{args.task_id} → {name} ({COLUMN_IDS[name]})")


def cmd_summary(args: argparse.Namespace) -> None:
    task = fetch_task(args.task_id)
    name = task.get("name") or ""
    col = column_name_of(task)
    stage = stage_of(task)
    wiki = task.get("wiki") or ""
    # Find last "## " section header for at-a-glance "where are we?"
    last_section = ""
    for line in wiki.splitlines():
        if line.startswith("## "):
            last_section = line
    print(f"#{args.task_id} {name}")
    board = task.get("board") if task.get("board") is not None else task.get("board_id")
    print(f"Column: {col}    Stage: {stage}    Board: {board}")
    if last_section:
        print(f"Last wiki section: {last_section}")


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="c2_auto", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    cr = sub.add_parser("create", help="Create on Autopilot/Sidecar")
    cr.add_argument("--todo", action="store_true", help="Move to TODO after create")
    cr.add_argument("subject", nargs="+")
    cr.set_defaults(func=cmd_create)

    pf = sub.add_parser("preflight", help="Check column eligibility for promote")
    pf.add_argument("task_id", type=int)
    pf.set_defaults(func=cmd_preflight)

    pr = sub.add_parser("promote", help="Write Brief+Plan+Review:Plan + → WORK")
    pr.add_argument("task_id", type=int)
    pr.add_argument("--brief-file", required=True)
    pr.add_argument("--plan-file", required=True)
    pr.add_argument(
        "--force-overwrite", action="store_true",
        help="Allow promote from PLAN_REVIEW (overwrites daemon plan)",
    )
    pr.add_argument(
        "--wiki-only", action="store_true",
        help="Write Brief/Plan/Review:Plan to the wiki but do NOT advance stage "
             "or move to WORK (the WORK move is the human plan-confirm gate)",
    )
    pr.set_defaults(func=cmd_promote)

    st = sub.add_parser("status", help="Move to a human-owned column by name")
    st.add_argument("task_id", type=int)
    st.add_argument("column")
    st.set_defaults(func=cmd_status)

    sm = sub.add_parser("summary", help="Print column / stage / last wiki section")
    sm.add_argument("task_id", type=int)
    sm.set_defaults(func=cmd_summary)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
