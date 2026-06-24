from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = AUTOMATE_DIR.parent

LOG_PATH = ROOT_DIR / "logs" / "user_questions.jsonl"
REPORTS_DIR = AUTOMATE_DIR / "reports"

LOG_ANALYSIS_MD = REPORTS_DIR / "log_analysis.md"
ROUTER_FIX_JSON = REPORTS_DIR / "router_fix_candidates.json"
QDRANT_TERMS_JSON = REPORTS_DIR / "qdrant_business_terms_candidates.json"

SLOW_QUERY_MS = 3000

KNOWN_WRONG_TABLES = [
    "INVENTORY.SUPPLIER",
    "INVENTORY.C12",
]

FALLBACK_SOURCE_KEYWORDS = [
    "fallback",
    "qwen_schema_fallback",
    "qwen_fallback",
    "llm_fallback",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    if not path.exists():
        print(f"Log file not found: {path}")
        return records

    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append(
                    {
                        "question": "",
                        "success": False,
                        "source": "invalid_log_line",
                        "error": f"Invalid JSON at line {line_no}",
                        "raw_line": line,
                    }
                )

    return records


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def is_failed_success(value: Any) -> bool:
    if value is False:
        return True

    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no", "failed", "fail"}

    return False


def get_sql_text(record: dict[str, Any]) -> str:
    return str(
        record.get("sql")
        or record.get("generated_sql")
        or record.get("query")
        or ""
    )


def combined_record_text(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("question", "")),
        get_sql_text(record),
        str(record.get("answer", "")),
        str(record.get("error", "")),
    ]
    return "\n".join(parts).upper()


