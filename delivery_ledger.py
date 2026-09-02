#!/usr/bin/env python3
"""Write a per-client, read-only delivery ledger from C2 task cards."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
import tark_cli  # noqa: E402


CLIENTS = {
    "aburg": {
        "title": "Arensburg OU (Tark Aeg)",
        "projects": [9],
        "extra_cards": [7647, 8312],
        "codes": ["ARB-AKT1", "ARB-AKT2", "ARB-WARRANTY", "ARB-MAINT"],
    },
    "ionix": {
        "title": "Ionix Systems",
        "projects": [8],
        "extra_cards": [],
        "codes": ["ION-PILOT"],
    },
    "sigma-workmaster": {
        "title": "Sigma Polymer Group (WorkMaster)",
        "projects": [18],
        "extra_cards": [],
        "codes": ["SIG-CONTRACT", "SIG-PERSONA", "SIG-ALLDEVICE"],
    },
    "hekotek": {
        "title": "Hekotek (Koskisen, Service)",
        "projects": [14, 16, 20],
        "extra_cards": [],
        "codes": ["HEK-TM"],
    },
    "akzo": {
        "title": "Akzo Nobel (Tark Kratt)",
        "projects": [13],
        "extra_cards": [],
        "codes": ["AKZ-KRATT"],
    },
    "bombay": {
        "title": "Bombay Group (pre-sales)",
        "projects": [29],
        "extra_cards": [],
        "codes": ["PRESALES"],
    },
    "sofaservice": {
        "title": "SofaService (Recipe project)",
        "projects": [7, 30],
        "extra_cards": [],
        "codes": ["SOF-RECIPE"],
    },
    "aimarcam": {
        "title": "Aimarcam (Coop Estonia via Multiresult)",
        "projects": [24],
        "extra_cards": [],
        "codes": ["AIM-PROJECT"],
    },
}

INVOICE_LEDGER = {
    # value / paid are the owner's figures (2026-09-02); amount = what is still on the table.
    # necessary=False lines are collected already or carry no money: their cards can be postponed.
    "ARB-AKT1": {
        "client": "Arensburg",
        "document": "Akt nr 1 (workforce) - SIGNED",
        "value": "22 400 EUR",
        "paid": "signed",
        "amount": "0 EUR (signed)",
        "due": "signed",
        "due_iso": None,
        "necessary": False,
    },
    "ARB-AKT2": {
        "client": "Arensburg",
        "document": "Akt nr 2 (finance + management reporting), unsigned",
        "value": "6 000 EUR",
        "paid": "0 EUR",
        "amount": "6 000 EUR",
        "due": "akt 15.09.2026, schedule 30.09.2026",
        "due_iso": "2026-09-30",
        "necessary": True,
    },
    "ARB-WARRANTY": {
        "client": "Arensburg",
        "document": "12-month warranty on delivered scope",
        "value": "0 EUR",
        "paid": "-",
        "amount": "0 EUR",
        "due": "rolling",
        "due_iso": None,
        "necessary": False,
    },
    "ARB-MAINT": {
        "client": "Arensburg",
        "document": "Lisaarendused maintenance fee",
        "value": "150 EUR/month",
        "paid": "-",
        "amount": "150 EUR/month",
        "due": "after akt 2",
        "due_iso": None,
        "necessary": False,
    },
    "SIG-CONTRACT": {
        "client": "Sigma",
        "document": "WorkMaster contract",
        "value": "34 200 EUR",
        "paid": "19 000 EUR",
        "amount": "~15 200 EUR remaining",
        "due": "overdue",
        "due_iso": "2026-04-07",
        "necessary": True,
    },
    "SIG-PERSONA": {
        "client": "Sigma",
        "document": "Persona integration (confirmed extra)",
        "value": "900 EUR",
        "paid": "0 EUR",
        "amount": "900 EUR",
        "due": "no date",
        "due_iso": None,
        "necessary": True,
    },
    "SIG-ALLDEVICE": {
        "client": "Sigma",
        "document": "AllDevice CMMS (confirmed extra)",
        "value": "500 EUR",
        "paid": "0 EUR",
        "amount": "500 EUR",
        "due": "no date",
        "due_iso": None,
        "necessary": True,
    },
    "ION-PILOT": {
        "client": "Ionix",
        "document": "Digital Traveller SaaS (pilot -> full acceptance)",
        "value": "~1 500 EUR/month",
        "paid": "0 EUR",
        "amount": "~1 500 EUR/month",
        "due": "on full acceptance",
        "due_iso": None,
        "necessary": True,
    },
    "SOF-RECIPE": {
        "client": "SofaService",
        "document": "Recipe / production module project",
        "value": "~8 000 EUR",
        "paid": "unknown",
        "amount": "~8 000 EUR",
        "due": "no date",
        "due_iso": None,
        "necessary": True,
    },
    "AIM-PROJECT": {
        "client": "Aimarcam",
        "document": "Coop retail video search project (IP owner Multiresult)",
        "value": "~25 000 EUR",
        "paid": "unknown",
        "amount": "~25 000 EUR",
        "due": "no date",
        "due_iso": None,
        "necessary": True,
    },
    "HEK-TM": {
        "client": "Hekotek",
        "document": "time & materials, monthly hours",
        "value": "hourly",
        "paid": "monthly",
        "amount": "hourly",
        "due": "monthly",
        "due_iso": None,
        "necessary": True,
    },
    "AKZ-KRATT": {
        "client": "Akzo",
        "document": "Tark Kratt contract",
        "value": "unknown",
        "paid": "unknown",
        "amount": "unknown",
        "due": "rolling",
        "due_iso": None,
        "necessary": True,
    },
    "PRESALES": {
        "client": "-",
        "document": "pre-sales",
        "value": "0 EUR",
        "paid": "-",
        "amount": "0 EUR",
        "due": "-",
        "due_iso": None,
        "necessary": False,
    },
    "NONE": {
        "client": "-",
        "document": "can be postponed",
        "value": "0 EUR",
        "paid": "-",
        "amount": "0 EUR",
        "due": "-",
        "due_iso": None,
        "necessary": False,
    },
}

OPEN_COLUMNS = {
    "DONE",
    "SHIPPED",
    "DEPLOYED",
    "TEST_SUCCESS",
    "DUPLICATE",
    "OBSOLETE",
    "REJECTED",
}
NO_DUE_ORDER = [
    "AIM-PROJECT",
    "SOF-RECIPE",
    "ION-PILOT",
    "HEK-TM",
    "AKZ-KRATT",
    "SIG-PERSONA",
    "SIG-ALLDEVICE",
]
POSTPONE_ORDER = ["ARB-AKT1", "ARB-WARRANTY", "ARB-MAINT", "PRESALES", "NONE"]
_SECTION_RE = re.compile(r"^## ([^\r\n]+?)\s*$", re.MULTILINE)
_FIELD_RE = re.compile(r"^\s*([^:\n]+):\s*(.*?)\s*$", re.MULTILINE)


def parse_sections(wiki: str) -> dict[str, str]:
    """Return exact, top-level ``## Name`` sections from markdown."""
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(wiki or ""))
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(wiki)
        sections[name] = wiki[match.end() : end].strip()
    return sections


