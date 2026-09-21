from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_correction import TextCorrectionError, correct_question_text


class TextCorrectionTests(unittest.TestCase):
    def test_corrects_unambiguous_business_words(self) -> None:
        result = correct_question_text("show suplier purchse qunatity")
        self.assertEqual(result.corrected_question, "show supplier purchase quantity")
        self.assertEqual([item.original_token for item in result.corrections], ["suplier", "purchse", "qunatity"])

    def test_preserves_alphanumeric_code(self) -> None:
        result = correct_question_text("LM111579 purchse")
        self.assertEqual(result.corrected_question, "LM111579 purchase")
        self.assertIn("LM111579", result.corrected_question)

    def test_preserves_codes_and_mixed_tokens(self) -> None:
        result = correct_question_text("PO-12043 qunatity 20s")
        self.assertEqual(result.corrected_question, "PO-12043 quantity 20s")

    def test_preserves_word_fragments_inside_alphanumeric_tokens(self) -> None:
        self.assertEqual(correct_question_text("purchse123").corrected_question, "purchse123")

    def test_preserves_dates_numbers_and_percentages(self) -> None:
        question = "show 12/03/2026 in 2026 10.5 20%"
        self.assertEqual(correct_question_text(question).corrected_question, question)

    def test_preserves_date_like_words(self) -> None:
        self.assertEqual(correct_question_text("januery purchase").corrected_question, "januery purchase")

    def test_known_words_remain_unchanged(self) -> None:
        question = "show supplier purchase quantity"
        result = correct_question_text(question)
        self.assertFalse(result.was_corrected)
        self.assertEqual(result.corrected_question, question)

    def test_likely_name_is_not_silently_changed(self) -> None:
        result = correct_question_text("show Deloit purchse")
        self.assertIn("Deloit", result.corrected_question)

    def test_empty_question_is_rejected(self) -> None:
        with self.assertRaises(TextCorrectionError):
            correct_question_text("  ")

    def test_result_is_traceable(self) -> None:
        result = correct_question_text("purchse")
        self.assertEqual(result.original_question, "purchse")
        self.assertTrue(result.was_corrected)
        self.assertEqual(result.corrections[0].corrected_token, "purchase")
        self.assertGreater(result.corrections[0].edit_distance, 0)

    def test_date_never_becomes_rate(self) -> None:
        result = correct_question_text("material mouse last purchased date?")
        self.assertEqual(result.corrected_question, "material mouse last purchased date?")
        self.assertFalse(result.was_corrected)

    def test_rate_remains_rate(self) -> None:
        result = correct_question_text("material mouse last purchased rate?")
        self.assertEqual(result.corrected_question, "material mouse last purchased rate?")
        self.assertFalse(result.was_corrected)

    def test_supplied_never_becomes_supplier(self) -> None:
        result = correct_question_text("who supplied the keyboard")
        self.assertEqual(result.corrected_question, "who supplied the keyboard")
        self.assertFalse(result.was_corrected)

    def test_lattest_becomes_latest(self) -> None:
        result = correct_question_text("dell lattest purchase order no?")
        self.assertIn("latest", result.corrected_question)
        self.assertNotIn("lattest", result.corrected_question)

    def test_two_word_quoted_value_is_unchanged(self) -> None:
        question = 'Last 5 purchase details of "dell system"'
        self.assertEqual(correct_question_text(question).corrected_question, question)

    def test_uppercase_quoted_value_is_unchanged(self) -> None:
        question = 'WHO are the suppliers for the item "BARCODE CHROMO LABEL"'
        self.assertEqual(correct_question_text(question).corrected_question, question)

    def test_multiword_lowercase_quoted_value_interior_word_is_unchanged(self) -> None:
        # A typo inside a 3+-word quoted span must not be corrected: the
        # interior word carries no quote character of its own, so it needs
        # cross-chunk quote-state tracking (not just per-chunk protection)
        # to stay verbatim.
        question = 'show "dell qunatity system" stock'
        self.assertEqual(correct_question_text(question).corrected_question, question)

    def test_typo_outside_quotes_is_still_corrected(self) -> None:
        result = correct_question_text('purchse rate of "dell system"')
        self.assertEqual(result.corrected_question, 'purchase rate of "dell system"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
