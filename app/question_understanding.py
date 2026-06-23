import re

INVENTORY_WORDS = {
    "stock",
    "item",
    "material",
    "po",
    "purchase",
    "purchase order",
    "grn",
    "goods receipt",
    "supplier",
    "vendor",
    "party",
    "mrs",
    "indent",
    "issue",
    "stores",
}

HR_WORDS = {
    "employee",
    "staff",
    "attendance",
    "department",
    "designation",
    "contractor",
    "overtime",
    "cashbank",
    "voucher",
    "camera",
    "vehicle",
    "document",
}

INVENTORY_COLLECTIONS = ["ajsmgpt_inventory_schema_metadata"]
HR_COLLECTIONS = ["ajsmgpt_schema_metadata"]
ADMIN_COLLECTIONS = ["ajsmgpt_schema_metadata"]
BOTH_COLLECTIONS = ["ajsmgpt_inventory_schema_metadata", "ajsmgpt_schema_metadata"]

KNOWN_TEMPLATE_INTENTS = {
    "last_purchase_date_by_item_name",
    "last_received_date_by_item_name",
    "pending_po_by_item_name",
    "po_summary_by_item_name",
    "stock_by_item_name",
    "stock_availability_by_item_name",
    "supplier_by_name",
}


def _clean_item_name(value: str) -> str | None:
    if not value:
        return None

    normalized = value.strip().lower()
    normalized = re.sub(r"[;'\"]", "", normalized)
    normalized = re.sub(r"\b(company|supplier|vendor|party|name|with|by|from|last|latest|purchase|purchased|supplied|order|orders|details|detail|show|please|what|which|was|did|we|the|a|an)\b", " ", normalized, flags=re.I)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if not normalized:
        return None

    return normalized.upper()


def _extract_item_name(question: str) -> str | None:
    q = question.strip()

    patterns = [
        r"last\s+purchase\s+of\s+(.+?)(?:\s+by\s+(?:company|supplier)\s+name|\s+with\s+(?:supplier|company)\s+name|$)",
        r"latest\s+purchase\s+of\s+(.+?)(?:\s+by\s+(?:company|supplier)\s+name|\s+with\s+(?:supplier|company)\s+name|$)",
        r"which\s+company\s+supplied\s+(.+?)\s+last",
        r"from\s+which\s+company\s+(.+?)\s+last\s+purchased",
        r"(.+?)\s+latest\s+purchase\s+(?:company\s+name|supplier\s+name)",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            extracted = m.group(1).strip()
            cleaned = _clean_item_name(extracted)
            if cleaned:
                return cleaned

    return _clean_item_name(q)


def _requested_output_columns(question: str, intent_hint: str | None) -> list[str]:
    outputs = []
    ql = question.lower()

    if intent_hint == "last_purchase_date_by_item_name":
        outputs.append("last_purchase_date")

    if re.search(r"supplier\s+name|company\s+name|vendor\s+name|party\s+name", ql):
        outputs.append("supplier_company_name")

    if re.search(r"qty|quantity", ql):
        outputs.append("quantity")

    if re.search(r"rate", ql):
        outputs.append("rate")

    if re.search(r"net|value", ql):
        outputs.append("net")

    return outputs


def _detect_intent_hint(question: str) -> str | None:
    ql = question.lower()

    if re.search(r"last\s+purchase|last\s+purchased|latest\s+purchase|last\s+bought|last\s+po\s+date|last\s+order\s+date", ql):
        return "last_purchase_date_by_item_name"
    if re.search(r"last\s+received|last\s+grn|last\s+goods\s+received|when\s+did\s+.*last\s+come", ql):
        return "last_received_date_by_item_name"
    if re.search(r"pending\s+po|pending\s+purchase\s+order|open\s+purchase\s+order|purchase\s+order\s+for", ql):
        return "pending_po_by_item_name"
    if re.search(r"po\s+summary|purchase\s+order\s+summary|how\s+much\s+did\s+we\s+order|what\s+did\s+we\s+order", ql):
        return "po_summary_by_item_name"
    if re.search(r"supplier\s+name|company\s+name|vendor\s+name|party\s+name", ql):
        return "supplier_by_name"
    if re.search(r"stock\s+availability|available\s+stock|stock\s+balance", ql):
        return "stock_availability_by_item_name"
    if re.search(r"stock\s+for|stock\s+of|show\s+stock", ql):
        return "stock_by_item_name"
    return None


def understand_question(question: str) -> dict:
    ql = question.lower()
    inventory_matches = sum(1 for word in INVENTORY_WORDS if word in ql)
    hr_matches = sum(1 for word in HR_WORDS if word in ql)
    admin_matches = 0
    # admin/other: use ADMIN_COLLECTIONS
    for w in ["cashbank", "voucher", "camera", "vehicle", "document", "gate", "mainpass", "video"]:
        if w in ql:
            admin_matches += 1

    if inventory_matches and not hr_matches and not admin_matches:
        domain = "inventory"
    elif hr_matches and not inventory_matches and not admin_matches:
        domain = "hr"
    elif admin_matches and not inventory_matches and not hr_matches:
        domain = "admin"
    elif inventory_matches and hr_matches:
        domain = "mixed"
    else:
        domain = "general"

    # Select collections based on detected domain
    if domain == "inventory":
        collections = INVENTORY_COLLECTIONS
    elif domain == "hr":
        collections = HR_COLLECTIONS
    elif domain == "admin":
        collections = ADMIN_COLLECTIONS
    else:
        collections = BOTH_COLLECTIONS

    intent_hint = _detect_intent_hint(question)
    item_name = _extract_item_name(question)

    entities = {}
    if item_name:
        entities["item_name"] = item_name

    requested_outputs = _requested_output_columns(question, intent_hint)

    detail_level = "latest_one" if intent_hint in {"last_purchase_date_by_item_name", "last_received_date_by_item_name"} else "summary" if intent_hint in {"po_summary_by_item_name"} else "basic"

    needs_template = intent_hint in KNOWN_TEMPLATE_INTENTS
    needs_qwen = not needs_template

    return {
        "domain": domain,
        "intent_hint": intent_hint,
        "entities": entities,
        "requested_outputs": requested_outputs,
        "detail_level": detail_level,
        "collections": collections,
        "needs_template": needs_template,
        "needs_qwen": needs_qwen,
    }
