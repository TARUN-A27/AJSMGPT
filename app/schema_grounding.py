"""Deterministic, offline grounding from a logical QueryPlan to verified V1 metadata."""

from __future__ import annotations

import json
from collections import deque
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.query_plan import QueryPlan


_APP_DIR = Path(__file__).resolve().parent
_CATALOG_PATH = _APP_DIR / "resources" / "business_schema_catalog.json"
_METADATA_PATH = _APP_DIR.parent / "data" / "multi_schema_metadata.json"
_RELATIONSHIPS_PATH = _APP_DIR.parent / "data" / "schema_relationships.json"


class GroundedTable(BaseModel):
    full_table_name: str
    role: str
    confidence: float = Field(ge=0, le=1)


class GroundedColumn(BaseModel):
    full_table_name: str
    column_name: str
    logical_concept: str
    role: str
    confidence: float = Field(ge=0, le=1)


class RelationshipPath(BaseModel):
    constraint_names: list[str]
    tables: list[str]
    confidence: float = Field(ge=0, le=1)


class GroundingEvidence(BaseModel):
    requirement: str
    logical_concept: str
    source: str
    confidence: float = Field(ge=0, le=1)


class GroundingAmbiguity(BaseModel):
    requirement: str
    reason: str
    candidates: list[str] = Field(default_factory=list)
    blocking: bool = True


class GroundingRejection(BaseModel):
    requirement: str
    reason: str


class GroundedSchemaPlan(BaseModel):
    source_query_plan: dict
    candidate_schemas: list[str]
    selected_tables: list[GroundedTable] = Field(default_factory=list)
    selected_columns: list[GroundedColumn] = Field(default_factory=list)
    allowed_relationship_paths: list[RelationshipPath] = Field(default_factory=list)
    entity_column_candidates: dict[str, list[str]] = Field(default_factory=dict)
    evidence: list[GroundingEvidence] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    ambiguities: list[GroundingAmbiguity] = Field(default_factory=list)
    reject_reasons: list[GroundingRejection] = Field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        return not self.reject_reasons and not any(item.blocking for item in self.ambiguities)


