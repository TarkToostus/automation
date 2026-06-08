"""Deterministic tests for sales_followup.py — the gate-safe drafting helper.

All tark_cli subprocess calls are stubbed via monkeypatching `run_cli` (mirrors
tests/test_c2_auto.py). No network, no real EmailTasks touched. The point of this
suite is the SAFETY GATE: automation may only ever move an email DRAFT -> REVIEW,
and must REFUSE (and never PATCH) anything else.

Run: cd /Users/martin/_tark/automation && python -m pytest tests/test_sales_followup.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import sales_followup  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_cli():
    """Patch run_cli with a recording stub.

    `stub.responses[(json_mode, *str_args)] = (rc, stdout, stderr)` programs a
    reply; everything else defaults to (0, "", "") success. Calls are recorded
    into `stub.calls` for assertion.
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
    with patch.object(sales_followup, "run_cli", stub):
        yield stub


def set_task(stub, task_id: int, status: str, **extra):
    """Program the `api sales/email-tasks/<id>` JSON fetch to return a task."""
    task = {"id": task_id, "status": status, "subject": "Hi", **extra}
    stub.responses[(True, "api", f"sales/email-tasks/{task_id}")] = (
        0, json.dumps(task), "",
    )


def run_cmd(*argv) -> int:
    """Invoke sales_followup via its parser; return the SystemExit code."""
    args = sales_followup.build_parser().parse_args([str(a) for a in argv])
    try:
        args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def patched_calls(stub) -> list[tuple]:
    return [c for c in stub.calls if "email-task-set" in c]


# ---------------------------------------------------------------------------
# The gate: refuse everything past REVIEW, and NEVER issue the PATCH
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", sorted(sales_followup.FORBIDDEN_STATUSES))
def test_draft_refuses_forbidden_status(stub_cli, capsys, status):
    set_task(stub_cli, 123, status)
    rc = run_cmd("draft", 123, "--body", "hello")
    assert rc == 3, f"{status} must refuse with rc=3"
    assert "REFUSE" in capsys.readouterr().err
    assert patched_calls(stub_cli) == [], (
        f"GATE BREACH: a {status} email was PATCHed by automation"
    )


@pytest.mark.parametrize("status", ["", "QUEUED", "UNKNOWN", "draft"])
def test_draft_fails_closed_on_unknown_status(stub_cli, capsys, status):
    """A status that isn't exactly DRAFT/REVIEW (incl. empty/missing) must refuse,
    not fall through to a PATCH. This is the fail-closed regression."""
    set_task(stub_cli, 7, status)
    rc = run_cmd("draft", 7, "--body", "hello")
    assert rc == 3
    assert patched_calls(stub_cli) == []


def test_draft_refuses_when_task_missing(stub_cli, capsys):
    # No programmed response -> _cli_json returns (0, "", "") -> empty stdout.
    # _get_email_task gets no 'id' and must refuse (rc=3), not PATCH.
    stub_cli.responses[(True, "api", "sales/email-tasks/999")] = (0, "{}", "")
    rc = run_cmd("draft", 999, "--body", "hello")
    assert rc == 3
    assert patched_calls(stub_cli) == []


# ---------------------------------------------------------------------------
# The safe half: DRAFT/REVIEW -> REVIEW does go through, with the right payload
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["DRAFT", "REVIEW"])
def test_draft_happy_path_moves_to_review(stub_cli, capsys, status):
    set_task(stub_cli, 42, status)
    rc = run_cmd("draft", 42, "--subject", "Subj", "--body", "Tere!")
    assert rc == 0, capsys.readouterr().err
    calls = patched_calls(stub_cli)
    assert len(calls) == 1, calls
    call = calls[0]
    # Always moves to REVIEW, never further; carries body + subject.
    assert "--status" in call and "REVIEW" in call
    assert sales_followup.TARGET_STATUS == "REVIEW"
    assert not (set(call) & {"CONFIRMED", "SENT", "FAILED", "CANCELLED"})
    assert "--body" in call and "Tere!" in call
    assert "--subject" in call and "Subj" in call


# ---------------------------------------------------------------------------
# --body-file: forward the path (don't slurp), and validate it exists
# ---------------------------------------------------------------------------

def test_draft_body_file_is_forwarded_not_slurped(stub_cli, capsys, tmp_path):
    f = tmp_path / "body.txt"
    f.write_text("Tere, Anna!\nKuidas läheb?", encoding="utf-8")
    set_task(stub_cli, 5, "DRAFT")
    rc = run_cmd("draft", 5, "--body-file", str(f))
    assert rc == 0, capsys.readouterr().err
    call = patched_calls(stub_cli)[0]
    assert "--body-file" in call and str(f) in call
    assert "--body" not in call, "file contents must not be slurped into argv"


def test_draft_body_file_missing_is_clean_error(stub_cli, capsys, tmp_path):
    missing = tmp_path / "nope.txt"
    set_task(stub_cli, 6, "DRAFT")
    rc = run_cmd("draft", 6, "--body-file", str(missing))
    assert rc == 1
    assert "not found" in capsys.readouterr().err
    # Refused before any fetch/PATCH.
    assert patched_calls(stub_cli) == []


def test_draft_requires_a_body(stub_cli, capsys):
    set_task(stub_cli, 8, "DRAFT")
    rc = run_cmd("draft", 8)
    assert rc == 1
    assert "needs --body" in capsys.readouterr().err
    assert patched_calls(stub_cli) == []


# ---------------------------------------------------------------------------
# list: parses the email-tasks JSON envelope (dict-with-results or bare list)
# ---------------------------------------------------------------------------

def test_list_renders_draft_tasks(stub_cli, capsys):
    payload = {"results": [
        {"id": 1, "subject": "Acme nudge", "lead_summary": {"company_name": "Acme OÜ"}},
        {"id": 2, "subject": "Follow up", "to_email": "x@y.ee"},
    ]}
    stub_cli.responses[(True, "email-tasks", "-f", "DRAFT")] = (0, json.dumps(payload), "")
    rc = run_cmd("list")
    assert rc == 0
    out = capsys.readouterr().out
    assert "Acme OÜ" in out and "#1" in out and "#2" in out


def test_list_json_mode_passthrough(stub_cli, capsys):
    payload = [{"id": 9, "subject": "Z", "status": "DRAFT"}]
    stub_cli.responses[(True, "email-tasks", "-f", "DRAFT")] = (0, json.dumps(payload), "")
    rc = run_cmd("list", "--json")
    assert rc == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == 9
