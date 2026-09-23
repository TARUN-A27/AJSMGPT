import unittest
from unittest.mock import patch

from app.query_plan import (
    Aggregation, BusinessSubject, DateRange, DateRangeKind, Dimension, FilterOperator, QueryFilter,
    EntityReference, EntityStatus, Measure, QueryPlan,
)
from app.schema_grounding import ground_query_plan


def plan(domain, subject, *, operation="detail", measures=None, dimensions=None, entities=None, date_range=None):
    return QueryPlan(
        original_question="offline grounding test",
        domain=domain,
        operation=operation,
        business_subject=BusinessSubject(concept=subject),
        measures=measures or [],
        dimensions=dimensions or [],
        entities=entities or [],
        date_range=date_range,
        confidence=0.9,
    )


DATE_RANGE = DateRange(kind=DateRangeKind.RELATIVE, original_text="last six months")


class SchemaGroundingTests(unittest.TestCase):
    def assert_grounded(self, result):
        self.assertTrue(result.is_grounded, result.model_dump())
        self.assertGreater(result.confidence, 0)

    def test_purchase_ranking_supplier_value_date(self):
        result = ground_query_plan(plan(
            "purchase", "purchase", operation="ranking",
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="supplier", grouping=True)], date_range=DATE_RANGE,
        ))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.PURCHASEORDER", "SCM.PARTYMASTER"])
        self.assertEqual([path.constraint_names for path in result.allowed_relationship_paths], [["SUPCODE_PARTYMASTER-PARTYCODE"]])
        columns = {(column.full_table_name, column.column_name): column.role for column in result.selected_columns}
        self.assertEqual(columns[("SCM.PARTYMASTER", "PARTYNAME")], "grouping")
        self.assertEqual(columns[("INVENTORY.PURCHASEORDER", "SUP_CODE")], "join_identifier")
        self.assertNotIn("grouping", [column.role for column in result.selected_columns if column.column_name == "SUP_CODE"])

    def test_generic_date_filter_is_satisfied_by_date_range(self):
        result = ground_query_plan(plan(
            "purchase", "purchase", operation="ranking",
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="supplier", grouping=True)],
            date_range=DATE_RANGE,
        ).model_copy(update={"filters": [QueryFilter(concept="date", operator=FilterOperator.GREATER_THAN, value="30 days ago", value_type="date")] }))
        self.assert_grounded(result)
        self.assertFalse(result.reject_reasons)
        self.assertIn(("INVENTORY.PURCHASEORDER", "ORDERDATE", "date_filter"),
                      [(c.full_table_name, c.column_name, c.role) for c in result.selected_columns])

    def test_plan_without_date_range_does_not_acquire_one(self):
        result = ground_query_plan(plan("purchase", "purchase"))
        self.assertFalse(any(c.role == "date_filter" for c in result.selected_columns))

    def test_purchase_supplier_code_keeps_anchor_identifier_without_display_table(self):
        result = ground_query_plan(plan(
            "purchase", "purchase", operation="ranking",
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="supplier code", grouping=True)],
        ))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.PURCHASEORDER"])
        self.assertEqual(result.allowed_relationship_paths, [])
        self.assertEqual(
            [(column.full_table_name, column.column_name, column.role) for column in result.selected_columns],
            [("INVENTORY.PURCHASEORDER", "NET", "measure"), ("INVENTORY.PURCHASEORDER", "SUP_CODE", "grouping")],
        )

    def test_purchase_by_material_uses_only_verified_item_path(self):
        result = ground_query_plan(plan(
            "purchase", "purchase",
            entities=[EntityReference(concept="material", original_value="example", confidence=0.8, status=EntityStatus.UNRESOLVED)],
        ))
        self.assert_grounded(result)
        self.assertEqual([path.constraint_names for path in result.allowed_relationship_paths], [["ITEMCODE_INVITEMS-ITEMCODE"]])
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.INVITEMS", "INVENTORY.PURCHASEORDER"])

    def test_mrs_detail_by_department_code_uses_minimal_table(self):
        result = ground_query_plan(plan("mrs", "mrs", dimensions=[Dimension(concept="department", grouping=True)]))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        self.assertEqual([column.column_name for column in result.selected_columns], ["DEPT_CODE"])

    def test_consumption_material_aggregate_with_date_uses_verified_path(self):
        result = ground_query_plan(plan(
            "consumption", "consumption", operation="aggregate",
            measures=[Measure(concept="consumption quantity", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="material", grouping=True)], date_range=DATE_RANGE,
        ))
        self.assert_grounded(result)
        self.assertEqual([path.constraint_names for path in result.allowed_relationship_paths], [["FK_ISSUE"]])
        columns = {(column.full_table_name, column.column_name): column.role for column in result.selected_columns}
        self.assertEqual(
            {column_name for _, column_name in columns},
            {"CODE", "ISSUEDATE", "ITEM_CODE", "ITEM_NAME", "QTY"},
        )
        self.assertEqual(columns[("INVENTORY.INVITEMS", "ITEM_NAME")], "grouping")
        self.assertEqual(columns[("INVENTORY.ISSUE", "CODE")], "join_identifier")

    def test_supplier_lookup_needs_only_party_master(self):
        result = ground_query_plan(plan("purchase", "supplier", operation="lookup", dimensions=[Dimension(concept="supplier name", grouping=True)]))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["SCM.PARTYMASTER"])

    def test_material_lookup_needs_only_verified_item_master(self):
        result = ground_query_plan(plan(
            "purchase",
            "material",
            operation="lookup",
            dimensions=[Dimension(concept="material", grouping=True)],
        ))
        self.assert_grounded(result)
        self.assertEqual(
            [table.full_table_name for table in result.selected_tables],
            ["INVENTORY.INVITEMS"],
        )

    def test_unverified_department_name_path_is_rejected(self):
        result = ground_query_plan(plan("mrs", "mrs", dimensions=[Dimension(concept="department name", grouping=True)]))
        self.assertFalse(result.is_grounded)
        self.assertIn("no verified relationship path", result.reject_reasons[0].reason.lower())

    def test_unknown_measure_is_rejected(self):
        result = ground_query_plan(plan("purchase", "purchase", measures=[Measure(concept="margin", aggregation=Aggregation.SUM)]))
        self.assertFalse(result.is_grounded)
        self.assertIn("No verified V1 column", result.reject_reasons[0].reason)

    def test_unsupported_domain_is_rejected(self):
        result = ground_query_plan(plan("stock", "stock"))
        self.assertFalse(result.is_grounded)
        self.assertEqual(result.reject_reasons[0].requirement, "domain")
        self.assertEqual(result.selected_tables, [])

    def test_ambiguous_name_is_blocking(self):
        result = ground_query_plan(plan("purchase", "purchase", dimensions=[Dimension(concept="name", grouping=True)]))
        self.assertFalse(result.is_grounded)
        self.assertTrue(result.ambiguities[0].blocking)

    def test_only_catalog_tables_are_selected_and_no_external_clients_are_used(self):
        with patch("app.schema_grounding._load_inputs", wraps=__import__("app.schema_grounding", fromlist=["_load_inputs"])._load_inputs) as loader:
            result = ground_query_plan(plan("consumption", "consumption"))
        self.assert_grounded(result)
        self.assertEqual(loader.call_count, 1)
        self.assertTrue(all(table.full_table_name.startswith(("INVENTORY.", "SCM.")) for table in result.selected_tables))

    def test_mrs_number_grounds_without_join(self):
        result = ground_query_plan(plan(
            "mrs", "mrs",
            entities=[EntityReference(
                concept="mrs number", original_value="890330", selected_value="890330",
                confidence=0.9, status=EntityStatus.RESOLVED,
            )],
        ))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        columns = {(column.full_table_name, column.column_name): column for column in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "MRSNO"), columns)
        self.assertEqual(columns[("INVENTORY.MRS_TEMP", "MRSNO")].logical_concept, "mrs_number")

    def test_mrs_due_date_grounds_without_join(self):
        result = ground_query_plan(plan(
            "mrs", "mrs",
            dimensions=[Dimension(concept="mrs due date", grouping=False)],
            date_range=DATE_RANGE,
        ))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        columns = {(column.full_table_name, column.column_name): column for column in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "DUEDATE"), columns)
        self.assertEqual(columns[("INVENTORY.MRS_TEMP", "DUEDATE")].logical_concept, "mrs_due_date")
        # The plan's own date_range still resolves through the existing generic
        # mrs_date concept, independent of the new mrs_due_date concept.
        self.assertIn(("INVENTORY.MRS_TEMP", "MRSDATE"), columns)

    def test_mrs_rejection_reason_grounds_as_display_without_status_semantics(self):
        result = ground_query_plan(plan(
            "mrs", "mrs",
            dimensions=[Dimension(concept="rejection reason", grouping=False)],
        ))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        columns = {(column.full_table_name, column.column_name): column for column in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "REASONFORREJECTIONSTORES"), columns)
        self.assertEqual(columns[("INVENTORY.MRS_TEMP", "REASONFORREJECTIONSTORES")].logical_concept, "mrs_rejection_reason")
        # A generic catch-all "mrs status" is still not a catalogued concept;
        # only the specific bounded conditions below are supported.
        status_result = ground_query_plan(plan("mrs", "mrs", dimensions=[Dimension(concept="mrs status", grouping=True)]))
        self.assertFalse(status_result.is_grounded)

    def _entity_plan(self, concept):
        return plan(
            "mrs", "mrs",
            entities=[EntityReference(
                concept=concept, original_value="1", selected_value="1",
                confidence=0.9, status=EntityStatus.RESOLVED,
            )],
        )

    def test_mrs_hold_flag_grounds_without_join(self):
        result = ground_query_plan(self._entity_plan("mrs hold"))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        columns = {(c.full_table_name, c.column_name): c for c in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "HOLDINGSTATUS"), columns)
        self.assertEqual(columns[("INVENTORY.MRS_TEMP", "HOLDINGSTATUS")].logical_concept, "mrs_hold_flag")
        self.assertEqual(result.compound_conditions, [])

    def test_mrs_ready_for_approval_grounds_without_join(self):
        result = ground_query_plan(self._entity_plan("ready for approval"))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        columns = {(c.full_table_name, c.column_name): c for c in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "READYFORAPPROVAL"), columns)
        self.assertEqual(columns[("INVENTORY.MRS_TEMP", "READYFORAPPROVAL")].logical_concept, "mrs_ready_for_approval")

    def test_mrs_rejected_grounds_to_both_columns_with_or(self):
        result = ground_query_plan(self._entity_plan("mrs rejected"))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        self.assertEqual(len(result.compound_conditions), 1)
        condition = result.compound_conditions[0]
        self.assertEqual(condition.logical_concept, "mrs_rejected")
        self.assertEqual(condition.combinator, "OR")
        self.assertEqual(
            set(condition.columns),
            {"INVENTORY.MRS_TEMP.REJECTIONSTATUS", "INVENTORY.MRS_TEMP.STORESREJECTIONSTATUS"},
        )
        selected = {(c.full_table_name, c.column_name) for c in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "REJECTIONSTATUS"), selected)
        self.assertIn(("INVENTORY.MRS_TEMP", "STORESREJECTIONSTATUS"), selected)

    def test_mrs_approved_grounds_to_both_columns_with_and(self):
        result = ground_query_plan(self._entity_plan("mrs approved"))
        self.assert_grounded(result)
        self.assertEqual([table.full_table_name for table in result.selected_tables], ["INVENTORY.MRS_TEMP"])
        self.assertEqual(len(result.compound_conditions), 1)
        condition = result.compound_conditions[0]
        self.assertEqual(condition.logical_concept, "mrs_approved")
        self.assertEqual(condition.combinator, "AND")
        self.assertEqual(
            set(condition.columns),
            {"INVENTORY.MRS_TEMP.APPROVALSTATUS", "INVENTORY.MRS_TEMP.READYFORAPPROVAL"},
        )

    # -- P3: verified measure columns from the 2026-09-22 schema study --------

    def test_purchase_rate_grounds_as_measure(self):
        # "last purchase rate of barcode scanner in 2026" failed grounding on
        # `measure:rate`; PURCHASEORDER.RATE is NUMBER(14,4), never null.
        result = ground_query_plan(plan(
            "purchase", "purchase", operation="detail",
            measures=[Measure(concept="rate")],
            entities=[EntityReference(concept="material", original_value="barcode scanner")],
        ))
        self.assert_grounded(result)
        self.assertIn(("INVENTORY.PURCHASEORDER", "RATE", "measure"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

    def test_cost_consumed_grounds_to_issue_value(self):
        # "how much cost consumed last month?" -> ISSUE.ISSUEVALUE.
        result = ground_query_plan(plan(
            "consumption", "consumption", operation="aggregate",
            measures=[Measure(concept="cost consumed", aggregation=Aggregation.SUM)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
        ))
        self.assert_grounded(result)
        self.assertIn(("INVENTORY.ISSUE", "ISSUEVALUE", "measure"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

    def test_generic_value_grounds_to_the_plans_own_domain_not_purchase(self):
        # fix.md #10 root cause: "value" is bare-aliased only on purchase_value
        # in the catalog, so a consumption-domain plan asking for the generic
        # word "value" matched ONLY that one concept and silently grounded to
        # INVENTORY.PURCHASEORDER.NET -- no ambiguity, no rejection, is_grounded
        # True, wrong table, full confidence. The tie-break scoring in
        # ground_query_plan() never engaged because there was nothing to break
        # a tie with: consumption_value had no bare "value" alias to compete.
        result = ground_query_plan(plan(
            "consumption", "consumption", operation="aggregate",
            measures=[Measure(concept="value", aggregation=Aggregation.SUM)],
        ))
        self.assert_grounded(result)
        selected = {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns}
        self.assertIn(("INVENTORY.ISSUE", "ISSUEVALUE", "measure"), selected)
        self.assertNotIn(("INVENTORY.PURCHASEORDER", "NET", "measure"), selected)

    def test_generic_value_still_grounds_to_purchase_for_a_purchase_plan(self):
        # Same generic word, purchase domain: must keep resolving to
        # PURCHASEORDER.NET now that consumption_value also claims "value".
        result = ground_query_plan(plan(
            "purchase", "purchase", operation="aggregate",
            measures=[Measure(concept="value", aggregation=Aggregation.SUM)],
        ))
        self.assert_grounded(result)
        self.assertIn(("INVENTORY.PURCHASEORDER", "NET", "measure"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

    def test_generic_rate_grounds_to_the_plans_own_domain_not_purchase(self):
        # Same bug shape as "value", for "rate": consumption_rate had no bare
        # "rate" alias, so a consumption plan asking generically for "rate"
        # would have silently grounded to INVENTORY.PURCHASEORDER.RATE.
        result = ground_query_plan(plan(
            "consumption", "consumption", operation="aggregate",
            measures=[Measure(concept="rate", aggregation=Aggregation.AVERAGE)],
        ))
        self.assert_grounded(result)
        selected = {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns}
        self.assertIn(("INVENTORY.ISSUE", "ISSRATE", "measure"), selected)
        self.assertNotIn(("INVENTORY.PURCHASEORDER", "RATE", "measure"), selected)

    def test_lookup_shortcut_does_not_override_a_different_unsupported_domain(self):
        # fix.md #12: _domain() used to check operation=="lookup" + subject
        # in {material,item} BEFORE ever looking at plan.domain, so a plan
        # explicitly tagged domain="grn" (0-concept, unsupported -- "is
        # material X received?") still grounded as a plain material lookup,
        # silently discarding the only place "received" was ever recorded.
        result = ground_query_plan(plan("grn", "material", operation="lookup",
                                         entities=[EntityReference(concept="material", original_value="keyboard",
                                                                    confidence=0.9, status=EntityStatus.RESOLVED,
                                                                    selected_value="keyboard")]))
        self.assertFalse(result.is_grounded)
        self.assertIn("outside the V1 business schema catalog", result.reject_reasons[0].reason)

    def test_lookup_shortcut_still_applies_when_domain_is_generic(self):
        # Same operation+subject shape, but nothing claims a DIFFERENT
        # unsupported family -- must still take the shortcut (no regression).
        for domain in ("unknown", "material lookup", ""):
            with self.subTest(domain=domain):
                result = ground_query_plan(plan(domain, "material", operation="lookup",
                                                 entities=[EntityReference(concept="material", original_value="keyboard",
                                                                            confidence=0.9, status=EntityStatus.RESOLVED,
                                                                            selected_value="keyboard")]))
                self.assertTrue(result.is_grounded, result.reject_reasons)
                self.assertIn(("INVENTORY.INVITEMS", "ITEM_NAME", "entity_filter"),
                              {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

    def test_cost_consumed_last_month_no_longer_ties_on_date(self):
        # The originally observed symptom: with "value" wrongly grounding to
        # PURCHASEORDER first, selected_tables gained PURCHASEORDER, which
        # made the later date_range:date step score ORDERDATE and ISSUEDATE
        # equally and reject as AMBIGUOUS. Fixing the measure resolution
        # removes the pollution and the date filter now resolves outright.
        result = ground_query_plan(plan(
            "consumption", "consumption", operation="aggregate",
            measures=[Measure(concept="value", aggregation=Aggregation.SUM)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
        ))
        self.assert_grounded(result)
        self.assertEqual(result.ambiguities, [])
        self.assertIn(("INVENTORY.ISSUE", "ISSUEDATE", "date_filter"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

    def test_compound_condition_columns_are_catalog_verified(self):
        # _catalog_is_verified() already runs on every ground_query_plan()
        # call and raises RuntimeError if any column (including compound
        # ones) is not in the verified metadata; reaching a grounded result
        # at all is itself proof every compound column passed that check.
        result = ground_query_plan(self._entity_plan("mrs rejected"))
        self.assertTrue(result.is_grounded)


class CatalogDatatypeCategoryTests(unittest.TestCase):
    def test_catalog_datatype_categories_agree_with_metadata(self) -> None:
        # DATE_TEXT is only valid on a VARCHAR2 column and every date_filter
        # column must say which it is: the company DB stores business dates
        # as 'YYYYMMDD' text (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md §5).
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / "app" / "resources" / "business_schema_catalog.json").read_text())
        metadata = {
            (entry["full_table_name"], col["column_name"]): col["data_type"]
            for entry in json.loads((root / "data" / "multi_schema_metadata.json").read_text())
            for col in entry["columns"]
        }
        for concept in catalog["concepts"]:
            for col in concept.get("columns", []):
                key = (col["table"], col["column"])
                category = col.get("datatype_category")
                if "date_filter" in col["roles"]:
                    self.assertEqual(category, "DATE_TEXT", key)
                if category == "DATE_TEXT":
                    self.assertEqual(metadata[key], "VARCHAR2", key)
                if category == "DATE":
                    self.assertIn(metadata[key], ("DATE", "TIMESTAMP(6)"), key)


if __name__ == "__main__":
    unittest.main()
