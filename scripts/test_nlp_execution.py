from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException

from app import nlp_router
from app.grounded_sql_generator import GroundedSqlResult
from app.grounded_sql_validator import GroundedSqlValidationError
from app.nlp_execution import (
    ExecutionRejectedError,
    ExecutionResultError,
    NLPExecutionDependencies,
    ParameterBindingError,
    ReportType,
    UnsupportedResultValueError,
    _normalise_result,
    build_bind_parameters,
    date_bounds,
    execute_nlp_query,
    relative_date_spec,
)
from app.query_plan import (
    Aggregation,
    Ambiguity,
    BusinessSubject,
    DateRange,
    DateRangeKind,
    Dimension,
    EntityReference,
    EntityStatus,
    FilterOperator,
    Measure,
    QueryFilter,
    QueryPlan,
    RequestedOutput,
    SortDirection,
    SortInstruction,
)
from app.schema_grounding import ground_query_plan


class RecordingRunner:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = result or {"columns": ["SUPPLIER", "TOTAL_PURCHASE_VALUE"], "rows": [["ACME", Decimal("12.50")]], "elapsed_ms": 4}
        self.error = error

    def __call__(self, sql: str, binds: dict):
        self.calls.append((sql, dict(binds)))
        if self.error:
            raise self.error
        return self.result


def ranking_plan(period: str = "last 30 days") -> QueryPlan:
    return QueryPlan(
        original_question=f"top 10 suppliers by purchase value {period}",
        domain="purchase",
        operation="ranking",
        business_subject=BusinessSubject(concept="purchase"),
        measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
        dimensions=[Dimension(concept="supplier", grouping=True)],
        date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text=period),
        sorting=[SortInstruction(field_concept="purchase value", direction=SortDirection.DESC, priority=0)],
        limit=10,
        requested_output=RequestedOutput(fields=["supplier", "purchase value"]),
        confidence=0.95,
    )


def ranking_sql(period: str = "last 30 days") -> str:
    # Date bounds are two binds; nlp_execution.date_bounds supplies 'YYYYMMDD' values.
    return """SELECT pm.PARTYNAME AS supplier, SUM(po.NET) AS total_purchase_value
FROM INVENTORY.PURCHASEORDER po
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
GROUP BY pm.PARTYNAME
ORDER BY total_purchase_value DESC"""


def preview(sql: str) -> GroundedSqlResult:
    return GroundedSqlResult(
        sql=sql,
        selected_fields=["model supplied value is ignored"],
        applied_filters=[],
        assumptions=[],
        confidence=0.9,
    )


def dependencies(
    plan: QueryPlan, sql: str, runner: RecordingRunner, *, resolve_entities=None,
) -> NLPExecutionDependencies:
    """Test fixture. `resolve_entities` defaults to a pass-through identity
    stub, so existing tests that hand-author an already-RESOLVED entity keep
    testing what they were designed to test (bind-mapping, SQL safety, ...)
    in isolation from resolution mechanics -- resolution itself is covered
    by scripts/test_entity_resolution.py and the resolver-integration tests
    below. Production code never uses this helper: NLPExecutionDependencies'
    own default (`_default_resolve_entities`) is the real, Oracle-backed
    resolver, and is what actually enforces that the model cannot bypass it.
    """
    return NLPExecutionDependencies(
        extract_plan=lambda *args, **kwargs: plan,
        resolve_entities=resolve_entities or (lambda p: p),
        ground_plan=ground_query_plan,
        generate_sql=lambda *args, **kwargs: preview(sql),
        runner=runner,
    )


