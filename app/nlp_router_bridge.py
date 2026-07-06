from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from app.rasa_nlu_client import understand_with_rasa_duckling


INTENT_TO_MODULE = {
    "purchase_last_supplier_by_material": "purchase",
    "purchase_last_supply_by_supplier": "purchase",
    "last_purchase_date_by_item_name": "purchase",
    "purchase_last_n_purchases_by_material": "purchase",
    "grn_by_supplier_code": "purchase",
    "grn_received_by_item": "purchase",

    "pending_mrs_by_item_name": "inventory_mrs",
    "approved_mrs_by_item_name": "inventory_mrs",
    "rejected_mrs_by_item_name": "inventory_mrs",
    "mrs_by_department": "inventory_mrs",
    "mrs_by_unit": "inventory_mrs",

    "stock_by_item_name": "inventory",
    "issue_by_item_name": "inventory",

    "camera_details": "admin_camera",
    "cashbank_voucher_details": "admin_cashbank",
    "document_details": "admin_document",

    "hrd_employee_or_attendance": "hrd",
    "employee_by_empcode": "hrd",
}


REQUIRED_ENTITIES = {
    "purchase_last_supplier_by_material": ["item_name"],
    "purchase_last_supply_by_supplier": ["supplier_name"],
    "last_purchase_date_by_item_name": ["item_name"],
    "purchase_last_n_purchases_by_material": ["item_name"],
    "grn_by_supplier_code": ["supplier_code"],
    "grn_received_by_item": ["item_name"],

    "pending_mrs_by_item_name": ["item_name"],
    "approved_mrs_by_item_name": ["item_name"],
    "rejected_mrs_by_item_name": ["item_name"],
    "mrs_by_department": ["dept_name"],
    "mrs_by_unit": ["unit_name"],

    "stock_by_item_name": ["item_name"],
    "issue_by_item_name": ["item_name"],

    "camera_details": [],
    "cashbank_voucher_details": ["party_code"],
    "document_details": ["party_code"],

    "hrd_employee_or_attendance": ["empcode"],
    "employee_by_empcode": ["empcode"],
}


@dataclass
class NLPRouterCandidate:
    success: bool
    question: str
    intent: Optional[str]
    confidence: float
    module: Optional[str]
    router_candidate: Optional[str]
    entities: Dict[str, Any]
    required_entities: List[str]
    missing_entities: List[str]
    required_entities_ok: bool
    safe_to_apply: bool
    source: str = "nlp_router_bridge"
    reason: Optional[str] = None
    nlp_observation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _has_entity(entities: Dict[str, Any], key: str) -> bool:
    value = entities.get(key)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, list) and not value:
        return False
    return True


def build_nlp_router_candidate(
    question: str,
    *,
    min_confidence: float = 0.80,
    allow_apply: bool = False,
) -> NLPRouterCandidate:
    question = (question or "").strip()
    nlp = understand_with_rasa_duckling(question)

    nlp_dict = {
        "source": nlp.source,
        "success": nlp.success,
        "intent": nlp.intent,
        "confidence": nlp.confidence,
        "entities": nlp.entities,
        "error": nlp.error,
    }

    if not nlp.success:
        return NLPRouterCandidate(
            success=False,
            question=question,
            intent=nlp.intent,
            confidence=nlp.confidence,
            module=None,
            router_candidate=None,
            entities=nlp.entities,
            required_entities=[],
            missing_entities=[],
            required_entities_ok=False,
            safe_to_apply=False,
            reason=nlp.error or "nlp_failed",
            nlp_observation=nlp_dict,
        )

    if nlp.confidence < min_confidence:
        return NLPRouterCandidate(
            success=False,
            question=question,
            intent=nlp.intent,
            confidence=nlp.confidence,
            module=None,
            router_candidate=None,
            entities=nlp.entities,
            required_entities=[],
            missing_entities=[],
            required_entities_ok=False,
            safe_to_apply=False,
            reason=f"confidence_below_threshold:{nlp.confidence:.4f}<{min_confidence}",
            nlp_observation=nlp_dict,
        )

    intent = nlp.intent
    module = INTENT_TO_MODULE.get(intent or "")
    required = REQUIRED_ENTITIES.get(intent or "", [])
    missing = [key for key in required if not _has_entity(nlp.entities, key)]
    required_ok = not missing

    if not module:
        reason = "unknown_intent_mapping"
    elif missing:
        reason = "missing_required_entities"
    else:
        reason = "ready_for_review"

    # Keep false by default. Later we turn on per-intent only after tests.
    safe_to_apply = bool(allow_apply and module and required_ok)

    return NLPRouterCandidate(
        success=bool(module and required_ok),
        question=question,
        intent=intent,
        confidence=nlp.confidence,
        module=module,
        router_candidate=intent,
        entities=nlp.entities,
        required_entities=required,
        missing_entities=missing,
        required_entities_ok=required_ok,
        safe_to_apply=safe_to_apply,
        reason=reason,
        nlp_observation=nlp_dict,
    )


def main() -> None:
    import json
    import sys

    question = " ".join(sys.argv[1:]).strip()
    if not question:
        print("Usage: python -m app.nlp_router_bridge 'last supplier for mouse'")
        raise SystemExit(2)

    result = build_nlp_router_candidate(question)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
