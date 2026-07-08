#!/usr/bin/env python3
"""
AutomateQuery Universal Learning Engine v1

Purpose:
- Diagnose any problematic question from retest/log results.
- Do NOT edit production router files.
- Run only safe SELECT probes through app.oracle_client.run_safe_select().
- Classify problems:
  PASS, EXPECTED_ZERO, BAD_ENTITY_EXTRACTION, WRONG_DATE_FILTER,
  AMBIGUOUS_QUESTION, MISSING_ROUTER, SQL_EXECUTION_ERROR,
  SLOW_QUERY, NEEDS_BUSINESS_MAPPING, MANUAL_REVIEW.
- Generate reports and candidate SQL for human review.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Runtime app folder has the real .env, Oracle credentials, Instant Client settings,
# and schema whitelist used by the live /ask API.
RUNTIME_ROOT = Path(
    os.environ.get("AJSMGPT_RUNTIME_ROOT", "/home/ajsmgpt/AJSMGPT")
).resolve()

def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        # Do not overwrite explicitly exported shell variables.
        os.environ.setdefault(key, value)

# Load runtime .env before importing app.oracle_client.
load_env_file(RUNTIME_ROOT / ".env")
load_env_file(PROJECT_ROOT / ".env")

# Prefer runtime app code for Oracle execution because that is what /ask uses.
for path in [str(RUNTIME_ROOT), str(PROJECT_ROOT)]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from app.oracle_client import run_safe_select
except Exception as exc:  # noqa: BLE001
    run_safe_select = None
    IMPORT_ERROR = repr(exc)
else:
    IMPORT_ERROR = None


DEFAULT_INPUT = PROJECT_ROOT / "AutomateQuery/reports/retest_from_logs/latest_all_distinct_retest.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis"


QUESTION_WORDS = {
    "WHAT", "WHEN", "WHERE", "WHO", "WHICH", "HOW",
    "IS", "ARE", "WAS", "WERE", "THE", "A", "AN",
    "FOR", "FROM", "OF", "IN", "ON", "BY", "TO",
    "SHOW", "LIST", "ALL", "DETAILS", "DETAIL",
    "LATEST", "LAST", "FIRST",
    "PURCHASE", "PURCHASED", "PURCHASES", "RATE", "PRICE",
    "SUPPLIER", "SUPPLIERS", "NAME",
    "ORDER", "ORDERS", "NO", "NUMBER",
    "GRN", "MRS", "STOCK", "RECEIVED",
    "DATE", "QTY", "QUANTITY",
    "ITEM", "ITEMS", "MATERIAL",
    "GIVEN", "LOWEST",
}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def clean_like(value: str) -> str:
    value = str(value or "").upper()
    value = value.replace("%", " ")
    value = re.sub(r"[;'\"]", " ", value)
    value = re.sub(r"[^A-Z0-9&./ -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    # Common typo normalization from real logs.
    value = re.sub(r"\bSYSTEN\b", "SYSTEM", value)
    value = re.sub(r"\bSYSTM\b", "SYSTEM", value)
    value = re.sub(r"\bLABLES\b", "LABELS", value)
    value = re.sub(r"\bLABLE\b", "LABEL", value)
    value = re.sub(r"\bLATEEST\b", "LATEST", value)
    value = re.sub(r"\bLATTEST\b", "LATEST", value)

    return value


def clean_code(value: str) -> str:
    value = str(value or "").upper().strip()
    value = re.sub(r"[;'\"]", "", value)
    value = re.sub(r"\s+", "", value)
    if re.fullmatch(r"[A-Z0-9_-]+", value):
        return value
    return ""


def sql_lit(value: str) -> str:
    return "'" + clean_like(value).replace("'", "''") + "'"


def item_terms(value: str) -> list[str]:
    value = clean_like(value)
    terms = []
    for part in value.split():
        if part in QUESTION_WORDS:
            continue
        if len(part) < 2:
            continue
        terms.append(part)
    return terms


def like_and(column: str, terms: list[str]) -> str:
    terms = [clean_like(t) for t in terms if clean_like(t)]
    if not terms:
        return "1 = 0"
    if len(terms) == 1:
        return f"UPPER({column}) LIKE '%{terms[0]}%'"
    phrase = " ".join(terms)
    and_clause = " AND ".join([f"UPPER({column}) LIKE '%{t}%'" for t in terms])
    return f"(UPPER({column}) LIKE '%{phrase}%' OR ({and_clause}))"


def like_or(column: str, terms: list[str]) -> str:
    terms = [clean_like(t) for t in terms if clean_like(t)]
    if not terms:
        return "1 = 0"
    return "(" + " OR ".join([f"UPPER({column}) LIKE '%{t}%'" for t in terms]) + ")"


def extract_year(question: str, sql: str = "") -> str:
    text = f"{question} {sql}"
    m = re.search(r"\b(20\d{2})\b", text)
    return m.group(1) if m else ""


def extract_quoted(question: str) -> str:
    m = re.search(r'"([^"]+)"', question or "")
    if m:
        return clean_like(m.group(1))
    m = re.search(r"'([^']+)'", question or "")
    if m:
        return clean_like(m.group(1))
    return ""


def extract_item_from_question(question: str) -> str:
    q = question or ""
    quoted = extract_quoted(q)
    if quoted:
        return " ".join(item_terms(quoted))

    patterns = [
        r"for the item",
        r"of the item",
        r"item name",
        r"material",
        r"item",
        r"stock of",
        r"purchase rate of",
        r"last purchase of",
        r"last supply of",
        r"for",
        r"of",
    ]

    for pat in patterns:
        m = re.search(pat, q, flags=re.I)
        if m:
            candidate = q[m.end():]
            terms = item_terms(candidate)
            if terms:
                return " ".join(terms)

    terms = item_terms(q)
    return " ".join(terms)


def extract_supplier_code(question: str, sql: str = "") -> str:
    text = f"{question}\n{sql}"

    patterns = [
        r"\bsupplier\s+([A-Za-z0-9_-]+)\b",
        r"\bSUP_CODE\)?\s*=\s*'([^']+)'",
        r"\bG\.SUP_CODE\)?\s*=\s*'([^']+)'",
        r"\bPO\.SUP_CODE\)?\s*=\s*'([^']+)'",
        r"\bV\.SUP_CODE\)?\s*=\s*'([^']+)'",
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            code = clean_code(m.group(1))
            # Avoid treating English words as supplier codes.
            if code and code not in {"NAME", "DETAILS", "THE", "FROM"}:
                return code
    return ""


def extract_party_code(question: str, sql: str = "") -> str:
    text = f"{question}\n{sql}"

    patterns = [
        r"\bparty\s+([A-Za-z0-9_-]+)\b",
        r"\bPARTYCODE\)?\s*=\s*'([^']+)'",
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            code = clean_code(m.group(1))
            if code:
                return code
    return ""


def extract_order_no(question: str, sql: str = "") -> str:
    text = f"{question}\n{sql}"

    patterns = [
        r"\border\s+number\s+([A-Za-z0-9_-]+)\b",
        r"\border\s+no\s+([A-Za-z0-9_-]+)\b",
        r"\bORDERNO\s*=\s*'?([A-Za-z0-9_-]+)'?",
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            code = clean_code(m.group(1))
            if code:
                return code
    return ""


def detect_module(sql: str, question: str = "") -> str:
    text = f"{question}\n{sql}".upper()

    if "ADMIN.DOCUMENT" in text:
        return "admin_document"
    if "ADMIN.TRN_VEHICLEMOVEMENT" in text:
        return "admin_vehicle"
    if "ADMIN.CAMERAIP" in text:
        return "admin_camera"
    if "INVENTORY.GRN" in text:
        return "inventory_grn"
    if "INVENTORY.MRS" in text or "MRS_TEMP" in text:
        return "inventory_mrs"
    if "INVENTORY.ITEMSTOCK" in text:
        return "inventory_stock"
    if "INVENTORY.PURCHASEORDER" in text:
        return "inventory_purchase"
    if "CURRENTATTENDANCE" in text or "HRDNEW" in text:
        return "hrd"
    if "DUAL" in text:
        return "clarification"
    return "unknown"


def is_ambiguous(question: str) -> bool:
    q = clean_like(question).lower().strip()

    ambiguous_exact = {
        "what is this",
        "supplier",
        "suppliers",
        "when was received",
        "when received",
        "received",
    }

    if q.rstrip("?") in ambiguous_exact:
        return True

    if "lowest price" in q and not any(
        x in q for x in ["item", "material", "keyboard", "mouse", "monitor", "printer", "scanner", "dell"]
    ):
        return True

    if "shift" in q and "empcode" not in q:
        return True

    return False


def run_probe(name: str, sql: str, probes_enabled: bool = True) -> dict[str, Any]:
    record = {
        "name": name,
        "sql": sql.strip(),
        "ok": False,
        "row_count": None,
        "value": None,
        "rows_preview": [],
        "error": None,
    }

    if not probes_enabled:
        record["error"] = "probes disabled"
        return record

    if run_safe_select is None:
        record["error"] = f"could not import run_safe_select: {IMPORT_ERROR}"
        return record

    try:
        res = run_safe_select(sql)
        rows = res.get("rows", []) or []
        record["ok"] = True
        record["row_count"] = res.get("row_count", 0)
        record["rows_preview"] = rows[:5]
        if rows and rows[0]:
            record["value"] = rows[0][0]
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"

    return record


def count_value(probe: dict[str, Any]) -> int | None:
    try:
        if probe.get("ok") and probe.get("rows_preview"):
            return int(probe["rows_preview"][0][0])
    except Exception:
        return None
    return None


def build_purchase_item_sql(item: str, limit: int = 100) -> str:
    terms = item_terms(item)
    where_clause = like_and("INV.ITEM_NAME", terms)
    return f"""
SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.SUP_CODE,
        NVL(P.PARTYNAME, PO.SUP_CODE) AS SUPPLIER_NAME,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.STATUS,
        PO.GRN_PENDINGSTATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE {where_clause}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= {int(limit)}
""".strip()


def build_stock_item_sql(item: str) -> str:
    terms = item_terms(item)
    where_clause = like_and("INV.ITEM_NAME", terms)
    return f"""
SELECT
    S.ITEMCODE,
    INV.ITEM_NAME,
    NVL(S.STOCK, 0) AS STOCK,
    NVL(S.STOCKVALUE, 0) AS STOCKVALUE,
    NVL(S.RESERVEDQTY, 0) AS RESERVEDQTY,
    NVL(S.INDENTQTY, 0) AS INDENTQTY,
    NVL(S.TRANSFERQTY, 0) AS TRANSFERQTY,
    S.MILLCODE
FROM INVENTORY.ITEMSTOCK S
JOIN INVENTORY.INVITEMS INV ON S.ITEMCODE = INV.ITEM_CODE
WHERE {where_clause}
ORDER BY INV.ITEM_NAME, S.ITEMCODE
""".strip()


def diagnose_row(row: dict[str, Any], probes_enabled: bool = True) -> dict[str, Any]:
    question = row.get("question") or ""
    sql = row.get("sql") or ""
    verdict = row.get("verdict") or ""
    source = row.get("source") or ""
    intent = row.get("intent") or ""
    row_count = row.get("row_count")
    error = row.get("error") or ""

    module = detect_module(sql, question)
    year = extract_year(question, sql)
    supplier_code = extract_supplier_code(question, sql)
    party_code = extract_party_code(question, sql)
    order_no = extract_order_no(question, sql)
    item_name = extract_item_from_question(question)

    diagnosis = {
        "question": question,
        "old_verdict": verdict,
        "old_source": source,
        "old_intent": intent,
        "old_row_count": row_count,
        "old_error": error,
        "module": module,
        "entities": {
            "year": year,
            "supplier_code": supplier_code,
            "party_code": party_code,
            "order_no": order_no,
            "item_name": item_name,
        },
        "classification": "MANUAL_REVIEW",
        "confidence": 0.50,
        "reason": "",
        "probes": [],
        "candidate": None,
        "expected_zero": False,
        "needs_human_review": True,
    }

    if verdict == "PASS":
        diagnosis["classification"] = "PASS"
        diagnosis["confidence"] = 1.0
        diagnosis["reason"] = "Question already passes current retest."
        diagnosis["needs_human_review"] = False
        return diagnosis

    if is_ambiguous(question):
        diagnosis["classification"] = "AMBIGUOUS_QUESTION"
        diagnosis["confidence"] = 0.95
        diagnosis["reason"] = "Question is missing required business filter/entity."
        diagnosis["candidate"] = {
            "type": "clarification",
            "suggestion": "Return a safe clarification asking for item/supplier/order/employee details.",
            "auto_apply": False,
        }
        return diagnosis

    if verdict == "FAIL":
        if "timed out" in error.lower():
            diagnosis["classification"] = "SLOW_QUERY"
            diagnosis["confidence"] = 0.90
            diagnosis["reason"] = "The current query timed out and needs a more selective template or row limit."
            return diagnosis

        if not sql:
            diagnosis["classification"] = "MISSING_ROUTER"
            diagnosis["confidence"] = 0.85
            diagnosis["reason"] = "No safe SQL was produced. Needs router/template candidate."
            return diagnosis

        diagnosis["classification"] = "SQL_EXECUTION_ERROR"
        diagnosis["confidence"] = 0.80
        diagnosis["reason"] = "SQL was produced but failed during Oracle execution."
        return diagnosis

    # ZERO_REVIEW / fallback with no useful rows
    if module == "inventory_grn" and supplier_code:
        p_all = run_probe(
            "grn_count_for_supplier",
            f"SELECT COUNT(*) AS CNT FROM INVENTORY.GRN WHERE UPPER(SUP_CODE) = '{supplier_code}'",
            probes_enabled,
        )
        diagnosis["probes"].append(p_all)
        all_count = count_value(p_all)

        p_range = run_probe(
            "grn_min_max_for_supplier",
            f"SELECT MIN(GRNDATE), MAX(GRNDATE) FROM INVENTORY.GRN WHERE UPPER(SUP_CODE) = '{supplier_code}'",
            probes_enabled,
        )
        diagnosis["probes"].append(p_range)

        year_count = None
        if year:
            p_year = run_probe(
                "grn_count_for_supplier_year",
                f"""
