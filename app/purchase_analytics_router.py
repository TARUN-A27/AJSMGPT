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


def _item_filter(alias: str, item: str | None) -> str:
    if not item:
        return ""
    safe_item = item.replace("'", "''")
    return f" AND UPPER({alias}.ITEM_NAME) LIKE '%{safe_item}%'"


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
    item = _like_item(q)

    # 1. Item price / last purchase supplier / last purchase date / last purchase rate
    if item and (
        "price" in q
        or "last purchased" in q
        or "last purchase" in q
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
