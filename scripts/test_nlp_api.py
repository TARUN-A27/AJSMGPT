from __future__ import annotations

import os
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.api import app
from app import nlp_router
from app.grounded_sql_generator import GroundedSqlModelUnavailableError, GroundedSqlResult
from app.grounded_sql_validator import UnsafeGroundedSqlError
from app.query_plan import (
    Aggregation,
    BusinessSubject,
    DateRange,
    DateRangeKind,
    Dimension,
    Measure,
    QueryPlan,
    RequestedOutput,
    SortDirection,
    SortInstruction,
)
from app.query_plan_extractor import QueryPlanExtractionError, QueryPlanValidationError
from app.schema_grounding import ground_query_plan


class _DirectResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body, default=str)

    def json(self) -> object:
        return self._body


class _DirectNLPClient:
    """Exercise registered sync handlers without the unavailable httpx2 test transport."""

    _handlers = {
        "/v1/nlp/analyze": nlp_router.analyze,
        "/v1/nlp/understand": nlp_router.understand,
        "/v1/nlp/ground": nlp_router.ground,
        "/v1/nlp/sql-preview": nlp_router.sql_preview,
        "/v1/nlp/execute": nlp_router.execute,
    }

    def post(self, path: str, json: dict) -> _DirectResponse:
        try:
            request = nlp_router.NLPAnalyzeRequest.model_validate(json)
            result = self._handlers[path](request)
        except ValidationError as exc:
            return _DirectResponse(422, {"detail": exc.errors(include_url=False)})
        except HTTPException as exc:
            return _DirectResponse(exc.status_code, {"detail": exc.detail})
        body = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
        return _DirectResponse(200, body)


def sql_preview_plan() -> QueryPlan:
    return QueryPlan(
        original_question="top 10 suppliers by purchase value last month",
        domain="purchase",
        operation="ranking",
        business_subject=BusinessSubject(concept="purchase"),
        measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
        dimensions=[Dimension(concept="supplier", grouping=True)],
        date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
        sorting=[SortInstruction(field_concept="purchase value", direction=SortDirection.DESC, priority=0)],
        limit=10,
        requested_output=RequestedOutput(fields=["supplier", "purchase value"]),
        confidence=0.95,
    )


class NLPApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _DirectNLPClient()

    def test_disabled_endpoint_returns_404(self) -> None:
        with patch.dict(os.environ, {"NLP_ANALYSIS_API_ENABLED": "false"}):
            self.assertEqual(self.client.post("/v1/nlp/analyze", json={"question": "purchase value"}).status_code, 404)

    def test_enabled_endpoint_returns_analysis(self) -> None:
        with patch.dict(os.environ, {"NLP_ANALYSIS_API_ENABLED": "true"}):
            response = self.client.post("/v1/nlp/analyze", json={"question": "Show top 10 suppliers by purchase value last month"})
        self.assertEqual(response.status_code, 200)
        analysis = response.json()["analysis"]
        self.assertEqual(analysis["ranking_limit"], 10)
        self.assertIn("purchase", analysis["detected_domains"])
        self.assertIn("original_question", analysis)

    def test_analyze_returns_correction_and_corrected_analysis(self) -> None:
        with patch.dict(os.environ, {"NLP_ANALYSIS_API_ENABLED": "true"}):
            response = self.client.post("/v1/nlp/analyze", json={"question": "show suplier purchse qunatity"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["correction"]["corrected_question"], "show supplier purchase quantity")
        self.assertTrue(body["correction"]["was_corrected"])
        self.assertEqual(body["analysis"]["original_question"], "show supplier purchase quantity")

    def test_empty_question_returns_422(self) -> None:
        with patch.dict(os.environ, {"NLP_ANALYSIS_API_ENABLED": "true"}):
            self.assertEqual(self.client.post("/v1/nlp/analyze", json={"question": "  "}).status_code, 422)

    def test_existing_ask_route_remains_registered(self) -> None:
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        self.assertIn("/ask", paths)
        self.assertEqual(self.client.post("/v1/nlp/analyze", json={"question": "purchase"}).status_code, 404)

    def test_understand_disabled_returns_404(self) -> None:
        with patch.dict(os.environ, {"NLP_QUERY_PLAN_API_ENABLED": "false"}):
            self.assertEqual(self.client.post("/v1/nlp/understand", json={"question": "purchase value"}).status_code, 404)

    def test_understand_enabled_returns_plan_with_mocked_model(self) -> None:
        plan = '{"original_question":"purchase value","domain":"purchase","operation":"aggregate","business_subject":{"concept":"purchases"},"measures":[{"concept":"value","aggregation":"sum"}],"confidence":0.9}'
        with patch.dict(os.environ, {"NLP_QUERY_PLAN_API_ENABLED": "true"}), patch("app.nlp_router.extract_query_plan") as extractor:
            from app.query_plan import QueryPlan
            extractor.return_value = QueryPlan.model_validate_json(plan)
            response = self.client.post("/v1/nlp/understand", json={"question": "purchase value"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["query_plan"]["domain"], "purchase")
        self.assertFalse(response.json()["requires_clarification"])
        self.assertIsNotNone(extractor.call_args.kwargs["nlp_analysis"])

    def test_understand_uses_corrected_question_and_preserves_raw_question(self) -> None:
        from app.query_plan import QueryPlan

        raw = "show suplier purchse qunatity"
        corrected = "show supplier purchase quantity"
        plan = QueryPlan.model_validate({
            "original_question": raw,
            "domain": "purchase",
            "operation": "aggregate",
            "business_subject": {"concept": "purchases"},
            "measures": [{"concept": "quantity", "aggregation": "sum"}],
            "confidence": 0.9,
        })
        with patch.dict(os.environ, {"NLP_QUERY_PLAN_API_ENABLED": "true"}), patch("app.nlp_router.extract_query_plan", return_value=plan) as extractor:
            response = self.client.post("/v1/nlp/understand", json={"question": raw})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(extractor.call_args.args[0], corrected)
        self.assertEqual(extractor.call_args.kwargs["original_question"], raw)
        self.assertEqual(response.json()["query_plan"]["original_question"], raw)

    def test_understand_empty_question_does_not_call_model(self) -> None:
        with patch.dict(os.environ, {"NLP_QUERY_PLAN_API_ENABLED": "true"}), patch("app.nlp_router.extract_query_plan") as extractor:
            response = self.client.post("/v1/nlp/understand", json={"question": "  "})
        self.assertEqual(response.status_code, 422)
        extractor.assert_not_called()

    def test_understand_model_failure_is_502(self) -> None:
        with patch.dict(os.environ, {"NLP_QUERY_PLAN_API_ENABLED": "true"}), patch("app.nlp_router.extract_query_plan", side_effect=QueryPlanExtractionError("offline")):
            self.assertEqual(self.client.post("/v1/nlp/understand", json={"question": "purchase value"}).status_code, 502)

    def test_understand_invalid_model_output_is_422(self) -> None:
        with patch.dict(os.environ, {"NLP_QUERY_PLAN_API_ENABLED": "true"}), patch("app.nlp_router.extract_query_plan", side_effect=QueryPlanValidationError("invalid")):
            self.assertEqual(self.client.post("/v1/nlp/understand", json={"question": "purchase value"}).status_code, 422)

    def test_ground_disabled_returns_404(self) -> None:
        with patch.dict(os.environ, {"NLP_GROUNDING_API_ENABLED": "false"}):
            self.assertEqual(self.client.post("/v1/nlp/ground", json={"question": "purchase value"}).status_code, 404)

    def test_ground_enabled_returns_complete_pipeline(self) -> None:
        from app.query_plan import QueryPlan

        plan = QueryPlan.model_validate({
            "original_question": "purchase value",
            "domain": "purchase",
            "operation": "aggregate",
            "business_subject": {"concept": "purchase"},
            "measures": [{"concept": "purchase value", "aggregation": "sum"}],
            "confidence": 0.9,
        })
        with patch.dict(os.environ, {"NLP_GROUNDING_API_ENABLED": "true"}), patch("app.nlp_router.extract_query_plan", return_value=plan) as extractor:
            response = self.client.post("/v1/nlp/ground", json={"question": "purchase value"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"correction", "analysis", "query_plan", "grounding", "requires_clarification"})
        self.assertEqual(body["query_plan"]["domain"], "purchase")
        self.assertTrue(body["grounding"]["selected_tables"])
        self.assertFalse(body["requires_clarification"])
        self.assertIsNotNone(extractor.call_args.kwargs["nlp_analysis"])

    def test_ground_model_failure_is_502(self) -> None:
        with patch.dict(os.environ, {"NLP_GROUNDING_API_ENABLED": "true"}), patch("app.nlp_router.extract_query_plan", side_effect=QueryPlanExtractionError("offline")):
            self.assertEqual(self.client.post("/v1/nlp/ground", json={"question": "purchase value"}).status_code, 502)

    def test_sql_preview_disabled_returns_404(self) -> None:
        with patch.dict(os.environ, {"NLP_SQL_PREVIEW_API_ENABLED": "false"}):
            response = self.client.post("/v1/nlp/sql-preview", json={"question": "purchase value"})
        self.assertEqual(response.status_code, 404)

    def test_sql_preview_enabled_returns_full_mocked_pipeline(self) -> None:
        plan = sql_preview_plan()
        grounding = ground_query_plan(plan)
        preview = GroundedSqlResult(
            sql="SELECT pm.PARTYNAME, SUM(po.NET) AS purchase_value FROM INVENTORY.PURCHASEORDER po JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE WHERE po.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -1) GROUP BY pm.PARTYNAME ORDER BY purchase_value DESC FETCH FIRST 10 ROWS ONLY",
            selected_fields=["supplier", "purchase value"],
            applied_filters=["last month"],
            assumptions=[],
            confidence=0.92,
        )
        with (
            patch.dict(os.environ, {"NLP_SQL_PREVIEW_API_ENABLED": "true"}),
            patch("app.nlp_router.extract_query_plan", return_value=plan) as extractor,
            patch("app.nlp_router.ground_query_plan", return_value=grounding),
            patch("app.nlp_router.generate_grounded_sql", return_value=preview) as generator,
        ):
            response = self.client.post(
                "/v1/nlp/sql-preview",
                json={"question": "top 10 suppliers by purchase value last month"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            set(body),
            {"correction", "analysis", "query_plan", "grounding", "sql_preview", "requires_clarification"},
        )
        self.assertFalse(body["requires_clarification"])
        self.assertEqual(body["sql_preview"]["assumptions"], [])
        self.assertIsNotNone(extractor.call_args.kwargs["nlp_analysis"])
        generator.assert_called_once_with(plan, grounding)

    def test_sql_preview_empty_question_returns_422(self) -> None:
        with patch.dict(os.environ, {"NLP_SQL_PREVIEW_API_ENABLED": "true"}), patch(
            "app.nlp_router.extract_query_plan"
        ) as extractor:
            response = self.client.post("/v1/nlp/sql-preview", json={"question": "  "})
        self.assertEqual(response.status_code, 422)
        extractor.assert_not_called()

    def test_sql_preview_unavailable_model_returns_502(self) -> None:
        plan = sql_preview_plan()
        grounding = ground_query_plan(plan)
        with (
            patch.dict(os.environ, {"NLP_SQL_PREVIEW_API_ENABLED": "true"}),
            patch("app.nlp_router.extract_query_plan", return_value=plan),
            patch("app.nlp_router.ground_query_plan", return_value=grounding),
            patch(
                "app.nlp_router.generate_grounded_sql",
                side_effect=GroundedSqlModelUnavailableError("offline"),
            ),
        ):
            response = self.client.post("/v1/nlp/sql-preview", json={"question": plan.original_question})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "SQL preview model is unavailable.")

    def test_sql_preview_invalid_sql_returns_422(self) -> None:
        plan = sql_preview_plan()
        grounding = ground_query_plan(plan)
        with (
            patch.dict(os.environ, {"NLP_SQL_PREVIEW_API_ENABLED": "true"}),
            patch("app.nlp_router.extract_query_plan", return_value=plan),
            patch("app.nlp_router.ground_query_plan", return_value=grounding),
            patch("app.nlp_router.generate_grounded_sql", side_effect=UnsafeGroundedSqlError("unsafe")),
        ):
            response = self.client.post("/v1/nlp/sql-preview", json={"question": plan.original_question})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "SQL preview could not be validated.")

    def test_all_nlp_and_ask_routes_remain_registered(self) -> None:
        expected = {
            "analyze": "/v1/nlp/analyze",
            "understand": "/v1/nlp/understand",
            "ground": "/v1/nlp/ground",
            "sql_preview": "/v1/nlp/sql-preview",
            "execute": "/v1/nlp/execute",
            "ask": "/ask",
        }
        self.assertEqual({name: str(app.url_path_for(name)) for name in expected}, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
