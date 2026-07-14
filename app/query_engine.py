from __future__ import annotations

import json
import time
from typing import Any

from app.backend_logger import log_event, new_request_id, set_request_id
from app.business_template_engine import match_business_template
from app.purchase_analytics_router import match_purchase_analytics_template
from app.mrs_template_router import match_mrs_template
from app.date_filter_engine import apply_date_filter_to_sql, strip_date_filter_phrases
from app.oracle_client import run_safe_select
from app.question_understanding import understand_question
from app.answer_formatter import format_answer
from app.sql_generator_v2 import generate_select_sql_v2
from app.query_planner import (
    _detect_module as planner_detect_module,
    _match_router as planner_match_router,
    _resolver_enriched_question as planner_resolver_enriched_question,
)


def _safe_preview(value, limit=1200):
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _safe_error_message(exc: Exception) -> str:
    error_text = str(exc)

    if "Invalid datatype comparison" in error_text:
        return (
            "I could not safely execute this query because the generated SQL used an invalid datatype comparison. "
            + error_text
        )

    if "Invalid LIKE comparison" in error_text:
        return (
            "I could not safely execute this query because the generated SQL used LIKE on a non-text column. "
            + error_text
        )

    if "Invalid column" in error_text:
        return error_text

    if "Only SELECT statements are allowed" in error_text:
        return "This request was blocked because only SELECT queries are allowed."

    if "ORA-01722" in error_text:
        return (
            "Oracle rejected the query because of an invalid number comparison. "
            "This usually means a NUMBER column was compared with text."
        )

    if "ORA-" in error_text:
        return "Oracle could not execute the generated query. Please try a simpler wording or check the backend log."

    if "JSON" in error_text:
        return "The model returned an invalid response format. Please try again."

    return "Backend could not process this question safely. Please check the backend log."


def _resolver_enriched_question(question: str, resolved_values: dict[str, Any]) -> str:
    """
    Stage 2 resolver integration.

    This does not generate SQL directly.
    It only replaces a user phrase with the best real DB value before router matching.

    Example:
        barcode chromo label last purchase
        -> BARCODE CHROMO LABLES 40 X 25MM last purchase
    """
    import re

    q = question or ""
    q_upper = q.upper()

    business_words = {
        "PURCHASE", "SUPPLY", "SUPPLIER", "MATERIAL", "ITEM",
        "QTY", "QUANTITY", "GRN", "MRS", "ISSUE", "STOCK",
        "DOCUMENT", "CASHBANK", "VOUCHER", "PARTY", "EMPLOYEE",
        "EMPCODE", "ATTENDANCE", "VEHICLE", "CAMERA",
    }

    results = resolved_values.get("results") or []
    semantic_queries = resolved_values.get("semantic_queries") or []

    if not results:
        return q

    # For exact code questions like ODUT001, keep original question.
    # Existing fallbacks already use the exact code safely.
    if resolved_values.get("exact_terms"):
        return q

    # Choose the best high-confidence real DB value.
    best = None
    for item in results:
        entity_type = item.get("entity_type")
        score = float(item.get("score") or 0)
        value = str(item.get("resolved_value") or "").strip()

        if not value:
            continue

        if entity_type in {"material", "supplier_or_party", "department", "unit", "vehicle", "employee"} and score >= 80:
            best = item
            break

    if not best:
        return q

    resolved_value = str(best.get("resolved_value") or "").strip()
    if not resolved_value:
        return q

    # Try replacing the clean semantic phrase first.
    for phrase in semantic_queries:
        phrase = str(phrase or "").strip()
        if not phrase:
            continue

        phrase_words = set(phrase.upper().split())
        if phrase_words and phrase_words.issubset(business_words):
            continue

        pattern = re.compile(re.escape(phrase), flags=re.IGNORECASE)
        if pattern.search(q):
            return pattern.sub(resolved_value, q, count=1)

    # Fallback: append resolved value as a router hint.
    return f"{q} resolved value {resolved_value}"




def _best_resolved_material(resolved_values: dict[str, Any]) -> str | None:
    """
    Pick the best material value found by hybrid resolver.
    """
    for item in resolved_values.get("results", []) or []:
        if item.get("entity_type") != "material":
            continue

        score = float(item.get("score") or 0)
        value = str(item.get("resolved_value") or "").strip()

        if value and score >= 80:
            return value

    return None


def _sql_text_literal(value: str) -> str:
    return str(value or "").replace("'", "''").strip()


def _apply_resolved_material_to_sql(sql: str, resolved_values: dict[str, Any]) -> str:
    """
    If resolver found an exact material value, preserve that value in router SQL.

    Example:
        Router produced:
            UPPER(INV.ITEM_NAME) LIKE '%BARCODE CHROMO LABLE 40 X 25MM%'

        Resolver found:
            BARCODE CHROMO LABLES 40 X 25MM

        Final SQL:
            UPPER(INV.ITEM_NAME) LIKE '%BARCODE CHROMO LABLES 40 X 25MM%'
    """
    import re

    if not sql:
        return sql

    material = _best_resolved_material(resolved_values)
    if not material:
        return sql

    # broad_one_word_material_sql_guard:
    # Do not replace broad one-word material filters in MRS/stock/issue style SQL.
    # Example: pending MRS for keyboard should remain LIKE '%KEYBOARD%'.
    semantic_queries_for_guard = [
        str(x or "").strip()
        for x in (resolved_values.get("semantic_queries") or [])
    ]
    is_broad_one_word_material = any(
        len(re.findall(r"[A-Za-z0-9]+", phrase)) <= 1
        for phrase in semantic_queries_for_guard
        if phrase
    )

    sql_upper = sql.upper()

    if is_broad_one_word_material and any(
        token in sql_upper
        for token in ["MRS_TEMP", "ITEMSTOCK", "INVENTORY.STOCK", " STOCK ", "STOCK S", "PURCHASEORDER", "INVENTORY.PURCHASEORDER", " ISSUE ", " INVENTORY.ISSUE"]
    ):
        return sql


    # Only patch purchase item-name filters. Do not touch other modules.
    if "INV.ITEM_NAME" not in sql_upper:
        return sql

    material_sql = _sql_text_literal(material).upper()

    pattern = re.compile(
        r"UPPER\s*\(\s*INV\.ITEM_NAME\s*\)\s+LIKE\s+'%[^']*%'",
        flags=re.IGNORECASE,
    )

    patched_sql = pattern.sub(
        f"UPPER(INV.ITEM_NAME) LIKE '%{material_sql}%'",
        sql,
        count=1,
    )

    return patched_sql




