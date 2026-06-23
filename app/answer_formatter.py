from __future__ import annotations


def _normalize_row(row: dict) -> dict:
    normalized = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[key.upper()] = value
    return normalized


def format_answer(result: dict) -> str:
    row_count = result.get("row_count", 0)
    if row_count == 0:
        return "No matching records found for this question."

    intent = result.get("intent")
    rows = result.get("rows") or []
    if not rows:
        return f"Found {row_count} records. Showing key fields below."

    first_row = _normalize_row(rows[0])
    if intent == "last_purchase_date_by_item_name":
        item_name = None
        parameters = result.get("parameters") or {}
        entities = result.get("understanding", {}).get("entities", {})
        item_name = parameters.get("item_name") or entities.get("item_name")

        order_no = first_row.get("ORDERNO") or first_row.get("PO.ORDERNO") or first_row.get("ORDER_NO")
        date = first_row.get("LAST_PURCHASE_DATE") or first_row.get("ORDERDATE") or first_row.get("PO.ORDERDATE")
        company = first_row.get("COMPANY_NAME") or first_row.get("PARTYNAME")
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

        return ": ".join([summary_parts[0], ", ".join(summary_parts[1:])]) if len(summary_parts) > 1 else summary_parts[0]

    return f"Found {row_count} records. Showing key fields below."