def parse_invoice(body: str | None) -> dict[str, str]:
    """Parse the small key/value Invoice section, preserving empty fields."""
    fields = {
        "blocks": "UNSCORED",
        "client": "",
        "document": "",
        "amount": "",
        "due": "",
        "gate": "",
        "why": "",
    }
    if body is None:
        return fields
    for label, value in _FIELD_RE.findall(body):
        key = label.strip().lower()
        if key in fields:
            fields[key] = value.strip()
    if not fields["blocks"]:
        fields["blocks"] = "UNSCORED"
    return fields


def parse_wsjf(body: str | None) -> dict[str, int | float | None]:
    """Parse WSJF components and score, recalculating when no score is written."""
    names = (
        "user_business_value",
        "time_criticality",
        "risk_reduction",
        "opportunity_enablement",
        "attention_cost",
    )
    result: dict[str, int | float | None] = {name: None for name in names}
    result["score"] = None
    if body is None:
        return result
    for name in names:
        match = re.search(
            r"^\s*\|\s*" + re.escape(name) + r"\s*\|\s*(-?\d+)\s*\|", body, re.MULTILINE
        )
        if match:
            result[name] = int(match.group(1))
    score_match = re.search(
        r"\*\*\s*WSJF\s*=.*?=\s*(-?\d+(?:\.\d+)?)\s*\*\*", body, re.IGNORECASE
    )
    if score_match:
        result["score"] = float(score_match.group(1))
    elif all(result[name] is not None for name in names):
        cost = result["attention_cost"]
        numerator = sum(int(result[name]) for name in names[:-1])
        result["score"] = numerator / int(cost) if cost else None
    return result


