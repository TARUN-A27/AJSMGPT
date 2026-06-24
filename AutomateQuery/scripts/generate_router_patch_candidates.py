from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = AUTOMATE_DIR / "reports"

QUESTION_BANK_JSON = REPORTS_DIR / "question_bank.json"
ROUTER_FIX_JSON = REPORTS_DIR / "router_fix_candidates.json"

ROUTER_PATCH_JSON = REPORTS_DIR / "router_patch_candidates.json"
ROUTER_PATCH_MD = REPORTS_DIR / "router_patch_candidates.md"


def read_json(path: Path) -> Any:
    if not path.exists():
        print(f"Missing file: {path}")
        return []

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_year(question: str) -> str | None:
    match = re.search(r"\b(20\d{2})\b", question)
    if match:
        return match.group(1)
    return None


def extract_supplier_token(question: str) -> str | None:
    patterns = [
        r"item\s+(.+?)(?:\s+in\s+\d{4}|$)",
        r"of the item\s+(.+?)(?:\s+in\s+\d{4}|$)",
        r"supplier\s+([a-zA-Z0-9&.\-\s]+)",
        r"vendor\s+([a-zA-Z0-9&.\-\s]+)",
        r"party\s+([a-zA-Z0-9&.\-\s]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            value = re.sub(r"\s+", " ", value)
            return value.upper()

    return None


def extract_material_guess(question: str) -> str | None:
    q = normalize_text(question)

    patterns = [
        r"rate of (.+?)(?: in \d{4}|$)",
        r"price of (.+?)(?: in \d{4}|$)",
        r"purchase of (.+?)(?: in \d{4}|$)",
        r"purchase rate of (.+?)(?: in \d{4}|$)",
        r"supply of (.+?)(?: in \d{4}|$)",
        r"material (.+?) last purchased",
        r"for (.+?)(?: in \d{4}|$)",
    ]

    stop_words = {
        "the",
        "a",
        "an",
        "last",
        "latest",
        "purchase",
        "rate",
        "price",
        "material",
        "item",
        "mrs",
        "supplier",
        "who",
        "which",
        "what",
        "how",
        "many",
        "much",
        "cost",
    }

    for pattern in patterns:
        match = re.search(pattern, q)
        if match:
            value = match.group(1).strip()
            words = [w for w in value.split() if w not in stop_words]
            value = " ".join(words).strip()
            if value:
                return value.upper()

    known_materials = [
        "mouse",
        "keyboard",
        "key board",
        "monitor",
        "computer monitor",
        "barcode scanner",
        "yarn",
    ]

    for material in known_materials:
        if material in q:
            return material.upper()

    return None


def make_patch(
    patch_id: str,
    title: str,
    target_router_file: str,
    intent_name: str,
    question_patterns: list[str],
    suggested_extraction: dict[str, Any],
    suggested_sql_logic: list[str],
    expected_sql_contains: list[str],
    priority: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "patch_id": patch_id,
        "title": title,
        "status": "review_required",
        "priority": priority,
        "target_router_file": target_router_file,
        "intent_name": intent_name,
        "question_patterns": sorted(set(question_patterns)),
        "suggested_extraction": suggested_extraction,
        "suggested_sql_logic": suggested_sql_logic,
        "expected_sql_contains": expected_sql_contains,
        "reason": reason,
        "human_review_required": True,
        "auto_apply_allowed": False,
    }


def collect_questions() -> list[dict[str, Any]]:
    question_bank = read_json(QUESTION_BANK_JSON)
    router_candidates = read_json(ROUTER_FIX_JSON)

    rows: list[dict[str, Any]] = []

    if isinstance(question_bank, list):
        for item in question_bank:
            question = str(item.get("question") or "").strip()
            if question:
                rows.append(
                    {
                        "question": question,
                        "category": item.get("category"),
                        "source_type": "question_bank",
                        "needs_review": item.get("needs_review"),
                        "fallback_count": item.get("fallback_count"),
                        "failed_count": item.get("failed_count"),
                        "slow_count": item.get("slow_count"),
                    }
                )

    if isinstance(router_candidates, list):
        for item in router_candidates:
            question = str(item.get("question") or "").strip()
            if question:
                rows.append(
                    {
                        "question": question,
                        "category": "router_candidate",
                        "source_type": "router_fix_candidates",
                        "problem_types": item.get("problem_types", []),
                        "wrong_tables_detected": item.get("wrong_tables_detected", []),
                        "source": item.get("source"),
                    }
                )

    # Dedupe by normalized question.
    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []

    for row in rows:
        key = normalize_text(row["question"])
        if key in seen:
            continue

        seen.add(key)
        unique_rows.append(row)

    return unique_rows


def build_patch_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_questions: dict[str, list[str]] = defaultdict(list)
    extracted_materials: dict[str, set[str]] = defaultdict(set)
    extracted_years: dict[str, set[str]] = defaultdict(set)
    extracted_suppliers: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        question = row["question"]
        q = normalize_text(question)

        year = extract_year(question)
        material = extract_material_guess(question)
        supplier = extract_supplier_token(question)

        # Supplier first/last supply
        if "supplier" in q and "supply" in q and "first" in q:
            key = "purchase_first_supply_by_supplier"
            grouped_questions[key].append(question)
            if supplier:
                extracted_suppliers[key].add(supplier)

        if "supplier" in q and "supply" in q and ("last" in q or "latest" in q):
            key = "purchase_last_supply_by_supplier"
            grouped_questions[key].append(question)
            if supplier:
                extracted_suppliers[key].add(supplier)

        # Material supply
        if "supply of" in q and ("last" in q or "latest" in q):
            key = "purchase_last_supply_by_material"
            grouped_questions[key].append(question)
            if material:
                extracted_materials[key].add(material)

                # Last N purchase quantity/details by material
        if (
            "last" in q
            and "purchase" in q
            and ("qty" in q or "quantity" in q)
            and material
        ):
            key = "purchase_last_n_purchases_by_material"
            grouped_questions[key].append(question)
            extracted_materials[key].add(material)

            n_match = re.search(r"\blast\s+(\d+)\b", q)
            if n_match:
                extracted_years[key].add("N=" + n_match.group(1))

        # Purchase rate / price by material
        is_purchase_rate = (
            "purchase rate" in q
            or "purchased rate" in q
            or "price of" in q
            or "last purchased rate" in q
            or "last purchase rate" in q
            or "purchase of" in q
        )

        if (
            is_purchase_rate
            and material
            and "qty" not in q
            and "quantity" not in q
        ):
            key = "purchase_rate_by_material"
            grouped_questions[key].append(question)
            extracted_materials[key].add(material)
            if year:
                extracted_years[key].add(year)

        # Latest purchase order by supplier/year
        if "purchase order" in q and "supplier" in q:
            key = "latest_purchase_order_by_supplier_year"
            grouped_questions[key].append(question)
            if supplier:
                extracted_suppliers[key].add(supplier)
            if year:
                extracted_years[key].add(year)

        # GRN by supplier/year
        if "grn" in q and "supplier" in q:
            key = "grn_by_supplier_year"
            grouped_questions[key].append(question)
            if supplier:
                extracted_suppliers[key].add(supplier)
            if year:
                extracted_years[key].add(year)

        # Attendance by empcode/date
        if "attendance" in q and "empcode" in q:
            key = "attendance_by_empcode_date"
            grouped_questions[key].append(question)
            if year:
                extracted_years[key].add(year)

        # Document/voucher by party/year
        if ("document" in q or "voucher" in q) and "party" in q:
            key = "document_details_by_party_year"
            grouped_questions[key].append(question)
            if supplier:
                extracted_suppliers[key].add(supplier)
            if year:
                extracted_years[key].add(year)

    patches: list[dict[str, Any]] = []

    if grouped_questions.get("purchase_first_supply_by_supplier"):
        patches.append(
            make_patch(
                patch_id="patch_purchase_first_supply_by_supplier",
                title="First supply by supplier",
                target_router_file="app/purchase_analytics_router.py",
                intent_name="purchase_first_supply_by_supplier",
                question_patterns=grouped_questions["purchase_first_supply_by_supplier"],
                suggested_extraction={
                    "supplier_name_or_code": sorted(extracted_suppliers["purchase_first_supply_by_supplier"]),
                    "ordering": "oldest first",
                },
                suggested_sql_logic=[
                    "Use INVENTORY.PURCHASEORDER PO",
                    "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                    "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                    "Filter supplier using UPPER(P.PARTYNAME) LIKE '%SUPPLIER%' or PO.SUP_CODE",
                    "Order by PO.ORDERDATE ASC NULLS LAST, PO.ORDERNO ASC",
                    "Return first row only using outer SELECT WHERE ROWNUM <= 1",
                ],
                expected_sql_contains=[
                    "INVENTORY.PURCHASEORDER",
                    "INVENTORY.INVITEMS",
                    "SCM.PARTYMASTER",
                    "ORDER BY PO.ORDERDATE ASC",
                    "ROWNUM <= 1",
                ],
                priority="high",
                reason="Repeated real user question and known fallback problem.",
            )
        )

    if grouped_questions.get("purchase_last_supply_by_supplier"):
        patches.append(
            make_patch(
                patch_id="patch_purchase_last_supply_by_supplier",
                title="Last supply by supplier",
                target_router_file="app/purchase_analytics_router.py",
                intent_name="purchase_last_supply_by_supplier",
                question_patterns=grouped_questions["purchase_last_supply_by_supplier"],
                suggested_extraction={
                    "supplier_name_or_code": sorted(extracted_suppliers["purchase_last_supply_by_supplier"]),
                    "ordering": "latest first",
                },
                suggested_sql_logic=[
                    "Use INVENTORY.PURCHASEORDER PO",
                    "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                    "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                    "Filter supplier using UPPER(P.PARTYNAME) LIKE '%SUPPLIER%' or PO.SUP_CODE",
                    "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                    "Return first row only using outer SELECT WHERE ROWNUM <= 1",
                ],
                expected_sql_contains=[
                    "INVENTORY.PURCHASEORDER",
                    "INVENTORY.INVITEMS",
                    "SCM.PARTYMASTER",
                    "ORDER BY PO.ORDERDATE DESC",
                    "ROWNUM <= 1",
                ],
                priority="high",
                reason="Common supplier supply question pattern.",
            )
        )

    if grouped_questions.get("purchase_last_supply_by_material"):
        patches.append(
            make_patch(
                patch_id="patch_purchase_last_supply_by_material",
                title="Last supply by material",
                target_router_file="app/purchase_analytics_router.py",
                intent_name="purchase_last_supply_by_material",
                question_patterns=grouped_questions["purchase_last_supply_by_material"],
                suggested_extraction={
                    "material_name": sorted(extracted_materials["purchase_last_supply_by_material"]),
                    "ordering": "latest first",
                },
                suggested_sql_logic=[
                    "Use INVENTORY.PURCHASEORDER PO",
                    "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                    "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                    "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                    "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                    "Return first row only using outer SELECT WHERE ROWNUM <= 1",
                ],
                expected_sql_contains=[
                    "INVENTORY.PURCHASEORDER",
                    "INVENTORY.INVITEMS",
                    "SCM.PARTYMASTER",
                    "UPPER(INV.ITEM_NAME) LIKE",
                    "ORDER BY PO.ORDERDATE DESC",
                    "ROWNUM <= 1",
                ],
                priority="high",
                reason="User asked material supply question that should not need Qwen fallback.",
            )
        )

    if grouped_questions.get("purchase_last_n_purchases_by_material"):
        patches.append(
            make_patch(
                patch_id="patch_purchase_last_n_purchases_by_material",
                title="Last N purchase quantity/details by material",
                target_router_file="app/purchase_analytics_router.py",
                intent_name="purchase_last_n_purchases_by_material",
                question_patterns=grouped_questions["purchase_last_n_purchases_by_material"],
                suggested_extraction={
                    "material_name": sorted(extracted_materials["purchase_last_n_purchases_by_material"]),
                    "limit": sorted(extracted_years["purchase_last_n_purchases_by_material"]),
                    "ordering": "latest first",
                },
                suggested_sql_logic=[
                    "Use INVENTORY.PURCHASEORDER PO",
                    "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                    "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                    "Extract N from phrases like 'last 3'",
                    "Extract material from phrases like 'item Keyboard'",
                    "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                    "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                    "Return N rows using outer SELECT WHERE ROWNUM <= N",
                ],
                expected_sql_contains=[
                    "INVENTORY.PURCHASEORDER",
                    "INVENTORY.INVITEMS",
                    "SCM.PARTYMASTER",
                    "UPPER(INV.ITEM_NAME) LIKE",
                    "ORDER BY PO.ORDERDATE DESC",
                    "ROWNUM <=",
                ],
                priority="high",
                reason="HOD asked last N purchase quantity/details for a material and AJSMGPT fallback generated wrong SQL.",
            )
        )

    if grouped_questions.get("purchase_rate_by_material"):
        patches.append(
            make_patch(
                patch_id="patch_purchase_rate_by_material",
                title="Purchase rate / price by material",
                target_router_file="app/purchase_analytics_router.py",
                intent_name="purchase_rate_by_material",
                question_patterns=grouped_questions["purchase_rate_by_material"],
                suggested_extraction={
                    "material_name": sorted(extracted_materials["purchase_rate_by_material"]),
                    "year": sorted(extracted_years["purchase_rate_by_material"]),
                    "date_phrases": ["last year", "last one year", "in YYYY"],
                },
                suggested_sql_logic=[
                    "Use INVENTORY.PURCHASEORDER PO",
                    "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                    "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                    "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                    "If year exists, filter EXTRACT(YEAR FROM PO.ORDERDATE) = YYYY",
                    "If 'last one year', filter PO.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -12)",
                    "For latest/last rate, order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                    "For list all rates, return multiple purchase rows ordered latest first",
                ],
                expected_sql_contains=[
                    "INVENTORY.PURCHASEORDER",
                    "INVENTORY.INVITEMS",
                    "UPPER(INV.ITEM_NAME) LIKE",
                    "PO.RATE",
                    "PO.ORDERDATE",
                ],
                priority="high",
                reason="Many real user purchase rate/price questions can share one router intent family.",
            )
        )

    if grouped_questions.get("latest_purchase_order_by_supplier_year"):
        patches.append(
            make_patch(
                patch_id="patch_latest_purchase_order_by_supplier_year",
                title="Latest purchase order by supplier and year",
                target_router_file="app/purchase_analytics_router.py",
                intent_name="latest_purchase_order_by_supplier_year",
                question_patterns=grouped_questions["latest_purchase_order_by_supplier_year"],
                suggested_extraction={
                    "supplier_name_or_code": sorted(extracted_suppliers["latest_purchase_order_by_supplier_year"]),
                    "year": sorted(extracted_years["latest_purchase_order_by_supplier_year"]),
                },
                suggested_sql_logic=[
                    "Use INVENTORY.PURCHASEORDER PO",
                    "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                    "Filter supplier by PO.SUP_CODE or UPPER(P.PARTYNAME)",
                    "Filter year using EXTRACT(YEAR FROM PO.ORDERDATE)",
                    "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                    "Return first row only",
                ],
                expected_sql_contains=[
                    "INVENTORY.PURCHASEORDER",
                    "SCM.PARTYMASTER",
                    "PO.SUP_CODE",
                    "EXTRACT(YEAR FROM PO.ORDERDATE)",
                    "ORDER BY PO.ORDERDATE DESC",
                ],
                priority="medium",
                reason="Supplier/year purchase order questions should use deterministic router.",
            )
        )

    if grouped_questions.get("grn_by_supplier_year"):
        patches.append(
            make_patch(
                patch_id="patch_grn_by_supplier_year",
                title="GRN by supplier and year",
                target_router_file="app/purchase_analytics_router.py",
                intent_name="grn_by_supplier_year",
                question_patterns=grouped_questions["grn_by_supplier_year"],
                suggested_extraction={
                    "supplier_name_or_code": sorted(extracted_suppliers["grn_by_supplier_year"]),
                    "year": sorted(extracted_years["grn_by_supplier_year"]),
                },
                suggested_sql_logic=[
                    "Review actual GRN table before applying.",
                    "Likely use purchase receipt / GRN table joined with supplier master.",
                    "Filter supplier by code/name.",
                    "Filter GRN date year.",
                    "Return GRN number, date, material, quantity, supplier.",
                ],
                expected_sql_contains=[
                    "REVIEW_ACTUAL_GRN_TABLE",
                    "SUPPLIER",
                    "GRN",
                ],
                priority="medium",
                reason="GRN table names must be verified before router implementation.",
            )
        )

    if grouped_questions.get("attendance_by_empcode_date"):
        patches.append(
            make_patch(
                patch_id="patch_attendance_by_empcode_date",
                title="Attendance by empcode and optional date",
                target_router_file="NEW_ROUTER_OR_REVIEW_REQUIRED",
                intent_name="attendance_by_empcode_date",
                question_patterns=grouped_questions["attendance_by_empcode_date"],
                suggested_extraction={
                    "empcode": "extract numeric empcode",
                    "date": "extract YYYYMMDD or date phrase if present",
                },
                suggested_sql_logic=[
                    "Review attendance table names before applying.",
                    "Filter by employee code.",
                    "If date exists, filter attendance date.",
                    "Return current/date attendance details.",
                ],
                expected_sql_contains=[
                    "REVIEW_ATTENDANCE_TABLE",
                    "EMPCODE",
                ],
                priority="medium",
                reason="Attendance questions are currently fallback and should get a business router after schema verification.",
            )
        )

    if grouped_questions.get("document_details_by_party_year"):
        patches.append(
            make_patch(
                patch_id="patch_document_details_by_party_year",
                title="Document/voucher details by party and optional year",
                target_router_file="NEW_ROUTER_OR_REVIEW_REQUIRED",
                intent_name="document_details_by_party_year",
                question_patterns=grouped_questions["document_details_by_party_year"],
                suggested_extraction={
                    "party_code": sorted(extracted_suppliers["document_details_by_party_year"]),
                    "year": sorted(extracted_years["document_details_by_party_year"]),
                },
                suggested_sql_logic=[
                    "Review document/voucher tables before applying.",
                    "Filter by party code/name.",
                    "If year exists, filter document date year.",
                    "Return document number, date, party, amount/status.",
                ],
                expected_sql_contains=[
                    "REVIEW_DOCUMENT_TABLE",
                    "PARTY",
                ],
                priority="medium",
                reason="Document/party questions are fallback and need table verification.",
            )
        )

    patches.sort(key=lambda p: {"high": 0, "medium": 1, "low": 2}.get(p["priority"], 9))
    return patches


def write_markdown(patches: list[dict[str, Any]]) -> None:
    lines: list[str] = []

    lines.append("# AutomateQuery Router Patch Candidates")
    lines.append("")
    lines.append(f"Generated at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append("")
    lines.append("## Safety")
    lines.append("")
    lines.append("- This file contains suggestions only.")
    lines.append("- No router code was modified.")
    lines.append("- No Oracle SQL was executed.")
    lines.append("- Every patch requires human review.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Patch candidates: `{len(patches)}`")
    lines.append("")

    for patch in patches:
        lines.append(f"## {patch['patch_id']}")
        lines.append("")
        lines.append(f"- Title: `{patch['title']}`")
        lines.append(f"- Priority: `{patch['priority']}`")
        lines.append(f"- Target router: `{patch['target_router_file']}`")
        lines.append(f"- Intent name: `{patch['intent_name']}`")
        lines.append(f"- Status: `{patch['status']}`")
        lines.append(f"- Auto apply allowed: `{patch['auto_apply_allowed']}`")
        lines.append(f"- Reason: {patch['reason']}")
        lines.append("")
        lines.append("### Question Patterns")
        lines.append("")
        for question in patch["question_patterns"]:
            lines.append(f"- `{question}`")
        lines.append("")
        lines.append("### Suggested Extraction")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(patch["suggested_extraction"], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
        lines.append("### Suggested SQL Logic")
        lines.append("")
        for logic in patch["suggested_sql_logic"]:
            lines.append(f"- {logic}")
        lines.append("")
        lines.append("### Expected SQL Contains")
        lines.append("")
        for token in patch["expected_sql_contains"]:
            lines.append(f"- `{token}`")
        lines.append("")

    ROUTER_PATCH_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    rows = collect_questions()
    patches = build_patch_candidates(rows)

    write_json(ROUTER_PATCH_JSON, patches)
    write_markdown(patches)

    print(f"Questions considered: {len(rows)}")
    print(f"Router patch candidates: {len(patches)}")
    print(f"Wrote: {ROUTER_PATCH_JSON}")
    print(f"Wrote: {ROUTER_PATCH_MD}")


if __name__ == "__main__":
    main()
