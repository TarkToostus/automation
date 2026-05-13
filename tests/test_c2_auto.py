"""Deterministic tests for c2_auto.py.

All tark_cli subprocess calls are stubbed via monkeypatching `run_cli`.
No network, no daemon, no real tasks touched. Each test asserts:
  - exit code
  - which CLI calls were made (in order)
  - what stdout/stderr surfaced

Run: cd /Users/martin/_tark/automation && python -m pytest tests/test_c2_auto.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import c2_auto  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_BRIEF = """\
# Some title

## Problem
Lorem ipsum.

## Goals
1. Foo.

## Non-Goals
- Bar.

## Acceptance Stories
### AS1: happy path
1. Click X.

## Constraints
- No PII leak.
"""

VALID_PLAN = """\
# Plan

## Architecture
Data flows X → Y.

## Pattern Reference
- `frontend/src/pages/Foo/FooList.tsx`

## Migration Order
1. Add model.
2. Add API.

## Risks
- Migration drift.
"""


@pytest.fixture
def stub_cli():
    """Patch run_cli with a recording stub.

    The stub returns programmable responses by command shape. Tests configure
    `stub.responses[(positional_args_tuple)] = (rc, stdout, stderr)`. Calls
    are recorded into `stub.calls` for assertion.

    Default for unmatched calls: (0, "", "") — success with empty output.
    """
    class Stub:
        def __init__(self):
            self.calls: list[tuple] = []
            self.responses: dict[tuple, tuple[int, str, str]] = {}

        def __call__(self, *args, json_mode: bool = False):
            key = (json_mode,) + tuple(str(a) for a in args)
            self.calls.append(key)
            return self.responses.get(key, (0, "", ""))

    stub = Stub()
    with patch.object(c2_auto, "run_cli", stub):
        yield stub


def make_task(
    task_id: int = 4280,
    column_name: str = "IDEA",
    stage: str = "brief",
    board: int = 48,
    wiki: str = "",
    name: str = "Test task",
) -> dict:
    return {
        "id": task_id,
        "board": board,
        "column": c2_auto.COLUMN_IDS.get(column_name, 0),
        "column_name": column_name,
        "stage": stage,
        "name": name,
        "wiki": wiki,
    }


def expect_call(stub, *args, json_mode: bool = False):
    key = (json_mode,) + tuple(str(a) for a in args)
    assert key in stub.calls, (
        f"expected CLI call {key!r} not made.\nactual calls: {stub.calls}"
    )


def run_cmd(*argv) -> int:
    """Invoke main() with argv; return SystemExit code."""
    parser = c2_auto.build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def test_preflight_idea_is_ok(stub_cli, capsys):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA")), "",
    )
    rc = run_cmd("preflight", "4280")
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out and "IDEA" in out


def test_preflight_todo_is_ok(stub_cli, capsys):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="TODO")), "",
    )
    rc = run_cmd("preflight", "4280")
    assert rc == 0


def test_preflight_plan_review_requires_confirm(stub_cli, capsys):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="PLAN_REVIEW")), "",
    )
    rc = run_cmd("preflight", "4280")
    assert rc == 2
    out = capsys.readouterr().out
    assert "CONFIRM_REQUIRED" in out


@pytest.mark.parametrize("col", sorted(c2_auto.DAEMON_OWNED))
def test_preflight_refuses_daemon_owned(stub_cli, capsys, col):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name=col)), "",
    )
    rc = run_cmd("preflight", "4280")
    assert rc == 3
    err = capsys.readouterr().err
    assert "REFUSE" in err and col in err


@pytest.mark.parametrize("col", ["WORK", "REVIEW", "TEST_SUCCESS", "DONE", "FAIL", "REJECTED"])
def test_preflight_refuses_terminal_or_post_work(stub_cli, capsys, col):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name=col)), "",
    )
    rc = run_cmd("preflight", "4280")
    assert rc == 3


def test_preflight_refuses_off_board(stub_cli, capsys):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA", board=47)), "",
    )
    rc = run_cmd("preflight", "4280")
    assert rc == 3
    err = capsys.readouterr().err
    assert "board 47" in err


# ---------------------------------------------------------------------------
# promote — validation
# ---------------------------------------------------------------------------

def test_promote_rejects_brief_missing_sections(stub_cli, capsys, tmp_path):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text("## Problem\nOnly this section.\n")
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "brief is missing required sections" in err
    # No wiki/stage/column writes should have happened.
    for call in stub_cli.calls:
        assert "wiki" not in call
        assert "stage" not in call
        assert "update" not in call


def test_promote_rejects_plan_missing_sections(stub_cli, capsys, tmp_path):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text("## Architecture\nThin.\n")

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "plan is missing required sections" in err


def test_promote_refuses_when_daemon_owned(stub_cli, capsys, tmp_path):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IN_PLAN")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "daemon owns" in err


def test_promote_refuses_plan_review_without_force(stub_cli, capsys, tmp_path):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="PLAN_REVIEW")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "force-overwrite" in err


# ---------------------------------------------------------------------------
# promote — happy path
# ---------------------------------------------------------------------------

def test_promote_happy_path_from_idea(stub_cli, capsys, tmp_path):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA", stage="brief")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 0, capsys.readouterr().err

    # Order of wiki writes: Brief, Plan, Review: Plan
    section_writes = [
        c for c in stub_cli.calls
        if len(c) >= 4 and c[1] == "wiki" and c[3] == "set"
    ]
    assert len(section_writes) == 3
    sections = [c[5] for c in section_writes]  # --section value
    assert sections == ["Brief", "Plan", "Review: Plan"]

    # Stage advance: brief → plan → review_plan → work
    stage_calls = [c for c in stub_cli.calls if len(c) >= 2 and c[1] == "stage"]
    advanced = [c[3] for c in stage_calls]
    assert advanced == ["plan", "review_plan", "work"]

    # Column move at end
    update_calls = [c for c in stub_cli.calls if len(c) >= 2 and c[1] == "update"]
    assert len(update_calls) == 1
    assert update_calls[0][-1] == str(c2_auto.COLUMN_IDS["WORK"])

    # The column update must come AFTER the last stage call.
    last_stage_idx = max(i for i, c in enumerate(stub_cli.calls) if c[1] == "stage")
    update_idx = next(i for i, c in enumerate(stub_cli.calls) if c[1] == "update")
    assert update_idx > last_stage_idx


def test_promote_skips_stage_when_already_past_work(stub_cli, capsys, tmp_path):
    # If task somehow has stage past 'work' (would only happen if user is
    # repairing state), don't run backward stage calls.
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA", stage="verify")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 0
    stage_calls = [c for c in stub_cli.calls if c[1] == "stage"]
    assert stage_calls == []  # none — stage already past 'work'
    out = capsys.readouterr().out
    assert "stage already at verify" in out


def test_promote_from_review_plan_with_force(stub_cli, capsys, tmp_path):
    # Resuming at PLAN_REVIEW means PlanEngine ran: stage is review_plan.
    # Only the final stage call (review_plan → work) is needed.
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="PLAN_REVIEW", stage="review_plan")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
        "--force-overwrite",
    )
    assert rc == 0
    stage_calls = [c for c in stub_cli.calls if c[1] == "stage"]
    advanced = [c[3] for c in stage_calls]
    assert advanced == ["work"]


def test_promote_aborts_on_wiki_write_failure(stub_cli, capsys, tmp_path):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA")), "",
    )
    # Make the first wiki set fail.
    stub_cli.responses[
        (False, "wiki", "4280", "set", "--section", "Brief", "--body", VALID_BRIEF)
    ] = (1, "", "wiki API timeout")
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 1
    # Stage advance and column move must not have been attempted.
    assert not any(c[1] == "stage" for c in stub_cli.calls)
    assert not any(c[1] == "update" for c in stub_cli.calls)


# ---------------------------------------------------------------------------
# status (column move)
# ---------------------------------------------------------------------------

def test_status_refuses_daemon_column(stub_cli, capsys):
    rc = run_cmd("status", "4280", "in_plan")
    assert rc == 3
    err = capsys.readouterr().err
    assert "daemon-owned" in err
    # Must NOT have made any CLI calls (fail fast before fetch).
    assert stub_cli.calls == []


def test_status_unknown_column(stub_cli, capsys):
    rc = run_cmd("status", "4280", "banana")
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown column" in err


def test_status_happy_path(stub_cli, capsys):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="FAIL")), "",
    )
    rc = run_cmd("status", "4280", "todo")
    assert rc == 0
    update = [c for c in stub_cli.calls if c[1] == "update"]
    assert len(update) == 1
    assert update[0][-1] == str(c2_auto.COLUMN_IDS["TODO"])


def test_status_case_insensitive(stub_cli, capsys):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA")), "",
    )
    rc = run_cmd("status", "4280", "TODO")
    assert rc == 0


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def test_create_default_lands_on_idea(stub_cli, capsys):
    stub_cli.responses[
        (True, "create", "Autopilot", "Add foo", "--board", "Sidecar")
    ] = (0, json.dumps({"id": 9999, "column_name": "IDEA"}), "")
    rc = run_cmd("create", "Add", "foo")
    assert rc == 0
    # No update call when --todo is absent.
    assert not any(c[1] == "update" for c in stub_cli.calls)
    out = capsys.readouterr().out
    assert "#9999 on IDEA" in out
    assert "/board/48/tasks/9999" in out


def test_create_with_todo_moves_to_todo_column(stub_cli, capsys):
    stub_cli.responses[
        (True, "create", "Autopilot", "Add foo", "--board", "Sidecar")
    ] = (0, json.dumps({"id": 9999}), "")
    rc = run_cmd("create", "--todo", "Add", "foo")
    assert rc == 0
    update = [c for c in stub_cli.calls if c[1] == "update"]
    assert len(update) == 1
    assert update[0][-1] == str(c2_auto.COLUMN_IDS["TODO"])


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_finds_last_section(stub_cli, capsys):
    wiki = (
        "## Brief\nFoo\n\n## Plan\nBar\n\n## Plan Confirmation\nBaz\n"
    )
    stub_cli.responses[(True, "task", "4280")] = (
        0,
        json.dumps(make_task(column_name="PLAN_REVIEW", stage="review_plan", wiki=wiki)),
        "",
    )
    rc = run_cmd("summary", "4280")
    assert rc == 0
    out = capsys.readouterr().out
    assert "PLAN_REVIEW" in out
    assert "review_plan" in out
    assert "## Plan Confirmation" in out


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_column_ids_match_daemon_canonical():
    """Sanity check: column IDs match orchestrator/runners/proof/c2_poller.py defaults."""
    expected = {
        "IDEA": 226, "TODO": 227, "IN_PLAN": 228, "PLAN_REVIEW": 229,
        "WORK": 230, "IN_PROGRESS": 231, "REVIEW": 232,
        "TEST_PRELIVE": 233, "TEST_SUCCESS": 234,
        "DONE": 235, "FAIL": 236, "REJECTED": 237,
    }
    assert c2_auto.COLUMN_IDS == expected


def test_daemon_owned_columns_are_not_human_settable():
    """Sets must be disjoint — humans should never be able to set daemon columns."""
    assert c2_auto.DAEMON_OWNED.isdisjoint(c2_auto.HUMAN_SETTABLE)


def test_promote_ok_subset_of_known_columns():
    assert c2_auto.PROMOTE_OK.issubset(set(c2_auto.COLUMN_IDS))
    assert c2_auto.PROMOTE_CONFIRM.issubset(set(c2_auto.COLUMN_IDS))


def test_stage_order_contains_work():
    assert "work" in c2_auto.STAGE_ORDER
    assert c2_auto.STAGE_ORDER[0] == "brief"


# ---------------------------------------------------------------------------
# Ordering invariants
# ---------------------------------------------------------------------------

def test_promote_writes_wiki_before_advancing_stage(stub_cli, tmp_path):
    """Server stage-gate requires the wiki section to exist BEFORE advance.

    If the script ever reorders so stage runs before wiki, the server returns
    400 missing_section and the task is left in a half-applied state. This
    test pins the order: all wiki writes must complete before the first
    stage call.
    """
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA", stage="brief")), "",
    )
    brief_path = tmp_path / "b.md"
    brief_path.write_text(VALID_BRIEF)
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)

    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(brief_path),
        "--plan-file", str(plan_path),
    )
    assert rc == 0
    wiki_indices = [i for i, c in enumerate(stub_cli.calls) if c[1] == "wiki"]
    stage_indices = [i for i, c in enumerate(stub_cli.calls) if c[1] == "stage"]
    assert wiki_indices and stage_indices, "expected wiki and stage calls"
    assert max(wiki_indices) < min(stage_indices), (
        f"wiki must precede stage; got wiki@{wiki_indices} stage@{stage_indices}"
    )


# ---------------------------------------------------------------------------
# HUMAN_SETTABLE correctness — review/test_success are read-only from human side
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("daemon_set_col", ["review", "test_success", "in_plan", "in_progress", "test_prelive"])
def test_status_refuses_daemon_only_destinations(stub_cli, capsys, daemon_set_col):
    """Humans must not push tasks INTO daemon-set columns.

    REVIEW is entered by WorkEngine.on_done; TEST_SUCCESS by PreviewVerifyEngine.
    Allowing manual destination there would confuse the poller and skip the
    PR/prelive verification it's meant to gate on.
    """
    rc = run_cmd("status", "4280", daemon_set_col)
    assert rc == 3, f"expected refusal for {daemon_set_col!r}"
    err = capsys.readouterr().err
    assert "daemon" in err.lower() or daemon_set_col.upper() in err


def test_human_settable_excludes_daemon_destinations():
    forbidden = {"REVIEW", "TEST_SUCCESS"} | c2_auto.DAEMON_OWNED
    assert forbidden.isdisjoint(c2_auto.HUMAN_SETTABLE)


# ---------------------------------------------------------------------------
# Robustness — bad inputs, network/timeout, malformed task
# ---------------------------------------------------------------------------

def test_promote_handles_missing_brief_file(stub_cli, capsys, tmp_path):
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps(make_task(column_name="IDEA")), "",
    )
    plan_path = tmp_path / "p.md"
    plan_path.write_text(VALID_PLAN)
    rc = run_cmd(
        "promote", "4280",
        "--brief-file", str(tmp_path / "does-not-exist.md"),
        "--plan-file", str(plan_path),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot read" in err and "brief-file" in err
    # No wiki/stage/update writes happened.
    assert not any(c[1] in ("wiki", "stage", "update") for c in stub_cli.calls)


def test_assert_on_sidecar_refuses_null_board(stub_cli, capsys):
    """A task with no `board` field is malformed — refuse rather than guess."""
    stub_cli.responses[(True, "task", "4280")] = (
        0, json.dumps({"id": 4280, "column_name": "IDEA", "stage": "brief", "board": None}), "",
    )
    rc = run_cmd("preflight", "4280")
    assert rc == 3
    err = capsys.readouterr().err
    assert "None" in err


def test_run_cli_times_out_on_hang(monkeypatch, capsys):
    """tark_cli hang must surface as rc=124 + clear error, not hang the caller."""
    import subprocess as sp

    def fake_run(*args, **kwargs):
        # Simulate hang by raising TimeoutExpired (what subprocess.run raises
        # when its timeout= kwarg fires).
        raise sp.TimeoutExpired(cmd=args[0] if args else "cmd", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(c2_auto.subprocess, "run", fake_run)
    rc, out, err = c2_auto.run_cli("task", "4280", json_mode=True)
    assert rc == 124
    assert "timed out" in err