def _normalise(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


@lru_cache(maxsize=1)
def _load_inputs() -> tuple[dict, dict[str, set[str]], list[dict]]:
    """Load source-controlled inputs only, relative to this application module."""
    catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    metadata = json.loads(_METADATA_PATH.read_text(encoding="utf-8"))
    relationships = json.loads(_RELATIONSHIPS_PATH.read_text(encoding="utf-8"))
    columns = {
        item["full_table_name"]: {column["column_name"] for column in item["columns"]}
        for item in metadata
    }
    return catalog, columns, relationships


def _catalog_is_verified(catalog: dict, metadata_columns: dict[str, set[str]], metadata_edges: list[dict]) -> None:
    actual_edges = {
        (edge["from_schema"] + "." + edge["from_table"], edge["from_column"],
         edge["to_schema"] + "." + edge["to_table"], edge["to_column"], edge["constraint_name"])
        for edge in metadata_edges
    }
    for concept in catalog["concepts"]:
        for column in concept["columns"]:
            if column["table"] not in metadata_columns or column["column"] not in metadata_columns[column["table"]]:
                raise RuntimeError("Business schema catalog contains an unverified column.")
    for edge in catalog["relationships"]:
        record = (edge["from_table"], edge["from_column"], edge["to_table"], edge["to_column"], edge["constraint_name"])
        if record not in actual_edges:
            raise RuntimeError("Business schema catalog contains an unverified relationship.")


def _requirements(plan: QueryPlan) -> list[tuple[str, str, str]]:
    requirements: list[tuple[str, str, str]] = []
    if plan.business_subject:
        requirements.append(("business_subject", plan.business_subject.concept, "identifier"))
    for measure in plan.measures:
        requirements.append(("measure", measure.concept, "measure"))
    for dimension in plan.dimensions:
        requirements.append(("dimension", dimension.concept, "grouping"))
    for entity in plan.entities:
        requirements.append(("entity", entity.concept, "entity_filter"))
    for query_filter in plan.filters:
        requirements.append(("filter", query_filter.concept, "entity_filter"))
    if plan.date_range and plan.date_range.kind.value != "unspecified":
        requirements.append(("date_range", "date", "date_filter"))
    return requirements


def _matching_columns(catalog: dict, phrase: str, role: str) -> list[tuple[dict, dict]]:
    target = _normalise(phrase)
    matches = []
    for concept in catalog["concepts"]:
        aliases = {_normalise(concept["name"]), *(_normalise(alias) for alias in concept["aliases"])}
        if target not in aliases:
            continue
        for column in concept["columns"]:
            if role in column["roles"]:
                matches.append((concept, column))
    return matches


def _domain(catalog: dict, plan: QueryPlan) -> dict | None:
    if plan.operation.lower() == "lookup" and plan.business_subject:
        subject = _normalise(plan.business_subject.concept)
        if subject in {"supplier", "vendor", "party"}:
            return next(domain for domain in catalog["domains"] if domain["name"] == "supplier_lookup")
    target = _normalise(plan.domain)
    for domain in catalog["domains"]:
        if target == _normalise(domain["name"]) or target in {_normalise(alias) for alias in domain["aliases"]}:
            return domain
    return None


def _shortest_path(start: str, end: str, edges: list[dict]) -> list[dict] | None:
    if start == end:
        return []
    adjacency: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from_table"], []).append((edge["to_table"], edge))
        adjacency.setdefault(edge["to_table"], []).append((edge["from_table"], edge))
    queue: deque[tuple[str, list[dict]]] = deque([(start, [])])
    seen = {start}
    while queue:
        table, path = queue.popleft()
        for neighbour, edge in adjacency.get(table, []):
            if neighbour in seen:
                continue
            next_path = path + [edge]
            if neighbour == end:
                return next_path
            seen.add(neighbour)
            queue.append((neighbour, next_path))
    return None


def _source_summary(plan: QueryPlan) -> dict:
    return {
        "domain": plan.domain,
        "operation": plan.operation,
        "business_subject": plan.business_subject.concept if plan.business_subject else None,
        "measures": [item.concept for item in plan.measures],
        "dimensions": [item.concept for item in plan.dimensions],
        "entities": [item.concept for item in plan.entities],
        "has_date_range": bool(plan.date_range and plan.date_range.kind.value != "unspecified"),
    }


def ground_query_plan(query_plan: QueryPlan) -> GroundedSchemaPlan:
    """Ground only V1 catalog concepts; never generate SQL or access external services."""
    catalog, metadata_columns, metadata_edges = _load_inputs()
    _catalog_is_verified(catalog, metadata_columns, metadata_edges)
    domain = _domain(catalog, query_plan)
    schemas = sorted({table.split(".", 1)[0] for item in catalog["domains"] for table in item["primary_tables"]} | {column["table"].split(".", 1)[0] for concept in catalog["concepts"] for column in concept["columns"]})
    result = GroundedSchemaPlan(source_query_plan=_source_summary(query_plan), candidate_schemas=schemas, confidence=0.0)
    if domain is None:
        result.reject_reasons.append(GroundingRejection(requirement="domain", reason="Domain is outside the V1 business schema catalog."))
        return result

    anchors = list(domain["primary_tables"])
    selected_tables = set(anchors)
    selected_columns: list[GroundedColumn] = []
    paths: list[RelationshipPath] = []
    confidences = [domain["confidence"]]

    for requirement_type, phrase, role in _requirements(query_plan):
        candidates = _matching_columns(catalog, phrase, role)
        if not candidates:
            domain_aliases = {_normalise(domain["name"]), *(_normalise(alias) for alias in domain["aliases"])}
            if requirement_type == "business_subject" and _normalise(phrase) in domain_aliases:
                continue
            result.reject_reasons.append(GroundingRejection(requirement=f"{requirement_type}:{phrase}", reason="No verified V1 column supports this required concept."))
            continue

        candidate_names = [f"{column['table']}.{column['column']}" for _, column in candidates]
        if requirement_type == "entity":
            result.entity_column_candidates[phrase] = sorted(candidate_names)

        def score(candidate: tuple[dict, dict]) -> tuple[int, float, str]:
            _, column = candidate
            return (2 if column["table"] in selected_tables else 1 if column["table"] in anchors else 0, column["confidence"], column["table"] + "." + column["column"])

        candidates.sort(key=score, reverse=True)
        best_score = score(candidates[0])[:2]
        tied = [candidate for candidate in candidates if score(candidate)[:2] == best_score]
        if len(tied) > 1:
            result.ambiguities.append(GroundingAmbiguity(requirement=f"{requirement_type}:{phrase}", reason="Multiple equally supported V1 columns match this concept.", candidates=sorted(f"{column['table']}.{column['column']}" for _, column in tied)))
            continue

        concept, column = candidates[0]
        target_table = column["table"]
        path = min((_shortest_path(anchor, target_table, catalog["relationships"]) for anchor in anchors), key=lambda item: len(item) if item is not None else 10_000)
        if path is None:
            result.reject_reasons.append(GroundingRejection(requirement=f"{requirement_type}:{phrase}", reason="Required table has no verified relationship path from the V1 domain anchor."))
            continue
        selected_tables.add(target_table)
        for edge in path:
            selected_tables.update((edge["from_table"], edge["to_table"]))
        if path:
            path_model = RelationshipPath(constraint_names=[edge["constraint_name"] for edge in path], tables=[anchors[0]] + [edge["to_table"] if edge["from_table"] == anchors[0] else edge["from_table"] for edge in path], confidence=min(edge["confidence"] for edge in path))
            if path_model not in paths:
                paths.append(path_model)
        selected_columns.append(GroundedColumn(full_table_name=target_table, column_name=column["column"], logical_concept=concept["name"], role=role, confidence=column["confidence"]))
        result.evidence.append(GroundingEvidence(requirement=f"{requirement_type}:{phrase}", logical_concept=concept["name"], source=column["evidence"], confidence=column["confidence"]))
        confidences.append(column["confidence"])

    # Keep exactly one record per physical column and order outputs deterministically.
    unique_columns = {(item.full_table_name, item.column_name, item.role): item for item in selected_columns}
    result.selected_columns = sorted(unique_columns.values(), key=lambda item: (item.full_table_name, item.column_name, item.role))
    result.allowed_relationship_paths = sorted(paths, key=lambda item: (item.constraint_names, item.tables))
    table_roles = {table: "anchor" if table in anchors else "related" for table in selected_tables}
    result.selected_tables = [GroundedTable(full_table_name=table, role=table_roles[table], confidence=domain["confidence"] if table in anchors else 1.0) for table in sorted(selected_tables)]
    if result.reject_reasons or any(item.blocking for item in result.ambiguities):
        result.confidence = 0.0
    else:
        result.confidence = round(min(confidences), 2)
    return result
