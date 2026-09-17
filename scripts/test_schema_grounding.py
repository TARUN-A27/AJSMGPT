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


if __name__ == "__main__":
    unittest.main()
