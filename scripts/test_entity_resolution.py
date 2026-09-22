from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.entity_resolution import (
    VerifiedEntitySource,
    resolvable_concepts,
    resolve_entity,
    resolve_plan_entities,
)
from app.query_plan import BusinessSubject, EntityReference, EntityStatus, QueryPlan


def entity(concept: str, original_value: str, **overrides) -> EntityReference:
    values = {"concept": concept, "original_value": original_value}
    values.update(overrides)
    return EntityReference(**values)


def fake_lookup(table_rows: dict[tuple[str, str], list[tuple[str, str]]]):
    """table_rows maps (table, column) -> a list of (code, name) rows already
    "in the store". Implements the same case/whitespace-normalized-equality
    contract the production lookup is required to, entirely in Python with
    no I/O -- this is the offline stand-in for `oracle_entity_lookup`."""

    def _normalize(value: str) -> str:
        return " ".join(value.split()).upper()

    def lookup(source: VerifiedEntitySource, normalized_value: str):
        rows = table_rows.get((source.table, source.column), [])
        # rows are (code, name) or (code, name, detail) -- returned as-is
        index = 0 if source.value_shape == "code" else 1
        return [row for row in rows if _normalize(row[index]) == normalized_value]

    return lookup


SUPPLIER_ROWS = {
    ("SCM.PARTYMASTER", "PARTYNAME"): [
        ("800967", "Prime Compu Systems"),
        ("800968", "THE GALAXY"),
        ("800969", "Prime Compu System Pvt Ltd"),
    ],
    ("SCM.PARTYMASTER", "PARTYCODE"): [
        ("800967", "Prime Compu Systems"),
        ("800968", "THE GALAXY"),
        ("800969", "Prime Compu System Pvt Ltd"),
    ],
}

MATERIAL_ROWS = {
    ("INVENTORY.INVITEMS", "ITEM_NAME"): [
        ("IT-001", "BARCODE SCANNER"),
        ("IT-002", "MONITOR"),
        ("IT-003", "KEYBOARD"),
    ],
    ("INVENTORY.INVITEMS", "ITEM_CODE"): [
        ("IT-001", "BARCODE SCANNER"),
        ("IT-002", "MONITOR"),
        ("IT-003", "KEYBOARD"),
    ],
}


