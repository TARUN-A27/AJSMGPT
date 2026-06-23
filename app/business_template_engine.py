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

    if required_parameter in {"item_code", "party_code", "empcode"}:
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
        parameter_value = re.sub(r"\\s+", " ", parameter_value)

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
