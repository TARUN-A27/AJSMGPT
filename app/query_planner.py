from __future__ import annotations

import json
import re
import time
from typing import Any

from app.business_template_engine import match_business_template
from app.date_filter_engine import strip_date_filter_phrases
from app.hybrid_value_resolver import resolve_question_values
from app.mrs_template_router import match_mrs_template
from app.purchase_analytics_router import match_purchase_analytics_template
from app.question_understanding import understand_question


UNSAFE_SQL_WORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "MERGE",
    "CREATE",
    "EXEC",
    "EXECUTE",
    "BEGIN",
    "DECLARE",
}


def _normalize(value: Any) -> str:
    value = str(value or "").upper()
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _safe_sql_preview(sql: str | None, limit: int = 1200) -> str | None:
    if not sql:
        return None
    sql = str(sql)
    if len(sql) > limit:
        return sql[:limit] + "...[truncated]"
    return sql


def _is_safe_select_sql(sql: str | None) -> bool:
    if not sql:
        return False

    compact = re.sub(r"\s+", " ", sql.strip()).upper()
    if not compact.startswith("SELECT"):
        return False

    tokens = set(re.findall(r"[A-Z]+", compact))
    return not bool(tokens.intersection(UNSAFE_SQL_WORDS))



def _best_resolved_material_value(resolved_values: dict[str, Any] | None) -> str | None:
    if not resolved_values:
        return None

    for item in resolved_values.get("results", []) or []:
        if item.get("entity_type") != "material":
            continue

        value = str(item.get("resolved_value") or "").strip()
        score = float(item.get("score") or 0)

        if value and score >= 80:
            return value

    return None


def _patch_template_sql_with_resolved_material(sql: str | None, resolved_values: dict[str, Any] | None) -> str | None:
    if not sql:
        return sql

    material = _best_resolved_material_value(resolved_values)
    if not material:
        return sql

    sql_upper = sql.upper()

    # plan_broad_one_word_material_sql_guard:
    # Do not patch broad one-word stock/MRS/issue searches.
    # Example: stock for keyboard should remain LIKE '%KEYBOARD%',
    # not one specific resolved item such as KEYBOARD W MOUSE WIRELESS COMBO.
    semantic_queries_for_guard = [
        str(x or "").strip()
        for x in ((resolved_values or {}).get("semantic_queries") or [])
    ]
    is_broad_one_word_material = any(
        len(re.findall(r"[A-Za-z0-9]+", phrase)) <= 1
        for phrase in semantic_queries_for_guard
        if phrase
    )

    if is_broad_one_word_material and any(
        token in sql_upper
        for token in ["MRS_TEMP", "ITEMSTOCK", "INVENTORY.STOCK", " STOCK ", "STOCK S", " ISSUE ", " INVENTORY.ISSUE"]
    ):
        return sql

    if "INV.ITEM_NAME" not in sql_upper:
        return sql

    material_sql = material.replace("'", "''").upper()

    return re.sub(
        r"UPPER\s*\(\s*INV\.ITEM_NAME\s*\)\s+LIKE\s+'%[^']*%'",
        f"UPPER(INV.ITEM_NAME) LIKE '%{material_sql}%'",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )


def _detect_module(question: str, understanding: dict[str, Any], resolved_values: dict[str, Any]) -> str:
    q = _normalize(question)

    if "CASHBANK" in q or "VOUCHER" in q:
        return "admin_cashbank"

    if "DOCUMENT" in q or "DOC" in q:
        return "admin_document"

    if "VEHICLE" in q and "MOVEMENT" in q:
        return "admin_vehicle_movement"

    if "CAMERA" in q or "CCTV" in q:
        return "admin_camera"

    if "ATTENDANCE" in q or "AUTHENTICATION" in q or "EMPCODE" in q:
        return "hrd"

    if "MRS" in q or "MATERIAL REQUISITION" in q:
        return "inventory_mrs"

    if any(word in q for word in ["PURCHASE", "SUPPLY", "SUPPLIER", "VENDOR", "PARTY", "PO", "ORDER"]):
        return "purchase"

    if any(word in q for word in ["STOCK", "ITEM", "MATERIAL", "GRN", "ISSUE"]):
        return "inventory"

    domain = understanding.get("domain")
    return str(domain or "general")