SELECT COUNT(*) AS CNT
FROM INVENTORY.GRN
WHERE UPPER(SUP_CODE) = '{supplier_code}'
  AND GRNDATE BETWEEN '{year}0101' AND '{year}1231'
""",
                probes_enabled,
            )
            diagnosis["probes"].append(p_year)
            year_count = count_value(p_year)

        if year and all_count and all_count > 0 and year_count == 0:
            diagnosis["classification"] = "EXPECTED_ZERO"
            diagnosis["confidence"] = 0.95
            diagnosis["expected_zero"] = True
            diagnosis["reason"] = f"Supplier {supplier_code} has GRN history, but no GRN rows in {year}."
            diagnosis["needs_human_review"] = False
            return diagnosis

        if all_count == 0:
            diagnosis["classification"] = "EXPECTED_ZERO"
            diagnosis["confidence"] = 0.85
            diagnosis["expected_zero"] = True
            diagnosis["reason"] = f"No GRN rows found for supplier code {supplier_code}."
            return diagnosis

    if module == "admin_document" and party_code:
        p_all = run_probe(
            "document_count_for_party",
            f"SELECT COUNT(*) AS CNT FROM ADMIN.DOCUMENT WHERE UPPER(PARTYCODE) = '{party_code}'",
            probes_enabled,
        )
        diagnosis["probes"].append(p_all)
        all_count = count_value(p_all)

        year_count = None
        if year:
            p_year = run_probe(
                "document_count_for_party_year",
                f"""
