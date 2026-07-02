from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SQLITE_DB = PROJECT_ROOT / "data" / "full_value_index.sqlite3"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
QDRANT_COLLECTION = os.getenv("QDRANT_VALUE_COLLECTION", "ajsmgpt_full_value_index_16m")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")


STOP_WORDS = {
    "show", "give", "details", "detail", "for", "from", "the", "last",
    "first", "latest", "purchase", "supply", "supplier", "party",
    "document", "cashbank", "material", "item", "of", "in", "on",
    "and", "or", "by", "date", "year", "qty", "quantity",
}


CONTEXT_HINTS = {
    "document": ["DOCUMENT", "DOCTYPE", "DOC"],
    "cashbank": ["CASHBANK", "CASH", "BANK"],
    "cash": ["CASHBANK", "CASH"],
    "bank": ["CASHBANK", "BANK"],
    "supplier": ["PARTYMASTER", "SUPPLIER", "PARTY"],
    "party": ["PARTYMASTER", "PARTY", "SUPPLIER"],
    "purchase": ["PURCHASEORDER", "INVITEMS", "PARTYMASTER"],
    "grn": ["GRN"],
    "mrs": ["MRS", "MRS_TEMP", "MRS_TEMP_DETAILS"],
    "issue": ["ISSUE"],
    "employee": ["EMP", "EMPCODE", "EMPLOYEE", "CURRENTATTENDANCE"],
    "empcode": ["EMP", "EMPCODE", "CURRENTATTENDANCE"],
    "attendance": ["CURRENTATTENDANCE", "ATTENDANCE"],
    "vehicle": ["VEHICLE", "TRN_VEHICLEMOVEMENT"],
    "camera": ["CAMERA", "CAMERAIP"],
    "material": ["ITEM", "MATERIAL", "INVITEMS", "ITEMSTOCK"],
    "item": ["ITEM", "MATERIAL", "INVITEMS", "ITEMSTOCK"],
}


ENTITY_BOOST = {
    "supplier_or_party": 20,
    "material": 18,
    "employee": 16,
    "department": 14,
    "vehicle": 14,
    "document": 14,
    "camera": 12,
    "unit": 10,
    "generic": 0,
}


