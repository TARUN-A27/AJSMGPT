from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "AutomateQuery" / "reports"

QUESTION_BANK_PATH = REPORTS_DIR / "question_bank.json"
ROUTER_FIX_CANDIDATES_PATH = REPORTS_DIR / "router_fix_candidates.json"
QUESTIONS_ONLY_PATH = PROJECT_ROOT / "logs" / "questions_only.txt"

OUT_JSON = REPORTS_DIR / "router_patch_candidates.json"
OUT_MD = REPORTS_DIR / "router_patch_candidates.md"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def clean_question(q: str) -> str:
    return re.sub(r"\s+", " ", str(q or "")).strip()


def norm(q: str) -> str:
    return clean_question(q).lower()


def add_unique(items: list[str], value: str | None) -> None:
    value = clean_question(value or "")
    if value and value not in items:
        items.append(value)


def read_questions() -> list[str]:
    questions: list[str] = []

    qb = read_json(QUESTION_BANK_PATH, [])
    if isinstance(qb, list):
        for row in qb:
            if isinstance(row, dict):
                add_unique(questions, row.get("question") or row.get("text") or row.get("q"))
            else:
                add_unique(questions, str(row))

    fixes = read_json(ROUTER_FIX_CANDIDATES_PATH, [])
    if isinstance(fixes, list):
        for row in fixes:
            if isinstance(row, dict):
                add_unique(questions, row.get("question") or row.get("user_question") or row.get("text"))
            else:
                add_unique(questions, str(row))

    if QUESTIONS_ONLY_PATH.exists():
        for line in QUESTIONS_ONLY_PATH.read_text(encoding="utf-8").splitlines():
            add_unique(questions, line.strip())

    return questions


def extract_year(q: str) -> str | None:
    m = re.search(r"\b(20\d{2})\b", q)
    return m.group(1) if m else None


def normalize_material(value: str | None) -> str | None:
    if not value:
        return None

    s = value.strip().strip('"').strip("'").strip(" .,-_/\\")
    s = re.sub(r"\s+", " ", s).strip()

    s = re.sub(r"^(the|item|material)\s+", "", s, flags=re.I)
    s = re.sub(
        r"\b(latest|last|purchase|purchases|order|orders|po|no|number|qty|quantity|details|detail|rate|price|supplier|in|year)\b.*$",
        "",
        s,
        flags=re.I,
    ).strip()

    if not s:
        return None

    s = s.replace("key board", "keyboard")
    return s.upper()


