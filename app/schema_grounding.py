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


class GroundedCompoundCondition(BaseModel):
    """A bounded, catalog-declared boolean combination of verified columns.

    This exists only for concepts whose business meaning requires combining
    two or more physical flag columns (e.g. an MRS "rejected" status is
    REJECTIONSTATUS=1 OR STORESREJECTIONSTATUS=1). The combinator and the
    exact columns come from the catalog, never from the model: Qwen is told
    which columns/operator are required, but the boolean structure itself is
    fixed here and re-checked verbatim by the SQL validator.

    `pinned_values` optionally fixes the exact required value for a column
    (full "TABLE.COLUMN" -> int), for concepts where different fixed values
    of the *same* columns mean different things (e.g. a multi-stage approval
    ladder: SOFLAG=1 AND IAFLAG=0 is a different status than SOFLAG=1 AND
    IAFLAG=1). A column absent from this mapping is checked only for
    presence, exactly as before -- existing catalog entries that never
    declare a value keep their current behaviour unchanged.
    """

    logical_concept: str
    combinator: str
    columns: list[str]
    pinned_values: dict[str, int] = Field(default_factory=dict)
    # full "TABLE.COLUMN" -> required value, meaning "col = value OR col IS
    # NULL" -- for a legacy nullable flag column where NULL and the default
    # value mean the same business thing (e.g. MILLCODE=0 OR MILLCODE IS
    # NULL). Kept apart from pinned_values because it needs a different SQL
    # shape (a parenthesized OR, not a bare equality) checked as its own
    # required fragment.
    value_or_null: dict[str, int] = Field(default_factory=dict)


class AntiJoinCondition(BaseModel):
    """A catalog-declared "no matching row in a second table" requirement.

    Distinct from `allowed_relationship_paths`: every path there traces to a
    real database foreign key in data/schema_relationships.json. This join
    does not have one -- it is verified from the ERP's own business logic
    (a cited production query), not a DDL constraint -- so it is kept in its
    own field rather than weakening that invariant.

    The SQL must LEFT JOIN `to_table` on `from_columns` = `to_columns`
    (composite equality, same order, both tables) and then require
    `NVL(to_table.null_check_column, 0) = 0` in WHERE -- the exact shape of
    the verified query, not a logically-equivalent rewrite such as IS NULL.
    """

    logical_concept: str
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    null_check_column: str


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
    compound_conditions: list[GroundedCompoundCondition] = Field(default_factory=list)
    anti_join_conditions: list[AntiJoinCondition] = Field(default_factory=list)

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
        for column in concept.get("columns", []):
            if column["table"] not in metadata_columns or column["column"] not in metadata_columns[column["table"]]:
                raise RuntimeError("Business schema catalog contains an unverified column.")
        compound = concept.get("compound_condition")
        if compound:
            for column in compound["columns"] + compound.get("value_or_null_columns", []):
                if column["table"] not in metadata_columns or column["column"] not in metadata_columns[column["table"]]:
                    raise RuntimeError("Business schema catalog contains an unverified compound-condition column.")
        anti_join = concept.get("anti_join_condition")
        if anti_join:
            if len(anti_join["from_columns"]) != len(anti_join["to_columns"]):
                raise RuntimeError(
                    "Business schema catalog anti-join condition has mismatched composite-key lengths."
                )
            checks = (
                [(anti_join["from_table"], column) for column in anti_join["from_columns"]]
                + [(anti_join["to_table"], column) for column in anti_join["to_columns"]]
                + [(anti_join["to_table"], anti_join["null_check_column"])]
            )
            for table, column in checks:
                if table not in metadata_columns or column not in metadata_columns[table]:
                    raise RuntimeError("Business schema catalog contains an unverified anti-join column.")
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
        if plan.date_range and query_filter.concept.lower() in {"date", "time", "period"}:
            continue
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
        for column in concept.get("columns", []):
            if role in column["roles"]:
                matches.append((concept, column))
    return matches


def _matching_compound_concept(catalog: dict, phrase: str, role: str) -> dict | None:
    """Bounded lookup for a compound-condition concept (never a free-form match).

    Compound conditions only ever satisfy an entity/filter requirement (role
    "entity_filter"): they represent "is this record in state X", not a
    measure, grouping dimension, or generic date filter.
    """
    if role != "entity_filter":
        return None
    target = _normalise(phrase)
    for concept in catalog["concepts"]:
        compound = concept.get("compound_condition")
        if not compound:
            continue
        aliases = {_normalise(concept["name"]), *(_normalise(alias) for alias in concept["aliases"])}
        if target in aliases:
            return concept
    return None


