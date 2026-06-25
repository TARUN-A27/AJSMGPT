from __future__ import annotations

import re
from typing import Any


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def normalize_material(value: str | None) -> str | None:
    if not value:
        return None

    value = clean_text(value).strip('"').strip("'").strip(" .,-_/\\")
    value = value.replace("key board", "keyboard")
    value = re.sub(r"\s+", " ", value).strip()

    if not value:
        return None

    return value.upper()


def extract_material_from_question(question: str, fallback_materials: list[str] | None = None) -> str | None:
    q = clean_text(question)

    quoted = re.search(r'"([^"]+)"', q)
    if quoted:
        return normalize_material(quoted.group(1))

    patterns = [
        r"\bof\s+the\s+item\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bitem\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bpurchase\s+details\s+of\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bpurchase\s+(?:qty|quantity)\s+(?:purchase\s+)?(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bsupply\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\brate\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bprice\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, q, flags=re.I)
        if match:
            material = normalize_material(match.group(1))
            if material:
                return material

    if fallback_materials:
        for item in fallback_materials:
            material = normalize_material(item)
            if material:
                return material

    return None


def extract_limit_from_question(question: str, default: int = 1) -> int:
    match = re.search(r"\blast\s+(\d+)\b", question, flags=re.I)

    if not match:
        return default

    try:
        value = int(match.group(1))
    except ValueError:
        return default

    return max(1, min(value, 50))


def material_filter_sql(material: str | None) -> str:
    if not material:
        return "-- MATERIAL FILTER MISSING"

    return f"AND UPPER(INV.ITEM_NAME) LIKE {sql_literal('%' + material + '%')}"


def purchase_order_base_sql(material: str | None, limit: int) -> str:
    return f"""SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE 1 = 1
      {material_filter_sql(material)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= {limit}"""


def purchase_rate_sql(material: str | None, limit: int = 20) -> str:
    return f"""SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE 1 = 1
      {material_filter_sql(material)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= {limit}"""




def suppliers_by_material_sql(material: str | None) -> str:
    return f"""SELECT DISTINCT
    PO.SUP_CODE,
    P.PARTYNAME AS SUPPLIER_NAME,
    INV.ITEM_NAME
FROM INVENTORY.PURCHASEORDER PO
JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
WHERE 1 = 1
  {material_filter_sql(material)}
  AND P.PARTYNAME IS NOT NULL
ORDER BY SUPPLIER_NAME"""


def generate_sql_for_patch_question(patch: dict[str, Any], question: str) -> str:
    intent = str(patch.get("intent_name") or "")
    extraction = patch.get("suggested_extraction") or {}

    fallback_materials = extraction.get("material_name") or []
    if not isinstance(fallback_materials, list):
        fallback_materials = [str(fallback_materials)]

    material = extract_material_from_question(question, fallback_materials)

    if intent == "purchase_last_n_purchases_by_material":
        limit = extract_limit_from_question(question, default=1)
        return purchase_order_base_sql(material, limit)

    if intent == "purchase_last_supply_by_material":
        return purchase_order_base_sql(material, 1)

    if intent == "purchase_latest_order_by_material":
        return purchase_order_base_sql(material, 1)

    if intent == "purchase_rate_by_material":
        return purchase_rate_sql(material, 20)

    if intent == "purchase_suppliers_by_material":
        return suppliers_by_material_sql(material)

    return "-- SQL generation not available for this intent yet."
