from datetime import datetime

import delivery_ledger as ledger


def _card(card_id, code, score=1.0, ac=5, column="Work", retired=""):
    card = {
        "id": card_id,
        "name": f"card {card_id}",
        "board": 3,
        "board_name": "Board",
        "project_id": 9,
        "column_name": column,
        "priority": "HIGH",
        "open": True,
        "invoice": {"blocks": code, "gate": ""},
        "wsjf": {"score": score, "attention_cost": ac},
        "retired": retired,
        "needs_customer": "",
        "simple": False,
    }
    info = ledger.invoice_info(code)
    card["simple"] = bool(info.get("due_iso") and ac <= 2)
    return card


def test_parse_sections():
    sections = ledger.parse_sections(
        "## Invoice\nblocks: ARB-AKT2\ngate: x\n## WSJF\n| user_business_value | 8 |\n"
    )
    assert sections == {
        "Invoice": "blocks: ARB-AKT2\ngate: x",
        "WSJF": "| user_business_value | 8 |",
    }


def test_parse_invoice_and_missing_section():
    parsed = ledger.parse_invoice("gate: x\nblocks: ARB-AKT2")
    assert parsed["blocks"] == "ARB-AKT2"
    assert parsed["gate"] == "x"
    assert parsed["why"] == ""
    assert ledger.parse_invoice(None)["blocks"] == "UNSCORED"


def test_parse_wsjf_score_and_recompute():
    body = "\n".join(
        (
            "| user_business_value | 8 |",
            "| time_criticality | 7 |",
            "| risk_reduction | 6 |",
            "| opportunity_enablement | 5 |",
            "| attention_cost | 5 |",
            "**WSJF = (26) / 5 = 5.2**",
        )
    )
    assert ledger.parse_wsjf(body)["score"] == 5.2
    assert (
        ledger.parse_wsjf(body.replace("\n**WSJF = (26) / 5 = 5.2**", ""))["score"]
        == 5.2
    )
    assert ledger.parse_wsjf(None)["score"] is None


def test_queue_sort_order_and_review_exclusion(monkeypatch):
    monkeypatch.setitem(
        ledger.INVOICE_LEDGER,
        "DUE-HIGH",
        {
            "client": "Test",
            "document": "larger same-date invoice",
            "amount": "50 000 EUR",
            "due": "soon",
            "due_iso": "2026-08-03",
        },
    )
    later = _card(1, "ARB-AKT2", score=9)
    costly = _card(2, "ARB-AKT1", score=2, ac=4)
    simple = _card(3, "ARB-AKT1", score=1, ac=2)
    larger_same_date = _card(8, "DUE-HIGH", score=1, ac=5)
    hek = _card(4, "HEK-TM", score=1)
    ion = _card(5, "ION-PILOT", score=99)
    none = _card(6, "NONE", score=100)
    shipped = _card(7, "ARB-AKT1", column="Review", retired="SHIPPED to customer")
    ordered = ledger.queue_cards(
        [later, costly, simple, larger_same_date, hek, ion, none, shipped]
    )
    assert [card["id"] for card in ordered] == [
        simple["id"],
        larger_same_date["id"],
        costly["id"],
        later["id"],
        hek["id"],
        ion["id"],
        none["id"],
    ]


def test_renderer_has_the_five_headings_in_order():
    text = ledger.render_markdown(
        {"title": "Fake", "projects": [9]},
        [_card(1, "NONE")],
        datetime(2026, 9, 2, 10, 30),
    )
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Invoices",
        "## Queue - deliver in this order",
        "## Shipped, awaiting customer acceptance",
        "## Needs the customer",
        "## Board state",
    ]


def test_fetch_client_cards_uses_mocked_get_for_a_done_list_row(monkeypatch):
    calls = []

    def fake_get(path, **params):
        calls.append((path, params))
        return {
            "results": [
                {
                    "id": 99,
                    "name": "already done",
                    "board": 3,
                    "board_name": "Board",
                    "column_name": "DONE",
                }
            ],
            "next": None,
        }

    monkeypatch.setattr(ledger.tark_cli, "_get", fake_get)
    cards = ledger.fetch_client_cards({"projects": [9], "extra_cards": []})
    assert [card["id"] for card in cards] == [99]
    assert calls == [
        ("/api/v1/pat/pm/tasks/", {"project": "9", "page_size": 200, "page": "1"})
    ]


def test_attach_blockers_orders_waited_on_cards_first() -> None:
    import delivery_ledger as dl

    def card(cid: int, blocks: str = "ARB-AKT2", ac: int = 3) -> dict:
        return {
            "id": cid, "open": True, "column_name": "To Do", "retired": "",
            "invoice": {"blocks": blocks, "gate": "", "why": ""},
            "wsjf": {"score": 20.0, "attention_cost": ac}, "simple": False,
        }

    cards = [card(3), card(2), card(1)]
    dl.attach_blockers(cards, {3: [2], 2: [1], 99: [3]})
    by_id = {c["id"]: c for c in cards}
    assert by_id[1]["block_depth"] == 0 and by_id[2]["block_depth"] == 1 and by_id[3]["block_depth"] == 2
    assert by_id[3]["waits_on"] == [2] and by_id[1]["waits_on"] == []
    assert [c["id"] for c in dl.queue_cards(cards)] == [1, 2, 3]