SELECT COUNT(*) AS CNT
FROM ADMIN.DOCUMENT
WHERE UPPER(PARTYCODE) = '{party_code}'
  AND REGEXP_LIKE(TO_CHAR(DOCDATE), '^[0-9]{{8}}$')
  AND TO_CHAR(DOCDATE) BETWEEN '{year}0101' AND '{year}1231'
""",
                probes_enabled,
            )
            diagnosis["probes"].append(p_year)
            year_count = count_value(p_year)

        if year and all_count and all_count > 0 and year_count == 0:
            diagnosis["classification"] = "EXPECTED_ZERO"
            diagnosis["confidence"] = 0.95
            diagnosis["expected_zero"] = True
            diagnosis["reason"] = f"Party {party_code} has documents, but none in {year}."
            diagnosis["needs_human_review"] = False
            return diagnosis

        if all_count == 0:
            diagnosis["classification"] = "EXPECTED_ZERO"
            diagnosis["confidence"] = 0.90
            diagnosis["expected_zero"] = True
            diagnosis["reason"] = f"No document rows found for party code {party_code}."
            return diagnosis

    if module == "admin_vehicle" and supplier_code:
        p_all = run_probe(
            "vehicle_count_for_supplier",
            f"SELECT COUNT(*) AS CNT FROM ADMIN.TRN_VEHICLEMOVEMENT WHERE UPPER(SUP_CODE) = '{supplier_code}'",
            probes_enabled,
        )
        diagnosis["probes"].append(p_all)
        all_count = count_value(p_all)

        if all_count == 0:
            diagnosis["classification"] = "EXPECTED_ZERO"
            diagnosis["confidence"] = 0.90
            diagnosis["expected_zero"] = True
            diagnosis["reason"] = f"No vehicle movement rows found for supplier code {supplier_code}."
            return diagnosis

    if module == "inventory_mrs" and order_no:
        p_all = run_probe(
            "mrs_count_for_order",
            f"SELECT COUNT(*) AS CNT FROM INVENTORY.MRS_TEMP WHERE ORDERNO = {order_no}",
            probes_enabled,
        )
        diagnosis["probes"].append(p_all)
        all_count = count_value(p_all)

        if all_count == 0:
            diagnosis["classification"] = "EXPECTED_ZERO"
            diagnosis["confidence"] = 0.90
            diagnosis["expected_zero"] = True
            diagnosis["reason"] = f"No MRS rows found for order number {order_no}."
            return diagnosis

    if module == "inventory_mrs" and item_name:
        terms = item_terms(item_name)
        if terms:
            p_item = run_probe(
                "mrs_count_for_item",
                f"""