def normalize_question(question: str) -> str:
    q = question.lower().strip()
    q = re.sub(r"[^a-z0-9\s]", " ", q)
    q = re.sub(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b", "<date>", q)
    q = re.sub(r"\b\d+\b", "<num>", q)

    # Group similar supplier questions.
    q = re.sub(r"from\s+supplier\s+.+$", "from supplier <name>", q)
    q = re.sub(r"supplier\s+.+$", "supplier <name>", q)

    q = re.sub(r"\s+", " ", q).strip()
    return q


def is_fallback_source(source: Any) -> bool:
    source_text = str(source or "").lower()
    return any(keyword in source_text for keyword in FALLBACK_SOURCE_KEYWORDS)


def detect_wrong_tables(record: dict[str, Any]) -> list[str]:
    text = combined_record_text(record)
    return [table for table in KNOWN_WRONG_TABLES if table.upper() in text]


def extract_supplier_name(question: str) -> str | None:
    patterns = [
        r"from\s+supplier\s+(.+)$",
        r"supplier\s+(.+)$",
        r"vendor\s+(.+)$",
        r"party\s+(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            value = re.sub(r"[^a-zA-Z0-9\s&.-]", "", value)
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                return value.upper()

    return None


def classify_problem(record: dict[str, Any], similar_count: int) -> list[str]:
    question = str(record.get("question", "")).lower()
    source = str(record.get("source", "")).lower()
    elapsed_ms = safe_int(record.get("elapsed_ms"))
    row_count = safe_int(record.get("row_count"), default=-1)

    failed = is_failed_success(record.get("success"))
    fallback = is_fallback_source(source)
    wrong_tables = detect_wrong_tables(record)

    problems: list[str] = []

    if failed:
        problems.append("failed_question")

    if fallback:
        problems.append("fallback_used")

    if elapsed_ms > SLOW_QUERY_MS:
        problems.append("slow_query")

    if wrong_tables:
        problems.append("wrong_fallback_table")

    if fallback and row_count == 0:
        problems.append("zero_rows_from_fallback")

    if similar_count > 1 and (failed or fallback):
        problems.append("repeated_similar_problem")

    if "supplier" in question or "supply" in question or "vendor" in question:
        problems.append("supplier_or_supply_question")

    if any(word in question for word in ["first", "oldest", "last", "latest", "recent"]):
        problems.append("date_ordering_needed")

    if any(word in question for word in ["today", "yesterday", "month", "year", "between", "from date", "to date"]):
        problems.append("date_handling_needed")

    if any(word in question for word in ["item", "material", "part", "product"]):
        problems.append("material_extraction_needed")

    return list(dict.fromkeys(problems))


def suggest_fix(record: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    question = str(record.get("question", "")).strip()
    question_lower = question.lower()
    supplier_name = extract_supplier_name(question)
    supplier_filter = supplier_name or "<SUPPLIER_NAME>"

    suggestion: dict[str, Any] = {
        "target_router_file": "REVIEW_REQUIRED",
        "suggested_intent_name": "REVIEW_REQUIRED",
        "suggested_sql_template_pattern": "Human review needed. Do not auto-apply.",
        "suggested_synonym_mapping": [],
        "suggested_qdrant_business_terms": [],
        "suggested_eval_test": {
            "question": question,
            "expected_source": "REVIEW_REQUIRED",
            "expected_intent": "REVIEW_REQUIRED",
            "expected_sql_contains": [],
        },
        "review_required": True,
        "auto_apply_allowed": False,
    }

    is_supplier_supply = (
        "supplier" in question_lower
        or "supply" in question_lower
        or "vendor" in question_lower
    )

    if is_supplier_supply and "first" in question_lower:
        suggestion.update(
            {
                "target_router_file": "app/purchase_analytics_router.py",
                "suggested_intent_name": "purchase_first_supply_by_supplier",
                "suggested_sql_template_pattern": (
                    "Use INVENTORY.PURCHASEORDER PO "
                    "JOIN INVENTORY.INVITEMS I "
                    "JOIN SCM.PARTYMASTER P. "
                    "Filter supplier with UPPER(P.PARTYNAME) LIKE supplier name. "
                    "Order by PO.ORDERDATE ASC and fetch first row only."
                ),
                "suggested_synonym_mapping": [
                    {
                        "words": [
                            "first supply",
                            "first purchase",
                            "oldest supply",
                            "initial supply",
                            "first supply from supplier",
                        ],
                        "intent": "purchase_first_supply_by_supplier",
                    }
                ],
                "suggested_qdrant_business_terms": [
                    {
                        "term": "supply",
                        "meaning": "purchase order / supplier purchase / GRN business flow",
                        "tables": [
                            "INVENTORY.PURCHASEORDER",
                            "INVENTORY.INVITEMS",
                            "SCM.PARTYMASTER",
                        ],
                    }
                ],
                "suggested_eval_test": {
                    "question": question,
                    "expected_source": "purchase_analytics_router",
                    "expected_intent": "purchase_first_supply_by_supplier",
                    "expected_sql_contains": [
                        "INVENTORY.PURCHASEORDER",
                        "INVENTORY.INVITEMS",
                        "SCM.PARTYMASTER",
                        f"UPPER(P.PARTYNAME) LIKE '%{supplier_filter}%'",
                        "ORDER BY PO.ORDERDATE ASC",
                    ],
                },
            }
        )

    elif is_supplier_supply and any(word in question_lower for word in ["last", "latest", "recent"]):
        suggestion.update(
            {
                "target_router_file": "app/purchase_analytics_router.py",
                "suggested_intent_name": "purchase_last_supply_by_supplier",
                "suggested_sql_template_pattern": (
                    "Use INVENTORY.PURCHASEORDER PO "
                    "JOIN INVENTORY.INVITEMS I "
                    "JOIN SCM.PARTYMASTER P. "
                    "Filter supplier with UPPER(P.PARTYNAME) LIKE supplier name. "
                    "Order by PO.ORDERDATE DESC and fetch first row only."
                ),
                "suggested_synonym_mapping": [
                    {
                        "words": [
                            "last supply",
                            "latest supply",
                            "recent supply",
                            "last purchase",
                            "latest purchase from supplier",
                        ],
                        "intent": "purchase_last_supply_by_supplier",
                    }
                ],
                "suggested_qdrant_business_terms": [
                    {
                        "term": "supply",
                        "meaning": "purchase order / supplier purchase / GRN business flow",
                        "tables": [
                            "INVENTORY.PURCHASEORDER",
                            "INVENTORY.INVITEMS",
                            "SCM.PARTYMASTER",
                        ],
                    }
                ],
                "suggested_eval_test": {
                    "question": question,
                    "expected_source": "purchase_analytics_router",
                    "expected_intent": "purchase_last_supply_by_supplier",
                    "expected_sql_contains": [
                        "INVENTORY.PURCHASEORDER",
                        "INVENTORY.INVITEMS",
                        "SCM.PARTYMASTER",
                        f"UPPER(P.PARTYNAME) LIKE '%{supplier_filter}%'",
                        "ORDER BY PO.ORDERDATE DESC",
                    ],
                },
            }
        )

    return suggestion


def should_create_candidate(problems: list[str]) -> bool:
    important_problem_types = {
        "failed_question",
        "fallback_used",
        "slow_query",
        "wrong_fallback_table",
        "zero_rows_from_fallback",
        "repeated_similar_problem",
    }

    return any(problem in important_problem_types for problem in problems)


def build_candidates(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_counts = Counter(
        normalize_question(str(record.get("question", "")))
        for record in logs
    )

    candidates: list[dict[str, Any]] = []

    for index, record in enumerate(logs, start=1):
        question = str(record.get("question", "")).strip()
        normalized_question = normalize_question(question)
        similar_count = normalized_counts[normalized_question]

        problems = classify_problem(record, similar_count)

        if not should_create_candidate(problems):
            continue

        suggestion = suggest_fix(record, problems)

        candidates.append(
            {
                "candidate_id": f"log_candidate_{index}",
                "question": question,
                "normalized_question": normalized_question,
                "source": record.get("source"),
                "intent": record.get("intent"),
                "success": record.get("success"),
                "row_count": record.get("row_count"),
                "elapsed_ms": record.get("elapsed_ms"),
                "wrong_tables_detected": detect_wrong_tables(record),
                "problem_types": problems,
                "similar_question_count": similar_count,
                "error": record.get("error"),
                "sql": get_sql_text(record),
                "suggestion": suggestion,
            }
        )

    return candidates


def build_qdrant_terms(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms: dict[str, dict[str, Any]] = {}

    for candidate in candidates:
        suggestion = candidate.get("suggestion", {})
        suggested_terms = suggestion.get("suggested_qdrant_business_terms", [])

        for item in suggested_terms:
            term = item.get("term")
            if not term:
                continue

            if term not in terms:
                terms[term] = {
                    "term": term,
                    "meaning": item.get("meaning"),
                    "tables": item.get("tables", []),
                    "sample_questions": [],
                    "review_required": True,
                    "auto_apply_allowed": False,
                }

            terms[term]["sample_questions"].append(candidate.get("question"))

    return list(terms.values())


def write_markdown_report(logs: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> None:
    problem_counter = Counter()

    for candidate in candidates:
        problem_counter.update(candidate.get("problem_types", []))

    lines: list[str] = []

    lines.append("# AJSMGPT AutomateQuery Log Analysis")
    lines.append("")
    lines.append(f"Generated at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Log file: `{LOG_PATH}`")
    lines.append(f"- Total log records read: `{len(logs)}`")
    lines.append(f"- Problem candidates found: `{len(candidates)}`")
    lines.append(f"- Slow query threshold: `{SLOW_QUERY_MS} ms`")
    lines.append("")
    lines.append("## Safety")
    lines.append("")
    lines.append("- This report is read-only analysis.")
    lines.append("- No Oracle SQL was executed.")
    lines.append("- No production router file was modified.")
    lines.append("- Every candidate requires human review.")
    lines.append("")
    lines.append("## Problem Type Counts")
    lines.append("")

    if problem_counter:
        for problem, count in problem_counter.most_common():
            lines.append(f"- `{problem}`: `{count}`")
    else:
        lines.append("- No problems detected.")

    lines.append("")
    lines.append("## Router Fix Candidates")
    lines.append("")

    if not candidates:
        lines.append("No router fix candidates found.")
    else:
        for candidate in candidates:
            suggestion = candidate.get("suggestion", {})

            lines.append(f"### {candidate.get('candidate_id')}")
            lines.append("")
            lines.append(f"- Question: `{candidate.get('question')}`")
            lines.append(f"- Source: `{candidate.get('source')}`")
            lines.append(f"- Intent: `{candidate.get('intent')}`")
            lines.append(f"- Success: `{candidate.get('success')}`")
            lines.append(f"- Row count: `{candidate.get('row_count')}`")
            lines.append(f"- Elapsed ms: `{candidate.get('elapsed_ms')}`")
            lines.append(f"- Wrong tables detected: `{candidate.get('wrong_tables_detected')}`")
            lines.append(f"- Problem types: `{candidate.get('problem_types')}`")
            lines.append(f"- Similar question count: `{candidate.get('similar_question_count')}`")
            lines.append("")
            lines.append("Suggested fix:")
            lines.append("")
            lines.append(f"- Target router file: `{suggestion.get('target_router_file')}`")
            lines.append(f"- Suggested intent: `{suggestion.get('suggested_intent_name')}`")
            lines.append(f"- SQL template pattern: {suggestion.get('suggested_sql_template_pattern')}")
            lines.append(f"- Review required: `{suggestion.get('review_required')}`")
            lines.append(f"- Auto apply allowed: `{suggestion.get('auto_apply_allowed')}`")
            lines.append("")

            eval_test = suggestion.get("suggested_eval_test")
            if eval_test:
                lines.append("Suggested evaluation test:")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(eval_test, indent=2, ensure_ascii=False))
                lines.append("```")
                lines.append("")

    LOG_ANALYSIS_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    logs = read_jsonl(LOG_PATH)
    candidates = build_candidates(logs)
    qdrant_terms = build_qdrant_terms(candidates)

    write_markdown_report(logs, candidates)
    write_json(ROUTER_FIX_JSON, candidates)
    write_json(QDRANT_TERMS_JSON, qdrant_terms)

    print(f"Read logs: {len(logs)}")
    print(f"Problem candidates: {len(candidates)}")
    print(f"Wrote: {LOG_ANALYSIS_MD}")
    print(f"Wrote: {ROUTER_FIX_JSON}")
    print(f"Wrote: {QDRANT_TERMS_JSON}")


if __name__ == "__main__":
    main()
