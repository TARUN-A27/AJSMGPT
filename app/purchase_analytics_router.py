from __future__ import annotations

import re
from typing import Any


def _clean(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


def _like_item(q: str) -> str | None:
    ql = _clean(q)

    known_items = [
        "mouse",
        "keyboard",
        "yarn",
        "diesel",
        "firewood",
        "bearing",
        "belt",
        "chair",
        "paper",
    ]

    for item in known_items:
        if re.search(rf"\b{re.escape(item)}\b", ql):
            return item.upper()

    m = re.search(r"\bmaterial\s+([a-z0-9\s\-]+?)\s+(?:last|received|placed|price|rate|mrs|is|was|in)\b", ql)
    if m:
        item = m.group(1).strip()
        if item:
            return item.upper()

    m = re.search(r"^([a-z0-9\-]+)\s+(?:last|is|was|received|placed|price|rate)", ql)
    if m:
        item = m.group(1).strip()
        if item and item not in {"what", "which", "who", "how", "list", "today", "mrs"}:
            return item.upper()

    return None



def _supplier_name(q: str) -> str | None:
    ql = _clean(q)

    m = re.search(r"\bsupplier\s+([a-z0-9\s.&,\-]+)$", ql)
    if m:
        name = m.group(1).strip()
        if name:
            return name.upper()

    m = re.search(r"\bfrom\s+supplier\s+([a-z0-9\s.&,\-]+)", ql)
    if m:
        name = m.group(1).strip()
        if name:
            return name.upper()

    return None




def _last_n_limit(q: str, default: int = 1) -> int:
    m = re.search(r"\blast\s+(\d+)\b", q)
    if not m:
        return default

    try:
        value = int(m.group(1))
    except ValueError:
        return default

    return max(1, min(value, 50))


def _clean_material_name(value: str | None) -> str | None:
    if not value:
        return None

    item = value.strip(" .,-_/\\")
    item = re.sub(r"\s+", " ", item).strip()

    # Remove common trailing words if captured accidentally.
    item = re.sub(r"\b(qty|quantity|purchase|purchases|rate|price|details)\b.*$", "", item, flags=re.I).strip()

    if not item:
        return None

    return item.upper()


def _material_from_item_phrase(q: str) -> str | None:
    patterns = [
        r"\bof\s+the\s+item\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bitem\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_material_name(m.group(1))

    return None


def _material_from_last_supply(q: str) -> str | None:
    patterns = [
        r"\blast\s+supply\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\blast\s+purchase\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_material_name(m.group(1))

    return _material_from_item_phrase(q)


def _material_from_last_purchase_qty(q: str) -> str | None:
    # Handles quoted material names:
    # Example: Last 3 purchase details of "dell system"
    quoted = re.search(r'"([^"]+)"', q)
    if quoted:
        return _clean_material_name(quoted.group(1))

    item = _material_from_item_phrase(q)
    if item:
        return item

    patterns = [
        r"\blast\s+(?:\d+\s+)?purchase\s+(?:qty|quantity|details|detail)\s+(?:purchase\s+)?(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\blast\s+(?:\d+\s+)?purchase\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_material_name(m.group(1))

    return None



def _material_from_supplier_question(q: str) -> str | None:
    # Example: WHO are the suppliers for the item "barcode label"
    quoted = re.search(r'"([^"]+)"', q)
    if quoted:
        return _clean_material_name(quoted.group(1))

    patterns = [
        r"\bsuppliers?\s+(?:for|of)\s+(?:the\s+)?(?:item|material)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\b(?:item|material)\s+([a-zA-Z0-9&.\-_/ ]+?)\s+suppliers?\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_material_name(m.group(1))

    return _material_from_item_phrase(q)


def _suppliers_by_material_select(material: str) -> str:
    where = _item_filter("INV", material)

    return f"""SELECT DISTINCT
    PO.SUP_CODE,
    NVL(P.PARTYNAME, PO.SUP_CODE) AS SUPPLIER_NAME,
    INV.ITEM_NAME
FROM INVENTORY.PURCHASEORDER PO
JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
WHERE 1 = 1
 {where}
ORDER BY SUPPLIER_NAME"""

def _item_filter(alias: str, material: str | None) -> str:
    material = _clean_material_name(material)

    if not material:
        return ""

    column = f"UPPER({alias}.ITEM_NAME)"
    phrase = material.upper().replace("'", "''")

    raw_tokens = [
        token.replace("'", "''").upper()
        for token in re.split(r"[^A-Z0-9]+", phrase)
        if len(token.strip()) >= 2
    ]

    if not raw_tokens:
        return ""

    # Normalize user words:
    # BARCODE should match BARCODE, BAR CODE, BAR + CODE.
    # LABEL should match LABEL, LABELS, LABLE, LABLES.
    tokens: list[str] = []
    i = 0
    while i < len(raw_tokens):
        if raw_tokens[i] == "BAR" and i + 1 < len(raw_tokens) and raw_tokens[i + 1] == "CODE":
            tokens.append("BARCODE")
            i += 2
        else:
            tokens.append(raw_tokens[i])
            i += 1

    def token_condition(token: str) -> str:
        if token == "BARCODE":
            return (
                f"({column} LIKE '%BARCODE%' "
                f"OR {column} LIKE '%BAR CODE%' "
                f"OR ({column} LIKE '%BAR%' AND {column} LIKE '%CODE%'))"
            )

        if token in {"LABEL", "LABELS", "LABLE", "LABLES"}:
            return f"({column} LIKE '%LABEL%' OR {column} LIKE '%LABLE%')"

        return f"{column} LIKE '%{token}%'"

    exact_condition = f"{column} LIKE '%{phrase}%'"
    token_conditions = " AND ".join(token_condition(token) for token in tokens)

    return f"""  AND (
    {exact_condition}
    OR ({token_conditions})
  )"""


def _supplier_filter(supplier: str | None) -> str:
    if not supplier:
        return ""
    safe_supplier = supplier.replace("'", "''")
    return f" AND UPPER(P.PARTYNAME) LIKE '%{safe_supplier}%' "


def _po_base_select(where: str = "", order_by: str = "PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC", limit: int = 100) -> str:
    return f"""
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
    WHERE 1 = 1
    {where}
    ORDER BY {order_by}
)
WHERE ROWNUM <= {limit}
""".strip()


def _grn_base_select(where: str = "", order_by: str = "G.GRNDATE DESC NULLS LAST, G.GRNNO DESC") -> str:
    return f"""
SELECT *
FROM (
    SELECT
        G.GRNNO,
        G.GRNDATE,
        G.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        G.ORDERNO,
        G.MRSNO,
        G.CODE AS ITEM_CODE,
        INV.ITEM_NAME,
        G.ORDERQTY,
        G.PENDING,
        G.INVQTY,
        G.INVRATE,
        G.QTY,
        G.GRNQTY,
        G.GRNVALUE,
        G.STATUS,
        G.GRNAUTHSTATUS,
        G.REJECTIONSTATUS,
        G.HOLDINGSTATUS
    FROM INVENTORY.GRN G
    JOIN INVENTORY.INVITEMS INV ON G.CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON G.SUP_CODE = P.PARTYCODE
    WHERE 1 = 1
    {where}
    ORDER BY {order_by}
)
WHERE ROWNUM <= 100
""".strip()


def _mrs_base_select(where: str = "", order_by: str = "M.MRSDATE DESC NULLS LAST, M.MRSNO DESC") -> str:
    return f"""
SELECT *
FROM (
    SELECT
        M.MRSNO,
        M.MRSDATE,
        M.ITEM_CODE,
        INV.ITEM_NAME,
        M.QTY,
        M.UNIT_CODE,
        U.UNIT_NAME,
        M.DEPT_CODE,
        D.DEPT_NAME,
        M.DUEDATE,
        M.ORDERNO,
        M.NETRATE,
        M.NETVALUE,
        M.APPROVALSTATUS,
        M.READYFORAPPROVAL,
        M.REJECTIONSTATUS,
        M.STORESREJECTIONSTATUS,
        M.HOLDINGSTATUS,
        M.ORDERCONVERTED,
        M.ENQUIRYSTATUS,
        M.REASONFORMRS,
        M.REASONFORREJECTIONSTORES,
        CASE
            WHEN NVL(M.REJECTIONSTATUS, 0) = 1 OR NVL(M.STORESREJECTIONSTATUS, 0) = 1 THEN 'REJECTED'
            WHEN NVL(M.HOLDINGSTATUS, 0) = 1 THEN 'HOLD'
            WHEN NVL(M.APPROVALSTATUS, 0) = 1 AND NVL(M.READYFORAPPROVAL, 0) = 1 THEN 'APPROVED'
            WHEN NVL(M.READYFORAPPROVAL, 0) = 0 THEN 'STORE_OFFICER_PENDING'
            ELSE 'PENDING'
        END AS MRS_STATUS
    FROM INVENTORY.MRS_TEMP M
    JOIN INVENTORY.INVITEMS INV ON M.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN INVENTORY.UNIT U ON M.UNIT_CODE = U.UNIT_CODE
    LEFT JOIN INVENTORY.DEPT D ON M.DEPT_CODE = D.DEPT_CODE
    WHERE 1 = 1
    {where}
    ORDER BY {order_by}
)
WHERE ROWNUM <= 100
""".strip()


def _result(intent: str, sql: str, confidence: float = 0.96) -> dict[str, Any]:
    return {
        "intent": intent,
        "sql": sql,
        "explanation": f"Purchase analytics router matched intent: {intent}",
        "tables_used": [],
        "relationships_used": [],
        "confidence": confidence,
        "source": "purchase_analytics_router",
        "parameters": {},
    }


def match_purchase_analytics_template(question: str) -> dict[str, Any] | None:
    q = _clean(question)

    # Approved patch: Suppliers by material/item
    # Example: WHO are the suppliers for the item "barcode label"
    material_for_suppliers = _material_from_supplier_question(q)
    if (
        ("supplier" in q or "suppliers" in q)
        and ("item" in q or "material" in q)
        and material_for_suppliers
        and "purchase order" not in q
    ):
        sql = _suppliers_by_material_select(material_for_suppliers)
        return _result("purchase_suppliers_by_material", sql)


    # Approved patch: Last N purchase quantity/details by material
    # Example: "Last 3 purchase qty purchase of the item Keyboard"
    material_for_last_n = _material_from_last_purchase_qty(q)
    if (
        "last" in q
        and "purchase" in q
        and ("qty" in q or "quantity" in q or "details" in q or "detail" in q)
        and material_for_last_n
    ):
        sql = _po_base_select(
            where=_item_filter("INV", material_for_last_n),
            order_by="PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
            limit=_last_n_limit(q, default=1),
        )
        return _result("purchase_last_n_purchases_by_material", sql)

    # Approved patch: Last supply by material
    # Example: "last supply of mouse"
    material_for_last_supply = _material_from_last_supply(q)
    if (
        "last" in q
        and "supply" in q
        and material_for_last_supply
    ):
        sql = _po_base_select(
            where=_item_filter("INV", material_for_last_supply),
            order_by="PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
            limit=1,
        )
        return _result("purchase_last_supply_by_material", sql)

    item = _like_item(q)
    supplier = _supplier_name(q)

    # 0. First supply / first purchase from supplier
    if supplier and ("first supply" in q or "first purchase" in q or "first order" in q):
        where = _supplier_filter(supplier)
        return _result(
            "purchase_first_supply_by_supplier",
            _po_base_select(
                where=where,
                order_by="PO.ORDERDATE ASC NULLS LAST, PO.ORDERNO ASC",
                limit=1,
            ),
        )

    # 0B. Last supply / last purchase from supplier
    if supplier and ("last supply" in q or "last purchase" in q or "last order" in q):
        where = _supplier_filter(supplier)
        return _result(
            "purchase_last_supply_by_supplier",
            _po_base_select(
                where=where,
                order_by="PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
                limit=1,
            ),
        )

    # 1. Item price / last purchase supplier / last purchase date / last purchase rate
    if item and (
        "price" in q
        or "last purchased" in q
        or "last purchase" in q
        or "last supply" in q
        or "last supplied" in q
        or "supply of" in q
        or "supplier of" in q
        or "purchased supplier" in q
        or "purchased date" in q
        or "purchased rate" in q
    ):
        where = _item_filter("INV", item)
        return _result("purchase_last_price_by_item", _po_base_select(where=where, limit=1))


    # 2A. Highest and lowest rate together
    if "highest" in q and "lowest" in q and "rate" in q:
        where = """
        AND PO.ORDERDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE), -12), 'YYYYMMDD')
                             AND TO_CHAR(TRUNC(SYSDATE), 'YYYYMMDD')
        """
        where += _item_filter("INV", item)
        sql = f"""
SELECT *
FROM (
    SELECT 'LOWEST_RATE' AS RATE_TYPE, X.*
    FROM (
        SELECT
            PO.ORDERNO, PO.ORDERDATE, PO.SUP_CODE, P.PARTYNAME AS SUPPLIER_NAME,
            PO.ITEM_CODE, INV.ITEM_NAME, PO.QTY, PO.RATE, PO.NET
        FROM INVENTORY.PURCHASEORDER PO
        JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
        LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
        WHERE 1 = 1 {where}
        ORDER BY PO.RATE ASC NULLS LAST, PO.ORDERDATE DESC NULLS LAST
    ) X
    WHERE ROWNUM <= 10
)
UNION ALL
SELECT *
FROM (
    SELECT 'HIGHEST_RATE' AS RATE_TYPE, Y.*
    FROM (
        SELECT
            PO.ORDERNO, PO.ORDERDATE, PO.SUP_CODE, P.PARTYNAME AS SUPPLIER_NAME,
            PO.ITEM_CODE, INV.ITEM_NAME, PO.QTY, PO.RATE, PO.NET
        FROM INVENTORY.PURCHASEORDER PO
        JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
        LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
        WHERE 1 = 1 {where}
        ORDER BY PO.RATE DESC NULLS LAST, PO.ORDERDATE DESC NULLS LAST
    ) Y
    WHERE ROWNUM <= 10
)
""".strip()
        return _result("purchase_highest_lowest_rate_last_year", sql)

    # 2. Lowest supplier price in last one year, optionally item-specific
    if "lowest price" in q or "lowest rate" in q:
        where = """
        AND PO.ORDERDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE), -12), 'YYYYMMDD')
                             AND TO_CHAR(TRUNC(SYSDATE), 'YYYYMMDD')
        """
        where += _item_filter("INV", item)
        return _result(
            "purchase_lowest_rate_last_year",
            _po_base_select(where=where, order_by="PO.RATE ASC NULLS LAST, PO.ORDERDATE DESC NULLS LAST"),
        )

    # 3. Highest rate
    if "highest rate" in q or "highest price" in q:
        where = """
        AND PO.ORDERDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE), -12), 'YYYYMMDD')
                             AND TO_CHAR(TRUNC(SYSDATE), 'YYYYMMDD')
        """
        where += _item_filter("INV", item)
        return _result(
            "purchase_highest_rate_last_year",
            _po_base_select(where=where, order_by="PO.RATE DESC NULLS LAST, PO.ORDERDATE DESC NULLS LAST"),
        )

    # 4. Highest and lowest rate together
    if "highest" in q and "lowest" in q and "rate" in q:
        where = """
        AND PO.ORDERDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE), -12), 'YYYYMMDD')
                             AND TO_CHAR(TRUNC(SYSDATE), 'YYYYMMDD')
        """
        where += _item_filter("INV", item)
        sql = f"""
SELECT *
FROM (
    SELECT 'LOWEST_RATE' AS RATE_TYPE, X.*
    FROM (
        SELECT
            PO.ORDERNO, PO.ORDERDATE, PO.SUP_CODE, P.PARTYNAME AS SUPPLIER_NAME,
            PO.ITEM_CODE, INV.ITEM_NAME, PO.QTY, PO.RATE, PO.NET
        FROM INVENTORY.PURCHASEORDER PO
        JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
        LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
        WHERE 1 = 1 {where}
        ORDER BY PO.RATE ASC NULLS LAST, PO.ORDERDATE DESC NULLS LAST
    ) X
    WHERE ROWNUM <= 10
)
UNION ALL
SELECT *
FROM (
    SELECT 'HIGHEST_RATE' AS RATE_TYPE, Y.*
    FROM (
        SELECT
            PO.ORDERNO, PO.ORDERDATE, PO.SUP_CODE, P.PARTYNAME AS SUPPLIER_NAME,
            PO.ITEM_CODE, INV.ITEM_NAME, PO.QTY, PO.RATE, PO.NET
        FROM INVENTORY.PURCHASEORDER PO
        JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
        LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
        WHERE 1 = 1 {where}
        ORDER BY PO.RATE DESC NULLS LAST, PO.ORDERDATE DESC NULLS LAST
    ) Y
    WHERE ROWNUM <= 10
)
""".strip()
        return _result("purchase_highest_lowest_rate_last_year", sql)

    # 5. How many materials placed last month? MRS placed count
    if "how many" in q and "materials" in q and "placed" in q and "last month" in q:
        sql = """
SELECT
    COUNT(*) AS MRS_LINE_COUNT,
    COUNT(DISTINCT M.MRSNO) AS MRS_COUNT,
    COUNT(DISTINCT M.ITEM_CODE) AS MATERIAL_COUNT,
    SUM(NVL(M.QTY, 0)) AS TOTAL_QTY
FROM INVENTORY.MRS_TEMP M
WHERE M.MRSDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1), 'YYYYMMDD')
                    AND TO_CHAR(LAST_DAY(ADD_MONTHS(SYSDATE, -1)), 'YYYYMMDD')
""".strip()
        return _result("mrs_last_month_material_count", sql)

    # 6. Last month consumed / placed cost
    if ("cost consumed" in q or ("cost" in q and "last month" in q and "high" not in q)) and "last month" in q:
        sql = """
SELECT
    COUNT(*) AS MRS_LINE_COUNT,
    COUNT(DISTINCT M.MRSNO) AS MRS_COUNT,
    SUM(NVL(M.QTY, 0)) AS TOTAL_QTY,
    SUM(NVL(M.NETVALUE, 0)) AS TOTAL_VALUE
FROM INVENTORY.MRS_TEMP M
WHERE M.MRSDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1), 'YYYYMMDD')
                    AND TO_CHAR(LAST_DAY(ADD_MONTHS(SYSDATE, -1)), 'YYYYMMDD')
""".strip()
        return _result("mrs_last_month_cost_summary", sql)

    # 7. Which material cost is high last month?
    if "cost" in q and ("high" in q or "highest" in q) and "last month" in q:
        where = """
        AND M.MRSDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1), 'YYYYMMDD')
                          AND TO_CHAR(LAST_DAY(ADD_MONTHS(SYSDATE, -1)), 'YYYYMMDD')
        """
        return _result("mrs_last_month_high_cost_materials", _mrs_base_select(where=where, order_by="NVL(M.NETVALUE, 0) DESC"))

    # 8. Item placed in last month MRS
    if item and "placed" in q and "last month" in q and "mrs" in q:
        where = """
        AND M.MRSDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1), 'YYYYMMDD')
                          AND TO_CHAR(LAST_DAY(ADD_MONTHS(SYSDATE, -1)), 'YYYYMMDD')
        """
        where += _item_filter("INV", item)
        return _result("mrs_item_placed_last_month", _mrs_base_select(where=where))

    # 9. Rejected MRS reason
    if "mrs" in q and "reject" in q and "reason" in q:
        where = """
        AND (
            NVL(M.REJECTIONSTATUS, 0) = 1
            OR NVL(M.STORESREJECTIONSTATUS, 0) = 1
        )
        """
        where += _item_filter("INV", item)
        return _result("mrs_rejected_reason", _mrs_base_select(where=where))

    # 10. Last month MRS numbers
    if "last month" in q and "mrs" in q and ("nos" in q or "no" in q or "number" in q or "placed material" in q):
        where = """
        AND M.MRSDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1), 'YYYYMMDD')
                          AND TO_CHAR(LAST_DAY(ADD_MONTHS(SYSDATE, -1)), 'YYYYMMDD')
        """
        return _result("mrs_last_month_numbers", _mrs_base_select(where=where))

    # 11. Last month MRS status
    if "status" in q and "last month" in q and "mrs" in q:
        where = """
        AND M.MRSDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1), 'YYYYMMDD')
                          AND TO_CHAR(LAST_DAY(ADD_MONTHS(SYSDATE, -1)), 'YYYYMMDD')
        """
        return _result("mrs_last_month_status", _mrs_base_select(where=where))

    # 12. Received material questions
    if "received" in q or "receipt" in q:
        if "pending" in q:
            where = " AND (NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) > 0 "
            where += _item_filter("INV", item)
            return _result("po_receipt_pending_qty", _po_base_select(where=where, order_by="(NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) DESC"))

        if "today" in q:
            where = " AND G.GRNDATE = TO_CHAR(SYSDATE, 'YYYYMMDD') "
            return _result("grn_today_received_materials", _grn_base_select(where=where, order_by="G.GRNNO DESC"))

        if "last one year" in q or "last year" in q:
            where = """
            AND G.GRNDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE), -12), 'YYYYMMDD')
                              AND TO_CHAR(TRUNC(SYSDATE), 'YYYYMMDD')
            """
            where += _item_filter("INV", item)
            sql = f"""
SELECT
    COUNT(*) AS GRN_LINE_COUNT,
    COUNT(DISTINCT G.GRNNO) AS GRN_COUNT,
    SUM(NVL(G.QTY, 0)) AS TOTAL_RECEIVED_QTY,
    SUM(NVL(G.GRNVALUE, 0)) AS TOTAL_RECEIVED_VALUE
FROM INVENTORY.GRN G
JOIN INVENTORY.INVITEMS INV ON G.CODE = INV.ITEM_CODE
WHERE 1 = 1
{where}
""".strip()
            return _result("grn_received_qty_last_year", sql)

        where = _item_filter("INV", item)
        return _result("grn_received_by_item", _grn_base_select(where=where))

    # 13. Order pending material names
    if "order pending" in q:
        where = " AND (NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) > 0 "
        return _result("po_order_pending_materials", _po_base_select(where=where, order_by="PO.ORDERDATE DESC NULLS LAST"))

    # 14. Store officer hold
    if "hold" in q and "store" in q:
        where = " AND NVL(M.HOLDINGSTATUS, 0) = 1 "
        return _result("mrs_store_officer_hold", _mrs_base_select(where=where))

    # 15. Store officer approval pending
    if "approval pending" in q and "store" in q:
        where = """
        AND NVL(M.READYFORAPPROVAL, 0) = 0
        AND NVL(M.APPROVALSTATUS, 0) = 0
        AND NVL(M.REJECTIONSTATUS, 0) = 0
        AND NVL(M.STORESREJECTIONSTATUS, 0) = 0
        AND NVL(M.ITEMDELETE, 0) = 0
        AND NVL(M.ISDELETE, 0) = 0
        """
        return _result("mrs_store_officer_approval_pending", _mrs_base_select(where=where))

    # 16. Supplier highest orders
    if "supplier" in q and ("highest orders" in q or "taken highest orders" in q):
        sql = """
SELECT *
FROM (
    SELECT
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        COUNT(DISTINCT PO.ORDERNO) AS ORDER_COUNT,
        COUNT(*) AS LINE_COUNT,
        SUM(NVL(PO.NET, 0)) AS TOTAL_ORDER_VALUE
    FROM INVENTORY.PURCHASEORDER PO
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE PO.ORDERDATE BETWEEN TO_CHAR(ADD_MONTHS(TRUNC(SYSDATE), -12), 'YYYYMMDD')
                           AND TO_CHAR(TRUNC(SYSDATE), 'YYYYMMDD')
    GROUP BY PO.SUP_CODE, P.PARTYNAME
    ORDER BY COUNT(DISTINCT PO.ORDERNO) DESC
)
WHERE ROWNUM <= 100
""".strip()
        return _result("supplier_highest_orders_last_year", sql)

    return None
