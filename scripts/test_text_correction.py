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


if __name__ == "__main__":
    unittest.main(verbosity=2)
