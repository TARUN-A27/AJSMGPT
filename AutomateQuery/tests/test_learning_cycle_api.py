import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from AutomateQuery.web.learning_cycle_api import _normalize_ask_response


def test_normalizes_nested_ask_payload() -> None:
    raw_response = {
        "success": True,
        "data": {
            "success": True,
            "question": "latest GRN for supplier 800967 in 2025",
            "sql": "SELECT * FROM grn WHERE supplier_code = '800967' AND year = 2025",
            "source": {"name": "layman_router"},
            "intent": "grn_by_supplier_code",
            "row_count": 0,
        },
    }

    normalized = _normalize_ask_response("latest GRN for supplier 800967 in 2025", raw_response)

    assert normalized["ok"] is True
    assert normalized["http_status"] == 200
    assert normalized["extracted"]["success"] is True
    assert normalized["extracted"]["sql"].startswith("SELECT")
    assert normalized["extracted"]["source"] == "layman_router"
    assert normalized["extracted"]["intent"] == "grn_by_supplier_code"
    assert normalized["extracted"]["row_count"] == 0
    assert normalized["raw_response"] == raw_response