def _matching_anti_join_concept(catalog: dict, phrase: str, role: str) -> dict | None:
    """Bounded lookup for a concept requiring a "no matching row" anti-join.

    Same shape and rationale as `_matching_compound_concept`: only ever
    satisfies an entity/filter requirement, never a free-form match. A
    concept may have both a `compound_condition` and an `anti_join_condition`
    (e.g. mrs_pending is a flag-AND plus an anti-join) -- the two lookups are
    independent so `ground_query_plan` can apply whichever pieces a concept
    declares.
    """
    if role != "entity_filter":
        return None
    target = _normalise(phrase)
    for concept in catalog["concepts"]:
        if not concept.get("anti_join_condition"):
            continue
        aliases = {_normalise(concept["name"]), *(_normalise(alias) for alias in concept["aliases"])}
        if target in aliases:
            return concept
    return None


def correct_mislabeled_entity_concepts(plan: QueryPlan) -> QueryPlan:
    """Repair a confirmed, repeatable extraction mistake before anything else
    sees the plan: the model sometimes writes the raw spoken value itself as
    entities[].concept (e.g. concept="Keyboard") instead of the fixed
    vocabulary token "material". Three separate prompt-side fix attempts
    (two worked examples, one declarative sentence) each regressed a
    different real, previously-passing question -- fix.md #25, #26 -- so
    this is fixed deterministically instead, matching the architecture's own
    "Qwen proposes, deterministic code verifies" principle.

    Only rewrites an entity whose concept matches neither a real catalog
    column concept nor a compound-condition concept. A genuine status or
    workflow entity (concept="pending", "pending at store officer", ...)
    always matches the compound-condition lookup on its own literal phrase
    -- that is exactly how it already grounds today -- so it is never
    touched. Must run before both entity resolution and schema grounding see
    the plan: resolution only forces real verification for a concept in
    `resolvable_concepts()` (fix.md #16), and grounding only recognises the
    same fixed vocabulary, so rewriting the concept once, here, is the only
    change that fixes both. A wrong guess still fails closed -- real entity
    resolution rejects any value with no genuine match (fix.md #2) -- so the
    worst case is a refusal, never a wrong answer.
    """
    if not plan.entities:
        return plan
    catalog, _, _ = _load_inputs()
    corrected = []
    changed = False
    for entity in plan.entities:
        if _matching_columns(catalog, entity.concept, "entity_filter") or _matching_compound_concept(
            catalog, entity.concept, "entity_filter"
        ):
            corrected.append(entity)
            continue
        corrected.append(entity.model_copy(update={"concept": "material"}))
        changed = True
    return plan.model_copy(update={"entities": corrected}) if changed else plan


# Kept in sync by hand with app/v1_capabilities.py's identical exclusion
# (fix.md #12) -- domains the model can tag that name a different, explicitly
# unsupported family. None of these are schema-catalog domains (they have no
# verified columns), so excluding them from the lookup shortcut below simply
# lets them fall through to the catalog lookup, which correctly finds nothing
# and rejects with "Domain is outside the V1 business schema catalog."
_OTHER_UNSUPPORTED_DOMAINS = {"stock", "inventory", "grn", "goods receipt", "goods receipt note"}


def _domain(catalog: dict, plan: QueryPlan) -> dict | None:
    # See app/v1_capabilities.py:_family_name for why the exclusion is
    # needed: a plan explicitly tagged with a different unsupported domain
    # (e.g. "grn" for "is material X received?") must fail closed as that
    # domain, not be silently answered as a plain material/supplier lookup
    # that never represents what was actually asked.
    if (plan.operation.lower() == "lookup" and plan.business_subject
            and _normalise(plan.domain) not in _OTHER_UNSUPPORTED_DOMAINS):
        subject = _normalise(plan.business_subject.concept)
        if subject in {"supplier", "vendor", "party"}:
            return next(domain for domain in catalog["domains"] if domain["name"] == "supplier_lookup")
        if subject in {"material", "item"}:
            return next(domain for domain in catalog["domains"] if domain["name"] == "material_lookup")
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


def _path_tables(start: str, path: list[dict]) -> list[str]:
    """Return the ordered table traversal for a verified relationship path."""
    tables = [start]
    current = start
    for edge in path:
        if edge["from_table"] == current:
            current = edge["to_table"]
        elif edge["to_table"] == current:
            current = edge["from_table"]
        else:
            raise RuntimeError("Relationship path is not contiguous.")
        tables.append(current)
    return tables


