from __future__ import annotations

import re


BASE_MRS_SELECT = """
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
    M.APPROVALSTATUS,
    M.READYFORAPPROVAL,
    M.REJECTIONSTATUS,
    M.STORESREJECTIONSTATUS,
    M.ITEMDELETE,
    M.ISDELETE,
    M.REASONFORMRS
FROM INVENTORY.MRS_TEMP M
JOIN INVENTORY.INVITEMS INV ON M.ITEM_CODE = INV.ITEM_CODE
LEFT JOIN INVENTORY.UNIT U ON M.UNIT_CODE = U.UNIT_CODE
LEFT JOIN INVENTORY.DEPT D ON M.DEPT_CODE = D.DEPT_CODE
""".strip()


ACTIVE_MRS_FILTER = """
M.MRSFLAG = 1
AND NVL(M.ITEMDELETE, 0) = 0
AND NVL(M.ISDELETE, 0) = 0
""".strip()


PENDING_MRS_FILTER = """
M.MRSFLAG = 1
AND NVL(M.REJECTIONSTATUS, 0) = 0
AND NVL(M.STORESREJECTIONSTATUS, 0) = 0
AND NVL(M.ITEMDELETE, 0) = 0
AND NVL(M.ISDELETE, 0) = 0
""".strip()


APPROVED_MRS_FILTER = """
M.MRSFLAG = 1
AND NVL(M.APPROVALSTATUS, 0) = 1
AND NVL(M.READYFORAPPROVAL, 0) = 1
AND NVL(M.REJECTIONSTATUS, 0) = 0
AND NVL(M.STORESREJECTIONSTATUS, 0) = 0
AND NVL(M.ITEMDELETE, 0) = 0
AND NVL(M.ISDELETE, 0) = 0
""".strip()


REJECTED_MRS_FILTER = """
M.MRSFLAG = 1
AND (
    NVL(M.REJECTIONSTATUS, 0) = 1
    OR NVL(M.STORESREJECTIONSTATUS, 0) = 1
)
""".strip()


def _clean_text(value: str) -> str:
    value = re.sub(r"\b(in|on)\s+(?:19|20)\d{2}\b", "", value, flags=re.I)
    value = re.sub(r"\bon\s+(?:19|20)\d{6}\b", "", value, flags=re.I)
    value = re.sub(r"\bon\s+\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b", "", value, flags=re.I)
    value = re.sub(r"[?.,;:]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _clean_item_name(value: str) -> str:
    value = _clean_text(value)
    value = re.sub(
        r"\b(mrs|material|requisition|request|details|detail|for|item|code|number|no|approved|rejected|pending|latest|recent|due|linked|with|order|department|dept|unit|in|on|the|show|give|get)\b",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s+", " ", value).strip()
    if value.endswith("s") and len(value) > 4:
        value = value[:-1]
    return value.upper()


def _result(intent: str, sql: str, parameters: dict) -> dict:
    return {
        "sql": sql,
        "source": "mrs_template_router",
        "intent": intent,
        "parameters": parameters,
        "tables_used": [
            "INVENTORY.MRS_TEMP",
            "INVENTORY.INVITEMS",
            "INVENTORY.UNIT",
            "INVENTORY.DEPT",
        ],
        "relationships_used": [
            "M.ITEM_CODE = INV.ITEM_CODE",
            "M.UNIT_CODE = U.UNIT_CODE",
            "M.DEPT_CODE = D.DEPT_CODE",
        ],
        "confidence": 0.98,
        "explanation": "Matched deterministic MRS business route.",
    }


def _sql(where_clause: str, order_by: str = "M.MRSDATE DESC NULLS LAST, M.MRSNO DESC") -> str:
    return f"{BASE_MRS_SELECT} WHERE {where_clause} ORDER BY {order_by}"


def match_mrs_template(question: str) -> dict | None:
    q = question.strip()
    ql = q.lower()

    if not re.search(r"\b(mrs|material requisition|material request)\b", ql):
        return None

    # MRS details for MRS number 890330
    m = re.search(r"\bmrs\s*(?:number|no|details\s+for\s+mrs\s+number|details\s+for\s+mrs\s+no)?\s*(\d{3,})\b", ql, flags=re.I)
    if not m:
        m = re.search(r"\bmrs\s+(?:details\s+)?(?:number|no)\s+(\d{3,})\b", ql, flags=re.I)
    if m:
        mrs_no = m.group(1)
        sql = _sql(f"M.MRSNO = {mrs_no}")
        return _result("mrs_by_mrs_no", sql, {"mrs_no": mrs_no})

    # MRS linked with order number 800151
    m = re.search(r"\border\s*(?:number|no)?\s*(\d{3,})\b", ql, flags=re.I)
    if m and re.search(r"\b(linked|against|converted|order)\b", ql):
        order_no = m.group(1)
        sql = _sql(f"M.ORDERNO = {order_no}")
        return _result("mrs_by_order_no", sql, {"order_no": order_no})

    # MRS for department EDP
    m = re.search(r"\b(?:department|dept)\s+([\w\-\s]+)$", q, flags=re.I)
    if m:
        dept_name = _clean_text(m.group(1)).upper()
        sql = _sql(f"{ACTIVE_MRS_FILTER} AND UPPER(D.DEPT_NAME) LIKE '%{dept_name}%'")
        return _result("mrs_by_department", sql, {"dept_name": dept_name})

    # MRS for unit B-Unit
    m = re.search(r"\bunit\s+([\w\-\s]+)$", q, flags=re.I)
    if m:
        unit_name = _clean_text(m.group(1)).upper()
        sql = _sql(f"{ACTIVE_MRS_FILTER} AND UPPER(U.UNIT_NAME) LIKE '%{unit_name}%'")
        return _result("mrs_by_unit", sql, {"unit_name": unit_name})

    # MRS due for keyboard / MRS due in 2026
    if re.search(r"\bdue\b", ql):
        m = re.search(r"\bdue\s+for\s+(.+)$", q, flags=re.I)
        if m:
            item_name = _clean_item_name(m.group(1))
            sql = _sql(f"{ACTIVE_MRS_FILTER} AND UPPER(INV.ITEM_NAME) LIKE '%{item_name}%'", "M.DUEDATE DESC NULLS LAST, M.MRSNO DESC")
            return _result("mrs_due_by_item_name", sql, {"item_name": item_name})

        sql = _sql(ACTIVE_MRS_FILTER, "M.DUEDATE DESC NULLS LAST, M.MRSNO DESC")
        return _result("mrs_due_all", sql, {})

    # approved MRS for keyboard
    m = re.search(r"\bapproved\s+(?:mrs|material requisition|material request)\s+for\s+(.+)$", q, flags=re.I)
    if m:
        item_name = _clean_item_name(m.group(1))
        sql = _sql(f"{APPROVED_MRS_FILTER} AND UPPER(INV.ITEM_NAME) LIKE '%{item_name}%'")
        return _result("approved_mrs_by_item_name", sql, {"item_name": item_name})

    # rejected MRS for keyboard
    m = re.search(r"\brejected\s+(?:mrs|material requisition|material request)\s+for\s+(.+)$", q, flags=re.I)
    if m:
        item_name = _clean_item_name(m.group(1))
        sql = _sql(f"{REJECTED_MRS_FILTER} AND UPPER(INV.ITEM_NAME) LIKE '%{item_name}%'")
        return _result("rejected_mrs_by_item_name", sql, {"item_name": item_name})

    return None
