"""Deterministic, offline-testable verification of QueryPlan entity references
against the verified ERP master-data sources.

Qwen may say WHAT an entity means (`concept`) and WHAT the user typed
(`original_value`). It never gets to say whether that value is real, and it
never supplies the lookup SQL, table, column, or operator that verifies it --
that mapping is fixed below, directly from the verified business schema
catalog (app/resources/business_schema_catalog.json), and is never derived
from model output. Only this module may transition an entity's status
UNRESOLVED -> RESOLVED; any status the model already put in its JSON is
discarded and re-verified from scratch.

Two distinct kinds of database interaction exist in this pipeline, and they
must never be confused:
  1. Entity-resolution lookup (this module): read-only, narrowly scoped to
     one verified table/column, bind-parameterized, used only to confirm
     identity. See `oracle_entity_lookup`.
  2. Business execution (app/nlp_execution.py's `runner()` call): the actual
     report query, exactly zero times on failed resolution, exactly once on
     success. This module has no opinion on and no access to that call.

Resolution algorithm (no fuzzy matching, no edit distance, no "closest"
selection -- see the module docstring rationale in the design notes):
  - Fetch every verified row whose column value is case/whitespace-normalized
    equal to the (equally normalized) query value.
  - Zero rows  -> UNRESOLVED (not found; there is no separate NOT_FOUND
    status -- UNRESOLVED already fails execution closed, see
    app/query_plan.py's EntityStatus and app/nlp_execution.py's gate).
  - One row    -> RESOLVED, `selected_value` set to the row's own verified
    value (never the user's raw text).
  - >1 rows    -> AMBIGUOUS, `candidates` set to the distinct verified
    values found; execution must still refuse to pick one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from app.query_plan import EntityReference, EntityStatus, QueryPlan


class EntityLookupError(RuntimeError):
    """The verified lookup could not be performed safely."""


@dataclass(frozen=True)
class VerifiedEntitySource:
    """One verified master-data column an entity concept may resolve against."""

    table: str
    column: str
    value_shape: str  # "text" or "code" -- see _select_source


# The ONLY tables/columns entity resolution may ever query, taken directly
# from the verified `entity_filter` concepts in
# app/resources/business_schema_catalog.json (supplier/supplier_name/
# supplier_identifier -> SCM.PARTYMASTER; material/item_identifier ->
# INVENTORY.INVITEMS). Qwen only ever supplies a `concept` string chosen
# from the catalog's own alias vocabulary -- it is looked up here, never
# interpreted as SQL, a table name, or a column name.
_VERIFIED_SOURCES: dict[str, tuple[VerifiedEntitySource, ...]] = {
    "supplier": (
        VerifiedEntitySource("SCM.PARTYMASTER", "PARTYCODE", "code"),
        VerifiedEntitySource("SCM.PARTYMASTER", "PARTYNAME", "text"),
    ),
    "supplier_name": (VerifiedEntitySource("SCM.PARTYMASTER", "PARTYNAME", "text"),),
    "supplier_identifier": (VerifiedEntitySource("SCM.PARTYMASTER", "PARTYCODE", "code"),),
    "material": (VerifiedEntitySource("INVENTORY.INVITEMS", "ITEM_NAME", "text"),),
    "item_identifier": (VerifiedEntitySource("INVENTORY.INVITEMS", "ITEM_CODE", "code"),),
}

_CODE_SHAPE = re.compile(r"^\d+$")


def resolvable_concepts() -> frozenset[str]:
    """Concepts this module can verify. Anything else fails closed to UNRESOLVED."""
    return frozenset(_VERIFIED_SOURCES)


def _normalize(value: str) -> str:
    """Case and whitespace normalization only -- never a fuzzy transform."""
    return " ".join(value.split()).upper()


def _select_source(concept: str, value: str) -> VerifiedEntitySource | None:
    """Pick the one verified column to check for this concept.

    For a concept with a single verified column, that's the only choice.
    For a generic concept covering both a code and a name column (e.g.
    "supplier"), the VALUE's own shape (all-digits vs. not) decides which
    verified column to check -- a plain, deterministic classification of the
    text itself, never a guess at its content or a search across both.
    """
    sources = _VERIFIED_SOURCES.get(concept)
    if not sources:
        return None
    if len(sources) == 1:
        return sources[0]
    is_code_shaped = bool(_CODE_SHAPE.match(value.strip()))
    for source in sources:
        if (source.value_shape == "code") == is_code_shaped:
            return source
    return sources[-1]


EntityLookup = Callable[[VerifiedEntitySource, str], Sequence[tuple[str, str]]]
"""(source, normalized_value) -> matching (code, display_value) rows.