def _log_learning_router(question: str) -> dict[str, Any] | None:
    """
    Deterministic fixes learned from real user log retest.

    This is NOT free SQL generation.
    It only maps known real question patterns to safe SELECT templates.
    """
    import re

    q = str(question or "").strip()
    q_l = q.lower()

    def clean_like(value: str) -> str:
        value = str(value or "").strip().upper()
        value = value.replace("%", " ")
        value = re.sub(r"[;'\"]", " ", value)
        value = re.sub(r"[^A-Z0-9&./ -]+", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def clean_code(value: str) -> str:
        value = str(value or "").strip().upper()
        value = re.sub(r"[;'\"]", "", value)
        value = re.sub(r"\s+", "", value)
        if not re.fullmatch(r"[A-Z0-9_-]+", value):
            return ""
        return value

    def quoted_value() -> str:
        m = re.search(r'"([^"]+)"', q)
        if m:
            return clean_like(m.group(1))
        m = re.search(r"'([^']+)'", q)
        if m:
            return clean_like(m.group(1))
        return ""

    def like_clause(column: str, value: str) -> str:
        value = clean_like(value)
        tokens = [t for t in value.split() if t]
        if not tokens:
            return "1 = 0"

        phrase = " ".join(tokens)
        token_clause = " AND ".join([f"UPPER({column}) LIKE '%{t}%'" for t in tokens])

        if len(tokens) == 1:
            return f"UPPER({column}) LIKE '%{tokens[0]}%'"

        return f"(UPPER({column}) LIKE '%{phrase}%' OR ({token_clause}))"

    def extract_after_words(words: list[str]) -> str:
        q_clean = q
        for word in words:
            m = re.search(word, q_clean, flags=re.I)
            if m:
                return clean_like(q_clean[m.end():])
        return ""

    def extract_item() -> str:
        def _refine_item_text(value: str) -> str:
            value = clean_like(value)

            # Common real-user typo normalization.
            value = re.sub(r"\bSYSTEN\b", "SYSTEM", value, flags=re.I)
            value = re.sub(r"\bSYSTM\b", "SYSTEM", value, flags=re.I)

            # Remove words that describe the question, not the item.
            value = re.sub(
                r"\b("
                r"MATERIAL|ITEM|ITEMS|NAME|"
                r"LATEST|LAST|PURCHASED|PURCHASE|PURCHASES|"
                r"ORDER|ORDERS|NO|NUMBER|"
                r"RATE|DATE|QTY|QUANTITY|STOCK|SUPPLIER|SUPPLIERS|"
                r"WHO|ARE|THE|FOR|FROM|OF|IN|ONE|YEAR|DETAILS|SHOW|LIST|ALL"
                r")\b",
                " ",
                value,
                flags=re.I,
            )

            value = re.sub(r"\s+", " ", value).strip()
            return value

        quoted = quoted_value()
        if quoted:
            return _refine_item_text(quoted)

        patterns = [
            r"for the item",
            r"of the item",
            r"item name",
            r"material",
            r"item",
            r"of",
        ]

        for pat in patterns:
            m = re.search(pat, q, flags=re.I)
            if m:
                value = q[m.end():]
                value = _refine_item_text(value)
                if value:
                    return value

        # common short patterns:
        # "keyboard stock"
        # "mouse last purchased date"
        # "mouse last purchased supplier name"
        value = _refine_item_text(q)
        return value

    def extract_supplier_name() -> str:
        quoted = quoted_value()
        if quoted:
            return quoted

        value = q
        value = re.sub(r"\b(what|they|have|has|supplied|supply|last|latest|purchase|from|supplier|name|its|it|is|the)\b", " ", value, flags=re.I)
        return clean_like(value)

    def limit_n(default: int = 100) -> int:
        m = re.search(r"\blast\s+(\d+)\b", q_l)
        if m:
            return max(1, min(int(m.group(1)), 100))
        return default

    def date_filter_po() -> str:
        if "last one year" in q_l or "last 1 year" in q_l or "last year" in q_l:
            return """
        AND PO.ORDERDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE), -12), 'YYYYMMDD')
                             AND TO_CHAR(TRUNC(SYSDATE), 'YYYYMMDD')
""".rstrip()
        y = re.search(r"\b(20\d{2})\b", q_l)
        if y:
            year = y.group(1)
            return f" AND PO.ORDERDATE BETWEEN '{year}0101' AND '{year}1231'"
        return ""

    # 0AAA) Ambiguous received-date question without item/order/supplier context.
    if q_l.strip() in {"when was received", "when was received?", "received?", "when received"}:
        sql = """
SELECT
    'Please provide item name, order number, supplier code, or GRN number. Example: when was scanner received, or GRN for supplier 800967 in 2025.' AS MESSAGE
FROM DUAL
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched ambiguous received-date question and returned safe clarification.",
            "tables_used": ["DUAL"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "received_date_needs_filter",
            "parameters": {},
            "retrieved_schema": [],
        }

    # 0AAB) Lowest price question without material/item context.
    if (
        "lowest price" in q_l
        and not any(word in q_l for word in ["item", "material", "keyboard", "mouse", "monitor", "printer", "scanner", "dell"])
    ):
        sql = """
SELECT
    'Please provide item/material name to find the lowest supplier price. Example: which supplier gave lowest price for keyboard?' AS MESSAGE
FROM DUAL
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched lowest-price question without item context and returned safe clarification.",
            "tables_used": ["DUAL"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "lowest_price_needs_material",
            "parameters": {},
            "retrieved_schema": [],
        }

    # 0AA) Vague supplier-only question.
    if q_l.strip() in {"supplier", "suppliers", "supplier?", "suppliers?"}:
        sql = """
SELECT
    'Please provide supplier code/name or ask a specific supplier question. Example: last supply from supplier Prime Compu Systems, or supplier details for code 800967.' AS MESSAGE
FROM DUAL
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched vague supplier-only question and returned safe clarification.",
            "tables_used": ["DUAL"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "supplier_question_needs_filter",
            "parameters": {},
            "retrieved_schema": [],
        }

    # 0A) Ambiguous/general question: return clarification safely.
    if q_l.strip() in {"what is this", "what is this?", "this", "help"}:
        sql = """
SELECT
    'Please ask with a module or business detail, for example: stock for keyboard, last purchase of mouse, document details for party 911378, or attendance for empcode 165224.' AS MESSAGE
FROM DUAL
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched ambiguous question and returned safe clarification.",
            "tables_used": ["DUAL"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "clarification_needed",
            "parameters": {},
            "retrieved_schema": [],
        }

    # 0B) Pending order list.
    if "pending" in q_l and "order" in q_l and ("list" in q_l or "details" in q_l or q_l.strip() == "pending order"):
        sql = """
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
        PO.INVQTY,
        (NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) AS RECEIPT_PENDING_QTY,
        PO.STATUS,
        PO.GRN_PENDINGSTATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE (NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) > 0
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 100
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched pending purchase order list.",
            "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
            "relationships_used": [
                "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
            ],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "purchase_pending_order_list",
            "parameters": {},
            "retrieved_schema": [],
        }

    # 0C) Employee name-only shift question.
    # We do not safely know which ARUNKUMAR without empcode, so ask for empcode instead of guessing.
    if "shift" in q_l and ("time" in q_l or "today" in q_l) and not re.search(r"\bempcode\s+\d+\b", q_l):
        sql = """
SELECT
    'Please provide employee code to check today shift time, for example: today shift time for empcode 165224.' AS MESSAGE
FROM DUAL
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched employee shift request without empcode and returned safe clarification.",
            "tables_used": ["DUAL"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "employee_shift_needs_empcode",
            "parameters": {},
            "retrieved_schema": [],
        }

    # 0D) Pending order by user name is ambiguous unless we know the ERP user/employee mapping.
    if "pending" in q_l and "order" in q_l and "user" in q_l:
        sql = """
SELECT
    'Please provide item name, supplier code, order number, or employee code. Example: pending order list, pending order for keyboard, or pending order for supplier 800967.' AS MESSAGE
FROM DUAL
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched ambiguous pending-order-by-user question and returned safe clarification.",
            "tables_used": ["DUAL"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "pending_order_needs_business_filter",
            "parameters": {},
            "retrieved_schema": [],
        }

    # 0E) Stock by item name. Prevent over-specific item resolver from turning "keyboard stock"
    # into one exact long item name.
    if "stock" in q_l and not ("purchase" in q_l or "supplier" in q_l):
        item = extract_item()
        if item:
            sql = f"""
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
WHERE {like_clause("INV.ITEM_NAME", item)}
ORDER BY INV.ITEM_NAME, S.ITEMCODE
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched stock by item name.",
                "tables_used": ["INVENTORY.ITEMSTOCK", "INVENTORY.INVITEMS"],
                "relationships_used": ["INVENTORY.ITEMSTOCK.ITEMCODE = INVENTORY.INVITEMS.ITEM_CODE"],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "stock_by_item_name",
                "parameters": {"item_name": item},
                "retrieved_schema": [],
            }

    # 0F) Multi-item latest purchase rate.
    # Example: "last purchase rate of the computer monitor and keyboard"
    if "purchase" in q_l and "rate" in q_l and " and " in q_l:
        raw_item = extract_item()
        words = [w for w in raw_item.split() if w and w not in {"AND"}]
        item_terms = [w for w in words if len(w) >= 3]

        if item_terms:
            where_clause = " OR ".join([f"UPPER(INV.ITEM_NAME) LIKE '%{t}%'" for t in item_terms])

            sql = f"""
SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE AS LAST_PURCHASE_DATE,
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
    WHERE ({where_clause})
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 100
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched multi-item purchase rate query.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [
                    "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                    "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
                ],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_rate_history_multi_material",
                "parameters": {"item_terms": item_terms},
                "retrieved_schema": [],
            }

    # 1) Supplier supplied-items: "PRIME COMPU system what they have supplied"
    if ("what" in q_l and "supplied" in q_l) or ("what they have supplied" in q_l):
        supplier = extract_supplier_name()
        if supplier:
            sql = f"""
