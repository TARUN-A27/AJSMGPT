"""Consolidate every real user question we hold into one deduped bank.

Sources (all already in the repo, no network, no Oracle):
  AutomateQuery/reports/question_bank.json   review-tool bank with occurrence counts
  data/evaluation_questions.json             the curated evaluation set
  data/user_purchase_mrs_questions.txt       questions Tarun collected by hand
  logs/user_questions.jsonl                  what users actually typed at /ask

Output: data/question_bank_v1.json  (deduped, one record per normalised question,
with its sources, occurrence count and a PROPOSED family) plus a coverage table
against the V1 family targets. The family is a proposal for review, never an
answer: it decides only which bucket a question is counted in, never how the
pipeline treats it.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# How many questions each family needs before "it generalises" is measurable
# (half of each family is held back as a test set).
TARGETS = {
    "purchase": 30, "mrs": 25, "consumption": 25, "stock": 25,
    "grn": 25, "supplier_lookup": 15, "material_lookup": 15,
}

# Ordered most specific first: the first rule that matches wins, so a question
# about receiving an item is a GRN question, not a material one.
RULES: list[tuple[str, str]] = [
    # A question naming a document type belongs to that document's family even
    # when it also mentions an item or a supplier, so the document rules run
    # first and MRS runs before GRN ("MRS rejected reason" is an MRS question,
    # not a goods-rejection one).
    ("schema_metadata", r"\btable\b|\bcolumn\b|\bschema\b|database|describe the|what does .* mean"),
    ("out_of_scope_hr", r"attendance|empcode|\bshift\b|overtime|\bstaff\b|salary|designation|employee"),
    ("out_of_scope_admin", r"camera|vehicle|gate ?pass|insurance|log ?book"),
    ("mrs", r"\bmrs\b|requisition|indent|approval|store ?officer|\bhold\b|\bdue\b"),
    ("stock", r"\bstock\b|reorder|\broq\b|minimum qty|available qty|balance qty"),
    ("grn", r"receiv|\bgrn\b|gate ?in|inward|invoice|rejec|inspect|\bdc\b"),
    ("consumption", r"consum|\bissue|\bissued\b|usage|used by"),
    ("purchase", r"purchase|\bpo\b|\border|\brate\b|\bprice\b|\bsupply\b|supplied|\bqty\b|quantity|\bcost\b|\bvalue\b"),
    ("supplier_lookup", r"who is|supplier|which supplier|\bvendor\b|\bparty\b|\bgst\b|\bpan\b"),
    ("material_lookup", r"item ?code|item ?name|material ?name|which item|find item|item master|item details"),
]


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _family(question: str) -> str:
    normalised = _normalise(question)
    for family, pattern in RULES:
        if re.search(pattern, normalised):
            return family
    return "unclassified"


def _load() -> dict[str, dict]:
    """normalised question -> record. First spelling seen wins as the display text."""
    bank: dict[str, dict] = {}

    def add(question: str, source: str, occurrences: int = 1) -> None:
        if not question or not question.strip():
            return
        key = _normalise(question)
        record = bank.setdefault(key, {"question": question.strip(), "sources": [], "occurrences": 0})
        if source not in record["sources"]:
            record["sources"].append(source)
        record["occurrences"] += occurrences

    for item in json.loads((ROOT / "AutomateQuery/reports/question_bank.json").read_text()):
        add(item.get("question", ""), "question_bank", int(item.get("occurrence_count") or 1))
    for item in json.loads((ROOT / "data/evaluation_questions.json").read_text()):
        add(item.get("question", ""), "evaluation_questions")
    for line in (ROOT / "data/user_purchase_mrs_questions.txt").read_text().splitlines():
        add(line, "user_collected")
    for line in (ROOT / "logs/user_questions.jsonl").read_text().splitlines():
        try:
            add(json.loads(line).get("question", ""), "ask_log")
        except json.JSONDecodeError:
            continue
    return bank


def main() -> None:
    bank = _load()
    for record in bank.values():
        record["proposed_family"] = _family(record["question"])

    records = sorted(bank.values(), key=lambda r: (r["proposed_family"], -r["occurrences"]))
    output = ROOT / "data/question_bank_v1.json"
    output.write_text(json.dumps(
        {"version": "1.0", "total": len(records), "note": "proposed_family is a review proposal, not a verdict",
         "questions": records}, ensure_ascii=False, indent=1) + "\n")

    counts = collections.Counter(r["proposed_family"] for r in records)
    print(f"{len(records)} unique questions -> {output.relative_to(ROOT)}\n")
    print(f"{'family':20}{'have':>6}{'need':>6}{'gap':>6}")
    for family, target in TARGETS.items():
        have = counts[family]
        print(f"{family:20}{have:>6}{target:>6}{min(0, have - target):>6}")
    print()
    for family in sorted(set(counts) - set(TARGETS)):
        print(f"{family:20}{counts[family]:>6}     -     -")


if __name__ == "__main__":
    main()
