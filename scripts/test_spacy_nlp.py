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

    # -- recency / limits --------------------------------------------------

    def test_recency_with_explicit_number_sets_limit(self) -> None:
        self.assertEqual(analyze_question_with_spacy("last 5 purchase details").recency_limit, 5)
        self.assertEqual(analyze_question_with_spacy("latest 10 orders").recency_limit, 10)
        self.assertEqual(analyze_question_with_spacy("first 3 supplies").recency_limit, 3)

    def test_recency_direction(self) -> None:
        self.assertEqual(analyze_question_with_spacy("latest purchase order").recency_direction, "desc")
        self.assertEqual(analyze_question_with_spacy("last supply of mouse").recency_direction, "desc")
        self.assertEqual(analyze_question_with_spacy("recent issue for yarn").recency_direction, "desc")
        self.assertEqual(analyze_question_with_spacy("first supply of mouse").recency_direction, "asc")
        self.assertEqual(analyze_question_with_spacy("earliest purchase").recency_direction, "asc")

    def test_recency_does_not_fire_on_date_phrases(self) -> None:
        for question in ("last month", "last year", "last one year", "last 6 months", "last 30 days"):
            result = analyze_question_with_spacy(question)
            self.assertIsNone(result.recency_limit, question)
            self.assertIn(question, result.date_expressions)

    def test_last_year_and_last_n_days_are_date_expressions(self) -> None:
        self.assertIn("last year", analyze_question_with_spacy("purchases last year").date_expressions)
        self.assertIn("last one year", analyze_question_with_spacy("price in last one year").date_expressions)
        self.assertIn("last 30 days", analyze_question_with_spacy("received in last 30 days").date_expressions)

    def test_on_yyyymmdd_is_a_date_expression(self) -> None:
        result = analyze_question_with_spacy("attendance for empcode 165224 on 20260212")
        self.assertIn("on 20260212", result.date_expressions)

    # -- quoted entities -----------------------------------------------------

    def test_quoted_entities_are_captured_verbatim(self) -> None:
        self.assertEqual(analyze_question_with_spacy('what is the price of "Keyboard"').quoted_entities, ["Keyboard"])
        self.assertEqual(analyze_question_with_spacy('"dell system" stock').quoted_entities, ["dell system"])
        self.assertEqual(
            analyze_question_with_spacy('suppliers for the item "BARCODE CHROMO LABEL"').quoted_entities,
            ["BARCODE CHROMO LABEL"],
        )

    # -- entity boundaries -----------------------------------------------------

    def test_entity_span_drops_trailing_preposition(self) -> None:
        result = analyze_question_with_spacy("last purchase rate of barcode scanner in 2026")
        self.assertIn("barcode scanner", result.candidate_entity_spans)
        self.assertNotIn("barcode scanner in", result.candidate_entity_spans)

    # -- contextual numeric identifiers --------------------------------------

    def test_contextual_numeric_identifiers_are_captured(self) -> None:
        self.assertIn("890330", analyze_question_with_spacy("MRS number 890330").contextual_identifiers)
        self.assertIn("890330", analyze_question_with_spacy("MRS no 890330").contextual_identifiers)
        self.assertIn("800967", analyze_question_with_spacy("supplier 800967").contextual_identifiers)
        self.assertIn("12345", analyze_question_with_spacy("order no 12345").contextual_identifiers)

    def test_bare_numbers_are_not_treated_as_identifiers(self) -> None:
        self.assertEqual(analyze_question_with_spacy("show 5 items").contextual_identifiers, [])

    # -- negation -------------------------------------------------------------

    def test_no_is_not_negation_in_identifier_context(self) -> None:
        self.assertEqual(analyze_question_with_spacy('"dell" latest purchase order no?').negations, [])
        self.assertEqual(analyze_question_with_spacy("MRS no 445").negations, [])
        self.assertEqual(analyze_question_with_spacy("supplier no 800967").negations, [])

    def test_genuine_negation_still_detected(self) -> None:
        self.assertIn("no", analyze_question_with_spacy("no returns accepted").negations)
        self.assertIn("without", analyze_question_with_spacy("purchases without GST").negations)

    # -- unsupported-domain vocabulary (advisory only) -------------------------

    def test_stock_and_grn_vocabulary_is_recognized_as_a_domain(self) -> None:
        self.assertIn("stock", analyze_question_with_spacy("keyboard stock").detected_domains)
        self.assertIn("stock", analyze_question_with_spacy("dell system inventory").detected_domains)
        self.assertIn("grn", analyze_question_with_spacy("GRN for supplier 800967").detected_domains)
        self.assertIn("grn", analyze_question_with_spacy("how many qty received in last one year").detected_domains)


if __name__ == "__main__":
    unittest.main(verbosity=2)
