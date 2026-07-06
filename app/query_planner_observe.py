from __future__ import annotations

from typing import Any, Dict

from app.query_planner import plan_query as base_plan_query
from app.rasa_nlu_client import understand_with_rasa_duckling


def plan_query_with_nlp_observation(*args, **kwargs) -> Dict[str, Any]:
    """
    Safe observe-mode wrapper.

    It does not change planner routing or SQL.
    It only attaches Rasa + Duckling NLP output for comparison.
    """
    result = base_plan_query(*args, **kwargs)

    try:
        question = None

        if args:
            question = args[0]
        elif "question" in kwargs:
            question = kwargs.get("question")

        if question and isinstance(result, dict):
            nlp = understand_with_rasa_duckling(str(question))
            result["nlp_observation"] = {
                "source": nlp.source,
                "success": nlp.success,
                "intent": nlp.intent,
                "confidence": nlp.confidence,
                "entities": nlp.entities,
                "error": nlp.error,
            }

    except Exception as exc:
        if isinstance(result, dict):
            result["nlp_observation"] = {
                "source": "rasa_nlu_duckling",
                "success": False,
                "intent": None,
                "confidence": 0.0,
                "entities": {},
                "error": f"{type(exc).__name__}: {exc}",
            }

    return result