def normalize_text(text: Any) -> str:
    text = str(text or "").upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def post_json(url: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_code_terms(question: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-/\.]*", question or "")
    out = []

    for token in tokens:
        clean = token.strip("._-/ ")
        upper = clean.upper()

        if not upper or upper.lower() in STOP_WORDS:
            continue

        has_digit = any(ch.isdigit() for ch in upper)
        has_alpha = any(ch.isalpha() for ch in upper)

        if has_digit and len(upper) >= 4:
            out.append(clean)
        elif has_alpha and has_digit and len(upper) >= 3:
            out.append(clean)

    seen = set()
    final = []
    for item in out:
        key = item.upper()
        if key not in seen:
            seen.add(key)
            final.append(item)

    return final


def extract_semantic_queries(question: str) -> list[str]:
    q = (question or "").strip()
    if not q:
        return []

    cleaned = q

    for code in extract_code_terms(q):
        cleaned = re.sub(re.escape(code), " ", cleaned, flags=re.IGNORECASE)

    words = []
    for word in re.findall(r"[A-Za-z0-9]+", cleaned):
        if word.lower() not in STOP_WORDS:
            words.append(word)

    queries = [q]

    phrase = " ".join(words).strip()
    if len(phrase) >= 3 and phrase.upper() != q.upper():
        queries.append(phrase)

    seen = set()
    final = []
    for item in queries:
        key = item.upper().strip()
        if key and key not in seen:
            seen.add(key)
            final.append(item)

    return final


def has_any_word(normalized_question: str, words: set[str]) -> bool:
    q_words = set(normalized_question.split())
    return bool(q_words.intersection(words))


def token_overlap_score(query: str, value: Any) -> float:
    """
    Boost results whose real DB value shares words with the user phrase.
    Example: barcode chromo label -> BARCODE CHROMO LABLES 40 X 25MM
    """
    query_tokens = set(normalize_text(query).split())
    value_tokens = set(normalize_text(value).split())

    if not query_tokens or not value_tokens:
        return 0.0

    overlap = query_tokens.intersection(value_tokens)
    return min(len(overlap) * 8.0, 40.0)


def context_score(question: str, schema: Any, table: Any, column: Any, entity_type: Any) -> float:
    q = normalize_text(question)
    location = normalize_text(f"{schema or ''} {table or ''} {column or ''}")
    etype = str(entity_type or "generic")

    score = 0.0

    for word, hints in CONTEXT_HINTS.items():
        if word.upper() in q:
            for hint in hints:
                if hint in location:
                    score += 25.0

    score += ENTITY_BOOST.get(etype, 0)

    purchase_material_words = {
        "PURCHASE", "SUPPLY", "MATERIAL", "ITEM", "QTY", "QUANTITY",
        "GRN", "MRS", "ISSUE", "STOCK"
    }

    camera_words = {"CAMERA", "IP", "CCTV"}

    document_words = {"DOCUMENT", "DOC", "VOUCHER", "CASHBANK", "CASH", "BANK"}

    # For purchase/material questions, prefer material/item locations.
    if has_any_word(q, purchase_material_words):
        if etype == "material":
            score += 45.0
        if "ITEM" in location or "INVITEMS" in location or "ITEMSTOCK" in location:
            score += 20.0
        if etype == "camera" and not has_any_word(q, camera_words):
            score -= 45.0
        if "RDCMEMO" in location and not has_any_word(q, camera_words):
            score -= 30.0
        if etype == "generic":
            score -= 15.0

    # For document/cashbank questions, prefer document/cashbank locations.
    if has_any_word(q, document_words):
        if "DOCUMENT" in location:
            score += 35.0
        if "CASHBANK" in location:
            score += 35.0

    return score


def make_result(
    source: str,
    input_text: str,
    resolved_value: Any,
    normalized_value: Any,
    entity_type: Any,
    schema_name: Any,
    table_name: Any,
    column_name: Any,
    value_count: Any,
    score: float,
    sqlite_id: Any = None,
    qdrant_score: Any = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "source": source,
        "input_text": input_text,
        "resolved_value": str(resolved_value),
        "normalized_value": normalized_value,
        "entity_type": entity_type,
        "schema_name": schema_name,
        "table_name": table_name,
        "column_name": column_name,
        "value_count": int(value_count or 1),
        "score": round(float(score), 4),
        "sqlite_id": int(sqlite_id) if sqlite_id is not None else None,
        "qdrant_score": qdrant_score,
        "reason": reason,
        "location": f"{schema_name}.{table_name}.{column_name}",
    }


def sqlite_exact_lookup(term: str, question: str, limit: int = 50) -> list[dict[str, Any]]:
    if not SQLITE_DB.exists():
        return []

    norm = normalize_text(term)

    sql = """
    SELECT
        id, schema_name, table_name, column_name, data_type,
        entity_type, original_value, normalized_value, value_count
    FROM value_index
    WHERE UPPER(CAST(original_value AS TEXT)) = UPPER(?)
       OR UPPER(CAST(normalized_value AS TEXT)) = UPPER(?)
    LIMIT ?
    """

    with sqlite3.connect(SQLITE_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, [term, norm, limit]).fetchall()

    results = []
    for r in rows:
        score = 100
        score += context_score(question, r["schema_name"], r["table_name"], r["column_name"], r["entity_type"])

        if str(r["original_value"]).upper() == term.upper():
            score += 20

        results.append(
            make_result(
                source="sqlite_exact",
                input_text=term,
                resolved_value=r["original_value"],
                normalized_value=r["normalized_value"],
                entity_type=r["entity_type"],
                schema_name=r["schema_name"],
                table_name=r["table_name"],
                column_name=r["column_name"],
                value_count=r["value_count"],
                score=score,
                sqlite_id=r["id"],
                reason="exact_code_or_value_match",
            )
        )

    return sorted(results, key=lambda x: x["score"], reverse=True)


def sqlite_fuzzy_lookup(phrase: str, question: str, limit: int = 20) -> list[dict[str, Any]]:
    if not SQLITE_DB.exists():
        return []

    norm = normalize_text(phrase)
    if len(norm) < 3:
        return []

    sql = """
    SELECT
        id, schema_name, table_name, column_name, data_type,
        entity_type, original_value, normalized_value, value_count
    FROM value_index
    WHERE UPPER(CAST(normalized_value AS TEXT)) LIKE ?
    ORDER BY
        CASE entity_type
            WHEN 'supplier_or_party' THEN 1
            WHEN 'material' THEN 2
            WHEN 'employee' THEN 3
            WHEN 'department' THEN 4
            WHEN 'vehicle' THEN 5
            WHEN 'document' THEN 6
            WHEN 'camera' THEN 7
            ELSE 99
        END,
        value_count DESC
    LIMIT ?
    """

    with sqlite3.connect(SQLITE_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, [f"%{norm}%", limit]).fetchall()

    results = []
    for r in rows:
        score = 70
        score += context_score(question, r["schema_name"], r["table_name"], r["column_name"], r["entity_type"])

        results.append(
            make_result(
                source="sqlite_fuzzy",
                input_text=phrase,
                resolved_value=r["original_value"],
                normalized_value=r["normalized_value"],
                entity_type=r["entity_type"],
                schema_name=r["schema_name"],
                table_name=r["table_name"],
                column_name=r["column_name"],
                value_count=r["value_count"],
                score=score,
                sqlite_id=r["id"],
                reason="sqlite_like_match",
            )
        )

    return sorted(results, key=lambda x: x["score"], reverse=True)


def embed_query(text: str) -> list[float]:
    data = post_json(
        f"{OLLAMA_URL}/api/embeddings",
        {"model": EMBED_MODEL, "prompt": text},
        timeout=120,
    )

    vector = data.get("embedding")
    if not isinstance(vector, list):
        raise RuntimeError(f"No embedding returned: {data}")

    return vector


def qdrant_search(query: str, question: str, limit: int = 10) -> list[dict[str, Any]]:
    vector = embed_query(query)

    try:
        data = post_json(
            f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search",
            {"vector": vector, "limit": limit, "with_payload": True},
            timeout=120,
        )
        hits = data.get("result", [])
    except Exception:
        data = post_json(
            f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/query",
            {"query": vector, "limit": limit, "with_payload": True},
            timeout=120,
        )
        result = data.get("result", {})
        hits = result.get("points", []) if isinstance(result, dict) else result

    results = []
    for hit in hits:
        payload = hit.get("payload") or {}
        qscore = float(hit.get("score") or 0)

        score = qscore * 100
        score += context_score(
            question,
            payload.get("schema_name"),
            payload.get("table_name"),
            payload.get("column_name"),
            payload.get("entity_type"),
        )
        score += token_overlap_score(query, payload.get("original_value"))

        results.append(
            make_result(
                source="qdrant_semantic",
                input_text=query,
                resolved_value=payload.get("original_value"),
                normalized_value=payload.get("normalized_value"),
                entity_type=payload.get("entity_type"),
                schema_name=payload.get("schema_name"),
                table_name=payload.get("table_name"),
                column_name=payload.get("column_name"),
                value_count=payload.get("value_count"),
                score=score,
                sqlite_id=payload.get("sqlite_id"),
                qdrant_score=qscore,
                reason="semantic_vector_match",
            )
        )

    return sorted(results, key=lambda x: x["score"], reverse=True)


def resolve_question_values(question: str, final_limit: int = 20) -> dict[str, Any]:
    started = time.perf_counter()

    exact_terms = extract_code_terms(question)
    semantic_queries = extract_semantic_queries(question)

    candidates = []

    for term in exact_terms:
        candidates.extend(sqlite_exact_lookup(term, question, limit=50))

    for phrase in semantic_queries[1:]:
        candidates.extend(sqlite_fuzzy_lookup(phrase, question, limit=20))

    for query in semantic_queries:
        # If query contains an exact code like ODUT001/165224/800967,
        # do not let semantic search replace it with similar wrong codes.
        if exact_terms and any(code.upper() in query.upper() for code in exact_terms):
            continue

        try:
            candidates.extend(qdrant_search(query, question, limit=10))
        except Exception as exc:
            candidates.append(
                make_result(
                    source="qdrant_error",
                    input_text=query,
                    resolved_value="",
                    normalized_value=None,
                    entity_type=None,
                    schema_name=None,
                    table_name=None,
                    column_name=None,
                    value_count=0,
                    score=-1,
                    reason=str(exc),
                )
            )

    best = {}

    for item in candidates:
        if not item.get("resolved_value"):
            continue

        key = (
            str(item["resolved_value"]).upper(),
            item.get("schema_name"),
            item.get("table_name"),
            item.get("column_name"),
        )

        if key not in best or item["score"] > best[key]["score"]:
            best[key] = item

    results = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:final_limit]

    return {
        "question": question,
        "exact_terms": exact_terms,
        "semantic_queries": semantic_queries,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    result = resolve_question_values(args.question, final_limit=args.limit)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
