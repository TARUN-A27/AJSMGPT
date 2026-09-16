from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.spacy_nlp import NLPAnalysisError, _ANALYZER, _ONTOLOGY_PATH, analyze_question_with_spacy


class SpacyQuestionAnalyzerTests(unittest.TestCase):
    def test_packaged_ontology_exists_and_loads(self) -> None:
        self.assertTrue(_ONTOLOGY_PATH.is_file())
        self.assertIn("purchase", _ANALYZER.ontology["domains"])

    def test_purchase_quantity_by_supplier(self) -> None:
        result = analyze_question_with_spacy("Purchase quantity by supplier")
        self.assertIn("purchase", result.detected_domains)
        self.assertIn("quantity", result.detected_measures)
        self.assertIn("supplier", result.detected_dimensions)

    def test_mrs_detail(self) -> None:
        self.assertIn("mrs", analyze_question_with_spacy("Show MRS details").detected_domains)

    def test_consumption_trend(self) -> None:
        result = analyze_question_with_spacy("Consumption trend by month")
        self.assertIn("consumption", result.detected_domains)
        self.assertIn("trend", result.detected_operations)

    def test_top_and_bottom_limits(self) -> None:
        self.assertEqual(analyze_question_with_spacy("Top 10 suppliers").ranking_limit, 10)
        self.assertEqual(analyze_question_with_spacy("Bottom 5 materials").ranking_limit, 5)

    def test_primary_operation_priorities(self) -> None:
        self.assertEqual(
            analyze_question_with_spacy("Show top 10 suppliers by purchase value in the last 6 months").primary_operation,
            "ranking",
        )
        self.assertEqual(analyze_question_with_spacy("Compare top 10 suppliers").primary_operation, "comparison")

    def test_date_expressions(self) -> None:
        self.assertIn("last 6 months", analyze_question_with_spacy("Consumption last 6 months").date_expressions)
        self.assertIn("in 2026", analyze_question_with_spacy("Purchases in 2026").date_expressions)
        self.assertIn("between january and march", analyze_question_with_spacy("Purchases between January and March").date_expressions)

    def test_temporal_filter_is_not_time_grouping(self) -> None:
        result = analyze_question_with_spacy("Show top 10 suppliers by purchase value in the last 6 months")
        self.assertIn("last 6 months", result.date_expressions)
        self.assertFalse(result.has_explicit_time_grouping)
        self.assertIsNone(result.time_grouping_granularity)
        self.assertNotIn("month", result.detected_dimensions)

    def test_explicit_temporal_grouping(self) -> None:
        for question in ("purchase value by month in the last 6 months", "monthly purchase value"):
            result = analyze_question_with_spacy(question)
            self.assertTrue(result.has_explicit_time_grouping)
            self.assertEqual(result.time_grouping_granularity, "month")
            self.assertIn("month", result.detected_dimensions)

    def test_comparison_and_negation(self) -> None:
        result = analyze_question_with_spacy("Compare higher purchase value versus last month, excluding returns")
        self.assertIn("compare", result.comparative_terms)
        self.assertIn("excluding", result.negations)

    def test_code_preservation_and_normalization(self) -> None:
        result = analyze_question_with_spacy("  Show   LM111579  purchases ")
        self.assertEqual(result.normalized_question, "show lm111579 purchases")
        self.assertIn("LM111579", result.candidate_entity_spans)

    def test_unknown_domain(self) -> None:
        self.assertEqual(analyze_question_with_spacy("Explain weather").detected_domains, [])

    def test_empty_input(self) -> None:
        with self.assertRaises(NLPAnalysisError):
            analyze_question_with_spacy(" \t ")

    def test_pipeline_reuse(self) -> None:
        pipeline = _ANALYZER.nlp
        analyze_question_with_spacy("purchase value")
        analyze_question_with_spacy("MRS details")
        self.assertIs(_ANALYZER.nlp, pipeline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