class EntityResolutionTests(unittest.TestCase):
    # -- supplier -------------------------------------------------------------

    def test_supplier_exact_name_match(self) -> None:
        resolved = resolve_entity(entity("supplier_name", "Prime Compu Systems"), fake_lookup(SUPPLIER_ROWS))
        self.assertEqual(resolved.status, EntityStatus.RESOLVED)
        self.assertEqual(resolved.selected_value, "Prime Compu Systems")

    def test_supplier_case_insensitive_match(self) -> None:
        resolved = resolve_entity(entity("supplier_name", "prime compu systems"), fake_lookup(SUPPLIER_ROWS))
        self.assertEqual(resolved.status, EntityStatus.RESOLVED)
        self.assertEqual(resolved.selected_value, "Prime Compu Systems")

    def test_supplier_whitespace_normalized_match(self) -> None:
        resolved = resolve_entity(entity("supplier_name", "  Prime   Compu    Systems "), fake_lookup(SUPPLIER_ROWS))
        self.assertEqual(resolved.status, EntityStatus.RESOLVED)
        self.assertEqual(resolved.selected_value, "Prime Compu Systems")

    def test_supplier_exact_code_match(self) -> None:
        resolved = resolve_entity(entity("supplier_identifier", "800967"), fake_lookup(SUPPLIER_ROWS))
        self.assertEqual(resolved.status, EntityStatus.RESOLVED)
        self.assertEqual(resolved.selected_value, "Prime Compu Systems")

    def test_generic_supplier_concept_dispatches_by_value_shape(self) -> None:
        lookup = fake_lookup(SUPPLIER_ROWS)
        self.assertEqual(resolve_entity(entity("supplier", "800967"), lookup).selected_value, "Prime Compu Systems")
        self.assertEqual(resolve_entity(entity("supplier", "THE GALAXY"), lookup).selected_value, "THE GALAXY")

    def test_supplier_not_found(self) -> None:
        resolved = resolve_entity(entity("supplier_name", "Nonexistent Supplier Co"), fake_lookup(SUPPLIER_ROWS))
        self.assertEqual(resolved.status, EntityStatus.UNRESOLVED)
        self.assertIsNone(resolved.selected_value)

    def test_supplier_ambiguous_name(self) -> None:
        # Ambiguity must come from two DISTINCT verified rows that both
        # normalize to the SAME query value -- never from a partial or
        # substring hit (no "Prime Compu System" vs "...Systems" fuzziness).
        rows = {
            ("SCM.PARTYMASTER", "PARTYNAME"): [
                ("900001", "Prime Compu Systems"),
                ("900002", "PRIME COMPU SYSTEMS"),
            ],
        }
        resolved = resolve_entity(entity("supplier_name", "Prime Compu Systems"), fake_lookup(rows))
        self.assertEqual(resolved.status, EntityStatus.AMBIGUOUS)
        self.assertEqual(len(resolved.candidates), 2)
        self.assertIsNone(resolved.selected_value)

    # -- material ---------------------------------------------------------

    def test_material_exact_match(self) -> None:
        resolved = resolve_entity(entity("material", "BARCODE SCANNER"), fake_lookup(MATERIAL_ROWS))
        self.assertEqual(resolved.status, EntityStatus.RESOLVED)
        self.assertEqual(resolved.selected_value, "BARCODE SCANNER")

    def test_material_normalized_match(self) -> None:
        resolved = resolve_entity(entity("material", "barcode  scanner"), fake_lookup(MATERIAL_ROWS))
        self.assertEqual(resolved.status, EntityStatus.RESOLVED)
        self.assertEqual(resolved.selected_value, "BARCODE SCANNER")

    def test_material_not_found(self) -> None:
        resolved = resolve_entity(entity("material", "Nonexistent Widget"), fake_lookup(MATERIAL_ROWS))
        self.assertEqual(resolved.status, EntityStatus.UNRESOLVED)

    def test_material_ambiguous(self) -> None:
        rows = {
            ("INVENTORY.INVITEMS", "ITEM_NAME"): [
                ("IT-010", "MONITOR"),
                ("IT-011", "Monitor"),
            ],
        }
        resolved = resolve_entity(entity("material", "MONITOR"), fake_lookup(rows))
        self.assertEqual(resolved.status, EntityStatus.AMBIGUOUS)
        self.assertEqual(len(resolved.candidates), 2)

    # -- live master-data facts (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md) -----

    def test_supplier_lookup_is_scoped_to_goodstypecode_2(self) -> None:
        # The ERP's own INVENTORY.SUPPLIER view is PARTYMASTER WHERE
        # GOODSTYPECODE = 2; PARTYMASTER also holds ~11.5k customers/others.
        from unittest.mock import patch
        from app.entity_resolution import _VERIFIED_SOURCES, oracle_entity_lookup
        captured = {}

        def fake_run_safe_select(sql, binds):
            captured["sql"], captured["binds"] = sql, binds
            return {"rows": [("800967", "Prime Compu Systems")]}

        with patch("app.oracle_client.run_safe_select", fake_run_safe_select):
            rows = oracle_entity_lookup(_VERIFIED_SOURCES["supplier_name"][0], "PRIME COMPU SYSTEMS")
        self.assertEqual(rows, [("800967", "Prime Compu Systems")])
        self.assertIn("AND GOODSTYPECODE = 2", captured["sql"])
        self.assertEqual(captured["binds"], {"normalized_value": "PRIME COMPU SYSTEMS"})
        for source in _VERIFIED_SOURCES["material"] + _VERIFIED_SOURCES["item_identifier"]:
            self.assertEqual(source.scope, "")
            self.assertEqual(source.detail_column, "OBSOLETE")

    def test_duplicate_item_name_with_two_codes_is_ambiguous(self) -> None:
        # 407 ITEM_NAMEs are shared by more than one ITEM_CODE in the live
        # master: same name, two materials -> the name cannot pick one.
        rows = {("INVENTORY.INVITEMS", "ITEM_NAME"): [("IT-020", "KEYBOARD", "0"), ("IT-021", "KEYBOARD", "1")]}
        resolved = resolve_entity(entity("material", "keyboard"), fake_lookup(rows))
        self.assertIs(resolved.status, EntityStatus.AMBIGUOUS)
        self.assertEqual(resolved.candidates, ["KEYBOARD [IT-020]", "KEYBOARD [IT-021] (obsolete)"])

    def test_same_code_returned_twice_is_still_one_match(self) -> None:
        rows = {("INVENTORY.INVITEMS", "ITEM_NAME"): [("IT-020", "KEYBOARD", "0"), ("IT-020", "KEYBOARD", "0")]}
        resolved = resolve_entity(entity("material", "keyboard"), fake_lookup(rows))
        self.assertIs(resolved.status, EntityStatus.RESOLVED)
        self.assertEqual(resolved.selected_value, "KEYBOARD")

    # -- safety -------------------------------------------------------------

    def test_model_claimed_resolved_status_is_discarded(self) -> None:
        # Even if Qwen's JSON claimed this entity was already resolved with
        # a fabricated value, resolve_entity must independently re-verify --
        # never trust the incoming status/selected_value.
        claimed = entity(
            "supplier_name", "Totally Fake Supplier",
            status=EntityStatus.RESOLVED, selected_value="Totally Fake Supplier",
        )
        resolved = resolve_entity(claimed, fake_lookup(SUPPLIER_ROWS))
        self.assertEqual(resolved.status, EntityStatus.UNRESOLVED)
        self.assertIsNone(resolved.selected_value)

    def test_unknown_concept_fails_closed(self) -> None:
        resolved = resolve_entity(entity("mrs_number", "890330"), fake_lookup({}))
        self.assertEqual(resolved.status, EntityStatus.UNRESOLVED)

    def test_not_required_entity_is_untouched(self) -> None:
        untouched = entity("mrs_hold_flag", "1", status=EntityStatus.NOT_REQUIRED)
        resolved = resolve_entity(untouched, fake_lookup({}))
        self.assertEqual(resolved.status, EntityStatus.NOT_REQUIRED)
        self.assertEqual(resolved.original_value, "1")

    def test_empty_original_value_fails_closed(self) -> None:
        resolved = resolve_entity(entity("supplier_name", "   "), fake_lookup(SUPPLIER_ROWS))
        self.assertEqual(resolved.status, EntityStatus.UNRESOLVED)

    def test_resolvable_concepts_matches_verified_catalog_concepts(self) -> None:
        self.assertEqual(
            resolvable_concepts(),
            {"supplier", "supplier_name", "supplier_identifier", "material", "item_identifier"},
        )

    # -- multi-entity plan-level resolution -----------------------------------

    def _mixed_lookup(self):
        def lookup(source: VerifiedEntitySource, normalized_value: str):
            if source.table == "INVENTORY.INVITEMS":
                return fake_lookup(MATERIAL_ROWS)(source, normalized_value)
            return fake_lookup(SUPPLIER_ROWS)(source, normalized_value)
        return lookup

    def test_resolve_plan_entities_resolves_independently(self) -> None:
        plan = QueryPlan(
            original_question="purchases of monitor from the galaxy",
            domain="purchase", operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            entities=[entity("material", "MONITOR"), entity("supplier_name", "THE GALAXY")],
            confidence=0.9,
        )
        resolved_plan = resolve_plan_entities(plan, self._mixed_lookup())
        statuses = {item.concept: item.status for item in resolved_plan.entities}
        self.assertEqual(statuses["material"], EntityStatus.RESOLVED)
        self.assertEqual(statuses["supplier_name"], EntityStatus.RESOLVED)

    def test_resolve_plan_entities_reports_each_outcome_independently(self) -> None:
        # Resolution itself resolves each entity independently and reports
        # both outcomes; refusing to execute on a mixed result is
        # nlp_execution's job, not this function's.
        plan = QueryPlan(
            original_question="purchases of widget from the galaxy",
            domain="purchase", operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            entities=[entity("material", "Nonexistent Widget"), entity("supplier_name", "THE GALAXY")],
            confidence=0.9,
        )
        resolved_plan = resolve_plan_entities(plan, self._mixed_lookup())
        statuses = {item.concept: item.status for item in resolved_plan.entities}
        self.assertEqual(statuses["material"], EntityStatus.UNRESOLVED)
        self.assertEqual(statuses["supplier_name"], EntityStatus.RESOLVED)

    def test_entity_free_plan_is_returned_unchanged(self) -> None:
        plan = QueryPlan(
            original_question="how many materials placed in last month?",
            domain="purchase", operation="aggregate",
            business_subject=BusinessSubject(concept="purchase"),
            confidence=0.9,
        )
        self.assertIs(resolve_plan_entities(plan, fake_lookup({})), plan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