SELECT DISTINCT
    PO.SUP_CODE,
    NVL(P.PARTYNAME, PO.SUP_CODE) AS SUPPLIER_NAME,
    INV.ITEM_CODE,
    INV.ITEM_NAME
FROM INVENTORY.PURCHASEORDER PO
JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
WHERE {like_clause("P.PARTYNAME", supplier)}
ORDER BY INV.ITEM_NAME, INV.ITEM_CODE
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched supplier supplied-items question.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [
                    "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                    "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
                ],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_items_supplied_by_supplier",
                "parameters": {"supplier_name": supplier},
                "retrieved_schema": [],
            }

    # 2) Supplier list by material/item.
    if "supplier" in q_l and "item" in q_l and ("who" in q_l or "which" in q_l or "list" in q_l or "suppliers" in q_l):
        item = extract_item()
        if item:
            sql = f"""
SELECT DISTINCT
    PO.SUP_CODE,
    NVL(P.PARTYNAME, PO.SUP_CODE) AS SUPPLIER_NAME,
    INV.ITEM_CODE,
    INV.ITEM_NAME
FROM INVENTORY.PURCHASEORDER PO
JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
WHERE {like_clause("INV.ITEM_NAME", item)}
ORDER BY SUPPLIER_NAME, INV.ITEM_NAME
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched suppliers-by-item question and preserved item text.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [
                    "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                    "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
                ],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_suppliers_by_material",
                "parameters": {"item_name": item},
                "retrieved_schema": [],
            }

    # 2B) Last year / last one year purchase history by item.
    if (
        "purchase" in q_l
        and ("last year" in q_l or "last one year" in q_l or "last 1 year" in q_l)
        and "supplier" not in q_l
    ):
        item = extract_item()
        if item:
            date_filter = date_filter_po()
            sql = f"""
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
    WHERE {like_clause("INV.ITEM_NAME", item)}
    {date_filter}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 100
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched last-year purchase history by item.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [
                    "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                    "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
                ],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_history_last_year_by_material",
                "parameters": {"item_name": item},
                "retrieved_schema": [],
            }

    # 2C) Latest/last purchase from supplier name without explicit "supplier" word.
    # Example: "last purchase from THE GALAXY"
    if re.search(r"\b(last|latest)\s+purchase\s+from\b", q_l):
        supplier = extract_supplier_name()
        if supplier:
            sql = f"""
SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.SUP_CODE,
        NVL(P.PARTYNAME, PO.SUP_CODE) AS SUPPLIER_NAME,
        INV.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE {like_clause("P.PARTYNAME", supplier)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 1
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched latest purchase from supplier name.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [
                    "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                    "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
                ],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_last_supply_by_supplier",
                "parameters": {"supplier_name": supplier},
                "retrieved_schema": [],
            }

    # 2D) Last purchased supplier/date/rate by material.
    # Examples:
    # "mouse last purchased supplier name"
    # "material mouse last purchased date?"
    # "material mouse last purchased rate?"
    if (
        ("last purchased" in q_l or "last purchase" in q_l)
        and " from " not in f" {q_l} "
        and (
            "supplier name" in q_l
            or "purchased supplier" in q_l
            or "purchased date" in q_l
            or "purchased rate" in q_l
            or q_l.strip().startswith("material ")
        )
    ):
        item = extract_item()
        if item:
            sql = f"""
SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE AS LAST_PURCHASE_DATE,
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
    WHERE {like_clause("INV.ITEM_NAME", item)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 1
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched last purchased supplier/date/rate by item.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [
                    "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                    "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
                ],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_last_n_purchases_by_material",
                "parameters": {"item_name": item, "limit": 1},
                "retrieved_schema": [],
            }

    # 3) Latest/last purchase order/rate by quoted or simple item.
    if (
        ("latest purchase" in q_l or "last purchase" in q_l or "purchase rate" in q_l or "purchase order no" in q_l or "purchase order number" in q_l)
        and "supplier" not in q_l
        and " from " not in f" {q_l} "
        and not ("list" in q_l and "purchase" in q_l and "rate" in q_l)
    ):
        item = extract_item()
        if item:
            n = limit_n(1)
            date_filter = date_filter_po()
            sql = f"""
SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE AS LAST_PURCHASE_DATE,
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
    WHERE {like_clause("INV.ITEM_NAME", item)}
    {date_filter}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= {n}
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched latest/list purchase by item and preserved quoted item text.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [
                    "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                    "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
                ],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_last_n_purchases_by_material",
                "parameters": {"item_name": item, "limit": n},
                "retrieved_schema": [],
            }

    # 4) "list all purchase rate of monitor"
    if "list" in q_l and "purchase" in q_l and "rate" in q_l:
        item = extract_item()
        if item:
            sql = f"""
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
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE {like_clause("INV.ITEM_NAME", item)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 100
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched list purchase rates by item.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_rate_history_by_material",
                "parameters": {"item_name": item},
                "retrieved_schema": [],
            }

    # 5) Supplier latest/last supply/purchase. Preserve supplier name; do not over-resolve.
    if ("supplier" in q_l or "galaxy" in q_l) and ("last supply" in q_l or "latest purchase" in q_l or "last purchase" in q_l):
        supplier = extract_supplier_name()
        code_match = re.search(r"\bsupplier\s+([A-Za-z0-9_-]+)\b", q, flags=re.I)
        supplier_code = clean_code(code_match.group(1)) if code_match else ""

        if supplier or supplier_code:
            where_parts = []
            if supplier_code:
                where_parts.append(f"UPPER(PO.SUP_CODE) = '{supplier_code}'")
            if supplier:
                where_parts.append(like_clause("P.PARTYNAME", supplier))

            sql = f"""
SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.SUP_CODE,
        NVL(P.PARTYNAME, PO.SUP_CODE) AS SUPPLIER_NAME,
        INV.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE {" OR ".join(where_parts)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 1
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched supplier latest/last supply and preserved supplier text.",
                "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
                "relationships_used": [],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "purchase_last_supply_by_supplier",
                "parameters": {"supplier_name": supplier, "supplier_code": supplier_code},
                "retrieved_schema": [],
            }

    # 6) Document details by party.
    if "document" in q_l and "party" in q_l:
        m = re.search(r"\bparty\s+([A-Za-z0-9_-]+)\b", q, flags=re.I)
        code = clean_code(m.group(1)) if m else ""
        y = re.search(r"\b(20\d{2})\b", q_l)
        if code:
            date_filter = ""
            if y:
                year = y.group(1)
                date_filter = (
                    " AND REGEXP_LIKE(TO_CHAR(DOCDATE), '^[0-9]{8}$')"
                    f" AND TO_CHAR(DOCDATE) BETWEEN '{year}0101' AND '{year}1231'"
                )

            sql = f"""
SELECT ID, PARTYCODE, DEPTCODE, CATECODE, REFNO, DOCDATE, DOCNAME, FILEFORMAT,
       ENTRYDATE, ENTRYUSER, IAAUTH, APPROVEUSER, ENQUIRYDOCUMENTSTATUS,
       BLNUMBER, BLDATE, INVNO, INVDATE
FROM ADMIN.DOCUMENT
WHERE UPPER(PARTYCODE) = '{code}'{date_filter}
ORDER BY DOCDATE DESC NULLS LAST, ID DESC
""".strip()

            # Guard: if the user asked a year but the SQL did not get the year filter,
            # inject a safe DOCDATE filter before ORDER BY.
            if y and "TO_CHAR(DOCDATE) BETWEEN" not in sql:
                year = y.group(1)
                forced_date_filter = (
                    " AND REGEXP_LIKE(TO_CHAR(DOCDATE), '^[0-9]{8}$')"
                    f" AND TO_CHAR(DOCDATE) BETWEEN '{year}0101' AND '{year}1231'"
                )
                sql = sql.replace(
                    "ORDER BY DOCDATE DESC NULLS LAST, ID DESC",
                    forced_date_filter + "\nORDER BY DOCDATE DESC NULLS LAST, ID DESC",
                    1,
                )

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched ADMIN.DOCUMENT by party code.",
                "tables_used": ["ADMIN.DOCUMENT"],
                "relationships_used": [],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "document_details_by_party_code",
                "parameters": {"party_code": code},
                "retrieved_schema": ["ADMIN.DOCUMENT"],
            }

    # 7) Vehicle movement by supplier.
    if "vehicle" in q_l and "supplier" in q_l:
        m = re.search(r"\bsupplier\s+([A-Za-z0-9_-]+)\b", q, flags=re.I)
        code = clean_code(m.group(1)) if m else ""
        if code:
            sql = f"""
SELECT
    V.ID,
    V.VEHICLENO,
    V.DRIVERNAME,
    V.SUP_CODE,
    V.MILLCODE,
    V.ENTRYDATE,
    V.USED_STATUS,
    V.USEDTABLE,
    V.USEDTABLE_ID,
    V.STATUS,
    V.USED_DATE,
    D.RECORDTIME,
    D.STARTTIME,
    D.ENDTIME,
    D.FILENAME,
    D.IMG_FILENAME
