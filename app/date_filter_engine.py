from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DateFilter:
    kind: str  # "year" or "date"
    value: str  # YYYY or YYYYMMDD


def extract_date_filter(question: str) -> DateFilter | None:
    q = question.strip()

    # today
    if re.search(r"\btoday\b", q, flags=re.IGNORECASE):
        return DateFilter(kind="date", value=datetime.now().strftime("%Y%m%d"))

    # on 20260212
    m = re.search(r"\bon\s+((?:19|20)\d{6})\b", q, flags=re.IGNORECASE)
    if m:
        return DateFilter(kind="date", value=m.group(1))

    # on 12/02/2026 or on 12-02-2026
    m = re.search(r"\bon\s+(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})\b", q, flags=re.IGNORECASE)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3))
        return DateFilter(kind="date", value=f"{year:04d}{month:02d}{day:02d}")

    # in 2026
    m = re.search(r"\bin\s+((?:19|20)\d{2})\b", q, flags=re.IGNORECASE)
    if m:
        return DateFilter(kind="year", value=m.group(1))

    return None


def strip_date_filter_phrases(question: str) -> str:
    """
    Remove date phrases before business-template parameter extraction.
    Example:
      pending MRS for keyboard in 2026 -> pending MRS for keyboard
    """
    q = question

    q = re.sub(r"\btoday\b", "", q, flags=re.IGNORECASE)
    q = re.sub(r"\bon\s+(?:19|20)\d{6}\b", "", q, flags=re.IGNORECASE)
    q = re.sub(r"\bon\s+\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b", "", q, flags=re.IGNORECASE)
    q = re.sub(r"\bin\s+(?:19|20)\d{2}\b", "", q, flags=re.IGNORECASE)

    return re.sub(r"\s+", " ", q).strip()


def _date_column_for_sql(intent: str | None, tables_used: list[str] | None, sql: str) -> tuple[str, str] | None:
    """
    Return (column_expression, type_kind)
    type_kind:
      number = Oracle NUMBER date format YYYYMMDD
      text   = VARCHAR2 date format YYYYMMDD
      date   = Oracle DATE column
    """
    intent = (intent or "").lower()
    tables = {str(t).upper() for t in (tables_used or [])}
    upper_sql = sql.upper()

    # Business template intents
    if intent.startswith("mrs_due"):
        return "M.DUEDATE", "text"

    if "mrs" in intent:
        return "M.MRSDATE", "text"


    if intent.startswith("pending_po_") or intent.startswith("po_summary_"):
        return "PO.ORDERDATE", "text"

    if intent.startswith("last_purchase_date_"):
        return "PO.ORDERDATE", "text"

    if intent.startswith("grn_"):
        return "G.GRNDATE", "text"

    if intent.startswith("issue_"):
        return "I.ISSUEDATE", "text"

    # Fallback / table-based detection
    if "ADMIN.DOCUMENT" in tables or "ADMIN.DOCUMENT" in upper_sql:
        return "DOCDATE", "number"

    if "HRDNEW.CURRENTATTENDANCE" in tables or "HRDNEW.CURRENTATTENDANCE" in upper_sql:
        return "INDATE", "number"

    if "ADMIN.TRN_VEHICLEMOVEMENT" in tables or "ADMIN.TRN_VEHICLEMOVEMENT" in upper_sql:
        return "V.ENTRYDATE", "date"

    if "ADMIN.CASHBANK" in tables or "ADMIN.CASHBANK" in upper_sql:
        return "VOCDATE", "number"

    return None


def _condition_for_filter(column: str, type_kind: str, date_filter: DateFilter) -> str:
    if date_filter.kind == "year":
        year = int(date_filter.value)
        start = f"{year:04d}0101"
        end = f"{year:04d}1231"

        if type_kind == "number":
            return f"{column} BETWEEN {start} AND {end}"

        if type_kind == "text":
            return f"{column} BETWEEN '{start}' AND '{end}'"

        if type_kind == "date":
            return f"{column} >= DATE '{year:04d}-01-01' AND {column} < DATE '{year + 1:04d}-01-01'"

    if date_filter.kind == "date":
        yyyymmdd = date_filter.value
        year = yyyymmdd[:4]
        month = yyyymmdd[4:6]
        day = yyyymmdd[6:8]

        if type_kind == "number":
            return f"{column} = {yyyymmdd}"

        if type_kind == "text":
            return f"{column} = '{yyyymmdd}'"

        if type_kind == "date":
            return f"TRUNC({column}) = DATE '{year}-{month}-{day}'"

    raise ValueError(f"Unsupported date filter: {date_filter}")


def _insert_condition(sql: str, condition: str) -> str:
    upper = sql.upper()

    # Insert before ORDER BY if present.
    order_pos = upper.rfind(" ORDER BY ")
    if order_pos != -1:
        head = sql[:order_pos]
        tail = sql[order_pos:]

        if " WHERE " in head.upper():
            return f"{head} AND {condition}{tail}"

        return f"{head} WHERE {condition}{tail}"

    # No ORDER BY.
    if " WHERE " in upper:
        return f"{sql} AND {condition}"

    return f"{sql} WHERE {condition}"


def apply_date_filter_to_sql(
    sql: str | None,
    question: str,
    intent: str | None = None,
    tables_used: list[str] | None = None,
) -> str | None:
    if not sql:
        return sql

    date_filter = extract_date_filter(question)
    if not date_filter:
        return sql

    column_info = _date_column_for_sql(intent, tables_used, sql)
    if not column_info:
        return sql

    column, type_kind = column_info
    condition = _condition_for_filter(column, type_kind, date_filter)

    if condition.upper() in sql.upper():
        return sql

    return _insert_condition(sql, condition)