def _top_results(resolved_values: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    out = []
    for item in (resolved_values.get("results") or [])[:limit]:
        out.append(
            {
                "resolved_value": item.get("resolved_value"),
                "entity_type": item.get("entity_type"),
                "location": item.get("location"),
                "score": item.get("score"),
                "source": item.get("source"),
                "reason": item.get("reason"),
            }
        )
    return out


def _best_resolved_value(
    resolved_values: dict[str, Any],
    allowed_types: set[str] | None = None,
    min_score: float = 80.0,
) -> dict[str, Any] | None:
    for item in resolved_values.get("results", []) or []:
        entity_type = item.get("entity_type")
        score = float(item.get("score") or 0)
        value = str(item.get("resolved_value") or "").strip()

        if not value or score < min_score:
            continue

        if allowed_types and entity_type not in allowed_types:
            continue

        return item

    return None



def _best_resolved_supplier_value(resolved_values: dict[str, Any] | None) -> str | None:
    if not resolved_values:
        return None

    for item in resolved_values.get("results", []) or []:
        if item.get("entity_type") != "supplier_or_party":
            continue

        value = str(item.get("resolved_value") or "").strip()
        score = float(item.get("score") or 0)
        location = str(item.get("location") or "").upper()

        if value and score >= 80 and ("PARTYMASTER" in location or "PARTYNAME" in location or "PARTYCODE" in location):
            return value

    return None


def _clean_supplier_phrase(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[;'\"]", "", value)
    value = re.sub(
        r"\b(last|latest|first|purchase|purchased|supply|supplied|order|orders|details?|detail|show|give|please|what|did|we|was|were|by|from|for|of)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _purchase_supplier_rewrite_candidates(
    question: str,
    resolved_values: dict[str, Any] | None = None,
) -> list[str]:
    """
    Convert supplier/company phrasing into the form purchase_analytics_router already understands.

    Examples:
      what did Prime compu systems supply last -> last supply from supplier Prime compu systems
      last purchase from THE GALAXY -> last supply from supplier THE GALAXY 2019-2020
    """
    q = question or ""
    supplier_value = _best_resolved_supplier_value(resolved_values)

    patterns = [
        # Explicit supplier/supply phrasing only.
        # Do NOT include a generic "^(.+?) last purchase" pattern because it catches item questions like:
        # "barcode chromo label last purchase"
        r"\bwhat\s+did\s+(.+?)\s+supply\s+last\b",
        r"\bwhat\s+did\s+(.+?)\s+supplied\s+last\b",
        r"\b(?:last|latest|first)\s+(?:purchase|order|supply)\s+from\s+(?:supplier|vendor|party|company)?\s*(.+)$",
        r"\b(?:show\s+)?(?:last|latest|first)\s+(?:purchase|order|supply)\s+by\s+(?:supplier|vendor|party|company)?\s*(.+)$",
        r"\b(?:supplier|vendor|party|company)\s+(.+?)\s+(?:last|latest|first)\s+(?:supply|purchase|order)\b",
        r"^(.+?)\s+(?:last|latest|first)\s+supply\b",
    ]

    candidates: list[str] = []

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.IGNORECASE)
        if not m:
            continue

        phrase = supplier_value or _clean_supplier_phrase(m.group(1))
        if not phrase:
            continue

        candidates.append(f"last supply from supplier {phrase}")

    # If resolver has a high-confidence supplier and the question is purchase/supply-like,
    # try the supplier route even if regex extraction did not catch the phrase.
    qn = _normalize(q)
    supplier_context = any(w in qn for w in ["SUPPLIER", "VENDOR", "PARTY", "COMPANY", "FROM", "BY", "SUPPLY", "SUPPLIED"])
    if supplier_value and supplier_context:
        candidates.append(f"last supply from supplier {supplier_value}")

    seen = set()
    final = []
    for item in candidates:
        key = _normalize(item)
        if key and key not in seen:
            seen.add(key)
            final.append(item)

    return final



def _clean_router_question_text(value: str) -> str:
    value = str(value or "")
    value = re.sub(r"\bTHE\s+THE\b", "THE", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _resolver_enriched_question(question: str, resolved_values: dict[str, Any], module: str) -> str:
    """
    DB-free planner enrichment.

    Keep exact-code questions unchanged.
    Use high-confidence resolver values to help routers/templates.
    """
    q = question or ""

    if resolved_values.get("exact_terms"):
        return q

    allowed_types: set[str] | None = None

    if module in {"purchase", "inventory", "inventory_mrs"}:
        allowed_types = {"material", "supplier_or_party", "department", "unit"}

    if module.startswith("admin"):
        allowed_types = {"supplier_or_party", "document", "vehicle", "camera", "generic"}

    if module == "hrd":
        allowed_types = {"employee", "department", "unit"}

    best = _best_resolved_value(resolved_values, allowed_types=allowed_types, min_score=80.0)
    if not best:
        return q

    # Guard: do not replace broad one-word material searches.
    # Example:
    #   pending MRS for keyboard
    # should remain KEYBOARD so SQL uses LIKE '%KEYBOARD%',
    # not one specific resolved item such as KEYBOARD W MOUSE WIRELESS COMBO.
    if best.get("entity_type") == "material":
        qn = _normalize(q)
        semantic_queries_for_guard = [str(x or "").strip() for x in (resolved_values.get("semantic_queries") or [])]
        input_text_for_guard = str(best.get("input_text") or "").strip()
        if input_text_for_guard:
            semantic_queries_for_guard.append(input_text_for_guard)

        is_broad_one_word_material = any(
            len(re.findall(r"[A-Za-z0-9]+", phrase)) <= 1
            for phrase in semantic_queries_for_guard
            if phrase
        )

        broad_context = any(
            word in qn
            for word in ["MRS", "STOCK", "ISSUE", "PENDING", "REQUEST", "REQUISITION"]
        )

        if is_broad_one_word_material and broad_context:
            return q

    resolved_value = str(best.get("resolved_value") or "").strip()
    if not resolved_value:
        return q

    semantic_queries = resolved_values.get("semantic_queries") or []

    for phrase in semantic_queries:
        phrase = str(phrase or "").strip()
        if not phrase:
            continue

        pattern = re.compile(re.escape(phrase), flags=re.IGNORECASE)
        if pattern.search(q):
            return _clean_router_question_text(pattern.sub(resolved_value, q, count=1))

    return _clean_router_question_text(f"{q} resolved value {resolved_value}")


def _match_router(
    question: str,
    resolved_values: dict[str, Any] | None = None,
    module: str | None = None,
    original_question: str | None = None,
) -> dict[str, Any] | None:
    """
    Router priority mirrors query_engine, but does not execute Oracle.
    Planner may rewrite supplier-style purchase questions into a form the purchase router already supports.
    """
    questions_to_try: list[str] = []

    if module == "purchase":
        questions_to_try.extend(_purchase_supplier_rewrite_candidates(original_question or question, resolved_values))
        questions_to_try.extend(_purchase_supplier_rewrite_candidates(question, resolved_values))

    questions_to_try.append(question)

    seen = set()
    final_questions = []
    for q in questions_to_try:
        key = _normalize(q)
        if key and key not in seen:
            seen.add(key)
            final_questions.append(q)

    for q in final_questions:
        result = match_purchase_analytics_template(q)
        if result:
            result = dict(result)
            if q != question:
                result["planner_rewrite_question"] = q
            return result

    return match_mrs_template(question) or match_business_template(question)


def _template_summary(template_result: dict[str, Any] | None, resolved_values: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not template_result:
        return None

    sql = _patch_template_sql_with_resolved_material(template_result.get("sql"), resolved_values)

    return {
        "matched": True,
        "source": template_result.get("source"),
        "intent": template_result.get("intent"),
        "confidence": template_result.get("confidence"),
        "parameters": template_result.get("parameters"),
        "tables_used": template_result.get("tables_used", []),
        "relationships_used": template_result.get("relationships_used", []),
        "safe_select_sql": _is_safe_select_sql(sql),
        "sql_preview": _safe_sql_preview(sql),
    }


def _fallback_hint(question: str, module: str, resolved_values: dict[str, Any]) -> dict[str, Any]:
    exact_terms = resolved_values.get("exact_terms") or []

    if module == "admin_cashbank":
        return {
            "preferred_path": "known_schema_fallback",
            "intent_hint": "cashbank_voucher_details",
            "tables_hint": ["ADMIN.CASHBANK"],
            "code_hint": exact_terms[0] if exact_terms else None,
        }

    if module == "admin_document":
        return {
            "preferred_path": "known_schema_fallback",
            "intent_hint": "document_details",
            "tables_hint": ["ADMIN.DOCUMENT"],
            "code_hint": exact_terms[0] if exact_terms else None,
        }

    if module == "hrd":
        return {
            "preferred_path": "known_schema_fallback",
            "intent_hint": "hrd_employee_or_attendance",
            "tables_hint": ["HRDNEW.CURRENTATTENDANCE", "HRDNEW.HODDEPTAUTHENTICATION"],
            "code_hint": exact_terms[0] if exact_terms else None,
        }

    return {
        "preferred_path": "schema_qdrant_then_qwen_select",
        "intent_hint": None,
        "tables_hint": [],
        "code_hint": exact_terms[0] if exact_terms else None,
    }


def plan_query(question: str) -> dict[str, Any]:
    started = time.time()

    original_question = question or ""
    sql_question = strip_date_filter_phrases(original_question)

    understanding = understand_question(original_question)
    resolved_values = resolve_question_values(sql_question, final_limit=10)

    module = _detect_module(sql_question, understanding, resolved_values)
    router_question = _resolver_enriched_question(sql_question, resolved_values, module)

    template_result = _match_router(router_question, resolved_values=resolved_values, module=module, original_question=sql_question)

    # If enrichment did not help or accidentally hurt, try original question too.
    original_template_result = None
    if not template_result and router_question != sql_question:
        original_template_result = _match_router(sql_question, resolved_values=resolved_values, module=module, original_question=sql_question)
        template_result = original_template_result

    template = _template_summary(template_result, resolved_values=resolved_values)
    fallback = _fallback_hint(sql_question, module, resolved_values)

    top = _top_results(resolved_values, limit=5)

    elapsed_ms = int((time.time() - started) * 1000)

    return {
        "success": True,
        "question": original_question,
        "sql_question": sql_question,
        "module": module,
        "understanding": understanding,
        "resolved_values": {
            "resolver_status": "ok",
            "exact_terms": resolved_values.get("exact_terms", []),
            "semantic_queries": resolved_values.get("semantic_queries", []),
            "elapsed_seconds": resolved_values.get("elapsed_seconds"),
            "top_results": top,
        },
        "router_question": router_question,
        "template": template,
        "fallback": fallback if not template else None,
        "decision": {
            "preferred_path": "template_router" if template else fallback.get("preferred_path"),
            "needs_oracle": True,
            "oracle_executed": False,
            "safe_to_execute": bool(template and template.get("safe_select_sql")) or fallback.get("preferred_path") in {
                "known_schema_fallback",
                "schema_qdrant_then_qwen_select",
            },
            "note": "Planner only. No Oracle query was executed.",
        },
        "elapsed_ms": elapsed_ms,
    }



# ---------------------------------------------------------------------------
# AJSM_STRICT_ROUTE_OVERRIDES_V1
# These overrides keep planner routing business-safe after 16M value resolution.
# They intentionally avoid auto-routing admin/cashbank/camera/GRN questions into
# purchase supplier templates just because a party/supplier-like value was found.
# ---------------------------------------------------------------------------

_AJSM_ORIGINAL_DETECT_MODULE = _detect_module
_AJSM_ORIGINAL_RESOLVER_ENRICHED_QUESTION = _resolver_enriched_question
_AJSM_ORIGINAL_MATCH_ROUTER = _match_router


def _ajsm_norm_text(value):
    return re.sub(r"\s+", " ", str(value or "").upper()).strip()


def _ajsm_template_intent(result):
    if not result:
        return None
    if isinstance(result, dict):
        return result.get("intent")
    return getattr(result, "intent", None)


def _ajsm_is_cashbank_question(question):
    q = _ajsm_norm_text(question)
    return any(x in q for x in ["CASHBANK", "CASH BANK", "VOUCHER"])


def _ajsm_is_camera_question(question):
    q = _ajsm_norm_text(question)
    return "CAMERA" in q or "CAMERAIP" in q or "CAMERA IP" in q


def _ajsm_is_grn_question(question):
    q = _ajsm_norm_text(question)
    return any(x in q for x in ["GRN", "GOODS RECEIPT", "RECEIVED DATE", "RECEIPT FOR SUPPLIER"])


def _ajsm_is_unit_query(question):
    q = _ajsm_norm_text(question)
    return " MRS " in f" {q} " and re.search(r"\bUNIT\b", q) is not None


def _ajsm_is_item_supplier_lookup(question):
    q = _ajsm_norm_text(question)

    patterns = [
        r"\bLAST\s+SUPPLIER\s+FOR\s+",
        r"\bLATEST\s+SUPPLIER\s+FOR\s+",
        r"\bSUPPLIER\s+NAME\s+FOR\s+",
        r"\bLAST\s+PURCHASED\s+SUPPLIER\s+NAME\b",
        r"\bLAST\s+PURCHASE\s+SUPPLIER\s+NAME\b",
        r"\bLAST\s+PURCHASED\s+SUPPLIER\b",
    ]

    return any(re.search(pattern, q) for pattern in patterns)


def _ajsm_extract_item_supplier_material_phrase(question):
    q = _ajsm_norm_text(question)

    patterns = [
        r"\bLAST\s+SUPPLIER\s+FOR\s+(?:THE\s+ITEM\s+|ITEM\s+|MATERIAL\s+)?(.+)$",
        r"\bLATEST\s+SUPPLIER\s+FOR\s+(?:THE\s+ITEM\s+|ITEM\s+|MATERIAL\s+)?(.+)$",
        r"\bSUPPLIER\s+NAME\s+FOR\s+(?:THE\s+ITEM\s+|ITEM\s+|MATERIAL\s+)?(.+)$",
        r"^(.+?)\s+LAST\s+PURCHASED\s+SUPPLIER\s+NAME\b",
        r"^(.+?)\s+LAST\s+PURCHASED\s+SUPPLIER\b",
        r"^(.+?)\s+LAST\s+PURCHASE\s+SUPPLIER\s+NAME\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, q)
        if m:
            phrase = m.group(1)
            phrase = re.sub(r"\b(IN|ON|DURING)\s+\d{4}.*$", "", phrase).strip()
            phrase = re.sub(r"[^A-Z0-9 .&/_-]+", " ", phrase)
            phrase = re.sub(r"\s+", " ", phrase).strip()
            return phrase

    return ""


def _ajsm_best_material_value_for_purchase(question, resolved_values):
    phrase = _ajsm_extract_item_supplier_material_phrase(question)
    phrase_words = re.findall(r"[A-Z0-9]+", phrase)

    # For broad one-word material questions like "mouse", keep broad LIKE '%MOUSE%'.
    if phrase and len(phrase_words) <= 1:
        return phrase

    for row in ((resolved_values or {}).get("top_results") or []):
        entity_type = str(row.get("entity_type") or "").lower()
        location = str(row.get("location") or "").upper()
        value = str(row.get("resolved_value") or "").strip()

        if not value:
            continue

        if entity_type == "material" and "ADDRESS" not in location and "PARTYMASTER" not in location:
            return value

    return phrase


def _ajsm_purchase_last_supplier_for_material_template(question, resolved_values):
    material = _ajsm_best_material_value_for_purchase(question, resolved_values)
    if not material:
        return None

    material_sql = material.replace("'", "''").upper()

    sql = f"""SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE AS LAST_PURCHASE_DATE,
        PO.SUP_CODE,
        NVL(P.PARTYNAME, PO.SUP_CODE) AS SUPPLIER_NAME,
        INV.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE UPPER(INV.ITEM_NAME) LIKE '%{material_sql}%'
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 1"""

    return {
        "source": "purchase_analytics_router",
        "intent": "purchase_last_supplier_by_material",
        "confidence": 0.97,
        "parameters": {"item_name": material.upper()},
        "tables_used": [
            "INVENTORY.PURCHASEORDER",
            "INVENTORY.INVITEMS",
            "SCM.PARTYMASTER",
        ],
        "relationships_used": [
            "INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE",
            "INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE",
        ],
        "sql": sql,
        "safe_select_sql": True,
    }


def _detect_module(question, understanding=None, resolved_values=None):
    q = _ajsm_norm_text(question)

    # Highest priority: admin/cashbank must not be swallowed by purchase just because "party" exists.
    if _ajsm_is_cashbank_question(q):
        return "admin_cashbank"

    if _ajsm_is_camera_question(q):
        return "admin_camera"

    if any(x in q for x in ["VEHICLE MOVEMENT", "VEHICLE"]):
        return "admin_vehicle_movement"

    if any(x in q for x in ["DOCUMENT", "DOC "]) and ("PARTY" in q or re.search(r"\b[A-Z]{3,}\d+\b", q)):
        return "admin_document"

    return _AJSM_ORIGINAL_DETECT_MODULE(question, understanding, resolved_values)


def _resolver_enriched_question(question, resolved_values, module):
    q = _ajsm_norm_text(question)

    # Generic admin/camera/cashbank/document/HR questions should keep user terms.
    # Do not rewrite "camera details" to a supplier/party value from PARTYMASTER.
    if module in {"admin_cashbank", "admin_camera", "admin_document", "admin_vehicle_movement", "hrd", "hr"}:
        return str(question)

    # Unit queries should not be rewritten from "B-Unit" to a random remark like "B UNIT DRAWING".
    if _ajsm_is_unit_query(question):
        return str(question)

    # Item supplier lookup should stay item-driven, not supplier-address-driven.
    if _ajsm_is_item_supplier_lookup(question):
        return str(question)

    return _AJSM_ORIGINAL_RESOLVER_ENRICHED_QUESTION(question, resolved_values, module)


def _match_router(router_question, resolved_values=None, module=None, original_question=None):
    q_original = str(original_question or router_question or "")

    # Item-driven supplier lookup:
    # "last supplier for mouse" / "mouse last purchased supplier name"
    # must filter INV.ITEM_NAME, not PARTYMASTER address/name.
    if _ajsm_is_item_supplier_lookup(q_original):
        custom = _ajsm_purchase_last_supplier_for_material_template(q_original, resolved_values or {})
        if custom:
            return custom

    # Cashbank/admin questions should not fall into purchase supplier templates.
    if _ajsm_is_cashbank_question(q_original):
        return None

    # GRN/goods-receipt questions must not become purchase_last_supply_by_supplier.
    result = _AJSM_ORIGINAL_MATCH_ROUTER(
        router_question,
        resolved_values=resolved_values,
        module=module,
        original_question=original_question,
    )

    if _ajsm_is_grn_question(q_original) and _ajsm_template_intent(result) == "purchase_last_supply_by_supplier":
        return None

    return result


# ---------------------------------------------------------------------------
# AJSM_STRICT_ROUTE_OVERRIDES_V2
# Final DB-free audit cleanup:
# - Broad item supplier lookup should not be narrowed by resolved material patch.
# - Planner top_results should not show supplier address for item-material questions.
# - Generic camera questions should not expose PARTYMASTER supplier result.
# ---------------------------------------------------------------------------

if "AJSM_STRICT_ROUTE_OVERRIDES_V2_ACTIVE" not in globals():
    AJSM_STRICT_ROUTE_OVERRIDES_V2_ACTIVE = True

    _AJSM_ORIGINAL_PATCH_TEMPLATE_SQL_WITH_RESOLVED_MATERIAL_V2 = _patch_template_sql_with_resolved_material
    _AJSM_ORIGINAL_PLAN_QUERY_V2 = plan_query

    def _ajsm_has_broad_one_word_semantic_material(resolved_values):
        phrases = [
            str(x or "").strip()
            for x in ((resolved_values or {}).get("semantic_queries") or [])
        ]
        return any(
            len(re.findall(r"[A-Za-z0-9]+", phrase)) <= 1
            for phrase in phrases
            if phrase
        )

    def _patch_template_sql_with_resolved_material(sql, resolved_values):
        sql_text = str(sql or "")
        sql_upper = sql_text.upper()

        # For broad one-word item purchase/supplier lookups, do not replace:
        # LIKE '%MOUSE%' -> LIKE '%MOUSE USB%'
        # LIKE '%MOUSE%' -> LIKE '%TOUCH PAD MOUSE%'
        if _ajsm_has_broad_one_word_semantic_material(resolved_values) and "PURCHASEORDER" in sql_upper:
            if "UPPER(INV.ITEM_NAME) LIKE" in sql_upper:
                return sql

        return _AJSM_ORIGINAL_PATCH_TEMPLATE_SQL_WITH_RESOLVED_MATERIAL_V2(sql, resolved_values)

    def _ajsm_material_plan_top_result(question):
        phrase = _ajsm_extract_item_supplier_material_phrase(question)
        if not phrase:
            return None
        return {
            "resolved_value": phrase.upper(),
            "entity_type": "material",
            "location": "QUESTION.MATERIAL_PHRASE",
            "score": 999.0,
            "source": "planner_override",
            "reason": "item_supplier_lookup_material_phrase",
        }

    def _ajsm_clean_plan_resolved_values(plan):
        question = str(plan.get("question") or "")
        module = str(plan.get("module") or "")
        resolved = plan.get("resolved_values") or {}
        top_results = list(resolved.get("top_results") or [])

        # Item supplier questions are material-driven.
        # Do not show SCM.PARTYMASTER.ADDRESS* as top value.
        if _ajsm_is_item_supplier_lookup(question):
            synthetic = _ajsm_material_plan_top_result(question)
            filtered = [
                r for r in top_results
                if str(r.get("entity_type") or "").lower() == "material"
                and "PARTYMASTER" not in str(r.get("location") or "").upper()
                and "ADDRESS" not in str(r.get("location") or "").upper()
            ]
            if synthetic:
                resolved["top_results"] = [synthetic] + filtered[:4]
            else:
                resolved["top_results"] = filtered[:5]
            plan["resolved_values"] = resolved

        # Generic camera/admin camera questions should not show supplier party.
        # Example: "camera details" should not top-match SCM.PARTYMASTER.PARTYNAME.
        if module == "admin_camera" and _ajsm_is_camera_question(question):
            filtered = [
                r for r in top_results
                if "ADMIN.CAMERAIP" in str(r.get("location") or "").upper()
                or str(r.get("entity_type") or "").lower() == "camera"
                   and "PARTYMASTER" not in str(r.get("location") or "").upper()
            ]
            resolved["top_results"] = filtered[:5]
            plan["resolved_values"] = resolved

        return plan

    def plan_query(question):
        plan = _AJSM_ORIGINAL_PLAN_QUERY_V2(question)
        return _ajsm_clean_plan_resolved_values(plan)

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    args = parser.parse_args()

    print(json.dumps(plan_query(args.question), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
