#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_PATH = PROJECT_ROOT / "AutomateQuery" / "reports" / "eval_candidates" / "regression_cases.jsonl"
OUT_DIR = PROJECT_ROOT / "AutomateQuery" / "evals"
REPORT_DIR = PROJECT_ROOT / "AutomateQuery" / "reports" / "eval_candidates"

OUT_JSONL = OUT_DIR / "approved_regression_cases.jsonl"
OUT_MD = REPORT_DIR / "approved_regression_seed.md"

APPROVED_INTENTS = {
    "purchase_last_n_purchases_by_material",
    "purchase_last_supply_by_supplier",
    "purchase_suppliers_by_material",
    "stock_by_item_name",
    "purchase_history_last_year_by_material",
    "purchase_last_supplier_by_material",
    "latest_mrs_by_item_name",
    "pending_mrs_by_item_name",
    "grn_by_supplier_code",
    "cashbank_voucher_details",
    "document_details_by_party_code",
    "camera_details",
}

SKIPPED_INTENTS = {
    "clarification_needed",
    "supplier_question_needs_filter",
    "employee_shift_needs_empcode",
    "lowest_price_needs_material",
    "pending_order_needs_business_filter",
    "received_date_needs_filter",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input not found: {path}")

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def approve_case(case: dict[str, Any]) -> dict[str, Any]:
    case = json.loads(json.dumps(case, ensure_ascii=False, default=str))
    case["status"] = "approved_seed"
    case["approval"] = {
        "approved": True,
        "approved_by": "manual_seed_rule",
        "approved_at": datetime.now().isoformat(timespec="seconds"),
        "review_note": "Approved by intent allowlist for first regression seed batch.",
    }
    return case


def main() -> int:
    cases = load_jsonl(INPUT_PATH)

    approved = []
    skipped = []

    for case in cases:
        intent = (case.get("expected") or {}).get("intent")

        if intent in APPROVED_INTENTS:
            approved.append(approve_case(case))
        else:
            skipped.append(case)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    OUT_JSONL.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False, default=str) for case in approved) + ("\n" if approved else ""),
        encoding="utf-8",
    )

    lines = []
    lines.append("# Approved Regression Seed")
    lines.append("")
    lines.append(f"- Generated at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Input: `{INPUT_PATH}`")
    lines.append(f"- Approved: `{len(approved)}`")
    lines.append(f"- Skipped: `{len(skipped)}`")
    lines.append("")
    lines.append("## Approved Cases")
    lines.append("")

    for idx, case in enumerate(approved, start=1):
        expected = case.get("expected") or {}
        latest = case.get("latest_observation") or {}
        lines.append(f"### {idx}. {case.get('question')}")
        lines.append("")
        lines.append(f"- Case ID: `{case.get('case_id')}`")
        lines.append(f"- Intent: `{expected.get('intent')}`")
        lines.append(f"- Source: `{expected.get('source')}`")
        lines.append(f"- Observed rows: `{expected.get('observed_row_count')}`")
        lines.append(f"- SQL safe SELECT: `{latest.get('sql_is_safe_select')}`")
        lines.append("")

    lines.append("## Skipped Cases")
    lines.append("")

    for idx, case in enumerate(skipped, start=1):
        expected = case.get("expected") or {}
        lines.append(f"- {idx}. `{expected.get('intent')}` - {case.get('question')}")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print("Approved regression seed generated")
    print("Approved:", len(approved))
    print("Skipped:", len(skipped))
    print("JSONL:", OUT_JSONL)
    print("Markdown:", OUT_MD)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
