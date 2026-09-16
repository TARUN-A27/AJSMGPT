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


if __name__ == "__main__":
    unittest.main(verbosity=2)