SELECT COUNT(*) AS CNT
FROM INVENTORY.MRS_TEMP M
JOIN INVENTORY.INVITEMS INV ON M.ITEM_CODE = INV.ITEM_CODE
WHERE {like_and("INV.ITEM_NAME", terms)}
""",
                probes_enabled,
            )
            diagnosis["probes"].append(p_item)
            item_count = count_value(p_item)

            if "rejected" in question.lower():
                p_rej = run_probe(
                    "rejected_mrs_count_for_item",
                    f"""
SELECT COUNT(*) AS CNT
FROM INVENTORY.MRS_TEMP M
JOIN INVENTORY.INVITEMS INV ON M.ITEM_CODE = INV.ITEM_CODE
WHERE {like_and("INV.ITEM_NAME", terms)}
  AND (NVL(M.REJECTIONSTATUS, 0) = 1 OR NVL(M.STORESREJECTIONSTATUS, 0) = 1)
""",
                    probes_enabled,
                )
                diagnosis["probes"].append(p_rej)
                rej_count = count_value(p_rej)

                if item_count and item_count > 0 and rej_count == 0:
                    diagnosis["classification"] = "EXPECTED_ZERO"
                    diagnosis["confidence"] = 0.95
                    diagnosis["expected_zero"] = True
                    diagnosis["reason"] = f"MRS exists for item '{item_name}', but no rejected MRS rows match."
                    diagnosis["needs_human_review"] = False
                    return diagnosis

    if module == "inventory_stock" and item_name:
        terms = item_terms(item_name)
        if terms:
            p_stock = run_probe(
                "stock_count_for_item",
                f"""
