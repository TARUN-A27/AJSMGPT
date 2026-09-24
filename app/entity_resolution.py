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

Resolution algorithm (exact match only decides RESOLVED; no edit distance,
no "closest" selection ever auto-picks a result):
  - Fetch every verified row whose column value is case/whitespace-normalized
    equal to the (equally normalized) query value.
  - One row    -> RESOLVED, `selected_value` set to the row's own verified
    value (never the user's raw text).
  - >1 rows    -> AMBIGUOUS, `candidates` set to the distinct verified
    values found; execution must still refuse to pick one.
  - Zero rows  -> for a text-shaped source only (fix.md #2: a code is either
    right or wrong, there is no shorthand version of one), fall back to one
    CONTAINS search (`oracle_entity_lookup_fuzzy`) so a refusal can name real
    candidates instead of being a dead end -- real users type shorthand
    ("mouse" for a longer real item name) that an exact match correctly
    never finds. This fallback still never produces RESOLVED by itself: one
    partial match stays UNRESOLVED with that match surfaced in `candidates`
    as a hint, two or more become AMBIGUOUS exactly like two exact matches
    would. Zero fuzzy rows either -> UNRESOLVED with no candidates (not
    found; there is no separate NOT_FOUND status -- UNRESOLVED already fails
    execution closed, see app/query_plan.py's EntityStatus and
    app/nlp_execution.py's gate).
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
    # Fixed, catalog-derived SQL predicate narrowing the master rows this
    # concept may match (never user or model text). Suppliers are the parties
    # the ERP's own INVENTORY.SUPPLIER view selects: GOODSTYPECODE = 2
    # (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md §6.1).
    scope: str = ""
    # Optional flag column returned with each row so an AMBIGUOUS candidate
    # can say "(obsolete)"; INVITEMS.OBSOLETE = 1 on 29% of items.
    detail_column: str | None = None


# The ONLY tables/columns entity resolution may ever query, taken directly
# from the verified `entity_filter` concepts in
# app/resources/business_schema_catalog.json (supplier/supplier_name/
# supplier_identifier -> SCM.PARTYMASTER; material/item_identifier ->
# INVENTORY.INVITEMS). Qwen only ever supplies a `concept` string chosen
# from the catalog's own alias vocabulary -- it is looked up here, never
# interpreted as SQL, a table name, or a column name.
_SUPPLIER_SCOPE = "GOODSTYPECODE = 2"
_VERIFIED_SOURCES: dict[str, tuple[VerifiedEntitySource, ...]] = {
    "supplier": (
        VerifiedEntitySource("SCM.PARTYMASTER", "PARTYCODE", "code", _SUPPLIER_SCOPE),
        VerifiedEntitySource("SCM.PARTYMASTER", "PARTYNAME", "text", _SUPPLIER_SCOPE),
    ),
    "supplier_name": (VerifiedEntitySource("SCM.PARTYMASTER", "PARTYNAME", "text", _SUPPLIER_SCOPE),),
    "supplier_identifier": (VerifiedEntitySource("SCM.PARTYMASTER", "PARTYCODE", "code", _SUPPLIER_SCOPE),),
    "material": (VerifiedEntitySource("INVENTORY.INVITEMS", "ITEM_NAME", "text", "", "OBSOLETE"),),
    "item_identifier": (VerifiedEntitySource("INVENTORY.INVITEMS", "ITEM_CODE", "code", "", "OBSOLETE"),),
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


EntityLookup = Callable[[VerifiedEntitySource, str], Sequence[tuple]]
"""(source, normalized_value) -> matching (code, display_value) rows.

Contract: return every verified row whose `source.column` value, once
case/whitespace-normalized the same way as `normalized_value`, equals it.
The lookup never receives anything beyond the already-normalized value and
the fixed source descriptor -- never a raw model string, never SQL.
Production wiring backs this with one bind-parameterized, read-only SELECT
(`oracle_entity_lookup`). Tests back it with a plain in-memory fake.
"""


def _dedupe_by_code(rows: Sequence[tuple]) -> dict[str, tuple[str, object]]:
    """One verified row per CODE: two items sharing one name are two
    materials (407 duplicated ITEM_NAMEs in the live master), so the name
    alone cannot pick one of them."""
    by_code: dict[str, tuple[str, object]] = {}
    for row in rows:
        code, display = str(row[0]), str(row[1])
        detail = row[2] if len(row) > 2 else None
        by_code.setdefault(code, (display, detail))
    return by_code


def _candidate_labels(by_code: dict[str, tuple[str, object]]) -> list[str]:
    return [
        f"{display} [{code}]" + (" (obsolete)" if str(detail) == "1" else "")
        for code, (display, detail) in by_code.items()
    ]


def resolve_entity(
    entity: EntityReference,
    lookup: EntityLookup,
    fuzzy_lookup: EntityLookup | None = None,
) -> EntityReference:
    """Return a NEW EntityReference with `status`/`selected_value`/`candidates`
    set from a verified lookup -- never from whatever the model already put there.

    Any `status`/`selected_value` the model supplied is discarded before
    this runs; only this function may produce RESOLVED or AMBIGUOUS.

    `fuzzy_lookup`, when given, is tried only after the exact match finds
    nothing, and only for a text-shaped source (a code is either right or
    wrong; there is no "shorthand" version of one). It never produces
    RESOLVED by itself: even a single partial match stays UNRESOLVED, just
    with that match surfaced in `candidates` as a hint, and two or more
    become AMBIGUOUS exactly like two exact matches would (fix.md #2) --
    this only turns a dead-end refusal into an actionable one.
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

    by_code = _dedupe_by_code(rows)
    if not by_code:
        if fuzzy_lookup is not None and source.value_shape == "text":
            try:
                fuzzy_rows = fuzzy_lookup(source, normalized)
            except Exception as exc:
                raise EntityLookupError(f"Verified fuzzy lookup failed for concept '{entity.concept}'.") from exc
            fuzzy_by_code = _dedupe_by_code(fuzzy_rows)
            if fuzzy_by_code:
                return entity.model_copy(update={
                    "status": EntityStatus.AMBIGUOUS if len(fuzzy_by_code) > 1 else EntityStatus.UNRESOLVED,
                    "selected_value": None,
                    "candidates": _candidate_labels(fuzzy_by_code),
                })
        return entity.model_copy(update={"status": EntityStatus.UNRESOLVED, "selected_value": None, "candidates": []})
    if len(by_code) == 1:
        (display, _detail), = by_code.values()
        return entity.model_copy(update={
            "status": EntityStatus.RESOLVED,
            "selected_value": display,
            "normalized_value": normalized,
            "candidates": [],
        })
    return entity.model_copy(update={
        "status": EntityStatus.AMBIGUOUS,
        "selected_value": None,
        "candidates": _candidate_labels(by_code),
    })


def resolve_plan_entities(
    plan: QueryPlan,
    lookup: EntityLookup,
    fuzzy_lookup: EntityLookup | None = None,
) -> QueryPlan:
    """Resolve every entity in the plan independently. A plan with no
    entities is returned unchanged (identity, not just an equal copy)."""
    if not plan.entities:
        return plan
    resolved = [resolve_entity(entity, lookup, fuzzy_lookup) for entity in plan.entities]
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
    # The display column IS the identifier for a code-shaped source; projecting
    # the same name twice makes the inline view run_safe_select wraps this in
    # (SELECT * FROM (...) WHERE ROWNUM <= n) ambiguous -- ORA-00918.
    display_column = source.column if source.column != identifier_column else f"{source.column} AS DISPLAY_VALUE"
    select_list = f"{identifier_column}, {display_column}" + (f", {source.detail_column}" if source.detail_column else "")
    sql = (
        f"SELECT {select_list} FROM {source.table} "
        f"WHERE UPPER(TRIM(REGEXP_REPLACE({source.column}, '[[:space:]]+', ' '))) = :normalized_value"
        + (f" AND {source.scope}" if source.scope else "")
    )
    result = run_safe_select(sql, {"normalized_value": normalized_value})
    return [tuple(str(value) if value is not None else None for value in row) for row in result["rows"]]


_FUZZY_CANDIDATE_LIMIT = 10


def oracle_entity_lookup_fuzzy(source: VerifiedEntitySource, normalized_value: str) -> list[tuple[str, str]]:
    """Fallback CONTAINS search, used only when `oracle_entity_lookup`'s exact
    match finds nothing. Still never lets `resolve_entity` auto-select a
    result on its own -- see that function's docstring -- this only makes a
    refusal actionable instead of a dead end when the user typed shorthand
    (fix.md #2).

    Bind-parameterized exactly like the exact lookup; only the comparison
    operator differs. Literal '%'/'_' in the searched-for text are escaped so
    they are not misread as wildcards. Capped at `_FUZZY_CANDIDATE_LIMIT`
    results in Python -- a "did you mean" list only makes sense short;
    `run_safe_select`'s own row limit (up to 100) is too wide to present as
    candidates.
    """
    from app.oracle_client import run_safe_select  # local import: no Oracle dependency for offline tests

    identifier_column = "PARTYCODE" if source.table == "SCM.PARTYMASTER" else "ITEM_CODE"
    display_column = source.column if source.column != identifier_column else f"{source.column} AS DISPLAY_VALUE"
    select_list = f"{identifier_column}, {display_column}" + (f", {source.detail_column}" if source.detail_column else "")
    escaped_value = (
        normalized_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    sql = (
        f"SELECT {select_list} FROM {source.table} "
        f"WHERE UPPER(TRIM(REGEXP_REPLACE({source.column}, '[[:space:]]+', ' '))) "
        f"LIKE '%' || :normalized_value || '%' ESCAPE '\\'"
        + (f" AND {source.scope}" if source.scope else "")
    )
    result = run_safe_select(sql, {"normalized_value": escaped_value})
    rows = [tuple(str(value) if value is not None else None for value in row) for row in result["rows"]]
    return rows[:_FUZZY_CANDIDATE_LIMIT]
