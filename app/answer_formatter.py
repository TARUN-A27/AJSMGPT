from __future__ import annotations

from collections.abc import Mapping


def _normalize_row(row, columns=None) -> dict:
    """
    Oracle result rows may come as:
    1. dict: {"COL": value}
    2. list/tuple: [value1, value2] with separate columns list

    Convert both into:
    {"COL": value}
    """
    normalized = {}

    if isinstance(row, Mapping):
        for key, value in row.items():
            if key is None:
                continue
            normalized[str(key).upper()] = value
        return normalized

    if isinstance(row, (list, tuple)):
        columns = columns or []
        for index, value in enumerate(row):
            if index < len(columns):
                key = columns[index]
            else:
                key = f"COL_{index + 1}"

            if key is None:
                continue

            normalized[str(key).upper()] = value
        return normalized

    return {"VALUE": row}


def _first_non_empty(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def format_answer(result: dict) -> str:
    row_count = result.get("row_count", 0)
    if row_count == 0:
        return "No matching records found for this question."

    rows = result.get("rows") or []
    columns = result.get("columns") or []
    intent = result.get("intent")
    source = result.get("source")

    if not rows:
        return f"Found {row_count} records. Showing key fields below."

    first_row = _normalize_row(rows[0], columns)

    if intent == "last_purchase_date_by_item_name":
        parameters = result.get("parameters") or {}
        entities = result.get("understanding", {}).get("entities", {})
        item_name = parameters.get("item_name") or entities.get("item_name")

        order_no = _first_non_empty(
            first_row.get("ORDERNO"),
            first_row.get("PO.ORDERNO"),
            first_row.get("ORDER_NO"),
        )
        date = _first_non_empty(
            first_row.get("LAST_PURCHASE_DATE"),
            first_row.get("ORDERDATE"),
            first_row.get("PO.ORDERDATE"),
        )
        company = _first_non_empty(
            first_row.get("COMPANY_NAME"),
            first_row.get("PARTYNAME"),
            first_row.get("SUPPLIER_NAME"),
        )
        qty = first_row.get("QTY")
        rate = first_row.get("RATE")
        net = first_row.get("NET")

        summary_parts = []
        if item_name:
            summary_parts.append(f"Latest purchase found for {item_name}")
        else:
            summary_parts.append("Latest purchase found")

        if order_no is not None:
            summary_parts.append(f"Order No {order_no}")
        if date is not None:
            summary_parts.append(f"Date {date}")
        if company is not None:
            summary_parts.append(f"Supplier {company}")
        if qty is not None:
            summary_parts.append(f"Qty {qty}")
        if rate is not None:
            summary_parts.append(f"Rate {rate}")
        if net is not None:
            summary_parts.append(f"Net {net}")

        return (
            ": ".join([summary_parts[0], ", ".join(summary_parts[1:])])
            if len(summary_parts) > 1
            else summary_parts[0]
        )

    if source == "qwen_schema_fallback":
        retrieved_schema = result.get("retrieved_schema") or []
        table_text = retrieved_schema[0] if retrieved_schema else "retrieved schema"

        preview_parts = []
        for col in columns[:5]:
            key = str(col).upper()
            if key in first_row:
                preview_parts.append(f"{col}: {first_row[key]}")

        if preview_parts:
            return f"Found {row_count} records from {table_text}. First record: " + ", ".join(preview_parts)

        return f"Found {row_count} records from {table_text}. Showing key fields below."

    return f"Found {row_count} records. Showing key fields below."