def parse_dod(body: str | None) -> dict[str, Any]:
    first_line = next(
        (line.strip() for line in (body or "").splitlines() if line.strip()), ""
    )
    return {"present": body is not None, "first_line": first_line}


def parse_retired(body: str | None) -> str:
    return (body or "").strip()[:140]


def parse_needs_customer(body: str | None) -> str:
    if body is None:
        return ""
    match = re.search(
        r"^\s*needs_customer:\s*(.*?)\s*$", body, re.MULTILINE | re.IGNORECASE
    )
    if not match:
        return ""
    value = match.group(1).strip()
    return "" if value.lower() in {"", "no", "false", "none", "0"} else value


def amount_value(amount: str) -> int:
    """The invoice sort amount is the leading integer, ignoring separators."""
    match = re.search(r"[-~ ]*(\d[\d ]*)", amount or "")
    return int(match.group(1).replace(" ", "")) if match else 0


def invoice_info(code: str) -> dict[str, Any]:
    return INVOICE_LEDGER.get(
        code,
        {
            "client": "",
            "document": "",
            "value": "",
            "paid": "",
            "amount": "",
            "due": "",
            "due_iso": None,
            "necessary": False,
        },
    )


def is_necessary(card: dict[str, Any]) -> bool:
    """True when the card's invoice line still has money to collect."""
    code = str(card.get("invoice", {}).get("blocks", "UNSCORED"))
    return bool(invoice_info(code).get("necessary"))


def is_awaiting_acceptance(card: dict[str, Any]) -> bool:
    return (
        str(card.get("column_name", "")).strip().lower() == "review"
        and "SHIPPED" in str(card.get("retired", "")).upper()
    )


def queue_sort_key(card: dict[str, Any]) -> tuple[Any, ...]:
    """Return the delivery-queue ordering key for an eligible, open card."""
    code = str(card.get("invoice", {}).get("blocks", "UNSCORED"))
    info = invoice_info(code)
    score = card.get("wsjf", {}).get("score")
    score_value = float(score) if score is not None else -1.0
    card_id = int(card.get("id", 0))
    due = info.get("due_iso")
    depth = int(card.get("block_depth", 0) or 0)
    if due:
        simple = bool(card.get("simple"))
        return (
            0,
            due,
            depth,
            0 if simple else 1,
            -amount_value(str(info.get("amount", ""))),
            -score_value,
            card_id,
        )
    if code in NO_DUE_ORDER:
        return (1, NO_DUE_ORDER.index(code), depth, -score_value, card_id)
    if code in POSTPONE_ORDER:
        return (2, POSTPONE_ORDER.index(code), depth, -score_value, card_id)
    return (3, depth, -score_value, card_id)


def is_hour_bucket(card: dict[str, Any]) -> bool:
    """A time-and-materials card that only collects logged hours (never 'delivered')."""
    return (
        str(card.get("invoice", {}).get("blocks", "")) == "HEK-TM"
        and str(card.get("column_name", "")).strip().lower() == "in progress"
    )


def _deliverable(card: dict[str, Any]) -> bool:
    return bool(
        card.get("open")
        and not is_awaiting_acceptance(card)
        and not is_hour_bucket(card)
    )


def queue_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open cards that are necessary to collect a value, in delivery order."""
    return sorted(
        (card for card in cards if _deliverable(card) and is_necessary(card)),
        key=queue_sort_key,
    )


def postponed_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open cards whose invoice line is collected or carries no money."""
    return sorted(
        (card for card in cards if _deliverable(card) and not is_necessary(card)),
        key=queue_sort_key,
    )