def _add_join_identifiers(path: list[dict], selected_columns: list[GroundedColumn]) -> None:
    """Expose only verified FK endpoints needed to reach a display table."""
    for edge in path:
        for table_key, column_key in (("from_table", "from_column"), ("to_table", "to_column")):
            selected_columns.append(GroundedColumn(
                full_table_name=edge[table_key],
                column_name=edge[column_key],
                logical_concept="relationship_identifier",
                role="join_identifier",
                confidence=edge["confidence"],
            ))


def _apply_anti_join(
    anti_join_concept: dict,
    selected_tables: set[str],
    selected_columns: list[GroundedColumn],
    anti_join_conditions: list[AntiJoinCondition],
) -> None:
    """Add the join-identifier and null-check columns for one catalog-declared
    anti-join, and record it on the plan. `from_table` is always the domain
    anchor already in `selected_tables`; this only ever adds the far side."""
    anti_join = anti_join_concept["anti_join_condition"]
    selected_tables.add(anti_join["to_table"])
    for column in anti_join["from_columns"]:
        selected_columns.append(GroundedColumn(
            full_table_name=anti_join["from_table"], column_name=column,
            logical_concept=anti_join_concept["name"], role="join_identifier",
            confidence=anti_join_concept.get("confidence", 0.8),
        ))
    for column in anti_join["to_columns"]:
        selected_columns.append(GroundedColumn(
            full_table_name=anti_join["to_table"], column_name=column,
            logical_concept=anti_join_concept["name"], role="join_identifier",
            confidence=anti_join_concept.get("confidence", 0.8),
        ))
    selected_columns.append(GroundedColumn(
        full_table_name=anti_join["to_table"], column_name=anti_join["null_check_column"],
        logical_concept=anti_join_concept["name"], role="entity_filter",
        confidence=anti_join_concept.get("confidence", 0.8),
    ))
    anti_join_conditions.append(AntiJoinCondition(
        logical_concept=anti_join_concept["name"],
        from_table=anti_join["from_table"], from_columns=list(anti_join["from_columns"]),
        to_table=anti_join["to_table"], to_columns=list(anti_join["to_columns"]),
        null_check_column=anti_join["null_check_column"],
    ))