FROM ADMIN.TRN_VEHICLEMOVEMENT V
LEFT JOIN ADMIN.TRN_VEHICLEMOVEMENT_DETAILS D ON D.TRANSID = V.ID
WHERE UPPER(V.SUP_CODE) = '{code}'
ORDER BY V.ENTRYDATE DESC NULLS LAST, V.ID DESC
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched vehicle movement by supplier code.",
                "tables_used": ["ADMIN.TRN_VEHICLEMOVEMENT", "ADMIN.TRN_VEHICLEMOVEMENT_DETAILS"],
                "relationships_used": ["ADMIN.TRN_VEHICLEMOVEMENT.ID = ADMIN.TRN_VEHICLEMOVEMENT_DETAILS.TRANSID"],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "vehicle_movement_by_supplier_code",
                "parameters": {"supplier_code": code},
                "retrieved_schema": [],
            }

    # 8) Camera details.
    if "camera" in q_l and ("ip" in q_l or "details" in q_l):
        sql = """
SELECT ID, CAMERAIP, CAMERA_NAME, MILLCODE, ACTIVESTATUS, UNIT, HO_GODOWN, ANPR_STATUS
FROM ADMIN.CAMERAIP
ORDER BY ID DESC
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched camera IP details.",
            "tables_used": ["ADMIN.CAMERAIP"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "camera_details",
            "parameters": {},
            "retrieved_schema": ["ADMIN.CAMERAIP"],
        }

    # 9) Latest MRS number by item.
    if "mrs" in q_l and ("latest" in q_l or "lattest" in q_l or "last" in q_l) and ("item" in q_l or "scanner" in q_l):
        item = extract_item()
        if item:
            sql = f"""
SELECT *
FROM (
    SELECT
        M.MRSNO,
        M.MRSDATE,
        M.ITEM_CODE,
        INV.ITEM_NAME,
        M.QTY,
        M.UNIT_CODE,
        M.DEPT_CODE,
        M.DUEDATE,
        M.ORDERNO,
        M.APPROVALSTATUS,
        M.READYFORAPPROVAL,
        M.REJECTIONSTATUS,
        M.STORESREJECTIONSTATUS
    FROM INVENTORY.MRS_TEMP M
    JOIN INVENTORY.INVITEMS INV ON M.ITEM_CODE = INV.ITEM_CODE
    WHERE {like_clause("INV.ITEM_NAME", item)}
    ORDER BY M.MRSDATE DESC NULLS LAST, M.MRSNO DESC
)
WHERE ROWNUM <= 1
""".strip()

            return {
                "question": question,
                "sql": sql,
                "explanation": "Log-learning router matched latest MRS by item.",
                "tables_used": ["INVENTORY.MRS_TEMP", "INVENTORY.INVITEMS"],
                "relationships_used": ["INVENTORY.MRS_TEMP.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE"],
                "confidence": 0.99,
                "source": "log_learning_router",
                "intent": "latest_mrs_by_item_name",
                "parameters": {"item_name": item},
                "retrieved_schema": [],
            }

    # 10) Describe PARTYMASTER metadata.
    if "describe" in q_l and "partymaster" in q_l:
        sql = """
SELECT COLUMN_ID, COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE
FROM ALL_TAB_COLUMNS
WHERE OWNER = 'SCM'
  AND TABLE_NAME = 'PARTYMASTER'
ORDER BY COLUMN_ID
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "Log-learning router matched table description request for SCM.PARTYMASTER.",
            "tables_used": ["ALL_TAB_COLUMNS"],
            "relationships_used": [],
            "confidence": 0.99,
            "source": "log_learning_router",
            "intent": "describe_partymaster_table",
            "parameters": {"owner": "SCM", "table_name": "PARTYMASTER"},
            "retrieved_schema": ["SCM.PARTYMASTER"],
        }

    return None

def _nlp_template_override(question: str) -> dict[str, Any] | None:
    """
    High-confidence NLP pre-router for /ask.

    This does NOT generate free-form SQL.
    It only maps already-reviewed Rasa intents/entities to deterministic SELECT templates.
    """
    manual_log_result = _log_learning_router(question)
    if manual_log_result:
        return manual_log_result

    import re

    try:
        from app.nlp_router_bridge import build_nlp_router_candidate
        candidate = build_nlp_router_candidate(question).to_dict()
    except Exception:
        return None

    if not candidate.get("success"):
        return None

    confidence = float(candidate.get("confidence") or 0)
    if confidence < 0.95:
        return None

    if not candidate.get("required_entities_ok"):
        return None

    intent = candidate.get("intent")
    entities = candidate.get("entities") or {}

    def clean_like(value: str) -> str:
        value = str(value or "").strip()
        value = re.sub(r"[;'\"]", "", value)
        value = re.sub(r"\s+", " ", value)
        return value.upper()

    def clean_code(value: str) -> str:
        value = str(value or "").strip().upper()
        value = re.sub(r"[;'\"]", "", value)
        value = re.sub(r"\s+", "", value)
        if not re.fullmatch(r"[A-Z0-9_-]+", value):
            return ""
        return value

    if intent == "purchase_last_supplier_by_material":
        item_name = clean_like(entities.get("item_name"))
        if not item_name:
            return None

        sql = f"""
SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.INVQTY,
        (NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) AS RECEIPT_PENDING_QTY,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE UPPER(INV.ITEM_NAME) LIKE '%{item_name}%'
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 1
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "NLP pre-router matched purchase_last_supplier_by_material. It searches material name in INVITEMS and avoids supplier/address mis-resolution.",
            "tables_used": ["INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"],
            "relationships_used": [
                "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
                "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
            ],
            "confidence": confidence,
            "source": "nlp_pre_router",
            "intent": intent,
            "parameters": {"item_name": item_name},
            "retrieved_schema": [],
        }

    if intent == "cashbank_voucher_details":
        party_code = clean_code(entities.get("party_code"))
        if not party_code:
            return None

        sql = f"""
SELECT *
FROM (
    SELECT *
    FROM ADMIN.CASHBANK
    WHERE UPPER(ACCODE) = '{party_code}'
)
WHERE ROWNUM <= 100
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "NLP pre-router matched cashbank_voucher_details. It returns ADMIN.CASHBANK rows using ACCODE.",
            "tables_used": ["ADMIN.CASHBANK"],
            "relationships_used": [],
            "confidence": confidence,
            "source": "nlp_pre_router",
            "intent": intent,
            "parameters": {"party_code": party_code},
            "retrieved_schema": ["ADMIN.CASHBANK"],
        }

    if intent == "hrd_employee_or_attendance":
        empcode = clean_code(entities.get("empcode"))
        if not empcode or not empcode.isdigit():
            return None

        sql = f"""
SELECT *
FROM (
    SELECT *
    FROM HRDNEW.CURRENTATTENDANCE
    WHERE EMPCODE = {empcode}
)
WHERE ROWNUM <= 100
""".strip()

        return {
            "question": question,
            "sql": sql,
            "explanation": "NLP pre-router matched hrd_employee_or_attendance. It uses HRDNEW.CURRENTATTENDANCE for employee attendance.",
            "tables_used": ["HRDNEW.CURRENTATTENDANCE"],
            "relationships_used": [],
            "confidence": confidence,
            "source": "nlp_pre_router",
            "intent": intent,
            "parameters": {"empcode": empcode},
            "retrieved_schema": ["HRDNEW.CURRENTATTENDANCE"],
        }

    return None