def _get_task_pages(project_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = "1"
    while True:
        payload = tark_cli._get(
            "/api/v1/pat/pm/tasks/", project=str(project_id), page_size=200, page=page
        )
        if isinstance(payload, list):
            page_rows, next_url = payload, None
        else:
            page_rows = payload.get("results", [])
            next_url = payload.get("next")
        for row in page_rows:
            copied = dict(row)
            copied["project_id"] = project_id
            rows.append(copied)
        if not next_url:
            return rows
        next_page = parse_qs(urlparse(str(next_url)).query).get("page", [None])[0]
        if not next_page:
            print(
                "[WARN] C2 returned a next page without page=; stopping",
                file=sys.stderr,
            )
            return rows
        page = str(next_page)


def _project_from_card(card: dict[str, Any], fallback: int) -> int:
    project = card.get("project")
    if isinstance(project, dict):
        project = project.get("id")
    try:
        return (
            int(project)
            if project is not None
            else int(card.get("project_id", fallback))
        )
    except (TypeError, ValueError):
        return fallback


def _parse_card(row: dict[str, Any], wiki: str | None = None) -> dict[str, Any]:
    card = dict(row)
    if wiki is not None:
        card["wiki"] = wiki
    sections = parse_sections(str(card.get("wiki", "")))
    card["invoice"] = parse_invoice(sections.get("Invoice"))
    card["wsjf"] = parse_wsjf(sections.get("WSJF"))
    card["dod"] = parse_dod(sections.get("DoD"))
    card["retired"] = parse_retired(sections.get("Retired"))
    card["needs_customer"] = parse_needs_customer(sections.get("Next"))
    card["open"] = str(card.get("column_name", "")).upper() not in OPEN_COLUMNS
    card["project_id"] = _project_from_card(card, int(card.get("project_id", 0) or 0))
    attention_cost = card["wsjf"].get("attention_cost")
    card["simple"] = bool(
        invoice_info(card["invoice"]["blocks"]).get("due_iso")
        and attention_cost is not None
        and int(attention_cost) <= 2
    )
    return card


def fetch_dependencies() -> dict[int, list[int]]:
    """blocked_task -> [blocking_task, ...] for the whole tenant (one page; ~50 rows today)."""
    payload = tark_cli._get("/api/v1/pat/pm/task-dependencies/", page_size=500)
    rows = payload.get("results", []) if isinstance(payload, dict) else payload
    blocked: dict[int, list[int]] = {}
    for row in rows:
        try:
            blocked.setdefault(int(row["blocked_task"]), []).append(int(row["blocking_task"]))
        except (KeyError, TypeError, ValueError):
            continue
    return blocked


def attach_blockers(cards: list[dict[str, Any]], blocked_by: dict[int, list[int]]) -> None:
    """Set card['waits_on'] (open blockers in this set) and card['block_depth'] (longest open chain)."""
    open_ids = {int(card["id"]) for card in cards if card.get("open")}
    for card in cards:
        card["waits_on"] = sorted(
            b for b in blocked_by.get(int(card["id"]), []) if b in open_ids
        )
    by_id = {int(card["id"]): card for card in cards}
    memo: dict[int, int] = {}

    def depth(card_id: int, trail: tuple[int, ...] = ()) -> int:
        if card_id in memo:
            return memo[card_id]
        if card_id in trail:
            return 0
        waits = by_id.get(card_id, {}).get("waits_on", [])
        value = 1 + max((depth(b, trail + (card_id,)) for b in waits), default=-1)
        memo[card_id] = value
        return value

    for card in cards:
        card["block_depth"] = depth(int(card["id"]))


def fetch_client_cards(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch project lists, then only open-card details concurrently."""
    rows: list[dict[str, Any]] = []
    for project_id in config["projects"]:
        rows.extend(_get_task_pages(int(project_id)))
    known = {int(row["id"]): row for row in rows}
    fallback_project = int(config["projects"][0])
    for card_id in config.get("extra_cards", []):
        known.setdefault(
            int(card_id),
            {"id": int(card_id), "project_id": fallback_project, "_extra": True},
        )

    def detail(row: dict[str, Any]) -> dict[str, Any]:
        if not (
            str(row.get("column_name", "")).upper() not in OPEN_COLUMNS
            or row.get("_extra")
        ):
            return _parse_card(row)
        full = tark_cli._get(f"/api/v1/pat/pm/tasks/{row['id']}/")
        merged = dict(row)
        merged.update(full if isinstance(full, dict) else {})
        merged.setdefault("project_id", row.get("project_id", fallback_project))
        return _parse_card(merged)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        return list(executor.map(detail, known.values()))


def _table_cell(value: Any) -> str:
    return (
        str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
    )


def _card_link(card: dict[str, Any]) -> str:
    project = card.get("project_id", "")
    board = card.get("board", "")
    card_id = card.get("id", "")
    url = "https://c2.tarktoostus.ee/project-management/plan/pm-projects/"
    url += f"{project}/board/{board}/tasks/{card_id}"
    return f"[#{card_id}]({url})"


def render_markdown(
    client: dict[str, Any],
    cards: list[dict[str, Any]],
    generated_at: datetime | None = None,
) -> str:
    """Render one client ledger. This function deliberately performs no I/O."""
    now = generated_at or datetime.now()
    projects = ", ".join(str(project) for project in client["projects"])
    lines = [
        f"# {client['title']} - Delivery ledger - {now:%Y-%m-%d %H:%M}",
        "",
        f"> Generated by `automation/delivery_ledger.py` from C2 projects {projects}. Do not hand-edit - re-run to refresh.",
        "> The board is the source of truth; this file is the on-disk mirror and backup of its delivery state.",
        "",
        "## Value",
        "| Line | Document | Value | Paid | On the table | Due | Necessary | Postponable | Simple (ac<=2) | Awaiting acceptance |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    queue = queue_cards(cards)
    postponed = postponed_cards(cards)
    accepted = [
        card for card in cards if card.get("open") and is_awaiting_acceptance(card)
    ]
    counts = Counter(card["invoice"]["blocks"] for card in queue)
    postponed_counts = Counter(card["invoice"]["blocks"] for card in postponed)
    simple_counts = Counter(
        card["invoice"]["blocks"] for card in queue if card.get("simple")
    )
    accepted_counts = Counter(card["invoice"]["blocks"] for card in accepted)
    own = list(client.get("codes", []))
    codes = own + [
        code
        for code in INVOICE_LEDGER
        if code != "NONE"
        and code not in own
        and (counts[code] or postponed_counts[code] or accepted_counts[code])
    ]
    if postponed_counts["UNSCORED"]:
        codes.append("UNSCORED")
    for code in codes + ["NONE"]:
        info = invoice_info(code)
        if code == "UNSCORED":
            info = dict(info, document="no ## Invoice section yet - score it")
        lines.append(
            "| "
            + " | ".join(
                _table_cell(value)
                for value in (
                    code,
                    info["document"],
                    info["value"],
                    info["paid"],
                    info["amount"],
                    info["due"],
                    counts[code],
                    postponed_counts[code],
                    simple_counts[code],
                    accepted_counts[code],
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Necessary to deliver - in this order",
            "| # | Card | Column | Prio | Blocks | Waits on | Gate | WSJF | ac | Subject |",
            "| ---: | --- | --- | --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for index, card in enumerate(queue, 1):
        invoice = card["invoice"]
        blocks = invoice["blocks"] + (" **simple**" if card.get("simple") else "")
        lines.append(
            "| "
            + " | ".join(
                _table_cell(value)
                for value in (
                    index,
                    _card_link(card),
                    card.get("column_name", ""),
                    card.get("priority", ""),
                    blocks,
                    ", ".join(f"#{b}" for b in card.get("waits_on", [])) or "-",
                    invoice["gate"],
                    card["wsjf"].get("score")
                    if card["wsjf"].get("score") is not None
                    else "",
                    card["wsjf"].get("attention_cost")
                    if card["wsjf"].get("attention_cost") is not None
                    else "",
                    card.get("name", ""),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Can be postponed",
            "| # | Card | Column | Prio | Line | WSJF | ac | Subject |",
            "| ---: | --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for index, card in enumerate(postponed, 1):
        lines.append(
            "| "
            + " | ".join(
                _table_cell(value)
                for value in (
                    index,
                    _card_link(card),
                    card.get("column_name", ""),
                    card.get("priority", ""),
                    card["invoice"]["blocks"],
                    card["wsjf"].get("score")
                    if card["wsjf"].get("score") is not None
                    else "",
                    card["wsjf"].get("attention_cost")
                    if card["wsjf"].get("attention_cost") is not None
                    else "",
                    card.get("name", ""),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Shipped, awaiting customer acceptance",
            "| Card | Prio | Blocks | Retired | Subject |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for card in accepted:
        lines.append(
            "| "
            + " | ".join(
                _table_cell(value)
                for value in (
                    _card_link(card),
                    card.get("priority", ""),
                    card["invoice"]["blocks"],
                    card["retired"],
                    card.get("name", ""),
                )
            )
            + " |"
        )
    lines.extend(["", "## Needs the customer", "| Card | Question |", "| --- | --- |"])
    for card in cards:
        if card.get("open") and card.get("needs_customer"):
            lines.append(
                f"| {_card_link(card)} | {_table_cell(card['needs_customer'])} |"
            )
    buckets = [card for card in cards if card.get("open") and is_hour_bucket(card)]
    if buckets:
        lines.extend(
            [
                "",
                "## Hour buckets (time & materials)",
                "| Card | Project | Bucket | Hours logged |",
                "| --- | ---: | --- | ---: |",
            ]
        )
        for card in sorted(buckets, key=lambda item: (int(item.get("project_id", 0)), int(item.get("id", 0)))):
            lines.append(
                f"| {_card_link(card)} | {card.get('project_id', '')} | {_table_cell(card.get('name', ''))} | {_table_cell(card.get('total_hours', ''))} |"
            )
    lines.extend(
        ["", "## Board state", "| Project | Board | Column | Cards |", "| ---: | --- | --- | ---: |"]
    )
    board_state = Counter(
        (
            int(card.get("project_id", 0) or 0),
            str(card.get("board_name", card.get("board", ""))),
            str(card.get("column_name", "")),
        )
        for card in cards
    )
    for (project, board, column), count in sorted(board_state.items()):
        lines.append(f"| {project} | {_table_cell(board)} | {_table_cell(column)} | {count} |")
    return "\n".join(lines) + "\n"


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def write_snapshot(directory: Path, cards: list[dict[str, Any]]) -> None:
    snapshot_dir = directory / "_c2-cards"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for card in cards:
        if not card.get("open"):
            continue
        header = f"# {card['id']} - {card.get('name', '')}\n{card.get('column_name', '')} | {card.get('priority', '')} | {card.get('updated_at', '')}\n\n"
        (snapshot_dir / f"{card['id']}.md").write_text(
            header + str(card.get("wiki", "")), encoding="utf-8"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", required=True, help="Directory that will contain client folders"
    )
    parser.add_argument("--client", help="Comma-separated client keys (default: all)")
    parser.add_argument(
        "--snapshot", action="store_true", help="Also save raw open-card wiki backups"
    )
    parser.add_argument(
        "--summary", type=int, metavar="N", help="Print cross-client top N queue cards"
    )
    parser.add_argument(
        "--json", dest="json_path", metavar="PATH", help="Write parsed data as JSON"
    )
    args = parser.parse_args(argv)
    keys = args.client.split(",") if args.client else list(CLIENTS)
    unknown = [key for key in keys if key not in CLIENTS]
    if unknown:
        parser.error("unknown client(s): " + ", ".join(unknown))

    out_dir = Path(args.out_dir)
    parsed: dict[str, Any] = {}
    summaries: list[tuple[str, dict[str, Any]]] = []
    blocked_by = fetch_dependencies()
    for key in keys:
        config = CLIENTS[key]
        cards = fetch_client_cards(config)
        attach_blockers(cards, blocked_by)
        client_dir = out_dir / key
        client_dir.mkdir(parents=True, exist_ok=True)
        (client_dir / "delivery.md").write_text(
            render_markdown(config, cards), encoding="utf-8"
        )
        if args.snapshot:
            write_snapshot(client_dir, cards)
        parsed[key] = {
            "title": config["title"],
            "projects": config["projects"],
            "cards": cards,
        }
        summaries.extend((key, card) for card in queue_cards(cards))
        print(_ascii(f"[OK] {key}: wrote {client_dir / 'delivery.md'}"))
    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(_ascii(f"[OK] wrote {json_path}"))
    if args.summary is not None:
        ranked = sorted(summaries, key=lambda item: queue_sort_key(item[1]))[
            : max(args.summary, 0)
        ]
        for key, card in ranked:
            subject = str(card.get("name", ""))[:60]
            print(
                _ascii(
                    f"> {key}, {card['invoice']['blocks']}, {card['wsjf'].get('score')}, {card['id']}, {subject}"
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
