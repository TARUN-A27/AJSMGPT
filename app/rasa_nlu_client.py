from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import requests


RASA_PARSE_URL = os.getenv(
    "AJSMGPT_RASA_PARSE_URL",
    "http://127.0.0.1:5005/model/parse",
)

DUCKLING_PARSE_URL = os.getenv(
    "AJSMGPT_DUCKLING_PARSE_URL",
    "http://127.0.0.1:8001/parse",
)

YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
CODE_ENTITY_NAMES = {"supplier_code", "party_code", "empcode", "order_no", "mrs_no"}


@dataclass
class NLPUnderstandingResult:
    success: bool
    question: str
    intent: Optional[str]
    confidence: float
    entities: Dict[str, Any]
    rasa_entities: List[Dict[str, Any]]
    duckling_entities: List[Dict[str, Any]]
    source: str = "rasa_nlu_duckling"
    error: Optional[str] = None
    raw_rasa: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _put_entity(entities: Dict[str, Any], key: str, value: Any) -> None:
    if value is None or value == "":
        return

    if key not in entities:
        entities[key] = value
        return

    existing = entities[key]

    if existing == value:
        return

    if isinstance(existing, list):
        if value not in existing:
            existing.append(value)
        return

    entities[key] = [existing, value]


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def _protected_code_ranges(rasa_entities: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []

    for ent in rasa_entities:
        if ent.get("entity") not in CODE_ENTITY_NAMES:
            continue

        start = ent.get("start")
        end = ent.get("end")

        if isinstance(start, int) and isinstance(end, int):
            ranges.append((start, end))

    return ranges


def _parse_rasa(question: str, timeout_seconds: float = 2.5) -> Dict[str, Any]:
    response = requests.post(
        RASA_PARSE_URL,
        json={"text": question},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def _parse_duckling(question: str, timeout_seconds: float = 2.5) -> List[Dict[str, Any]]:
    response = requests.post(
        DUCKLING_PARSE_URL,
        data={
            "locale": "en_GB",
            "tz": "Asia/Kolkata",
            "text": question,
            "dims": json.dumps(["time", "number", "amount-of-money", "duration"]),
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def _merge_rasa_entities(rasa_entities: List[Dict[str, Any]]) -> Dict[str, Any]:
    entities: Dict[str, Any] = {}

    for ent in rasa_entities:
        name = ent.get("entity")
        value = ent.get("value")

        if not name or value is None:
            continue

        value_str = str(value).strip()

        # Rasa may confuse a year like 2025 as supplier_code.
        if name in CODE_ENTITY_NAMES and YEAR_RE.match(value_str):
            _put_entity(entities, "year", value_str)
            continue

        _put_entity(entities, name, value)

    return entities


def _merge_duckling_entities(
    entities: Dict[str, Any],
    duckling_entities: List[Dict[str, Any]],
    protected_ranges: List[Tuple[int, int]],
) -> None:
    for ent in duckling_entities:
        dim = ent.get("dim")
        body = str(ent.get("body") or "").strip()
        value_obj = ent.get("value") or {}
        start = ent.get("start")
        end = ent.get("end")

        overlaps_known_code = False
        if isinstance(start, int) and isinstance(end, int):
            overlaps_known_code = any(
                _ranges_overlap(start, end, p_start, p_end)
                for p_start, p_end in protected_ranges
            )

        if dim == "number":
            # Do not turn supplier_code/party_code/empcode pieces into generic number.
            # Example: 800967, ODUT001->001, 165224.
            if overlaps_known_code:
                continue

            # Do not store years as generic number.
            if YEAR_RE.match(body):
                continue

            value = value_obj.get("value")
            if value is not None:
                _put_entity(entities, "number", value)

        elif dim == "time":
            grain = value_obj.get("grain")
            value = value_obj.get("value")

            if grain == "year" and body:
                year_match = re.search(r"(?:19|20)\d{2}", body)
                if year_match:
                    _put_entity(entities, "year", year_match.group(0))

            if value:
                _put_entity(
                    entities,
                    "time",
                    {
                        "body": body,
                        "value": value,
                        "grain": grain,
                    },
                )

        elif dim == "amount-of-money":
            _put_entity(entities, "amount", value_obj)

        elif dim == "duration":
            _put_entity(entities, "duration", value_obj)


def _dedupe_and_flatten(value: Any) -> Any:
    if not isinstance(value, list):
        return value

    deduped: List[Any] = []
    for item in value:
        if item not in deduped:
            deduped.append(item)

    if len(deduped) == 1:
        return deduped[0]

    return deduped


def _final_cleanup(entities: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}

    for key, value in entities.items():
        value = _dedupe_and_flatten(value)

        if key in {"supplier_code", "empcode"}:
            values = value if isinstance(value, list) else [value]
            values = [v for v in values if not YEAR_RE.match(str(v).strip())]

            if not values:
                continue

            value = values[0] if len(values) == 1 else values

        cleaned[key] = value

    return cleaned


def understand_with_rasa_duckling(
    question: str,
    *,
    min_confidence: float = 0.45,
    include_raw: bool = False,
) -> NLPUnderstandingResult:
    question = (question or "").strip()

    if not question:
        return NLPUnderstandingResult(
            success=False,
            question=question,
            intent=None,
            confidence=0.0,
            entities={},
            rasa_entities=[],
            duckling_entities=[],
            error="empty_question",
        )

    try:
        rasa_data = _parse_rasa(question)
        duckling_data = _parse_duckling(question)

        intent_obj = rasa_data.get("intent") or {}
        intent_name = intent_obj.get("name")
        confidence = float(intent_obj.get("confidence") or 0.0)

        rasa_entities = rasa_data.get("entities") or []
        protected_ranges = _protected_code_ranges(rasa_entities)

        entities = _merge_rasa_entities(rasa_entities)
        _merge_duckling_entities(entities, duckling_data, protected_ranges)
        entities = _final_cleanup(entities)

        return NLPUnderstandingResult(
            success=bool(intent_name) and confidence >= min_confidence,
            question=question,
            intent=intent_name,
            confidence=confidence,
            entities=entities,
            rasa_entities=rasa_entities,
            duckling_entities=duckling_data,
            raw_rasa=rasa_data if include_raw else None,
        )

    except Exception as exc:
        return NLPUnderstandingResult(
            success=False,
            question=question,
            intent=None,
            confidence=0.0,
            entities={},
            rasa_entities=[],
            duckling_entities=[],
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> None:
    import sys

    question = " ".join(sys.argv[1:]).strip()
    if not question:
        print("Usage: python -m app.rasa_nlu_client 'latest GRN for supplier 800967 in 2025'")
        raise SystemExit(2)

    result = understand_with_rasa_duckling(question)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
