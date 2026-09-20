"""Machine-readable allowlist for the deliberately narrow V1 execution API."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.query_plan import QueryPlan


_CAPABILITY_PATH = Path(__file__).resolve().parent / "resources" / "v1_query_capabilities.json"


class CapabilityFamily(BaseModel):
    name: str
    status: str
    domains: list[str]
    operations: list[str]
    subject_aliases: list[str] = Field(default_factory=list)
    verified_concepts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CapabilityCatalog(BaseModel):
    version: str
    policy: str
    families: list[CapabilityFamily]


class CapabilityDecision(BaseModel):
    family: str
    supported: bool
    reject_reasons: list[str] = Field(default_factory=list)


def _normalise(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


@lru_cache(maxsize=1)
def load_capability_catalog() -> CapabilityCatalog:
    return CapabilityCatalog.model_validate_json(_CAPABILITY_PATH.read_text(encoding="utf-8"))


def _subject(plan: QueryPlan) -> str:
    return _normalise(plan.business_subject.concept) if plan.business_subject else ""


def _family_name(plan: QueryPlan) -> str | None:
    domain = _normalise(plan.domain)
    operation = _normalise(plan.operation)
    subject = _subject(plan)
    if operation == "lookup" and subject in {"supplier", "vendor", "party"}:
        return "supplier_lookup"
    if operation == "lookup" and subject in {"material", "item"}:
        return "material_lookup"
    if domain in {"purchase", "purchasing", "purchase order", "po"}:
        return "purchase_orders"
    if domain in {"supplier lookup"}:
        return "supplier_lookup"
    if domain in {"material lookup"}:
        return "material_lookup"
    if domain in {"stock", "inventory"}:
        return "stock"
    if domain in {"grn", "goods receipt", "goods receipt note"}:
        return "grn"
    if domain in {"mrs", "material requisition", "material request"}:
        return "mrs"
    if domain in {"consumption", "issue", "issued"}:
        return "consumption"
    return None


def evaluate_capability(plan: QueryPlan) -> CapabilityDecision:
    """Reject any family or operation not explicitly allowed by the V1 catalog."""
    family_name = _family_name(plan)
    if family_name is None:
        return CapabilityDecision(
            family="unknown",
            supported=False,
            reject_reasons=["The question is outside the verified V1 capability catalog."],
        )

    family = next(item for item in load_capability_catalog().families if item.name == family_name)
    reasons: list[str] = []
    if family.status != "supported":
        reasons.extend(family.limitations or ["This business family is not supported in V1."])
    if _normalise(plan.operation) not in {_normalise(item) for item in family.operations}:
        reasons.append(
            f"Operation '{plan.operation}' is not supported for the V1 {family.name} family."
        )
    return CapabilityDecision(
        family=family.name,
        supported=not reasons,
        reject_reasons=reasons,
    )


__all__ = [
    "CapabilityCatalog",
    "CapabilityDecision",
    "CapabilityFamily",
    "evaluate_capability",
    "load_capability_catalog",
]