SELECT COUNT(*) AS CNT
FROM INVENTORY.ITEMSTOCK S
JOIN INVENTORY.INVITEMS INV ON S.ITEMCODE = INV.ITEM_CODE
WHERE {like_and("INV.ITEM_NAME", terms)}
""",
                probes_enabled,
            )
            diagnosis["probes"].append(p_stock)
            stock_count = count_value(p_stock)

            if stock_count and stock_count > 0:
                diagnosis["classification"] = "BAD_ENTITY_EXTRACTION"
                diagnosis["confidence"] = 0.90
                diagnosis["reason"] = "A cleaner item extraction returns stock rows."
                candidate_sql = build_stock_item_sql(item_name)
                diagnosis["candidate"] = {
                    "type": "candidate_sql",
                    "suggested_intent": "stock_by_item_name",
                    "sql": candidate_sql,
                    "auto_apply": False,
                }
                return diagnosis

            p_items_or = run_probe(
                "invitems_relaxed_or_count",
                f"""
SELECT COUNT(*) AS CNT
FROM INVENTORY.INVITEMS INV
WHERE {like_or("INV.ITEM_NAME", terms)}
""",
                probes_enabled,
            )
            diagnosis["probes"].append(p_items_or)
            relaxed_count = count_value(p_items_or)

            if relaxed_count and relaxed_count > 0:
                diagnosis["classification"] = "BAD_ENTITY_EXTRACTION"
                diagnosis["confidence"] = 0.80
                diagnosis["reason"] = "Strict item match failed, but relaxed item search found possible matches."
                diagnosis["candidate"] = {
                    "type": "candidate_sql",
                    "suggested_intent": "stock_by_item_name",
                    "sql": build_stock_item_sql(" ".join(terms)),
                    "auto_apply": False,
                }
                return diagnosis

    if module == "inventory_purchase" and item_name:
        terms = item_terms(item_name)
        if terms:
            p_purchase = run_probe(
                "purchase_count_for_item",
                f"""
