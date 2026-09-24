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
        # "stock" is no longer this example (2026-09-23: it is now a
        # supported domain, fix.md #13) -- "attendance" was never
        # catalogued and has no profiled table.
        result = ground_query_plan(plan("attendance", "attendance"))
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

    def test_mrs_ready_for_approval_grounds_in_question_word_order(self):
        # fix.md #22: fix.md #17 verified the model fuses "approval pending at
        # Store officer" into one entity, concept="pending at store officer"
        # -- but never verified that string actually grounds. It didn't: the
        # catalog only had "store officer pending" (the reversed order), an
        # exact-match miss. Verified the model's real output word order
        # before adding this exact alias, not the reverse guess.
        result = ground_query_plan(plan(
            "mrs", "mrs",
            entities=[EntityReference(
                concept="pending at store officer", original_value="approval pending at Store officer",
                status=EntityStatus.NOT_REQUIRED,
            )],
        ))
        self.assert_grounded(result)
        columns = {(column.full_table_name, column.column_name): column for column in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "READYFORAPPROVAL"), columns)

    def test_material_dimension_grounds_directly_on_mrs_temp(self):
        # fix.md #22: "list out material approval pending at Store officer"
        # also needs `material` as a listed dimension -- the catalog only had
        # INVENTORY.INVITEMS.ITEM_NAME, which needs an (unverified) join from
        # the mrs domain anchor (MRS_TEMP). MRS_TEMP carries its own
        # denormalised ITEM_NAME (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md,
        # confirmed against the raw metadata: VARCHAR2(70) NULL) -- added as
        # a second column so this grounds without a join at all.
        result = ground_query_plan(plan(
            "mrs", "mrs",
            dimensions=[Dimension(concept="material", grouping=False)],
        ))
        self.assert_grounded(result)
        columns = {(column.full_table_name, column.column_name): column for column in result.selected_columns}
        self.assertIn(("INVENTORY.MRS_TEMP", "ITEM_NAME"), columns)

    def test_issue_number_grounds_without_join(self):
        # fix.md #21: found while hand-labeling real consumption questions for
        # a training/few-shot dataset -- "issue for issue number 737" named a
        # real, documented, NOT NULL column (ISSUE.ISSUENO, 177,627 distinct,
        # docs/ORACLE_SCHEMA_STUDY_2026-09-22.md) that had no catalog concept
        # at all, same class of gap as fix.md #18's grn_order_number.
        result = ground_query_plan(plan(
            "consumption", "consumption",
            entities=[EntityReference(
                concept="issue number", original_value="737", selected_value="737",
                confidence=0.9, status=EntityStatus.RESOLVED,
            )],
        ))
        self.assert_grounded(result)
        columns = {(column.full_table_name, column.column_name): column for column in result.selected_columns}
        self.assertIn(("INVENTORY.ISSUE", "ISSUENO"), columns)
        self.assertEqual(columns[("INVENTORY.ISSUE", "ISSUENO")].logical_concept, "issue_number")

    def test_grn_order_number_grounds_without_join(self):
        # 2026-09-24 held-out eval, fix.md #18: "grn for order 800151" failed
        # grounding entirely -- GRN.ORDERNO is real and documented
        # (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md: GRN.ORDERNO =
        # PURCHASEORDER.ORDERNO) but had no catalog concept at all. The real
        # model extracts this as the bare entity concept "order" (not "order
        # number"), so that bare word is included as an alias too.
        result = ground_query_plan(plan(
            "grn", "grn",
            entities=[EntityReference(
                concept="order", original_value="800151", selected_value="800151",
                confidence=0.9, status=EntityStatus.RESOLVED,
            )],
        ))
        self.assert_grounded(result)
        columns = {(column.full_table_name, column.column_name): column for column in result.selected_columns}
        self.assertIn(("INVENTORY.GRN", "ORDERNO"), columns)
        self.assertEqual(columns[("INVENTORY.GRN", "ORDERNO")].logical_concept, "grn_order_number")

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

    def test_stock_quantity_sums_across_mill_hod_rows(self):
        # fix.md #13: ITEMSTOCK holds 1-31 rows per item; the concept is
        # deliberately measure-only (never a display/grouping column) so a
        # non-aggregated read can never look like a valid single answer.
        result = ground_query_plan(plan(
            "stock", "stock", operation="aggregate",
            measures=[Measure(concept="stock quantity", aggregation=Aggregation.SUM)],
            entities=[EntityReference(concept="material", original_value="keyboard", confidence=0.8,
                                       status=EntityStatus.UNRESOLVED)],
        ))
        self.assert_grounded(result)
        self.assertIn(("INVENTORY.ITEMSTOCK", "STOCK", "measure"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

    def test_quantity_received_business_subject_matches_the_grn_domain(self):
        # Live Step 6 finding (2026-09-23, qwen3:14b, "how many qty received?"
        # and "...in last one year?"): business_subject="quantity received"
        # matched no domain alias ("grn"'s own aliases had "receipt" but not
        # this exact phrase) and no concept with role=identifier, so the
        # plan was rejected before the measure was ever reached. Added the
        # exact phrase (and its reverse word order) as grn domain aliases,
        # mirroring the "cost"/consumption domain-alias fix earlier the same
        # day. The plan should now fail only on the measure's own, separately
        # fixed, honest 3-way ambiguity -- not on business_subject.
        result = ground_query_plan(plan(
            "grn", "quantity received", operation="aggregate",
            measures=[Measure(concept="quantity", aggregation=Aggregation.SUM)],
        ))
        self.assertFalse(result.is_grounded)
        self.assertEqual(result.reject_reasons, [])
        self.assertEqual(len(result.ambiguities), 1)
        self.assertEqual(result.ambiguities[0].requirement, "measure:quantity")

    def test_generic_quantity_is_grn_ambiguous_not_a_silent_purchase_leak(self):
        # Live Step 6 finding (2026-09-23, qwen3:14b, "how many qty received?"):
        # received/pending/rejected_receipt_quantity had no bare "quantity"/
        # "qty" alias (unlike purchase/mrs/consumption_quantity, which all
        # three share it), so a GRN plan's generic "quantity" measure matched
        # ONLY those three foreign concepts and silently grounded to
        # INVENTORY.PURCHASEORDER.QTY -- is_grounded=True, no warning, for a
        # question about goods receipt. Fixed by adding the same bare
        # aliases to all three GRN quantity concepts; the correct outcome is
        # an honest ambiguity naming GRN's own three quantities, not a
        # silent cross-domain answer.
        result = ground_query_plan(plan(
            "grn", "grn", operation="aggregate",
            measures=[Measure(concept="quantity", aggregation=Aggregation.SUM)],
        ))
        self.assertFalse(result.is_grounded)
        self.assertEqual(len(result.ambiguities), 1)
        self.assertEqual(
            set(result.ambiguities[0].candidates),
            {"INVENTORY.GRN.GRNQTY", "INVENTORY.GRN.PENDING", "INVENTORY.GRN.REJQTY"},
        )
        self.assertNotIn(("INVENTORY.PURCHASEORDER", "QTY", "measure"),
                          {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

    def test_grn_received_and_pending_quantity_ground_to_grn_table(self):
        result = ground_query_plan(plan(
            "grn", "grn", operation="aggregate",
            measures=[Measure(concept="received quantity", aggregation=Aggregation.SUM)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last one year"),
        ))
        self.assert_grounded(result)
        self.assertIn(("INVENTORY.GRN", "GRNQTY", "measure"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})
        self.assertIn(("INVENTORY.GRN", "GRNDATE", "date_filter"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

        pending = ground_query_plan(plan(
            "grn", "grn", operation="aggregate",
            measures=[Measure(concept="pending receipt quantity", aggregation=Aggregation.SUM)],
        ))
        self.assert_grounded(pending)
        self.assertIn(("INVENTORY.GRN", "PENDING", "measure"),
                      {(c.full_table_name, c.column_name, c.role) for c in pending.selected_columns})

    def test_grn_joins_to_material_and_supplier_via_the_verified_fks(self):
        # FK_GRN (GRN.CODE -> INVITEMS.ITEM_CODE) and FK_GRN_SUPCODE
        # (GRN.SUP_CODE -> PARTYMASTER.PARTYCODE) already existed in
        # data/schema_relationships.json before this catalog work -- adding
        # them to the catalog's own relationships list only had to match,
        # never invent, a join.
        by_material = ground_query_plan(plan(
            "grn", "grn", operation="detail",
            measures=[Measure(concept="received quantity")],
            entities=[EntityReference(concept="material", original_value="keyboard", confidence=0.8,
                                       status=EntityStatus.UNRESOLVED)],
        ))
        self.assert_grounded(by_material)
        self.assertIn("INVENTORY.INVITEMS", {t.full_table_name for t in by_material.selected_tables})

        by_supplier = ground_query_plan(plan(
            "grn", "grn", operation="detail",
            measures=[Measure(concept="received quantity")],
            entities=[EntityReference(concept="supplier_name", original_value="Prime Compu Systems",
                                       confidence=0.8, status=EntityStatus.UNRESOLVED)],
        ))
        self.assert_grounded(by_supplier)
        self.assertIn("SCM.PARTYMASTER", {t.full_table_name for t in by_supplier.selected_tables})

    def test_generic_cost_measure_and_subject_ground_on_consumption(self):
        # Live Step 6 finding (2026-09-23, qwen3:14b): the model used the
        # bare word "cost" for BOTH business_subject and measure -- neither
        # matched anything ("cost consumed"/"consumption cost" are two-word
        # aliases, and "cost" is not a consumption domain alias either).
        result = ground_query_plan(plan(
            "consumption", "cost", operation="aggregate",
            measures=[Measure(concept="cost", aggregation=Aggregation.SUM)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
        ))
        self.assert_grounded(result)
        self.assertIn(("INVENTORY.ISSUE", "ISSUEVALUE", "measure"),
                      {(c.full_table_name, c.column_name, c.role) for c in result.selected_columns})

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
        # explicitly tagged domain="grn" -- "is material X received?" --
        # still grounded as a plain material lookup, silently discarding the
        # only place "received" was ever recorded.
        # 2026-09-23: grn became a real, supported domain (fix.md #13), so
        # the exclusion now surfaces as "business_subject:material has no
        # verified column" rather than "domain not in the catalog at all" --
        # the exclusion from the lookup shortcut is still what's proven here
        # (grn is genuinely in catalog["domains"] now; if the shortcut had
        # fired instead, this would ground successfully as material_lookup).
        result = ground_query_plan(plan("grn", "material", operation="lookup",
                                         entities=[EntityReference(concept="material", original_value="keyboard",
                                                                    confidence=0.9, status=EntityStatus.RESOLVED,
                                                                    selected_value="keyboard")]))
        self.assertFalse(result.is_grounded)
        self.assertEqual(result.reject_reasons[0].requirement, "business_subject:material")

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

    def _po_entity_plan(self, concept):
        return plan(
            "purchase", "purchase",
            entities=[EntityReference(
                concept=concept, original_value="1", selected_value="1",
                confidence=0.9, status=EntityStatus.RESOLVED,
            )],
        )

    def test_po_pending_at_so_pins_all_three_flags_to_zero(self):
        result = ground_query_plan(self._po_entity_plan("pending at so"))
        self.assert_grounded(result)
        condition = result.compound_conditions[0]
        self.assertEqual(condition.combinator, "AND")
        self.assertEqual(condition.pinned_values, {
            "INVENTORY.PURCHASEORDER.SOORDERAPPROVAL": 0,
            "INVENTORY.PURCHASEORDER.IAORDERAPPROVAL": 0,
            "INVENTORY.PURCHASEORDER.JMDORDERAPPROVAL": 0,
        })

    def test_po_pending_at_ia_deliberately_omits_jmd(self):
        # docs/ORACLE_SCHEMA_STUDY_2026-09-22.md §6.1 states this row as
        # SO=1,IA=0 only and never restates JMD -- pinning JMD would assert
        # an unverified fact, so the catalog concept only has two columns.
        result = ground_query_plan(self._po_entity_plan("pending at ia"))
        self.assert_grounded(result)
        condition = result.compound_conditions[0]
        self.assertEqual(condition.pinned_values, {
            "INVENTORY.PURCHASEORDER.SOORDERAPPROVAL": 1,
            "INVENTORY.PURCHASEORDER.IAORDERAPPROVAL": 0,
        })
        self.assertNotIn("INVENTORY.PURCHASEORDER.JMDORDERAPPROVAL", condition.pinned_values)

    def test_po_approved_pins_all_three_flags_to_one(self):
        result = ground_query_plan(self._po_entity_plan("po approved"))
        self.assert_grounded(result)
        condition = result.compound_conditions[0]
        self.assertEqual(condition.combinator, "AND")
        self.assertEqual(set(condition.pinned_values.values()), {1})
        self.assertEqual(len(condition.pinned_values), 3)

    def test_po_pending_any_stage_is_an_or_of_zero_pins(self):
        result = ground_query_plan(self._po_entity_plan("po pending"))
        self.assert_grounded(result)
        condition = result.compound_conditions[0]
        self.assertEqual(condition.combinator, "OR")
        self.assertEqual(set(condition.pinned_values.values()), {0})
        self.assertEqual(len(condition.pinned_values), 3)

    def test_po_approved_and_mrs_approved_do_not_collide_on_a_bare_alias(self):
        # Both concepts could plausibly claim a bare "approved" alias;
        # po_approved deliberately does not, so a purchase-domain plan asking
        # for "po approved" is not at the mercy of concept array order.
        result = ground_query_plan(self._po_entity_plan("approved"))
        self.assertFalse(result.is_grounded)

    def test_mrs_pending_grounds_flag_and_value_or_null_and_anti_join(self):
        result = ground_query_plan(self._entity_plan("mrs pending"))
        self.assert_grounded(result)
        condition = result.compound_conditions[0]
        self.assertEqual(condition.logical_concept, "mrs_pending")
        self.assertEqual(condition.combinator, "AND")
        self.assertEqual(condition.pinned_values, {
            "INVENTORY.MRS_TEMP.REJECTIONSTATUS": 0,
            "INVENTORY.MRS_TEMP.STORESREJECTIONSTATUS": 0,
            "INVENTORY.MRS_TEMP.ITEMDELETE": 0,
            "INVENTORY.MRS_TEMP.ISDELETE": 0,
            "INVENTORY.MRS_TEMP.MRSFLAG": 1,
        })
        self.assertEqual(condition.value_or_null, {"INVENTORY.MRS_TEMP.MILLCODE": 0})

        self.assertEqual(len(result.anti_join_conditions), 1)
        anti_join = result.anti_join_conditions[0]
        self.assertEqual(anti_join.logical_concept, "mrs_pending")
        self.assertEqual(anti_join.from_table, "INVENTORY.MRS_TEMP")
        self.assertEqual(anti_join.from_columns, ["MRSNO", "SLNO"])
        self.assertEqual(anti_join.to_table, "INVENTORY.MRS")
        self.assertEqual(anti_join.to_columns, ["MRSNO", "SLNO"])
        self.assertEqual(anti_join.null_check_column, "ORDERNO")
        self.assertIn("INVENTORY.MRS", {table.full_table_name for table in result.selected_tables})


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