def extract_material(question: str) -> str | None:
    q = clean_question(question)

    quoted = re.search(r'"([^"]+)"', q)
    if quoted:
        return normalize_material(quoted.group(1))

    patterns = [
        r"\bof\s+the\s+item\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bitem\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bpurchase\s+details\s+of\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bpurchase\s+(?:qty|quantity)\s+purchase\s+of\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\brate\s+of\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bprice\s+of\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bsupply\s+of\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bpurchase\s+of\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bmaterial\s+([a-zA-Z0-9&.\-_/ ]+?)\s+last\s+purchased",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            material = normalize_material(m.group(1))
            if material:
                return material

    return None


def extract_supplier(question: str) -> str | None:
    q = clean_question(question)
    m = re.search(r"\bsupplier\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)", q, flags=re.I)
    if m:
        return m.group(1).strip().upper()
    return None


def extract_empcode(question: str) -> str | None:
    m = re.search(r"\bempcode\s+(\d+)\b", question, flags=re.I)
    return m.group(1) if m else None


def extract_party(question: str) -> str | None:
    m = re.search(r"\bparty\s+([a-zA-Z0-9]+)\b", question, flags=re.I)
    return m.group(1).upper() if m else None


def extract_limit(question: str, default: int = 1) -> int:
    m = re.search(r"\blast\s+(\d+)\b", question, flags=re.I)
    if not m:
        return default
    return max(1, min(int(m.group(1)), 50))


def make_patch(
    *,
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
        "target_router_file": target_router_file,
        "intent_name": intent_name,
        "question_patterns": question_patterns,
        "suggested_extraction": suggested_extraction,
        "suggested_sql_logic": suggested_sql_logic,
        "expected_sql_contains": expected_sql_contains,
        "priority": priority,
        "reason": reason,
        "auto_apply_allowed": False,
    }


def generate_patches(questions: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    materials: dict[str, set[str]] = defaultdict(set)
    suppliers: dict[str, set[str]] = defaultdict(set)
    years: dict[str, set[str]] = defaultdict(set)
    limits: dict[str, set[str]] = defaultdict(set)
    empcodes: dict[str, set[str]] = defaultdict(set)
    parties: dict[str, set[str]] = defaultdict(set)

    for question in questions:
        q = norm(question)
        material = extract_material(question)
        supplier = extract_supplier(question)
        year = extract_year(question)
        empcode = extract_empcode(question)
        party = extract_party(question)

        # Last N purchase quantity/details by material.
        if (
            "last" in q
            and "purchase" in q
            and ("qty" in q or "quantity" in q or "details" in q or "detail" in q)
            and material
        ):
            key = "purchase_last_n_purchases_by_material"
            grouped[key].append(question)
            materials[key].add(material)
            limits[key].add(f"N={extract_limit(question, default=1)}")
            continue

        # Latest purchase order number/details by material.
        if (
            ("latest" in q or "last" in q)
            and "purchase" in q
            and ("order" in q or "po" in q)
            and material
            and "supplier" not in q
        ):
            key = "purchase_latest_order_by_material"
            grouped[key].append(question)
            materials[key].add(material)
            continue

        # First / last supply by supplier.
        if "supply" in q and "supplier" in q and supplier:
            if "first" in q:
                key = "purchase_first_supply_by_supplier"
            elif "last" in q or "latest" in q:
                key = "purchase_last_supply_by_supplier"
            else:
                key = ""
            if key:
                grouped[key].append(question)
                suppliers[key].add(supplier)
                continue

        # Last supply by material.
        if "last" in q and "supply" in q and material and "supplier" not in q:
            key = "purchase_last_supply_by_material"
            grouped[key].append(question)
            materials[key].add(material)
            continue

        # Purchase rate / price by material.
        if (
            "purchase" in q
            and ("rate" in q or "price" in q or "last year purchase" in q)
            and material
            and "qty" not in q
            and "quantity" not in q
            and "details" not in q
            and "order" not in q
        ):
            key = "purchase_rate_by_material"
            grouped[key].append(question)
            materials[key].add(material)
            if year:
                years[key].add(year)
            continue

        # Latest purchase order by supplier/year.
        if "purchase order" in q and "supplier" in q and supplier:
            key = "latest_purchase_order_by_supplier_year"
            grouped[key].append(question)
            suppliers[key].add(supplier)
            if year:
                years[key].add(year)
            continue

        # GRN by supplier/year.
        if "grn" in q and "supplier" in q and supplier:
            key = "grn_by_supplier_year"
            grouped[key].append(question)
            suppliers[key].add(supplier)
            if year:
                years[key].add(year)
            continue

        # Attendance by empcode/date.
        if "attendance" in q and empcode:
            key = "attendance_by_empcode_date"
            grouped[key].append(question)
            empcodes[key].add(empcode)
            continue

        # Document / cashbank by party/year.
        if ("document" in q or "cashbank" in q or "voucher" in q) and party:
            key = "document_details_by_party_year"
            grouped[key].append(question)
            parties[key].add(party)
            if year:
                years[key].add(year)
            continue

    patches: list[dict[str, Any]] = []

    if grouped.get("purchase_first_supply_by_supplier"):
        patches.append(make_patch(
            patch_id="patch_purchase_first_supply_by_supplier",
            title="First supply by supplier",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="purchase_first_supply_by_supplier",
            question_patterns=grouped["purchase_first_supply_by_supplier"],
            suggested_extraction={"supplier_name": sorted(suppliers["purchase_first_supply_by_supplier"]), "ordering": "oldest first"},
            suggested_sql_logic=[
                "Use INVENTORY.PURCHASEORDER PO",
                "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                "Filter supplier using UPPER(P.PARTYNAME) LIKE '%SUPPLIER%'",
                "Order by PO.ORDERDATE ASC NULLS LAST, PO.ORDERNO ASC",
                "Return first row only using outer SELECT WHERE ROWNUM <= 1",
            ],
            expected_sql_contains=["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER", "UPPER(P.PARTYNAME) LIKE", "ORDER BY PO.ORDERDATE ASC", "ROWNUM <= 1"],
            priority="high",
            reason="Real user asked first supply by supplier.",
        ))

    if grouped.get("purchase_last_supply_by_supplier"):
        patches.append(make_patch(
            patch_id="patch_purchase_last_supply_by_supplier",
            title="Last supply by supplier",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="purchase_last_supply_by_supplier",
            question_patterns=grouped["purchase_last_supply_by_supplier"],
            suggested_extraction={"supplier_name": sorted(suppliers["purchase_last_supply_by_supplier"]), "ordering": "latest first"},
            suggested_sql_logic=[
                "Use INVENTORY.PURCHASEORDER PO",
                "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                "Filter supplier using UPPER(P.PARTYNAME) LIKE '%SUPPLIER%'",
                "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                "Return first row only using outer SELECT WHERE ROWNUM <= 1",
            ],
            expected_sql_contains=["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER", "UPPER(P.PARTYNAME) LIKE", "ORDER BY PO.ORDERDATE DESC", "ROWNUM <= 1"],
            priority="high",
            reason="Real user asked last supply by supplier.",
        ))

    if grouped.get("purchase_last_supply_by_material"):
        patches.append(make_patch(
            patch_id="patch_purchase_last_supply_by_material",
            title="Last supply by material",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="purchase_last_supply_by_material",
            question_patterns=grouped["purchase_last_supply_by_material"],
            suggested_extraction={"material_name": sorted(materials["purchase_last_supply_by_material"]), "ordering": "latest first"},
            suggested_sql_logic=[
                "Use INVENTORY.PURCHASEORDER PO",
                "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                "Return first row only using outer SELECT WHERE ROWNUM <= 1",
            ],
            expected_sql_contains=["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER", "UPPER(INV.ITEM_NAME) LIKE", "ORDER BY PO.ORDERDATE DESC", "ROWNUM <= 1"],
            priority="high",
            reason="Real user asked last supply by material.",
        ))

    if grouped.get("purchase_last_n_purchases_by_material"):
        patches.append(make_patch(
            patch_id="patch_purchase_last_n_purchases_by_material",
            title="Last N purchase quantity/details by material",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="purchase_last_n_purchases_by_material",
            question_patterns=grouped["purchase_last_n_purchases_by_material"],
            suggested_extraction={
                "material_name": sorted(materials["purchase_last_n_purchases_by_material"]),
                "limit": sorted(limits["purchase_last_n_purchases_by_material"]),
                "ordering": "latest first",
            },
            suggested_sql_logic=[
                "Use INVENTORY.PURCHASEORDER PO",
                "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                "Extract N from phrases like 'last 3'",
                "Extract material from phrases like quoted text or 'item Keyboard'",
                "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                "Return N rows using outer SELECT WHERE ROWNUM <= N",
            ],
            expected_sql_contains=["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER", "UPPER(INV.ITEM_NAME) LIKE", "ORDER BY PO.ORDERDATE DESC", "ROWNUM <="],
            priority="high",
            reason="User asked last N purchase quantity/details for a material.",
        ))

    if grouped.get("purchase_latest_order_by_material"):
        patches.append(make_patch(
            patch_id="patch_purchase_latest_order_by_material",
            title="Latest purchase order number/details by material",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="purchase_latest_order_by_material",
            question_patterns=grouped["purchase_latest_order_by_material"],
            suggested_extraction={"material_name": sorted(materials["purchase_latest_order_by_material"]), "ordering": "latest first"},
            suggested_sql_logic=[
                "Use INVENTORY.PURCHASEORDER PO",
                "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                "Extract material from quoted text or item name",
                "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                "Order by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                "Return first row using outer SELECT WHERE ROWNUM <= 1",
            ],
            expected_sql_contains=["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER", "UPPER(INV.ITEM_NAME) LIKE", "ORDER BY PO.ORDERDATE DESC", "ROWNUM <= 1"],
            priority="high",
            reason="User asked latest purchase order number/details for a material.",
        ))

    if grouped.get("purchase_rate_by_material"):
        patches.append(make_patch(
            patch_id="patch_purchase_rate_by_material",
            title="Purchase rate / price by material",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="purchase_rate_by_material",
            question_patterns=grouped["purchase_rate_by_material"],
            suggested_extraction={"material_name": sorted(materials["purchase_rate_by_material"]), "year": sorted(years["purchase_rate_by_material"])},
            suggested_sql_logic=[
                "Use INVENTORY.PURCHASEORDER PO",
                "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                "Return latest purchase rates ordered by PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
            ],
            expected_sql_contains=["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER", "UPPER(INV.ITEM_NAME) LIKE", "PO.RATE", "ORDER BY PO.ORDERDATE DESC"],
            priority="high",
            reason="Many real user purchase rate/price questions can share one router intent family.",
        ))

    if grouped.get("latest_purchase_order_by_supplier_year"):
        patches.append(make_patch(
            patch_id="patch_latest_purchase_order_by_supplier_year",
            title="Latest purchase order by supplier and year",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="latest_purchase_order_by_supplier_year",
            question_patterns=grouped["latest_purchase_order_by_supplier_year"],
            suggested_extraction={"supplier": sorted(suppliers["latest_purchase_order_by_supplier_year"]), "year": sorted(years["latest_purchase_order_by_supplier_year"])},
            suggested_sql_logic=["Use purchase order tables and filter supplier/year."],
            expected_sql_contains=["INVENTORY.PURCHASEORDER", "ORDER BY"],
            priority="medium",
            reason="User asked latest purchase order for supplier/year.",
        ))

    if grouped.get("grn_by_supplier_year"):
        patches.append(make_patch(
            patch_id="patch_grn_by_supplier_year",
            title="GRN by supplier and year",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="grn_by_supplier_year",
            question_patterns=grouped["grn_by_supplier_year"],
            suggested_extraction={"supplier": sorted(suppliers["grn_by_supplier_year"]), "year": sorted(years["grn_by_supplier_year"])},
            suggested_sql_logic=["Use INVENTORY.GRN and supplier/year filters."],
            expected_sql_contains=["INVENTORY.GRN"],
            priority="medium",
            reason="User asked GRN details for supplier/year.",
        ))

    if grouped.get("attendance_by_empcode_date"):
        patches.append(make_patch(
            patch_id="patch_attendance_by_empcode_date",
            title="Attendance by employee code/date",
            target_router_file="REVIEW_REQUIRED",
            intent_name="attendance_by_empcode_date",
            question_patterns=grouped["attendance_by_empcode_date"],
            suggested_extraction={"empcode": sorted(empcodes["attendance_by_empcode_date"])},
            suggested_sql_logic=["Needs HRDNEW table verification."],
            expected_sql_contains=["HRDNEW"],
            priority="medium",
            reason="Attendance questions need HRDNEW router/table verification.",
        ))

    if grouped.get("document_details_by_party_year"):
        patches.append(make_patch(
            patch_id="patch_document_details_by_party_year",
            title="Document / voucher details by party/year",
            target_router_file="REVIEW_REQUIRED",
            intent_name="document_details_by_party_year",
            question_patterns=grouped["document_details_by_party_year"],
            suggested_extraction={"party": sorted(parties["document_details_by_party_year"]), "year": sorted(years["document_details_by_party_year"])},
            suggested_sql_logic=["Needs ADMIN table verification."],
            expected_sql_contains=["ADMIN"],
            priority="medium",
            reason="Document/cashbank questions need ADMIN router/table verification.",
        ))


    # Forced safety catch: suppliers by material/item questions.
    # This catches cases that appear in Router Candidates because fallback used wrong table.
    # Example: WHO are the suppliers for the item "barcode label"
    supplier_material_questions: list[str] = []
    supplier_material_names: set[str] = set()

    for question in questions:
        q = norm(question)
        material = extract_material(question)

        if (
            ("supplier" in q or "suppliers" in q)
            and ("item" in q or "material" in q)
            and material
            and "purchase order" not in q
        ):
            if question not in supplier_material_questions:
                supplier_material_questions.append(question)
            supplier_material_names.add(material)

    already_has_supplier_material_patch = any(
        patch.get("patch_id") == "patch_purchase_suppliers_by_material"
        for patch in patches
    )

    if supplier_material_questions and not already_has_supplier_material_patch:
        patches.append(make_patch(
            patch_id="patch_purchase_suppliers_by_material",
            title="Suppliers by material/item",
            target_router_file="app/purchase_analytics_router.py",
            intent_name="purchase_suppliers_by_material",
            question_patterns=supplier_material_questions,
            suggested_extraction={
                "material_name": sorted(supplier_material_names),
            },
            suggested_sql_logic=[
                "Use INVENTORY.PURCHASEORDER PO",
                "Join INVENTORY.INVITEMS INV on PO.ITEM_CODE = INV.ITEM_CODE",
                "Left join SCM.PARTYMASTER P on PO.SUP_CODE = P.PARTYCODE",
                "Extract material from quoted text or item phrase",
                "Filter material using UPPER(INV.ITEM_NAME) LIKE '%MATERIAL%'",
                "Return distinct suppliers using PO.SUP_CODE and P.PARTYNAME",
                "Do not use INVENTORY.SUPPLIER because fallback used wrong table",
            ],
            expected_sql_contains=[
                "INVENTORY.PURCHASEORDER",
                "INVENTORY.INVITEMS",
                "SCM.PARTYMASTER",
                "UPPER(INV.ITEM_NAME) LIKE",
                "DISTINCT",
                "P.PARTYNAME",
            ],
            priority="high",
            reason="User asked suppliers for a material and fallback used wrong supplier table.",
        ))


    return patches


def write_markdown(patches: list[dict[str, Any]]) -> None:
    lines = [
        "# Router Patch Candidates",
        "",
        f"Total candidates: {len(patches)}",
        "",
    ]

    for patch in patches:
        lines.extend([
            f"## {patch['patch_id']}",
            "",
            f"- Title: {patch['title']}",
            f"- Target router: `{patch['target_router_file']}`",
            f"- Intent: `{patch['intent_name']}`",
            f"- Priority: `{patch['priority']}`",
            "",
            "Questions:",
        ])
        for q in patch["question_patterns"]:
            lines.append(f"- {q}")
        lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    questions = read_questions()
    patches = generate_patches(questions)

    OUT_JSON.write_text(json.dumps(patches, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(patches)

    print(f"Questions considered: {len(questions)}")
    print(f"Router patch candidates: {len(patches)}")
    print(f"Wrote: {OUT_JSON}")
    print(f"Wrote: {OUT_MD}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