SELECT COUNT(*) AS CNT
FROM INVENTORY.PURCHASEORDER PO
JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
WHERE {like_and("INV.ITEM_NAME", terms)}
""",
                probes_enabled,
            )
            diagnosis["probes"].append(p_purchase)
            purchase_count = count_value(p_purchase)

            if purchase_count and purchase_count > 0:
                diagnosis["classification"] = "BAD_ENTITY_EXTRACTION"
                diagnosis["confidence"] = 0.90
                diagnosis["reason"] = "A cleaner item/material extraction returns purchase rows."
                diagnosis["candidate"] = {
                    "type": "candidate_sql",
                    "suggested_intent": "purchase_last_n_purchases_by_material",
                    "sql": build_purchase_item_sql(item_name, 100),
                    "auto_apply": False,
                }
                return diagnosis

    if verdict == "ZERO_REVIEW":
        diagnosis["classification"] = "EXPECTED_ZERO"
        diagnosis["confidence"] = 0.65
        diagnosis["expected_zero"] = True
        diagnosis["reason"] = "Current SQL is safe and returned zero rows. No generic better candidate was proven by probes."
        return diagnosis

    diagnosis["classification"] = "MANUAL_REVIEW"
    diagnosis["confidence"] = 0.50
    diagnosis["reason"] = "No generic diagnosis rule matched. Needs human review."
    return diagnosis


def build_md_report(diagnoses: list[dict[str, Any]]) -> str:
    counts = Counter(d["classification"] for d in diagnoses)
    total = len(diagnoses)

    lines = []
    lines.append("# AutomateQuery Universal Diagnosis Report")
    lines.append("")
    lines.append(f"- Generated: `{now_iso()}`")
    lines.append(f"- Questions diagnosed: `{total}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")

    for key, count in sorted(counts.items()):
        lines.append(f"- {key}: `{count}`")

    lines.append("")
    lines.append("## Needs Review / Candidates")
    lines.append("")

    for d in diagnoses:
        if d["classification"] in {"PASS"}:
            continue

        lines.append(f"### {d['question']}")
        lines.append("")
        lines.append(f"- Classification: `{d['classification']}`")
        lines.append(f"- Confidence: `{d['confidence']}`")
        lines.append(f"- Old verdict: `{d.get('old_verdict')}`")
        lines.append(f"- Module: `{d.get('module')}`")
        lines.append(f"- Intent: `{d.get('old_intent')}`")
        lines.append(f"- Reason: {d.get('reason')}")
        lines.append(f"- Expected zero: `{d.get('expected_zero')}`")
        lines.append("")

        entities = d.get("entities") or {}
        non_empty_entities = {k: v for k, v in entities.items() if v}
        if non_empty_entities:
            lines.append("Entities:")
            lines.append("")
            for k, v in non_empty_entities.items():
                lines.append(f"- {k}: `{v}`")
            lines.append("")

        if d.get("candidate"):
            lines.append("Candidate:")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(d["candidate"], indent=2, ensure_ascii=False, default=str))
            lines.append("```")
            lines.append("")

        probes = d.get("probes") or []
        if probes:
            lines.append("Probes:")
            lines.append("")
            for p in probes:
                lines.append(f"- `{p['name']}` ok={p['ok']} value=`{p.get('value')}` error=`{p.get('error')}`")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="latest_all_distinct_retest.json path")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUT_DIR), help="output reports directory")
    ap.add_argument("--include-pass", action="store_true", help="also include PASS rows in diagnosis output")
    ap.add_argument("--no-probes", action="store_true", help="do not execute Oracle probe SQL")
    args = ap.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.output_dir)
    probes_enabled = not args.no_probes

    rows = load_json(input_path)
    if not isinstance(rows, list):
        raise SystemExit(f"Expected list in {input_path}")

    selected = []
    for row in rows:
        verdict = row.get("verdict")
        source = row.get("source") or ""
        if args.include_pass:
            selected.append(row)
        elif verdict != "PASS" or "fallback" in source.lower():
            selected.append(row)

    diagnoses = [diagnose_row(row, probes_enabled=probes_enabled) for row in selected]

    candidates = [d for d in diagnoses if d.get("candidate")]
    expected_zero = [d for d in diagnoses if d.get("expected_zero")]

    out_dir.mkdir(parents=True, exist_ok=True)

    write_json(out_dir / "latest_diagnosis.json", diagnoses)
    write_json(out_dir / "latest_candidates.json", candidates)
    write_json(out_dir / "expected_zero_candidates.json", expected_zero)
    (out_dir / "latest_diagnosis.md").write_text(build_md_report(diagnoses), encoding="utf-8")

    counts = Counter(d["classification"] for d in diagnoses)

    print("Universal diagnosis generated")
    print(f"Input: {input_path}")
    print(f"Questions diagnosed: {len(diagnoses)}")
    for key, count in sorted(counts.items()):
        print(f"{key}: {count}")

    print("")
    print(f"JSON: {out_dir / 'latest_diagnosis.json'}")
    print(f"MD  : {out_dir / 'latest_diagnosis.md'}")
    print(f"CAND: {out_dir / 'latest_candidates.json'}")
    print(f"ZERO: {out_dir / 'expected_zero_candidates.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
