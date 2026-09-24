from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.nlp_execution import ExecutionRejectedError, NLPExecuteFailureResponse
from scripts.evaluate_v1_real_questions import _classify, _is_genuine_multi_candidate_ambiguity


def _rejection(ambiguities: list[str]) -> ExecutionRejectedError:
    response = NLPExecuteFailureResponse(
        error_code="clarification_required",
        question="placeholder",
        ambiguities=ambiguities,
    )
    return ExecutionRejectedError(response)


class GenuineMultiCandidateAmbiguityTests(unittest.TestCase):
    def test_two_or_more_candidates_is_genuine(self) -> None:
        text = (
            "Entity 'material' requires verified resolution before execution. "
            "Candidates: KEYBOARD .W. MOUSE - COMBO [A14003069]; SEALED KEYBOARD FOR PT3 [A14700084]."
        )
        self.assertTrue(_is_genuine_multi_candidate_ambiguity(text))

    def test_zero_candidates_is_not_genuine(self) -> None:
        # fix.md #16's shape: a non-entity word tagged as one, or a real
        # value with no real match -- neither is a safe, correct refusal.
        text = "Entity 'pending' requires verified resolution before execution."
        self.assertFalse(_is_genuine_multi_candidate_ambiguity(text))

    def test_one_candidate_is_not_genuine(self) -> None:
        # An UNRESOLVED fuzzy-fallback hint (fix.md #2) -- still unresolved,
        # not an offered choice between real matches.
        text = "Entity 'material' requires verified resolution before execution. Candidates: YARN [A14701462]."
        self.assertFalse(_is_genuine_multi_candidate_ambiguity(text))

    def test_non_entity_ambiguity_is_not_genuine(self) -> None:
        self.assertFalse(_is_genuine_multi_candidate_ambiguity(
            "The validated QueryPlan confidence is below the execution threshold."
        ))


class ClassifyEntityAmbiguityTests(unittest.TestCase):
    def test_pure_multi_candidate_ambiguity_is_deferred_not_rejected(self) -> None:
        exc = _rejection([
            "Entity 'material' requires verified resolution before execution. "
            "Candidates: KEYBOARD .W. MOUSE - COMBO [A14003069]; SEALED KEYBOARD FOR PT3 [A14700084]."
        ])
        result = _classify("how much keyboard did we buy", exc)
        self.assertEqual(result["classification"], "ENTITY_AMBIGUOUS_DEFERRED")

    def test_zero_candidate_entity_ambiguity_stays_a_rejection(self) -> None:
        exc = _rejection(["Entity 'pending' requires verified resolution before execution."])
        result = _classify("pending PO for item code C02000094", exc)
        self.assertEqual(result["classification"], "ENTITY_RESOLUTION_REJECTION")

    def test_genuine_ambiguity_mixed_with_low_confidence_stays_a_rejection(self) -> None:
        # Even if the entity ambiguity alone would be safe to defer, a
        # co-occurring, unrelated QueryPlan-quality flag means the question
        # still doesn't have a clean answer -- not credited as deferred.
        exc = _rejection([
            "The validated QueryPlan confidence is below the execution threshold.",
            "Entity 'material' requires verified resolution before execution. "
            "Candidates: KEYBOARD .W. MOUSE - COMBO [A14003069]; SEALED KEYBOARD FOR PT3 [A14700084].",
        ])
        result = _classify("keyboard?", exc)
        self.assertEqual(result["classification"], "ENTITY_RESOLUTION_REJECTION")

    def test_two_entities_one_genuinely_ambiguous_one_not_stays_a_rejection(self) -> None:
        exc = _rejection([
            "Entity 'material' requires verified resolution before execution. "
            "Candidates: KEYBOARD .W. MOUSE - COMBO [A14003069]; SEALED KEYBOARD FOR PT3 [A14700084].",
            "Entity 'pending' requires verified resolution before execution.",
        ])
        result = _classify("pending keyboard order", exc)
        self.assertEqual(result["classification"], "ENTITY_RESOLUTION_REJECTION")


if __name__ == "__main__":
    unittest.main()