def _apply_known_sql_corrections(sql: str, sql_result: dict[str, Any] | None = None) -> str:
    """
    Final deterministic SQL corrections before execution.

    This is not free-form generation.
    It only fixes known verified table mapping mistakes.
    """
    if not sql:
        return sql

    sql_result = sql_result or {}
    intent = str(sql_result.get("intent") or "").strip()

    if intent in {"stock_by_item_name", "stock_availability_by_item_name", "stock_by_item_code"}:
        sql = sql.replace("FROM INVENTORY.STOCK S", "FROM INVENTORY.ITEMSTOCK S")
        sql = sql.replace("JOIN INVENTORY.STOCK S", "JOIN INVENTORY.ITEMSTOCK S")
        sql = sql.replace("INVENTORY.STOCK.ITEMCODE", "INVENTORY.ITEMSTOCK.ITEMCODE")

        tables_used = sql_result.get("tables_used")
        if isinstance(tables_used, list):
            sql_result["tables_used"] = [
                "INVENTORY.ITEMSTOCK" if str(t).upper() == "INVENTORY.STOCK" else t
                for t in tables_used
            ]

        relationships_used = sql_result.get("relationships_used")
        if isinstance(relationships_used, list):
            sql_result["relationships_used"] = [
                str(r).replace("INVENTORY.STOCK.ITEMCODE", "INVENTORY.ITEMSTOCK.ITEMCODE")
                for r in relationships_used
            ]

        explanation = str(sql_result.get("explanation") or "")
        if "INVENTORY.STOCK" in explanation:
            sql_result["explanation"] = explanation.replace("INVENTORY.STOCK", "INVENTORY.ITEMSTOCK")

    return sql

