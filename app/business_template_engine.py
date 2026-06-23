import json
import re
from pathlib import Path


TEMPLATE_PATH = Path("data/business_query_templates.json")


def _load_templates() -> list[dict]:
    if not TEMPLATE_PATH.exists():
        return []

    with TEMPLATE_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _clean_material_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[;'\"]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.upper()


def _clean_exact_code(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[;'\"]", "", value)
    value = re.sub(r"\s+", " ", value)
    value = value.upper()
    if " " in value:
        value = value.split(" ", 1)[0]
    if not re.match(r"^[A-Z0-9_-]+$", value):
        return ""
    return value


def _extract_parameter(question: str, matched_phrase: str) -> str | None:
    lower_question = question.lower()
    start = lower_question.find(matched_phrase)

    if start == -1:
        return None

    raw_value = question[start + len(matched_phrase):].strip()

    # Support suffix style questions like "keyboard stock"
    # where the parameter appears before the matched phrase.
    if not raw_value:
        raw_value = question[:start].strip()

    # Remove common trailing words that are not part of search value.
    raw_value = re.sub(
        r"\b(today|this month|this year|details|list|report|please|show|give me)\b",
        "",
        raw_value,
        flags=re.IGNORECASE,
    ).strip()

    if not raw_value:
        return None

    return _clean_material_name(raw_value)


def match_business_template(question: str) -> dict | None:
    question_lower = question.lower().strip()
    templates = _load_templates()

    # First try deterministic layman router to map casual language to template intents
    layman_result = _layman_route(question, templates)
    if layman_result:
        return layman_result

    best_match = None
    best_phrase = ""

    for template in templates:
        for phrase in template.get("phrases", []):
            phrase_lower = phrase.lower()

            if phrase_lower in question_lower and len(phrase_lower) > len(best_phrase):
                best_match = (template, phrase_lower)
                best_phrase = phrase_lower

    if not best_match:
        return None

    template, phrase_lower = best_match
    parameter_value = _extract_parameter(question, phrase_lower)

    if not parameter_value:
        return None

    required_parameter = template.get("required_parameter", "item_name")
    placeholder = "{" + required_parameter.upper() + "}"

    if required_parameter in {"item_code", "party_code", "empcode", "supplier_code"}:
        parameter_value = _clean_exact_code(parameter_value)
        if not parameter_value:
            return None

    if required_parameter == "dept_name":
        parameter_value = re.sub(
            r"\b(department|dept)\b",
            "",
            parameter_value,
            flags=re.IGNORECASE,
        ).strip()
        parameter_value = re.sub(r"\s+", " ", parameter_value)

    sql = template["sql_template"].replace(
        placeholder,
        parameter_value,
    )

    return {
        "matched": True,
        "intent": template["intent"],
        "question": question,
        "sql": sql,
        "explanation": template.get("explanation"),
        "tables_used": template.get("tables_used", []),
        "relationships_used": template.get("relationships_used", []),
        "confidence": template.get("confidence", 0.95),
        "parameters": {
            required_parameter: parameter_value,
        },
        "source": "business_template",
    }


def _layman_route(question: str, templates: list[dict]) -> dict | None:
    """
    Deterministic layman router: match casual phrases and extract entities.
    Returns a template_result dict (same structure as match_business_template) or None.
    """
    q = question.strip()
    ql = q.lower()

    def find_template_by_intent(intent_name: str) -> dict | None:
        for t in templates:
            if t.get("intent") == intent_name:
                return t
        return None

    # Entity extractors
    def extract_item_code(s: str) -> str | None:
        m = re.search(r"\b([A-Za-z]\d{5,9})\b", s)
        return m.group(1).upper() if m else None

    def extract_supplier_code(s: str) -> str | None:
        m = re.search(r"(?:supplier|vendor|party|company)(?:\s+(?:details|details\s+for|gst|pan|by|name|code|for|is|party|vendor|supplier|company))*\s*[:\-]?\s*([A-Za-z0-9_-]{3,12})(?!\s+[A-Za-z])\b", s, flags=re.I)
        if m:
            return m.group(1).upper()
        m = re.search(r"\b(?:supplier|vendor|party)\s+([0-9]{3,7})(?!\s+[A-Za-z])\b", s, flags=re.I)
        if m:
            return m.group(1)
        return None

    def extract_party_code(s: str) -> str | None:
        m = re.search(r"(?:party)(?:\s+(?:code|details|details\s+for|is|for))*\s*[:\-]?\s*([A-Za-z0-9_-]{3,12})(?!\s+[A-Za-z])\b", s, flags=re.I)
        if m:
            return m.group(1).upper()
        return None

    def extract_empcode(s: str) -> str | None:
        m = re.search(r"empcode\s*[:\-]?\s*(\d{4,7})\b", s, flags=re.I)
        if m:
            return m.group(1)
        m = re.search(r"(?:staff|employee|emp|worker|person)\s*[:\-]?\s*(\d{4,7})\b", s, flags=re.I)
        if m:
            return m.group(1)
        return None

    def extract_issue_no(s: str) -> str | None:
        m = re.search(r"issue\s*(?:number|no)?\s*[:\-]?\s*(\d{1,7})\b", s, flags=re.I)
        if m:
            return m.group(1)
        return None

    def extract_indent_no(s: str) -> str | None:
        m = re.search(r"request\s*(?:no|number)?\s*[:\-]?\s*(\d{1,7})\b", s, flags=re.I)
        if m:
            return m.group(1)
        m = re.search(r"indent\s*(?:number|no)?\s*[:\-]?\s*(\d{1,7})\b", s, flags=re.I)
        if m:
            return m.group(1)
        return None

    def normalize_simple_plural(value: str) -> str:
        if value.endswith("ies") and len(value) > 4:
            return value[:-3] + "y"
        if value.endswith("ses") and len(value) > 4:
            return value[:-2]
        if value.endswith("s") and len(value) > 3 and not value.endswith(("ss", "us", "is")):
            return value[:-1]
        return value

    def clean_layman_item_name(value: str) -> str | None:
        if not value:
            return None
        normalized = re.sub(r"[^\w\s\-]+", " ", value.lower())
        normalized = re.sub(
            r"\b(in stock|in stores|in store|stock|stores|store|how much|do we have|show|please|available|availability|current|of|for|the|details|detail|materials|material|items|item|supplier|vendor|party|company|name|with|by|from|last|latest|purchase|purchased|supplied|gst|pan|who|is|which|was|po|order|orders|pending|open|summary|total|received|goods|receipt|dept|department|unit|requested|request|mr|mrs|issue|issued|issues|given|taken)\b",
            " ",
            normalized,
            flags=re.I,
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None
        tokens = [normalize_simple_plural(tok) for tok in normalized.split() if tok]
        result = " ".join(tokens).strip()
        return _clean_material_name(result) if result else None

    def build_like_clause(item_name: str, column: str = "INV.ITEM_NAME") -> str:
        terms = [t.strip() for t in re.split(r"\band\b|,|;|\s+&\s+", item_name, flags=re.I) if t.strip()]
        if len(terms) <= 1:
            return f"UPPER({column}) LIKE '%{item_name}%'"
        parts = [f"UPPER({column}) LIKE '%{term}%" + "'" for term in terms]
        return " OR ".join(parts)

    def extract_item_name(s: str) -> str | None:
        # Known phrase patterns first
        m = re.search(r"how\s+much\s+(.+?)\s+(?:did\s+we\s+order|did\s+we\s+purchase|did\s+we\s+buy|was\s+ordered|was\s+purchased|were\s+ordered|were\s+purchased)\b", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"(?:last\s+purchase\s+date\s+for|latest\s+purchase\s+date\s+for|last\s+bought\s+date\s+for|last\s+po\s+date\s+for|last\s+order\s+date\s+for|last\s+received\s+date\s+for|last\s+grn\s+date\s+for|last\s+goods\s+received\s+date\s+for)\s+(.+)$", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"\bwhen\s+did\s+we\s+buy\s+(.+)$", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"\bwhen\s+was\s+(?:the\s+)?(.+?)\s+last\s+purchased\b", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"\bwhen\s+did\s+(.+?)\s+last\s+come\b", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"(.+?)\s+(?:last\s+purchase\s+date|last\s+purchased|latest\s+purchase|last\s+bought|last\s+po\s+date|last\s+order\s+date|last\s+received\s+date|last\s+grn\s+date|last\s+goods\s+received\s+date)\b", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"(?:show\s+stock\s+for|stock\s+for|stock\s+of|available\s+stock\s+for|available\s+stock\s+of|current\s+stock\s+for|current\s+stock\s+of)\s+(.+)$", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"(?:pending\s+mrs\s+for|pending\s+material\s+requisition\s+for|mrs\s+pending\s+for|material\s+requisition\s+pending\s+for|issue\s+for|issued\s+for|issues\s+for|what\s+was\s+issued\s+for|what\s+were\s+the\s+issues\s+for)\s+(.+)$", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"(?:for|of)\s+(.+)$", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        m = re.search(r"([\w\-]+)\s+stock$", s, flags=re.I)
        if m:
            return clean_layman_item_name(m.group(1))
        return clean_layman_item_name(s)

    # Normalize casual keywords to intent hints
    # Stock / availability
    stock_keywords = ["stock", "available", "availability", "balance", "how much", "do we have", "in stores", "in stock", "available ah", "is there"]
    item_detail_keywords = ["item details", "item master", "item info", "material details", "show item", "details of item"]
    po_keywords = ["purchase order", "po", "ordered", "bought", "purchase", "orders for", "what did we order", "what did we buy"]
    grn_keywords = ["grn", "goods receipt", "goods received", "received", "came", "what came", "items received"]
    supplier_keywords = ["supplier", "vendor", "party", "who is", "gst", "pan"]
    employee_keywords = ["employee", "staff", "people", "workers", "empcode", "people working"]
    mrs_keywords = ["mrs", "material requisition", "material request", "requested", "pending request"]
    issue_keywords = ["issue", "issued", "given", "material given", "material issued", "taken from stores"]
    indent_keywords = ["indent", "request no", "request", "indent no", "department request"]
    # Admin/document/transaction keywords: if present, avoid generic supplier/employee routing
    admin_keywords = [
        "cashbank",
        "voucher",
        "document",
        "vehicle",
        "movement",
        "camera",
        "video",
        "attendance",
        "authentication",
        "department authentication",
        "gate",
        "mainpass",
        "current attendance",
    ]
    is_admin_like = any(k in ql for k in admin_keywords)

    # Priority 1: Exact code/entity routes (item/party/emp/supplier/issue/indent/order)
    def extract_order_no(s: str) -> str | None:
        m = re.search(r"(?:orderno|order\s*(?:number|no)?)\s*[:\-]?\s*(\d{1,10})\b", s, flags=re.I)
        return m.group(1) if m else None

    # Item code routes: prefer context-specific templates (PO/GRN/MRS/ISSUE/INDENT), else stock
    item_code = extract_item_code(q)
    if item_code:
        if any(k in ql for k in po_keywords):
            tmpl = find_template_by_intent("pending_po_by_item_code")
        elif any(k in ql for k in grn_keywords):
            tmpl = find_template_by_intent("grn_by_item_code")
        elif any(k in ql for k in mrs_keywords):
            tmpl = find_template_by_intent("pending_mrs_by_item_code")
        elif any(k in ql for k in issue_keywords):
            tmpl = find_template_by_intent("issue_by_item_code")
        elif any(k in ql for k in indent_keywords):
            tmpl = find_template_by_intent("indent_by_item_code")
        elif any(k in ql for k in item_detail_keywords):
            tmpl = find_template_by_intent("item_master_by_item_code")
        else:
            tmpl = find_template_by_intent("stock_by_item_code")

        if tmpl:
            sql = tmpl["sql_template"].replace("{ITEM_CODE}", item_code)
            return {
                "matched": True,
                "intent": tmpl["intent"],
                "question": question,
                "sql": sql,
                "explanation": tmpl.get("explanation"),
                "tables_used": tmpl.get("tables_used", []),
                "relationships_used": tmpl.get("relationships_used", []),
                "confidence": tmpl.get("confidence", 0.99),
                "parameters": {"item_code": item_code},
                "source": "layman_router",
            }

    # Latest purchase by item name should take priority before supplier/party extraction.
    if re.search(r"(?:last\s+purchase\s+(?:of|for)|latest\s+purchase\s+(?:of|for)|which\s+company\s+supplied|from\s+which\s+company|latest\s+purchase\s+company\s+name|latest\s+purchase\s+supplier\s+name)\b", ql):
        item = extract_item_name(q)
        if item:
            tmpl = find_template_by_intent("last_purchase_date_by_item_name")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.98),
                    "parameters": {"item_name": item},
                    "source": "layman_router",
                }

    # Party / supplier / empcode / issue / indent / order
    party = extract_party_code(q)
    if party and not is_admin_like:
        tmpl = find_template_by_intent("supplier_by_party_code")
        if tmpl:
            sql = tmpl["sql_template"].replace("{PARTY_CODE}", party)
            return {
                "matched": True,
                "intent": tmpl["intent"],
                "question": question,
                "sql": sql,
                "explanation": tmpl.get("explanation"),
                "tables_used": tmpl.get("tables_used", []),
                "relationships_used": tmpl.get("relationships_used", []),
                "confidence": tmpl.get("confidence", 0.99),
                "parameters": {"party_code": party},
                "source": "layman_router",
            }

    emp = extract_empcode(q)
    if emp and not is_admin_like:
        tmpl = find_template_by_intent("employee_by_empcode")
        if tmpl:
            sql = tmpl["sql_template"].replace("{EMPCODE}", emp)
            return {
                "matched": True,
                "intent": tmpl["intent"],
                "question": question,
                "sql": sql,
                "explanation": tmpl.get("explanation"),
                "tables_used": tmpl.get("tables_used", []),
                "relationships_used": tmpl.get("relationships_used", []),
                "confidence": tmpl.get("confidence", 0.99),
                "parameters": {"empcode": emp},
                "source": "layman_router",
            }

    sup = extract_supplier_code(q)
    if sup and not is_admin_like:
        if re.search(r"summary|total|how much", ql) and any(k in ql for k in grn_keywords):
            tmpl = find_template_by_intent("grn_summary_by_supplier_code")
        elif any(k in ql for k in grn_keywords):
            tmpl = find_template_by_intent("grn_by_supplier_code")
        elif re.search(r"summary|total|how much", ql) and any(k in ql for k in po_keywords):
            tmpl = find_template_by_intent("po_summary_by_supplier_code")
        elif any(k in ql for k in po_keywords):
            tmpl = find_template_by_intent("pending_po_by_supplier_code")
        else:
            tmpl = find_template_by_intent("supplier_by_party_code")

        if tmpl:
            placeholder = "{SUPPLIER_CODE}"
            if tmpl["intent"] == "supplier_by_party_code":
                placeholder = "{PARTY_CODE}"
            sql = tmpl["sql_template"].replace(placeholder, sup)
            return {
                "matched": True,
                "intent": tmpl["intent"],
                "question": question,
                "sql": sql,
                "explanation": tmpl.get("explanation"),
                "tables_used": tmpl.get("tables_used", []),
                "relationships_used": tmpl.get("relationships_used", []),
                "confidence": tmpl.get("confidence", 0.99),
                "parameters": {"supplier_code": sup},
                "source": "layman_router",
            }

    if any(k in ql for k in issue_keywords):
        issue_no = extract_issue_no(q)
        if issue_no:
            tmpl = find_template_by_intent("issue_by_issue_no")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ISSUE_NO}", issue_no)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.99),
                    "parameters": {"issue_no": issue_no},
                    "source": "layman_router",
                }
        indent_no = extract_indent_no(q)
        if indent_no:
            tmpl = find_template_by_intent("issue_by_indent_no")
            if tmpl:
                sql = tmpl["sql_template"].replace("{INDENT_NO}", indent_no)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.99),
                    "parameters": {"indent_no": indent_no},
                    "source": "layman_router",
                }
        item = extract_item_name(q)
        if item:
            tmpl = find_template_by_intent("issue_by_item_name")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.99),
                    "parameters": {"item_name": item},
                    "source": "layman_router",
                }

    indent_no = extract_indent_no(q)
    if indent_no and any(k in ql for k in issue_keywords):
        tmpl = find_template_by_intent("issue_by_indent_no")
        if tmpl:
            sql = tmpl["sql_template"].replace("{INDENT_NO}", indent_no)
            return {
                "matched": True,
                "intent": tmpl["intent"],
                "question": question,
                "sql": sql,
                "explanation": tmpl.get("explanation"),
                "tables_used": tmpl.get("tables_used", []),
                "relationships_used": tmpl.get("relationships_used", []),
                "confidence": tmpl.get("confidence", 0.99),
                "parameters": {"indent_no": indent_no},
                "source": "layman_router",
            }

    if indent_no:
        tmpl = find_template_by_intent("indent_by_indent_no")
        if tmpl:
            sql = tmpl["sql_template"].replace("{INDENT_NO}", indent_no)
            return {
                "matched": True,
                "intent": tmpl["intent"],
                "question": question,
                "sql": sql,
                "explanation": tmpl.get("explanation"),
                "tables_used": tmpl.get("tables_used", []),
                "relationships_used": tmpl.get("relationships_used", []),
                "confidence": tmpl.get("confidence", 0.99),
                "parameters": {"indent_no": indent_no},
                "source": "layman_router",
            }

    order_no = extract_order_no(q)
    if order_no and re.search(r"order", ql):
        # Prefer purchase order route for supplier-related purchase enquiries.
        sup = extract_supplier_code(q)
        if sup and any(k in ql for k in po_keywords):
            tmpl = find_template_by_intent("pending_po_by_supplier_code")
            if tmpl:
                sql = tmpl["sql_template"].replace("{SUPPLIER_CODE}", sup)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.99),
                    "parameters": {"supplier_code": sup},
                    "source": "layman_router",
                }
        tmpl = find_template_by_intent("grn_by_order_no")
        if tmpl:
            sql = tmpl["sql_template"].replace("{ORDER_NO}", order_no)
            return {
                "matched": True,
                "intent": tmpl["intent"],
                "question": question,
                "sql": sql,
                "explanation": tmpl.get("explanation"),
                "tables_used": tmpl.get("tables_used", []),
                "relationships_used": tmpl.get("relationships_used", []),
                "confidence": tmpl.get("confidence", 0.99),
                "parameters": {"order_no": order_no},
                "source": "layman_router",
            }

    # Supplier by name (who is dutch blue / dutch blue gst)
    # keep this later so it doesn't preempt codes
    if not is_admin_like and re.search(r"(?:who is|show supplier|show vendor|supplier details|vendor details|supplier gst|vendor gst|supplier by gst|vendor by gst|supplier by name|vendor by name)\b", ql):
        # capture the trailing supplier name after the intent phrase
        m = re.search(r"(?:who is|show supplier(?: details)?(?: for)?|show vendor(?: details)?(?: for)?|supplier details(?: for)?|vendor details(?: for)?|supplier gst(?: for)?|vendor gst(?: for)?|supplier by gst|vendor by gst|supplier by name|vendor by name)\s+(.+?)(?:\?|$)", ql)
        name = None
        if m:
            name = m.group(1).strip()
        else:
            # fallback to any trailing words
            m2 = re.search(r"who is\s+([\w\s]+?)(?:\?|$)", ql)
            if m2:
                name = m2.group(1).strip()
        if name:
            cleaned_name = clean_layman_item_name(name) or _clean_material_name(name)
            if cleaned_name:
                tmpl = find_template_by_intent("supplier_by_name")
                if tmpl:
                    sql = tmpl["sql_template"].replace("{PARTY_NAME}", cleaned_name)
                    return {
                        "matched": True,
                        "intent": tmpl["intent"],
                        "question": question,
                        "sql": sql,
                        "explanation": tmpl.get("explanation"),
                        "tables_used": tmpl.get("tables_used", []),
                        "relationships_used": tmpl.get("relationships_used", []),
                        "confidence": tmpl.get("confidence", 0.95),
                        "parameters": {"party_name": cleaned_name},
                        "source": "layman_router",
                    }

    # PO summary by supplier code
    if re.search(r"(po summary|purchase order summary|purchase total|purchase summary|supplier purchase total|how much purchased|total ordered)", ql):
        sup = extract_supplier_code(q)
        if sup:
            tmpl = find_template_by_intent("po_summary_by_supplier_code")
            if tmpl:
                sql = tmpl["sql_template"].replace("{SUPPLIER_CODE}", sup)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"supplier_code": sup},
                    "source": "layman_router",
                }

    # Purchase order summary by item name
    po_summary_match = re.search(r"how\s+much\s+(.+?)\s+(?:did\s+we\s+order|did\s+we\s+purchase|did\s+we\s+buy|was\s+ordered|was\s+purchased|were\s+ordered|were\s+purchased)\b", ql)
    if po_summary_match:
        item = clean_layman_item_name(po_summary_match.group(1))
        if item:
            tmpl = find_template_by_intent("po_summary_by_item_name")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.98),
                    "parameters": {"item_name": item},
                    "source": "layman_router",
                }

    # Purchase order last purchase date by item name
    if re.search(r"last\s+purchase|last\s+purchased|latest\s+purchase|last\s+bought|when\s+did\s+we\s+buy|last\s+po\s+date|last\s+order\s+date", ql):
        item = extract_item_name(q)
        if item:
            tmpl = find_template_by_intent("last_purchase_date_by_item_name")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.98),
                    "parameters": {"item_name": item},
                    "source": "layman_router",
                }

    # Goods receipt last received date by item name
    if re.search(r"last\s+received|last\s+grn|last\s+goods\s+received|when\s+did\s+.*last\s+come|last\s+grn\s+date", ql):
        item = extract_item_name(q)
        if item:
            tmpl = find_template_by_intent("last_received_date_by_item_name")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.98),
                    "parameters": {"item_name": item},
                    "source": "layman_router",
                }

    # Purchase order by item name
    if any(k in ql for k in po_keywords) or re.search(r"how much\s+.*\s+(?:did we order|did we purchase|did we buy|was ordered|was purchased|were ordered)\b", ql):
        if re.search(r"(?:purchase\s+orders?|po\s+for|open\s+purchase\s+orders?|pending\s+purchase\s+orders?|open\s+purchase\s+order|pending\s+purchase\s+order|purchase\s+order\s+for|orders\s+for)\b", ql) or re.search(r"how much\s+.*\s+(?:did we order|did we purchase|did we buy|was ordered|was purchased|were ordered)\b", ql):
            item = extract_item_name(q)
            if item:
                tmpl = find_template_by_intent("pending_po_by_item_name")
                if tmpl:
                    if re.search(r"\band\b|,", item, flags=re.I):
                        clause = build_like_clause(item)
                        sql = tmpl["sql_template"].replace("WHERE UPPER(INV.ITEM_NAME) LIKE '%{ITEM_NAME}%'", f"WHERE ({clause})")
                    else:
                        sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                    return {
                        "matched": True,
                        "intent": tmpl["intent"],
                        "question": question,
                        "sql": sql,
                        "explanation": tmpl.get("explanation"),
                        "tables_used": tmpl.get("tables_used", []),
                        "relationships_used": tmpl.get("relationships_used", []),
                        "confidence": tmpl.get("confidence", 0.98),
                        "parameters": {"item_name": item},
                        "source": "layman_router",
                    }

    # Item details / master
    if any(k in ql for k in item_detail_keywords):
        code = extract_item_code(q)
        if code:
            tmpl = find_template_by_intent("item_master_by_item_code")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_CODE}", code)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"item_code": code},
                    "source": "layman_router",
                }
        else:
            m = re.search(r"(?:for|of)\s+([\w\s\-]+)$", ql)
            if m:
                item = m.group(1).strip()
                tmpl = find_template_by_intent("item_master_by_item_name")
                if tmpl:
                    param = clean_layman_item_name(item) or _clean_material_name(item)
                    sql = tmpl["sql_template"].replace("{ITEM_NAME}", param)
                    return {
                        "matched": True,
                        "intent": tmpl["intent"],
                        "question": question,
                        "sql": sql,
                        "explanation": tmpl.get("explanation"),
                        "tables_used": tmpl.get("tables_used", []),
                        "relationships_used": tmpl.get("relationships_used", []),
                        "confidence": tmpl.get("confidence", 0.95),
                        "parameters": {"item_name": param},
                        "source": "layman_router",
                    }

    # Employee intents
    if any(k in ql for k in employee_keywords) and not is_admin_like:
        emp = extract_empcode(q)
        if emp:
            tmpl = find_template_by_intent("employee_by_empcode")
            if tmpl:
                sql = tmpl["sql_template"].replace("{EMPCODE}", emp)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"empcode": emp},
                    "source": "layman_router",
                }
        # department based
        m = re.search(r"(?:in|for|working in)\s+([\w\s\-]+)$", ql)
        if m:
            dept = m.group(1).strip()
            tmpl = find_template_by_intent("employee_by_department")
            if tmpl:
                param = re.sub(r"\b(department|dept)\b", "", dept, flags=re.I).strip()
                param = re.sub(r"\s+", " ", param)
                sql = tmpl["sql_template"].replace("{DEPT_NAME}", _clean_material_name(param))
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"dept_name": param},
                    "source": "layman_router",
                }

    # MRS intents
    if any(k in ql for k in mrs_keywords):
        code = extract_item_code(q)
        if code:
            tmpl = find_template_by_intent("pending_mrs_by_item_code")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_CODE}", code)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"item_code": code},
                    "source": "layman_router",
                }
        else:
            m = re.search(r"(?:for|of)\s+([\w\s\-]+)$", ql)
            if m:
                item = m.group(1).strip()
                tmpl = find_template_by_intent("pending_mrs_by_item_name")
                if tmpl:
                    param = clean_layman_item_name(item) or _clean_material_name(item)
                    sql = tmpl["sql_template"].replace("{ITEM_NAME}", param)
                    return {
                        "matched": True,
                        "intent": tmpl["intent"],
                        "question": question,
                        "sql": sql,
                        "explanation": tmpl.get("explanation"),
                        "tables_used": tmpl.get("tables_used", []),
                        "relationships_used": tmpl.get("relationships_used", []),
                        "confidence": tmpl.get("confidence", 0.95),
                        "parameters": {"item_name": param},
                        "source": "layman_router",
                    }

    # Issue intents
    if any(k in ql for k in issue_keywords):
        code = extract_item_code(q)
        if code:
            tmpl = find_template_by_intent("issue_by_item_code")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_CODE}", code)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"item_code": code},
                    "source": "layman_router",
                }
    if any(k in ql for k in issue_keywords):
        issue_no = extract_issue_no(q)
        if issue_no:
            tmpl = find_template_by_intent("issue_by_issue_no")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ISSUE_NO}", issue_no)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"issue_no": issue_no},
                    "source": "layman_router",
                }
        item = extract_item_name(q)
        if item:
            tmpl = find_template_by_intent("issue_by_item_name")
            if tmpl:
                sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"item_name": item},
                    "source": "layman_router",
                }
    # Indent intents
    if any(k in ql for k in indent_keywords):
        ind = extract_indent_no(q)
        if ind:
            tmpl = find_template_by_intent("indent_by_indent_no")
            if tmpl:
                sql = tmpl["sql_template"].replace("{INDENT_NO}", ind)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"indent_no": ind},
                    "source": "layman_router",
                }
        # dept based indent
        m = re.search(r"department\s+([\w\s\-]+)$", ql)
        if m:
            dept = m.group(1).strip()
            tmpl = find_template_by_intent("indent_by_department")
            if tmpl:
                param = _clean_material_name(dept)
                sql = tmpl["sql_template"].replace("{DEPT_NAME}", param)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"dept_name": param},
                    "source": "layman_router",
                }

    # Stock intents (LOWEST priority)
    if any(k in ql for k in stock_keywords):
        # department/unit specific stock
        m_dep = re.search(r"department\s+([\w\-\s]+)$", ql)
        if m_dep:
            dept = _clean_material_name(m_dep.group(1))
            if "summary" in ql:
                tmpl = find_template_by_intent("stock_summary_by_department")
            else:
                tmpl = find_template_by_intent("stock_by_department")
            if tmpl:
                sql = tmpl["sql_template"].replace("{DEPT_NAME}", dept)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"dept_name": dept},
                    "source": "layman_router",
                }
        m_unit = re.search(r"unit\s+([\w\-\s]+)$", ql)
        if m_unit:
            unit = _clean_material_name(m_unit.group(1))
            if "summary" in ql:
                tmpl = find_template_by_intent("stock_summary_by_unit")
            else:
                tmpl = find_template_by_intent("stock_by_unit")
            if tmpl:
                sql = tmpl["sql_template"].replace("{UNIT_NAME}", unit)
                return {
                    "matched": True,
                    "intent": tmpl["intent"],
                    "question": question,
                    "sql": sql,
                    "explanation": tmpl.get("explanation"),
                    "tables_used": tmpl.get("tables_used", []),
                    "relationships_used": tmpl.get("relationships_used", []),
                    "confidence": tmpl.get("confidence", 0.95),
                    "parameters": {"unit_name": unit},
                    "source": "layman_router",
                }

        prefer_stock_detail = bool(re.search(r"(?:show\s+stock\s+for|stock\s+for|stock\s+of|show\s+stock\s+of|current\s+stock\s+for|current\s+stock\s+of|stock\s+balance\s+for|do\s+we\s+have.*in\s+stock|how\s+much.*in\s+stock)", ql))
        prefer_stock_availability = bool(re.search(r"(?:stock\s+availability|available\s+stock|availability\s+for|how much|do we have)\b", ql)) and not re.search(r"\bin\s+stock\b", ql)

        m_summary = re.search(r"how\s+much\s+(.+?)\s+is\s+in\s+stock\b", ql)
        if m_summary:
            item = clean_layman_item_name(m_summary.group(1))
            if item:
                tmpl = find_template_by_intent("stock_summary_by_item_name")
                if tmpl:
                    sql = tmpl["sql_template"].replace("{ITEM_NAME}", item)
                    return {
                        "matched": True,
                        "intent": tmpl["intent"],
                        "question": question,
                        "sql": sql,
                        "explanation": tmpl.get("explanation"),
                        "tables_used": tmpl.get("tables_used", []),
                        "relationships_used": tmpl.get("relationships_used", []),
                        "confidence": tmpl.get("confidence", 0.95),
                        "parameters": {"item_name": item},
                        "source": "layman_router",
                    }

        candidate = None
        m = re.search(r"(?:show\s+stock\s+for|stock\s+for|stock\s+of|show\s+stock\s+of|available\s+stock\s+for|available\s+stock\s+of|current\s+stock\s+for|current\s+stock\s+of|stock\s+balance\s+for)\s+(.+)$", ql)
        if m:
            candidate = m.group(1).strip()
        elif m2 := re.search(r"do\s+we\s+have\s+([\w\-\s]+?)(?:\s+in\s+stock)?$", ql):
            candidate = m2.group(1).strip()
        elif m3 := re.search(r"([\w\-]+)\s+stock$", ql):
            candidate = m3.group(1).strip()
        if not candidate:
            candidate = extract_item_name(q)

        if candidate:
            cand_clean = clean_layman_item_name(candidate) or _clean_material_name(candidate)
            if cand_clean and cand_clean.upper() not in {"STOCK", "DEPARTMENT", "DEPT", "UNIT", "ORDER", "PO", "GRN", "SUPPLIER", "VENDOR", "PARTY"}:
                if prefer_stock_detail and find_template_by_intent("stock_by_item_name"):
                    tmpl = find_template_by_intent("stock_by_item_name")
                elif prefer_stock_availability and find_template_by_intent("stock_availability_by_item_name"):
                    tmpl = find_template_by_intent("stock_availability_by_item_name")
                else:
                    tmpl = find_template_by_intent("stock_availability_by_item_name") or find_template_by_intent("stock_by_item_name") or find_template_by_intent("stock_by_item_code")
                if tmpl:
                    sql = tmpl["sql_template"].replace("{ITEM_NAME}", cand_clean)
                    return {
                        "matched": True,
                        "intent": tmpl["intent"],
                        "question": question,
                        "sql": sql,
                        "explanation": tmpl.get("explanation"),
                        "tables_used": tmpl.get("tables_used", []),
                        "relationships_used": tmpl.get("relationships_used", []),
                        "confidence": tmpl.get("confidence", 0.95),
                        "parameters": {"item_name": cand_clean},
                        "source": "layman_router",
                    }

    return None