def ground_query_plan(query_plan: QueryPlan) -> GroundedSchemaPlan:
    """Ground only V1 catalog concepts; never generate SQL or access external services."""
    catalog, metadata_columns, metadata_edges = _load_inputs()
    _catalog_is_verified(catalog, metadata_columns, metadata_edges)
    domain = _domain(catalog, query_plan)
    schemas = sorted(
        {table.split(".", 1)[0] for item in catalog["domains"] for table in item["primary_tables"]}
        | {column["table"].split(".", 1)[0] for concept in catalog["concepts"] for column in concept.get("columns", [])}
        | {
            column["table"].split(".", 1)[0]
            for concept in catalog["concepts"]
            for column in (
                concept.get("compound_condition", {}).get("columns", [])
                + concept.get("compound_condition", {}).get("value_or_null_columns", [])
            )
        }
        | {
            table.split(".", 1)[0]
            for concept in catalog["concepts"]
            for table in (
                [concept["anti_join_condition"]["from_table"], concept["anti_join_condition"]["to_table"]]
                if concept.get("anti_join_condition")
                else []
            )
        }
    )
    result = GroundedSchemaPlan(source_query_plan=_source_summary(query_plan), candidate_schemas=schemas, confidence=0.0)
    if domain is None:
        result.reject_reasons.append(GroundingRejection(requirement="domain", reason="Domain is outside the V1 business schema catalog."))
        return result

    anchors = list(domain["primary_tables"])
    selected_tables = set(anchors)
    selected_columns: list[GroundedColumn] = []
    paths: list[RelationshipPath] = []
    confidences = [domain["confidence"]]

    compound_conditions: list[GroundedCompoundCondition] = []
    anti_join_conditions: list[AntiJoinCondition] = []

    for requirement_type, phrase, role in _requirements(query_plan):
        candidates = _matching_columns(catalog, phrase, role)
        if not candidates:
            compound_concept = _matching_compound_concept(catalog, phrase, role)
            if compound_concept is not None:
                compound = compound_concept["compound_condition"]
                out_of_scope = [
                    col for col in compound["columns"] if col["table"] not in anchors
                ]
                if out_of_scope:
                    result.reject_reasons.append(GroundingRejection(
                        requirement=f"{requirement_type}:{phrase}",
                        reason="Compound condition requires a table outside the V1 domain anchor; no join is supported for compound conditions.",
                    ))
                    continue
                for col in compound["columns"]:
                    selected_columns.append(GroundedColumn(
                        full_table_name=col["table"], column_name=col["column"],
                        logical_concept=compound_concept["name"], role="entity_filter",
                        confidence=compound_concept.get("confidence", 0.8),
                    ))
                compound_column_names = [f"{col['table']}.{col['column']}" for col in compound["columns"]]
                pinned_values = {
                    f"{col['table']}.{col['column']}": col["value"]
                    for col in compound["columns"]
                    if "value" in col
                }
                value_or_null_columns = compound.get("value_or_null_columns", [])
                for col in value_or_null_columns:
                    selected_columns.append(GroundedColumn(
                        full_table_name=col["table"], column_name=col["column"],
                        logical_concept=compound_concept["name"], role="entity_filter",
                        confidence=compound_concept.get("confidence", 0.8),
                    ))
                value_or_null = {
                    f"{col['table']}.{col['column']}": col["value"] for col in value_or_null_columns
                }
                compound_conditions.append(GroundedCompoundCondition(
                    logical_concept=compound_concept["name"],
                    combinator=compound["combinator"],
                    columns=compound_column_names,
                    pinned_values=pinned_values,
                    value_or_null=value_or_null,
                ))
                # Record under the literal plan phrase (not just the catalog's
                # canonical name), so the SQL validator can recognise this
                # requirement as a compound condition regardless of which
                # alias the plan used -- the same lookup ordinary entity
                # concepts already rely on.
                result.entity_column_candidates[phrase] = sorted(compound_column_names)
                result.evidence.append(GroundingEvidence(
                    requirement=f"{requirement_type}:{phrase}", logical_concept=compound_concept["name"],
                    source=compound_concept.get("evidence", "metadata"),
                    confidence=compound_concept.get("confidence", 0.8),
                ))
                confidences.append(compound_concept.get("confidence", 0.8))
                anti_join_concept = _matching_anti_join_concept(catalog, phrase, role)
                if anti_join_concept is not None:
                    # Only applied as an adjunct to a compound condition on the
                    # same concept (e.g. mrs_pending's flag-AND plus its
                    # anti-join to MRS) -- there is no verified concept today
                    # that is an anti-join with no compound condition at all,
                    # and the rest of the validator's bind-skipping logic
                    # (_is_compound_condition_concept) is keyed off the
                    # compound condition existing, so an anti-join-only
                    # concept is deliberately not supported until one is
                    # actually needed.
                    if anti_join_concept["anti_join_condition"]["from_table"] not in anchors:
                        result.reject_reasons.append(GroundingRejection(
                            requirement=f"{requirement_type}:{phrase}",
                            reason="Anti-join condition's base table is outside the V1 domain anchor.",
                        ))
                        continue
                    _apply_anti_join(anti_join_concept, selected_tables, selected_columns, anti_join_conditions)
                continue
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
            path_model = RelationshipPath(constraint_names=[edge["constraint_name"] for edge in path], tables=_path_tables(anchors[0], path), confidence=min(edge["confidence"] for edge in path))
            if path_model not in paths:
                paths.append(path_model)
            _add_join_identifiers(path, selected_columns)
        selected_columns.append(GroundedColumn(full_table_name=target_table, column_name=column["column"], logical_concept=concept["name"], role=role, confidence=column["confidence"]))
        result.evidence.append(GroundingEvidence(requirement=f"{requirement_type}:{phrase}", logical_concept=concept["name"], source=column["evidence"], confidence=column["confidence"]))
        confidences.append(column["confidence"])

    # Keep exactly one record per physical column and order outputs deterministically.
    unique_columns = {(item.full_table_name, item.column_name, item.role): item for item in selected_columns}
    result.selected_columns = sorted(unique_columns.values(), key=lambda item: (item.full_table_name, item.column_name, item.role))
    result.allowed_relationship_paths = sorted(paths, key=lambda item: (item.constraint_names, item.tables))
    result.compound_conditions = sorted(compound_conditions, key=lambda item: item.logical_concept)
    result.anti_join_conditions = sorted(anti_join_conditions, key=lambda item: item.logical_concept)
    table_roles = {table: "anchor" if table in anchors else "related" for table in selected_tables}
    result.selected_tables = [GroundedTable(full_table_name=table, role=table_roles[table], confidence=domain["confidence"] if table in anchors else 1.0) for table in sorted(selected_tables)]
    if result.reject_reasons or any(item.blocking for item in result.ambiguities):
        result.confidence = 0.0
    else:
        result.confidence = round(min(confidences), 2)
    return result
