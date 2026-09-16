from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.api import app
from app.query_plan_extractor import QueryPlanExtractionError, QueryPlanValidationError


class NLPApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