def _known_schema_fallback_sql(question: str) -> dict[str, Any] | None:
    """
    Deterministic fallback hints for common admin/HR questions.
    These still go through run_safe_select(), so SQL safety is preserved.
    """
    import re

    q = question.lower()

    def extract_code(patterns):
        for pattern in patterns:
            m = re.search(pattern, question, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip().upper()
        return None

    party_code = extract_code([
        r"\bparty\s+code\s+([A-Za-z0-9_-]+)",
        r"\bparty\s+([A-Za-z0-9_-]+)",
        r"\bsupplier\s+([A-Za-z0-9_-]+)",
        r"\bsupplier\s+code\s+([A-Za-z0-9_-]+)",
        r"\bvendor\s+([A-Za-z0-9_-]+)",
        r"\bvendor\s+code\s+([A-Za-z0-9_-]+)",
    ])

    empcode = extract_code([
        r"\bempcode\s+([0-9]+)",
        r"\bemployee\s+([0-9]+)",
        r"\bstaff\s+([0-9]+)",
    ])

    if "camera" in q and "ip" in q:
        return {
            "question": question,
            "sql": (
                "SELECT ID, CAMERAIP, CAMERA_NAME, MILLCODE, ACTIVESTATUS, UNIT, HO_GODOWN, ANPR_STATUS "
                "FROM ADMIN.CAMERAIP"
            ),
            "explanation": "Camera IP details are stored in ADMIN.CAMERAIP.",
            "tables_used": ["ADMIN.CAMERAIP"],
            "relationships_used": [],
            "confidence": 0.95,
            "retrieved_schema": ["ADMIN.CAMERAIP"],
            "source": "qwen_schema_fallback",
        }

    if "cashbank" in q or "voucher" in q:
        where_clause = f" WHERE UPPER(PARTYCODE) = '{party_code}'" if party_code else ""
        return {
            "question": question,
            "sql": (
                "SELECT VOCNO, VOCDATE, VOCTYPE, PARTYCODE, GSTPARTYCODE, ACCODE, CTCODE, "
                "AMOUNT, DEBIT, CREDIT, NARR, REF, BILLNO, BILLDATE, USERCODE, CREATIONDATETIME "
                "FROM ADMIN.CASHBANK"
                + where_clause
            ),
            "explanation": "Cashbank/voucher details are stored in ADMIN.CASHBANK.",
            "tables_used": ["ADMIN.CASHBANK"],
            "relationships_used": [],
            "confidence": 0.9,
            "retrieved_schema": ["ADMIN.CASHBANK"],
            "source": "qwen_schema_fallback",
        }

    if "vehicle" in q and "movement" in q:
        where_clause = f" WHERE UPPER(V.SUP_CODE) = '{party_code}'" if party_code else ""
        return {
            "question": question,
            "sql": (
                "SELECT V.ID, V.VEHICLENO, V.DRIVERNAME, V.SUP_CODE, V.MILLCODE, V.ENTRYDATE, "
                "V.USED_STATUS, V.USEDTABLE, V.USEDTABLE_ID, V.STATUS, V.USED_DATE, "
                "D.RECORDTIME, D.STARTTIME, D.ENDTIME, D.FILENAME, D.IMG_FILENAME "
                "FROM ADMIN.TRN_VEHICLEMOVEMENT V "
                "LEFT JOIN ADMIN.TRN_VEHICLEMOVEMENT_DETAILS D ON D.TRANSID = V.ID"
                + where_clause
                + " ORDER BY V.ENTRYDATE DESC NULLS LAST, V.ID DESC"
            ),
            "explanation": "Vehicle movement header is in ADMIN.TRN_VEHICLEMOVEMENT and image/time details are in ADMIN.TRN_VEHICLEMOVEMENT_DETAILS.",
            "tables_used": ["ADMIN.TRN_VEHICLEMOVEMENT", "ADMIN.TRN_VEHICLEMOVEMENT_DETAILS"],
            "relationships_used": ["ADMIN.TRN_VEHICLEMOVEMENT_DETAILS.TRANSID = ADMIN.TRN_VEHICLEMOVEMENT.ID"],
            "confidence": 0.9,
            "retrieved_schema": ["ADMIN.TRN_VEHICLEMOVEMENT", "ADMIN.TRN_VEHICLEMOVEMENT_DETAILS"],
            "source": "qwen_schema_fallback",
        }

    if "current attendance" in q:
        where_clause = f" WHERE EMPCODE = {empcode}" if empcode else ""
        return {
            "question": question,
            "sql": (
                "SELECT EMPCODE, INDATE, INTIME, OUTDATE, OUTTIME, LATETIME, STATUS, SHIFT, "
                "SALARYDAY, DEPTCODE, UNITCODE, WORKEDHRS, AUTHSTATUS, APPROVESTATUS, "
                "AOAUTHSTATUS, ONDUTYSTATUS, CAMERAFLAG "
                "FROM HRDNEW.CURRENTATTENDANCE"
                + where_clause
                + " ORDER BY INDATE DESC, INTIME DESC NULLS LAST"
            ),
            "explanation": "Current attendance details are stored in HRDNEW.CURRENTATTENDANCE.",
            "tables_used": ["HRDNEW.CURRENTATTENDANCE"],
            "relationships_used": [],
            "confidence": 0.95,
            "retrieved_schema": ["HRDNEW.CURRENTATTENDANCE"],
            "source": "qwen_schema_fallback",
        }

    if "department authentication" in q or "dept authentication" in q:
        where_clause = f" WHERE EMPCODE = {empcode}" if empcode else ""
        return {
            "question": question,
            "sql": (
                "SELECT EMPCODE, DEPTCODE, UNITCODE, AOSTATUS, AUTHSTATUS, COMPADJUSTSTATUS, OLDEMPCODE "
                "FROM HRDNEW.HODDEPTAUTHENTICATION"
                + where_clause
            ),
            "explanation": "Department authentication details are stored in HRDNEW.HODDEPTAUTHENTICATION.",
            "tables_used": ["HRDNEW.HODDEPTAUTHENTICATION"],
            "relationships_used": [],
            "confidence": 0.95,
            "retrieved_schema": ["HRDNEW.HODDEPTAUTHENTICATION"],
            "source": "qwen_schema_fallback",
        }

    if "document" in q:
        where_clause = f" WHERE UPPER(PARTYCODE) = '{party_code}'" if party_code else ""
        return {
            "question": question,
            "sql": (
                "SELECT ID, PARTYCODE, DEPTCODE, CATECODE, REFNO, DOCDATE, DOCNAME, FILEFORMAT, "
                "ENTRYDATE, ENTRYUSER, IAAUTH, APPROVEUSER, ENQUIRYDOCUMENTSTATUS, BLNUMBER, "
                "BLDATE, INVNO, INVDATE "
                "FROM ADMIN.DOCUMENT"
                + where_clause
                + " ORDER BY DOCDATE DESC NULLS LAST, ID DESC"
            ),
            "explanation": "Document details are stored in ADMIN.DOCUMENT.",
            "tables_used": ["ADMIN.DOCUMENT"],
            "relationships_used": [],
            "confidence": 0.9,
            "retrieved_schema": ["ADMIN.DOCUMENT"],
            "source": "qwen_schema_fallback",
        }

    return None


def _answer_question_core(question: str) -> dict[str, Any]:
    request_id = new_request_id()
    set_request_id(request_id)

    start_time = time.time()

    log_event(
        request_id,
        "question_received",
        "User question received",
        question=question,
    )

    try:
        understanding = understand_question(question)
        sql_question = strip_date_filter_phrases(question)

        resolved_values = {
            "question": sql_question,
            "exact_terms": [],
            "semantic_queries": [],
            "elapsed_seconds": 0,
            "results": [],
            "resolver_status": "not_run",
        }

        try:
            from app.hybrid_value_resolver import resolve_question_values

            resolved_values = resolve_question_values(sql_question, final_limit=10)
            resolved_values["resolver_status"] = "ok"

            log_event(
                request_id,
                "hybrid_value_resolver_complete",
                "Hybrid SQLite/Qdrant value resolver completed",
                resolved_values_preview=_safe_preview(resolved_values),
                elapsed_seconds=resolved_values.get("elapsed_seconds"),
                result_count=len(resolved_values.get("results", [])),
            )

        except Exception as resolver_exc:
            resolved_values = {
                "question": sql_question,
                "exact_terms": [],
                "semantic_queries": [],
                "elapsed_seconds": 0,
                "results": [],
                "resolver_status": "error",
                "error": f"{type(resolver_exc).__name__}: {resolver_exc}",
            }

            log_event(
                request_id,
                "hybrid_value_resolver_failed",
                "Hybrid value resolver failed; continuing without resolver",
                error=resolved_values["error"],
            )

        log_event(
            request_id,
            "question_understanding",
            "Question understanding generated",
            understanding=understanding,
            resolved_values_preview=_safe_preview(resolved_values),
        )

        planner_module = planner_detect_module(sql_question, understanding, resolved_values)

        router_question = planner_resolver_enriched_question(sql_question, resolved_values, planner_module)
        if router_question != sql_question:
            log_event(
                request_id,
                "resolver_enriched_router_question",
                "Router question enriched using resolved database value",
                original_question=sql_question,
                router_question=router_question,
                resolved_values_preview=_safe_preview(resolved_values),
            )

        nlp_template_result = _nlp_template_override(sql_question)

        if nlp_template_result:
            router_question = sql_question
            template_result = nlp_template_result

            log_event(
                request_id,
                "nlp_pre_router_matched",
                "High-confidence NLP pre-router selected deterministic SQL template",
                intent=template_result.get("intent"),
                source=template_result.get("source"),
                parameters=template_result.get("parameters"),
            )
        else:
            template_result = planner_match_router(
            
                router_question,
            
                resolved_values=resolved_values,
            
                module=planner_module,
            
                original_question=sql_question,
            
            )
        if template_result:
            sql_result = template_result
            sql = sql_result["sql"]

            log_event(
                request_id,
                "business_template_matched",
                "Business template matched; skipping Qdrant and Qwen",
                source=sql_result.get("source"),
                intent=sql_result.get("intent"),
                confidence=sql_result.get("confidence"),
                parameters=sql_result.get("parameters"),
                sql=sql,
                tables_used=sql_result.get("tables_used", []),
                relationships_used=sql_result.get("relationships_used", []),
                full_sql_result_preview=_safe_preview(sql_result),
            )

        else:
            log_event(
                request_id,
                "sql_generation_start",
                "No business template matched; starting Qdrant/Qwen SQL generation",
            )

            try:
                sql_result = _known_schema_fallback_sql(sql_question)
                if sql_result is None:
                    sql_result = generate_select_sql_v2(sql_question, limit=5, understanding=understanding)

                # Ensure fallback source label
                sql_result["source"] = sql_result.get("source") or "qwen_schema_fallback"
                sql = sql_result.get("sql")
                sql = apply_date_filter_to_sql(
                    sql,
                    question,
                    intent=sql_result.get("intent"),
                    tables_used=sql_result.get("tables_used"),
                )
                sql_result["sql"] = sql
            except Exception as gen_exc:
                # Do not crash; return a safe user-facing message when SQL cannot be generated safely
                log_event(
                    request_id,
                    "sql_generation_failed",
                    "Qwen/schema fallback could not produce a safe SELECT",
                    error=str(gen_exc),
                )

                return {
                    "success": False,
                    "request_id": request_id,
                    "question": question,
                    "error": "I could not safely create a SELECT query for this question. Please ask with a table/module name or more details.",
                    "error_type": type(gen_exc).__name__,
                    "source": "qwen_schema_fallback",
                }

        sql_before_resolved_material_patch = sql
        sql = _apply_resolved_material_to_sql(sql, resolved_values)
        sql_result["sql"] = sql

        if sql != sql_before_resolved_material_patch:
            log_event(
                request_id,
                "resolved_material_sql_patch_applied",
                "Applied exact resolved material value to generated SQL",
                before_sql=sql_before_resolved_material_patch,
                after_sql=sql,
                resolved_values_preview=_safe_preview(resolved_values),
            )

        log_event(
            request_id,
            "sql_generation_complete",
            "SQL generated successfully",
            source=sql_result.get("source", "qwen"),
            intent=sql_result.get("intent"),
            parameters=sql_result.get("parameters"),
            sql=sql,
            explanation=sql_result.get("explanation"),
            tables_used=sql_result.get("tables_used", []),
            relationships_used=sql_result.get("relationships_used", []),
            confidence=sql_result.get("confidence", 0.0),
            full_sql_result_preview=_safe_preview(sql_result),
        )

        log_event(
            request_id,
            "oracle_execution_start",
            "Starting safe Oracle SELECT execution",
            sql=sql,
        )

        # Some deterministic log-learning templates already include safe date filters.
        # Do not let the generic date-filter rewriter modify ADMIN.DOCUMENT year queries.
        skip_generic_date_filter = (
            sql_result.get("source") in {"log_learning_router", "purchase_analytics_router"}
            and sql_result.get("intent") in {
                "document_details_by_party_code",
                "stock_by_item_name",
                "purchase_this_year_by_material",
            }
        )

        if not skip_generic_date_filter:
            sql = apply_date_filter_to_sql(
                sql,
                question,
                intent=sql_result.get("intent"),
                tables_used=sql_result.get("tables_used"),
            )

        sql_result["sql"] = sql

        # Do not rewrite deterministic ADMIN.DOCUMENT SQL.
        # The correction layer can remove the year filter for document queries.
        skip_known_sql_corrections = (
            sql_result.get("source") in {"log_learning_router", "purchase_analytics_router"}
            and sql_result.get("intent") in {
                "document_details_by_party_code",
                "stock_by_item_name",
                "purchase_this_year_by_material",
            }
        )

        if not skip_known_sql_corrections:
            sql = _apply_known_sql_corrections(sql, sql_result)

        if sql_result is not None:
            sql_result["sql"] = sql

        # Final guard: preserve deterministic log-learning SQL exactly as produced
        # for templates where generic SQL correction/date layers may over-resolve values.
        if (
            sql_result.get("source") == "log_learning_router"
            and sql_result.get("intent") in {
                "document_details_by_party_code",
                "stock_by_item_name",
            }
        ):
            preserved_result = _log_learning_router(question)
            if preserved_result and preserved_result.get("sql"):
                sql = preserved_result["sql"]
                sql_result["sql"] = sql

        db_result = run_safe_select(sql)

        elapsed_ms = int((time.time() - start_time) * 1000)

        log_event(
            request_id,
            "oracle_execution_complete",
            "Oracle SELECT executed successfully",
            row_count=db_result.get("row_count", 0),
            columns=db_result.get("columns", []),
            elapsed_ms=elapsed_ms,
        )

        result = {
            "success": True,
            "request_id": request_id,
            "question": question,
            "sql": sql,
            "explanation": sql_result.get("explanation"),
            "tables_used": sql_result.get("tables_used", []),
            "relationships_used": sql_result.get("relationships_used", []),
            "confidence": sql_result.get("confidence", 0.0),
            "source": sql_result.get("source", "qwen"),
            "intent": sql_result.get("intent"),
            "parameters": sql_result.get("parameters"),
            "columns": db_result.get("columns", []),
            "rows": db_result.get("rows", []),
            "row_count": db_result.get("row_count", 0),
            "retrieved_schema": sql_result.get("retrieved_schema", []),
            "elapsed_ms": elapsed_ms,
            "understanding": understanding,
            "planner_module": planner_module,
            "resolved_values": resolved_values,
            "router_question": router_question,
        }

        result["answer"] = format_answer(result)

        return result

    except Exception as exc:
        elapsed_ms = int((time.time() - start_time) * 1000)
        safe_message = _safe_error_message(exc)

        log_event(
            request_id,
            "error",
            "Backend failed while processing question",
            question=question,
            error_type=type(exc).__name__,
            error=str(exc),
            safe_message=safe_message,
            elapsed_ms=elapsed_ms,
        )

        return {
            "success": False,
            "request_id": request_id,
            "question": question,
            "error": safe_message,
            "error_type": type(exc).__name__,
            "elapsed_ms": elapsed_ms,
        }


if __name__ == "__main__":
    result = answer_question("show supplier details")
    print(json.dumps(result, indent=2, default=str))


def answer_question(question: str, *args, **kwargs):
    """
    Public wrapper around the real query engine.
    Logs every user question to logs/user_questions.jsonl for future tuning.
    """
    import time
    from app.question_logger import log_question_event

    session_id = kwargs.pop("session_id", None) or "default"
    user_id = kwargs.pop("user_id", None)
    client_ip = kwargs.pop("client_ip", None)

    start = time.perf_counter()
    result = None

    try:
        result = _answer_question_core(question, *args, **kwargs)
        return result
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        log_question_event(
            question=question,
            result=None,
            elapsed_ms=elapsed_ms,
            session_id=session_id,
            user_id=user_id,
            client_ip=client_ip,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        if result is not None:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            try:
                log_question_event(
                    question=question,
                    result=result,
                    elapsed_ms=elapsed_ms,
                    session_id=session_id,
                    user_id=user_id,
                    client_ip=client_ip,
                )
            except Exception:
                # Logging must never break the main answer flow.
                pass