Contract: return every verified row whose `source.column` value, once
case/whitespace-normalized the same way as `normalized_value`, equals it.
The lookup never receives anything beyond the already-normalized value and
the fixed source descriptor -- never a raw model string, never SQL.
Production wiring backs this with one bind-parameterized, read-only SELECT
(`oracle_entity_lookup`). Tests back it with a plain in-memory fake.
"""


def resolve_entity(entity: EntityReference, lookup: EntityLookup) -> EntityReference:
    """Return a NEW EntityReference with `status`/`selected_value`/`candidates`
    set from a verified lookup -- never from whatever the model already put there.

    Any `status`/`selected_value` the model supplied is discarded before
    this runs; only this function may produce RESOLVED or AMBIGUOUS.
    """
    if entity.status is EntityStatus.NOT_REQUIRED:
        return entity
    if not entity.original_value or not entity.original_value.strip():
        return entity.model_copy(update={"status": EntityStatus.UNRESOLVED, "selected_value": None, "candidates": []})

    source = _select_source(entity.concept, entity.original_value)
    if source is None:
        return entity.model_copy(update={"status": EntityStatus.UNRESOLVED, "selected_value": None, "candidates": []})

    normalized = _normalize(entity.original_value)
    try:
        rows = lookup(source, normalized)
    except Exception as exc:
        raise EntityLookupError(f"Verified lookup failed for concept '{entity.concept}'.") from exc

    distinct_values = list(dict.fromkeys(display for _code, display in rows))
    if not distinct_values:
        return entity.model_copy(update={"status": EntityStatus.UNRESOLVED, "selected_value": None, "candidates": []})
    if len(distinct_values) == 1:
        return entity.model_copy(update={
            "status": EntityStatus.RESOLVED,
            "selected_value": distinct_values[0],
            "normalized_value": normalized,
            "candidates": [],
        })
    return entity.model_copy(update={
        "status": EntityStatus.AMBIGUOUS,
        "selected_value": None,
        "candidates": distinct_values,
    })


def resolve_plan_entities(plan: QueryPlan, lookup: EntityLookup) -> QueryPlan:
    """Resolve every entity in the plan independently. A plan with no
    entities is returned unchanged (identity, not just an equal copy)."""
    if not plan.entities:
        return plan
    resolved = [resolve_entity(entity, lookup) for entity in plan.entities]
    return plan.model_copy(update={"entities": resolved})


def oracle_entity_lookup(source: VerifiedEntitySource, normalized_value: str) -> list[tuple[str, str]]:
    """Production `EntityLookup` backed by a single verified, read-only,
    bind-only SELECT against the fixed table/column in `source`.

    The SQL text is entirely fixed by this module from `_VERIFIED_SOURCES`
    above -- never model-supplied, never string-built from user input beyond
    the one bind value. Goes through the project's existing safe-select path
    (read-only enforcement, schema allowlist, row limit, datatype checks);
    see app/oracle_client.run_safe_select / app/sql_safety.py.
    """
    from app.oracle_client import run_safe_select  # local import: no Oracle dependency for offline tests

    identifier_column = "PARTYCODE" if source.table == "SCM.PARTYMASTER" else "ITEM_CODE"
    sql = (
        f"SELECT {identifier_column}, {source.column} FROM {source.table} "
        f"WHERE UPPER(TRIM(REGEXP_REPLACE({source.column}, '[[:space:]]+', ' '))) = :normalized_value"
    )
    result = run_safe_select(sql, {"normalized_value": normalized_value})
    return [(str(row[0]), str(row[1])) for row in result["rows"]]