class NLPExecutionTests(unittest.TestCase):
    def test_successful_purchase_ranking_executes_once(self) -> None:
        plan = ranking_plan()
        runner = RecordingRunner()
        result = execute_nlp_query(
            plan.original_question,
            dependencies=dependencies(plan, ranking_sql(), runner),
            include_sql_preview=True,
        )
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(result.report_type, ReportType.RANKING)
        self.assertEqual(result.rows, [["ACME", "12.50"]])
        self.assertEqual(result.selected_fields, ["supplier", "total_purchase_value"])
        binds = runner.calls[0][1]
        self.assertRegex(binds["date_start"], r"^\d{8}$")
        self.assertRegex(binds["date_end"], r"^\d{8}$")
        self.assertLess(binds["date_start"], binds["date_end"])

    def test_six_month_predicate_executes_once(self) -> None:
        plan = ranking_plan("last 6 months")
        runner = RecordingRunner()
        execute_nlp_query(
            plan.original_question,
            dependencies=dependencies(plan, ranking_sql("last 6 months"), runner),
        )
        self.assertEqual(len(runner.calls), 1)
        self.assertNotIn("SYSDATE", runner.calls[0][0])
        self.assertEqual(sorted(runner.calls[0][1]), ["date_end", "date_start"])

    def test_aggregate_report_states_returned_value(self) -> None:
        plan = QueryPlan(
            original_question="total purchase value",
            domain="purchase",
            operation="aggregate",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            requested_output=RequestedOutput(fields=["purchase value"]),
            confidence=0.9,
        )
        sql = "SELECT SUM(po.NET) AS total_purchase_value FROM INVENTORY.PURCHASEORDER po"
        runner = RecordingRunner({"columns": ["TOTAL_PURCHASE_VALUE"], "rows": [[Decimal("99.25")]]})
        result = execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, runner))
        self.assertEqual(result.report_type, ReportType.AGGREGATE)
        self.assertEqual(result.summary, "Aggregate result: TOTAL_PURCHASE_VALUE=99.25.")

    def test_detail_and_empty_reports(self) -> None:
        plan = QueryPlan(
            original_question="purchase quantities",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity", aggregation=Aggregation.NONE)],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        sql = "SELECT po.QTY AS purchase_quantity FROM INVENTORY.PURCHASEORDER po"
        detail_runner = RecordingRunner({"columns": ["PURCHASE_QUANTITY"], "rows": [[5], [7]]})
        detail = execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, detail_runner))
        self.assertEqual(detail.report_type, ReportType.DETAIL)
        self.assertEqual(detail.summary, "Returned 2 detail record(s).")

        empty_runner = RecordingRunner({"columns": ["PURCHASE_QUANTITY"], "rows": []})
        empty = execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, empty_runner))
        self.assertEqual(empty.report_type, ReportType.EMPTY)
        self.assertEqual(empty.summary, "No matching records were returned.")

    def test_json_safe_serialization_and_unsupported_value(self) -> None:
        result = _normalise_result(
            {
                "columns": ["DECIMAL_VALUE", "DATE_VALUE", "DATETIME_VALUE", "NULL_VALUE"],
                "rows": [[
                    Decimal("123.4500"),
                    date(2026, 9, 18),
                    datetime(2026, 9, 18, 12, 30, tzinfo=timezone.utc),
                    None,
                ]],
            },
            row_limit=10,
            limit_source="service_default",
            fallback_elapsed_ms=0,
        )
        self.assertEqual(
            result.rows[0],
            ["123.4500", "2026-09-18", "2026-09-18T12:30:00+00:00", None],
        )
        with self.assertRaises(UnsupportedResultValueError):
            _normalise_result(
                {"columns": ["OPAQUE"], "rows": [[object()]]},
                row_limit=10,
                limit_source="service_default",
                fallback_elapsed_ms=0,
            )

    def test_truncation_uses_one_extra_row_without_second_call(self) -> None:
        plan = QueryPlan(
            original_question="purchase quantities",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        sql = "SELECT po.QTY AS purchase_quantity FROM INVENTORY.PURCHASEORDER po"
        runner = RecordingRunner({"columns": ["PURCHASE_QUANTITY"], "rows": [[1], [2], [3]]})
        result = execute_nlp_query(
            plan.original_question,
            dependencies=dependencies(plan, sql, runner),
            max_rows=2,
        )
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("WHERE ROWNUM <= 3", runner.calls[0][0])
        self.assertEqual(result.rows, [[1], [2]])
        self.assertTrue(result.truncated)

    def test_runner_receives_validated_sql_wrapped_only_by_rownum(self) -> None:
        # P1: the model's SQL is validated as written; the only thing added
        # before execution is the deterministic ROWNUM wrapper carrying the
        # plan's own limit (Oracle 11.2 has no FETCH FIRST).
        plan = ranking_plan()
        runner = RecordingRunner()
        execute_nlp_query(plan.original_question, dependencies=dependencies(plan, ranking_sql(), runner))
        executed = " ".join(runner.calls[0][0].split())
        self.assertIn(" ".join(ranking_sql().split()), executed)
        self.assertRegex(executed, r"(?i)^SELECT \* FROM \( SELECT .* \) WHERE ROWNUM <= 10$")
        self.assertNotIn("FETCH", executed)

    def test_model_limit_syntax_executes_zero_times(self) -> None:
        plan = ranking_plan()
        runner = RecordingRunner()
        with self.assertRaises(GroundedSqlValidationError):
            execute_nlp_query(
                plan.original_question,
                dependencies=dependencies(plan, ranking_sql() + "\nFETCH FIRST 10 ROWS ONLY", runner),
            )
        self.assertEqual(runner.calls, [])

    def test_clarification_and_rejected_grounding_execute_zero_times(self) -> None:
        ambiguous = ranking_plan().model_copy(update={
            "ambiguities": [Ambiguity(field="supplier", reason="Which supplier?", blocking=True)]
        })
        runner = RecordingRunner()
        with self.assertRaises(ExecutionRejectedError):
            execute_nlp_query(
                ambiguous.original_question,
                dependencies=dependencies(ambiguous, ranking_sql(), runner),
            )
        self.assertEqual(runner.calls, [])

        ungrounded = ranking_plan().model_copy(update={
            "measures": [Measure(concept="margin", aggregation=Aggregation.SUM)]
        })
        with self.assertRaises(ExecutionRejectedError):
            execute_nlp_query(
                ungrounded.original_question,
                dependencies=dependencies(ungrounded, ranking_sql(), runner),
            )
        self.assertEqual(runner.calls, [])

    def test_unsafe_sql_and_missing_bind_execute_zero_times(self) -> None:
        plan = ranking_plan()
        runner = RecordingRunner()
        with self.assertRaises(Exception):
            execute_nlp_query(
                plan.original_question,
                dependencies=dependencies(plan, ranking_sql() + "; DELETE FROM X", runner),
            )
        self.assertEqual(runner.calls, [])

        entity_plan = QueryPlan(
            original_question="purchase quantity for supplier ABC",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            entities=[EntityReference(
                concept="supplier",
                selected_value="ABC",
                confidence=0.95,
                status=EntityStatus.RESOLVED,
            )],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        missing_bind_sql = "SELECT po.QTY AS purchase_quantity FROM INVENTORY.PURCHASEORDER po WHERE po.SUP_CODE IS NOT NULL"
        with self.assertRaises(Exception):
            execute_nlp_query(
                entity_plan.original_question,
                dependencies=dependencies(entity_plan, missing_bind_sql, runner),
            )
        self.assertEqual(runner.calls, [])

    def test_entity_value_is_passed_only_as_bind(self) -> None:
        plan = QueryPlan(
            original_question="purchase quantity for supplier ABC",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            entities=[EntityReference(
                concept="supplier",
                selected_value="ABC",
                confidence=0.95,
                status=EntityStatus.RESOLVED,
            )],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        sql = "SELECT po.QTY AS purchase_quantity FROM INVENTORY.PURCHASEORDER po WHERE po.SUP_CODE = :supplier_code"
        runner = RecordingRunner({"columns": ["PURCHASE_QUANTITY"], "rows": [[4]]})
        execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, runner))
        executed_sql, binds = runner.calls[0]
        self.assertNotIn("ABC", executed_sql)
        self.assertEqual(binds, {"supplier_code": "ABC"})

    # -- entity-resolution integration (Phase 5) -----------------------------
    # These prove the *wiring*: execute_nlp_query trusts deps.resolve_entities'
    # output, not whatever extract_plan produced. Resolution's own matching
    # logic (exact/normalized/ambiguous/not-found) is proven separately and
    # offline in scripts/test_entity_resolution.py.

    def _one_entity_plan(self) -> QueryPlan:
        return QueryPlan(
            original_question="purchase quantity for supplier ABC",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            entities=[EntityReference(concept="supplier", original_value="ABC")],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )

    def test_unresolved_entity_after_resolution_executes_zero_times(self) -> None:
        plan = self._one_entity_plan()
        runner = RecordingRunner()
        resolve_entities = lambda p: p.model_copy(update={"entities": [
            EntityReference(concept="supplier", original_value="ABC", status=EntityStatus.UNRESOLVED),
        ]})
        with self.assertRaises(ExecutionRejectedError):
            execute_nlp_query(
                plan.original_question,
                dependencies=dependencies(plan, "SELECT 1 FROM DUAL", runner, resolve_entities=resolve_entities),
            )
        self.assertEqual(runner.calls, [])

    def test_ambiguous_entity_after_resolution_executes_zero_times(self) -> None:
        plan = self._one_entity_plan()
        runner = RecordingRunner()
        resolve_entities = lambda p: p.model_copy(update={"entities": [
            EntityReference(
                concept="supplier", original_value="ABC", status=EntityStatus.AMBIGUOUS,
                candidates=["ABC Textiles", "ABC Trading Co"],
            ),
        ]})
        with self.assertRaises(ExecutionRejectedError) as ctx:
            execute_nlp_query(
                plan.original_question,
                dependencies=dependencies(plan, "SELECT 1 FROM DUAL", runner, resolve_entities=resolve_entities),
            )
        self.assertEqual(runner.calls, [])
        # P4: the clarification names the verified candidates so the user can pick.
        self.assertIn("Candidates: ABC Textiles; ABC Trading Co.", "; ".join(ctx.exception.response.ambiguities))

    def test_unresolved_entity_with_a_fuzzy_hint_still_names_the_candidate(self) -> None:
        # fix.md #2: a single fuzzy-fallback match stays UNRESOLVED (never
        # auto-picked) but should still make the refusal actionable instead
        # of a dead end, the same as AMBIGUOUS's candidates already do.
        plan = self._one_entity_plan()
        runner = RecordingRunner()
        resolve_entities = lambda p: p.model_copy(update={"entities": [
            EntityReference(
                concept="supplier", original_value="galaxy", status=EntityStatus.UNRESOLVED,
                candidates=["THE GALAXY [800968]"],
            ),
        ]})
        with self.assertRaises(ExecutionRejectedError) as ctx:
            execute_nlp_query(
                plan.original_question,
                dependencies=dependencies(plan, "SELECT 1 FROM DUAL", runner, resolve_entities=resolve_entities),
            )
        self.assertEqual(runner.calls, [])
        self.assertIn("Candidates: THE GALAXY [800968].", "; ".join(ctx.exception.response.ambiguities))

    def test_resolver_output_overrides_a_model_claimed_resolved_entity(self) -> None:
        # extract_plan hands back an entity the model itself already marked
        # RESOLVED with a fabricated value; the resolver stub represents what
        # the real resolver would do when that value doesn't verify. Proves
        # execute_nlp_query gates on deps.resolve_entities' output, not on
        # whatever status/selected_value extraction produced.
        plan = QueryPlan(
            original_question="purchase quantity for supplier Totally Fake Co",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            entities=[EntityReference(
                concept="supplier", original_value="Totally Fake Co",
                selected_value="Totally Fake Co", status=EntityStatus.RESOLVED, confidence=0.9,
            )],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        runner = RecordingRunner()
        resolve_entities = lambda p: p.model_copy(update={"entities": [
            EntityReference(concept="supplier", original_value="Totally Fake Co", status=EntityStatus.UNRESOLVED),
        ]})
        with self.assertRaises(ExecutionRejectedError):
            execute_nlp_query(
                plan.original_question,
                dependencies=dependencies(plan, "SELECT 1 FROM DUAL", runner, resolve_entities=resolve_entities),
            )
        self.assertEqual(runner.calls, [])

    def test_mixed_resolution_one_unresolved_blocks_execution(self) -> None:
        plan = QueryPlan(
            original_question="purchase quantity for keyboard from supplier ABC",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            entities=[
                EntityReference(concept="material", original_value="keyboard"),
                EntityReference(concept="supplier", original_value="ABC"),
            ],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        runner = RecordingRunner()
        resolve_entities = lambda p: p.model_copy(update={"entities": [
            EntityReference(concept="material", original_value="keyboard", selected_value="KEYBOARD", status=EntityStatus.RESOLVED),
            EntityReference(concept="supplier", original_value="ABC", status=EntityStatus.UNRESOLVED),
        ]})
        with self.assertRaises(ExecutionRejectedError):
            execute_nlp_query(
                plan.original_question,
                dependencies=dependencies(plan, "SELECT 1 FROM DUAL", runner, resolve_entities=resolve_entities),
            )
        self.assertEqual(runner.calls, [])

    def test_successful_resolution_of_multiple_entities_still_executes_once(self) -> None:
        plan = QueryPlan(
            original_question="purchase quantity for keyboard from supplier ABC",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            entities=[
                EntityReference(concept="material", original_value="keyboard"),
                EntityReference(concept="supplier", original_value="ABC"),
            ],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        sql = (
            "SELECT po.QTY AS purchase_quantity FROM INVENTORY.PURCHASEORDER po "
            "JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE "
            "WHERE items.ITEM_NAME = :material_name AND po.SUP_CODE = :supplier_code"
        )
        runner = RecordingRunner({"columns": ["PURCHASE_QUANTITY"], "rows": [[4]]})
        resolve_entities = lambda p: p.model_copy(update={"entities": [
            EntityReference(concept="material", original_value="keyboard", selected_value="KEYBOARD", status=EntityStatus.RESOLVED),
            EntityReference(concept="supplier", original_value="ABC", selected_value="ABC Textiles", status=EntityStatus.RESOLVED),
        ]})
        execute_nlp_query(
            plan.original_question,
            dependencies=dependencies(plan, sql, runner, resolve_entities=resolve_entities),
        )
        self.assertEqual(len(runner.calls), 1)
        executed_sql, binds = runner.calls[0]
        self.assertEqual(binds, {"material_name": "KEYBOARD", "supplier_code": "ABC Textiles"})

    # -- entity-resolution bypass via QueryFilter (Phase 10 Finding 1) ------
    # resolve_plan_entities and the unresolved-entity gate above only ever
    # look at plan.entities. A resolvable concept (supplier/material) placed
    # in plan.filters instead of plan.entities would otherwise reach Oracle
    # with its raw, never-verified value. build_bind_parameters must refuse
    # this independently of how the plan was shaped.

    def test_supplier_filter_bypassing_entity_resolution_is_rejected(self) -> None:
        plan = QueryPlan(
            original_question="show supplier Totally Fake Supplier Inc",
            domain="supplier_lookup",
            operation="lookup",
            business_subject=BusinessSubject(concept="supplier"),
            filters=[QueryFilter(
                concept="supplier_name", operator=FilterOperator.EQUALS,
                value="Totally Fake Supplier Inc", value_type="text",
            )],
            requested_output=RequestedOutput(fields=["supplier_name"]),
            confidence=0.95,
        )
        sql = "SELECT pm.PARTYNAME AS supplier_name FROM SCM.PARTYMASTER pm WHERE pm.PARTYNAME = :party_name"
        runner = RecordingRunner()
        with self.assertRaises(ParameterBindingError):
            execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, runner))
        self.assertEqual(runner.calls, [])

    def test_material_filter_bypassing_entity_resolution_is_rejected(self) -> None:
        plan = QueryPlan(
            original_question="purchases of material Totally Fake Widget",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity")],
            filters=[QueryFilter(
                concept="material", operator=FilterOperator.EQUALS,
                value="Totally Fake Widget", value_type="text",
            )],
            requested_output=RequestedOutput(fields=["purchase quantity"]),
            confidence=0.9,
        )
        sql = (
            "SELECT po.QTY AS purchase_quantity FROM INVENTORY.PURCHASEORDER po "
            "JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE "
            "WHERE items.ITEM_NAME = :material_name"
        )
        runner = RecordingRunner()
        with self.assertRaises(ParameterBindingError):
            execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, runner))
        self.assertEqual(runner.calls, [])

    def test_entity_free_question_never_touches_the_resolver_or_oracle(self) -> None:
        # Uses the REAL production default (resolve_entities not stubbed) to
        # prove an entity-free plan never reaches oracle_entity_lookup at
        # all -- resolve_plan_entities short-circuits before any lookup.
        plan = ranking_plan()
        runner = RecordingRunner()
        deps = NLPExecutionDependencies(
            extract_plan=lambda *args, **kwargs: plan,
            ground_plan=ground_query_plan,
            generate_sql=lambda *args, **kwargs: preview(ranking_sql()),
            runner=runner,
        )
        result = execute_nlp_query(plan.original_question, dependencies=deps)
        self.assertEqual(len(runner.calls), 1)
        self.assertTrue(result.success)

    def test_absolute_date_binds_follow_predicate_role_not_text_order(self) -> None:
        plan = QueryPlan(
            original_question="purchase value in 2025",
            domain="purchase",
            operation="aggregate",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            date_range=DateRange(
                kind=DateRangeKind.ABSOLUTE,
                start="2025-01-01",
                end="2025-12-31",
                inclusive_start=True,
                inclusive_end=True,
                original_text="in 2025",
            ),
            requested_output=RequestedOutput(fields=["purchase value"]),
            confidence=0.9,
        )
        sql = """SELECT SUM(po.NET) AS total_purchase_value
FROM INVENTORY.PURCHASEORDER po
WHERE po.ORDERDATE < :end_date AND po.ORDERDATE >= :start_date"""
        runner = RecordingRunner({"columns": ["TOTAL_PURCHASE_VALUE"], "rows": [[1]]})
        execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, runner))
        # inclusive 2025-12-31 -> exclusive bound 20260101; values are 'YYYYMMDD' strings
        self.assertEqual(runner.calls[0][1], {"start_date": "20250101", "end_date": "20260101"})

    def test_reversed_absolute_date_range_executes_zero_times(self) -> None:
        plan = QueryPlan(
            original_question="purchase value in reversed dates",
            domain="purchase",
            operation="aggregate",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            date_range=DateRange(
                kind=DateRangeKind.ABSOLUTE,
                start="2025-12-31",
                end="2025-01-01",
                original_text="from 2025-12-31 to 2025-01-01",
            ),
            confidence=0.9,
        )
        sql = """SELECT SUM(po.NET) AS total_purchase_value
FROM INVENTORY.PURCHASEORDER po
WHERE po.ORDERDATE >= :start_date AND po.ORDERDATE < :end_date"""
        runner = RecordingRunner({"columns": ["TOTAL_PURCHASE_VALUE"], "rows": [[1]]})
        with self.assertRaises(Exception):
            execute_nlp_query(plan.original_question, dependencies=dependencies(plan, sql, runner))
        self.assertEqual(runner.calls, [])

    # -- date_bounds: deterministic 'YYYYMMDD' half-open bounds (P2) ---------

    def test_relative_month_bounds_use_calendar_months(self) -> None:
        # Oracle ADD_MONTHS semantics: month-end maps to month-end, otherwise clamp.
        six = DateRange(kind=DateRangeKind.RELATIVE, original_text="last 6 months")
        self.assertEqual(date_bounds(six, today=date(2026, 9, 22)), ("20260322", "20260923"))
        one = DateRange(kind=DateRangeKind.RELATIVE, original_text="last month")
        self.assertEqual(date_bounds(one, today=date(2026, 3, 31)), ("20260228", "20260401"))
        self.assertEqual(date_bounds(one, today=date(2026, 5, 31)), ("20260430", "20260601"))
        year = DateRange(kind=DateRangeKind.RELATIVE, original_text="last one year")
        self.assertEqual(date_bounds(year, today=date(2026, 9, 22)), ("20250922", "20260923"))

    def test_relative_day_bounds_include_today(self) -> None:
        thirty = DateRange(kind=DateRangeKind.RELATIVE, original_text="last 30 days")
        self.assertEqual(date_bounds(thirty, today=date(2026, 9, 22)), ("20260823", "20260923"))

    def test_absolute_bounds_are_half_open(self) -> None:
        inclusive = DateRange(kind=DateRangeKind.ABSOLUTE, start="2025-01-01", end="2025-12-31")
        self.assertEqual(date_bounds(inclusive), ("20250101", "20260101"))
        exclusive = DateRange(kind=DateRangeKind.ABSOLUTE, start="2025-01-01", end="2026-01-01",
                              inclusive_start=False, inclusive_end=False)
        self.assertEqual(date_bounds(exclusive), ("20250102", "20260101"))

    def test_date_binds_are_yyyymmdd_strings_never_python_dates(self) -> None:
        # Asserted on what the runner would actually receive, not on
        # date_bounds' own return: a Python date here is ORA-01861 on the
        # company database (business dates are VARCHAR2(8) text).
        plan = ranking_plan("last 6 months")
        sql = ranking_sql("last 6 months")
        binds = build_bind_parameters(sql, plan, ground_query_plan(plan), today=date(2026, 9, 22))
        date_binds = {name: value for name, value in binds.items() if name.startswith("date_")}
        self.assertEqual(set(date_binds), {"date_start", "date_end"})
        for value in date_binds.values():
            self.assertIsInstance(value, str)
            self.assertRegex(value, r"^\d{8}$")

    def test_quantity_inside_a_longer_phrase_is_not_a_relative_range(self) -> None:
        # "the 6 months ending March 2025" is not "the last 6 months": an
        # unanchored search would answer a different question than the one
        # asked. Fail closed instead (review finding, 2026-09-22).
        for text in ("the 6 months ending March 2025", "in the first 3 months of 2025", "3 days before diwali"):
            self.assertIsNone(relative_date_spec(text), text)
            with self.assertRaises(ParameterBindingError):
                date_bounds(DateRange(kind=DateRangeKind.RELATIVE, original_text=text), today=date(2026, 9, 22))
        self.assertEqual(relative_date_spec("last 6 months"), ("months", 6))
        self.assertEqual(relative_date_spec("6 months"), ("months", 6))

    def test_unparsed_relative_range_executes_zero_times(self) -> None:
        plan = ranking_plan("recently")
        runner = RecordingRunner()
        with self.assertRaises(ParameterBindingError):
            execute_nlp_query(plan.original_question, dependencies=dependencies(plan, ranking_sql(), runner))
        self.assertEqual(runner.calls, [])

    def test_unsupported_question_and_executor_failure_are_controlled(self) -> None:
        unsupported = QueryPlan(
            original_question="show stock value",
            domain="stock",
            operation="aggregate",
            business_subject=BusinessSubject(concept="stock"),
            measures=[Measure(concept="stock value", aggregation=Aggregation.SUM)],
            confidence=0.9,
        )
        runner = RecordingRunner()
        with self.assertRaises(ExecutionRejectedError) as caught:
            execute_nlp_query(
                unsupported.original_question,
                dependencies=dependencies(unsupported, "SELECT 1 FROM INVENTORY.STOCK", runner),
            )
        self.assertEqual(caught.exception.response.error_code, "unsupported")
        self.assertEqual(runner.calls, [])

        failing = RecordingRunner(error=RuntimeError("secret driver context"))
        with self.assertRaises(ExecutionResultError) as failure:
            execute_nlp_query(
                ranking_plan().original_question,
                dependencies=dependencies(ranking_plan(), ranking_sql(), failing),
            )
        self.assertNotIn("secret", str(failure.exception).lower())
        self.assertEqual(len(failing.calls), 1)

    def test_selected_fields_must_agree_with_executor_columns(self) -> None:
        runner = RecordingRunner({"columns": ["WRONG", "TOTAL_PURCHASE_VALUE"], "rows": [["ACME", 1]]})
        with self.assertRaises(ExecutionResultError):
            execute_nlp_query(
                ranking_plan().original_question,
                dependencies=dependencies(ranking_plan(), ranking_sql(), runner),
            )
        self.assertEqual(len(runner.calls), 1)

    def test_execute_endpoint_disabled_and_enabled_with_injected_result(self) -> None:
        request = nlp_router.NLPAnalyzeRequest(question=ranking_plan().original_question)
        with patch.dict(os.environ, {"NLP_QUERY_EXECUTION_API_ENABLED": "false"}):
            with self.assertRaises(HTTPException) as disabled:
                nlp_router.execute(request)
        self.assertEqual(disabled.exception.status_code, 404)

        runner = RecordingRunner()
        response = execute_nlp_query(
            request.question,
            dependencies=dependencies(ranking_plan(), ranking_sql(), runner),
        )
        with (
            patch.dict(os.environ, {"NLP_QUERY_EXECUTION_API_ENABLED": "true"}),
            patch("app.nlp_router.execute_nlp_query", return_value=response) as service,
        ):
            actual = nlp_router.execute(request)
        self.assertTrue(actual.success)
        service.assert_called_once_with(request.question)


if __name__ == "__main__":
    unittest.main(verbosity=2)
