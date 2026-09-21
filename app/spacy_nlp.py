"""Offline, schema-independent NLP signals for business questions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import spacy
from pydantic import BaseModel
from spacy.language import Language
from spacy.matcher import PhraseMatcher


class NLPAnalysisError(ValueError):
    """Raised when a question cannot be analyzed safely."""


class NLPAnalysis(BaseModel):
    original_question: str
    normalized_question: str
    lemmas: list[str]
    meaningful_tokens: list[str]
    detected_domains: list[str]
    detected_operations: list[str]
    primary_operation: str
    detected_measures: list[str]
    detected_dimensions: list[str]
    date_expressions: list[str]
    numeric_expressions: list[str]
    ranking_limit: int | None = None
    candidate_entity_spans: list[str]
    negations: list[str]
    comparative_terms: list[str]
    ontology_matches: dict[str, list[str]]
    confidence_signals: dict[str, Any]
    has_explicit_time_grouping: bool = False
    time_grouping_granularity: str | None = None
    quoted_entities: list[str] = []
    contextual_identifiers: list[str] = []
    recency_direction: str | None = None
    recency_limit: int | None = None


_ONTOLOGY_PATH = Path(__file__).resolve().parent / "resources" / "nlp_ontology.json"
_DATE_PATTERNS = (
    r"\btoday\b", r"\byesterday\b", r"\bthis month\b", r"\blast month\b",
    r"\blast \d+ months?\b", r"\blast (?:one\s+)?years?\b", r"\blast \d+ years?\b",
    r"\blast \d+ days?\b", r"\bbetween \w+ and \w+\b", r"\bfrom \w+ to \w+\b",
    r"\bin (?:19|20)\d{2}\b", r"\bon (?:19|20)\d{2}\d{2}\d{2}\b",
)
_TIME_GROUPING_PATTERNS = (
    ("month", r"\b(?:by\s+month|monthly|month[- ]wise|per\s+month|each\s+month)\b"),
    ("year", r"\b(?:by\s+year|yearly|year[- ]wise|per\s+year)\b"),
    ("day", r"\b(?:daily|by\s+day|day[- ]wise|per\s+day)\b"),
    ("week", r"\b(?:weekly|by\s+week|week[- ]wise|per\s+week)\b"),
    ("quarter", r"\b(?:quarterly|by\s+quarter|quarter[- ]wise|per\s+quarter)\b"),
)
_CODE_PATTERN = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9-]+\b")
_NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b")
_RANKING_PATTERN = re.compile(r"\b(?:top|bottom)\s+(\d+)\b", re.IGNORECASE)
_ENTITY_PATTERN = re.compile(r"\b(?:for|of)\s+([A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z][A-Za-z0-9-]*){0,3})", re.IGNORECASE)
# Recency wording, kept separate from _RANKING_PATTERN (top/bottom => a
# grouped ranking): "last/latest/recent N" or "first/earliest N" only ever
# yields a sort direction + optional row limit, never operation=ranking.
# The negative lookahead defers to _DATE_PATTERNS so "last month"/"last one
# year"/"last 6 months"/"last 30 days" are never mistaken for a limit.
_RECENCY_PATTERN = re.compile(
    r"\b(last|latest|recent|first|earliest)\b"
    r"(?!\s+(?:\d+\s+)?(?:one\s+)?(?:months?|years?|days?|weeks?)\b)"
    r"(?:\s+(\d+))?",
    re.IGNORECASE,
)
_QUOTED_PATTERN = re.compile(r'"([^"]+)"')
# Pure-digit identifiers are only trustworthy as entity references when a
# strong context word precedes them (never a bare number anywhere).
_CONTEXTUAL_ID_PATTERN = re.compile(
    r"\b(?:supplier|party|vendor|mrs|order|po|purchase\s*order|empcode|department|dept)\s*"
    r"(?:no\.?|number|code)?\s*[:#]?\s*(\d{3,})\b",
    re.IGNORECASE,
)
_TRAILING_STOPWORDS = {"in", "on", "at", "for", "of", "from", "to", "by", "with", "and", "or"}
# "no" is only a negation when it stands alone; as an identifier suffix
# ("order no", "mrs no 12345") it must not be flagged.
_NO_CONTEXT_PRECEDING = {
    "order", "orders", "mrs", "supplier", "suppliers", "party", "parties",
    "vendor", "vendors", "po", "grn",
}


def _strip_trailing_stopwords(text: str) -> str:
    words = text.split()
    while words and words[-1].lower() in _TRAILING_STOPWORDS:
        words.pop()
    return " ".join(words)


class SpacyQuestionAnalyzer:
    """Reusable spaCy blank-English pipeline plus configurable phrase ontology."""

    def __init__(self, ontology_path: Path = _ONTOLOGY_PATH) -> None:
        with ontology_path.open(encoding="utf-8") as source:
            self.ontology: dict[str, dict[str, list[str]]] = json.load(source)
        self.nlp: Language = spacy.blank("en")
        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self._labels: dict[str, tuple[str, str]] = {}
        for category, concepts in self.ontology.items():
            for concept, phrases in concepts.items():
                label = f"{category}:{concept}"
                self._labels[label] = (category, concept)
                self.matcher.add(label, [self.nlp.make_doc(phrase) for phrase in phrases])

    def analyze(self, question: str) -> NLPAnalysis:
        if not isinstance(question, str) or not question.strip():
            raise NLPAnalysisError("Question cannot be empty.")
        original = question
        normalized = " ".join(question.split()).lower()
        doc = self.nlp(normalized)
        dates = [match.group(0) for pattern in _DATE_PATTERNS for match in re.finditer(pattern, normalized)]
        date_spans = [match.span() for pattern in _DATE_PATTERNS for match in re.finditer(pattern, normalized)]
        grouping_matches = [(granularity, re.search(pattern, normalized, re.IGNORECASE)) for granularity, pattern in _TIME_GROUPING_PATTERNS]
        grouping_matches = [(granularity, match) for granularity, match in grouping_matches if match]
        has_explicit_time_grouping = bool(grouping_matches)
        time_grouping_granularity = grouping_matches[0][0] if grouping_matches else None
        matches: dict[str, list[str]] = {category: [] for category in self.ontology}
        matched_spans: list[tuple[int, int]] = []
        for match_id, start, end in self.matcher(doc):
            category, concept = self._labels[self.nlp.vocab.strings[match_id]]
            if (
                category == "dimensions"
                and concept in {"date", "month", "year"}
                and not has_explicit_time_grouping
                and self._inside_date_expression(doc[start].idx, doc[end - 1].idx + len(doc[end - 1]), date_spans)
            ):
                continue
            if concept not in matches[category]:
                matches[category].append(concept)
            matched_spans.append((start, end))

        meaningful = [token.text for token in doc if not token.is_punct and not token.is_space and not token.is_stop]
        numbers = _NUMBER_PATTERN.findall(normalized)
        rank = _RANKING_PATTERN.search(normalized)
        entity_spans = _CODE_PATTERN.findall(original)
        for match in _ENTITY_PATTERN.finditer(original):
            candidate = _strip_trailing_stopwords(match.group(1).strip())
            if candidate and candidate.lower() not in {item for values in matches.values() for item in values}:
                entity_spans.append(candidate)
        entity_spans = list(dict.fromkeys(entity_spans))
        quoted_entities = list(dict.fromkeys(value.strip() for value in _QUOTED_PATTERN.findall(original) if value.strip()))
        contextual_identifiers = list(dict.fromkeys(_CONTEXTUAL_ID_PATTERN.findall(original)))
        negations = []
        for token in doc:
            if token.lower_ not in {"not", "no", "without", "excluding", "except"}:
                continue
            if token.lower_ == "no":
                previous_token = doc[token.i - 1] if token.i > 0 else None
                next_token = doc[token.i + 1] if token.i + 1 < len(doc) else None
                if previous_token is not None and previous_token.lower_ in _NO_CONTEXT_PRECEDING:
                    continue
                if next_token is not None and next_token.text.isdigit():
                    continue
            negations.append(token.text)
        comparative = [token.text for token in doc if token.lower_ in {"more", "less", "higher", "lower", "than", "versus", "vs", "compare", "difference"}]
        primary_operation = self._primary_operation(matches["operations"], rank, comparative)
        matched_categories = sum(bool(values) for values in matches.values())
        recency_match = _RECENCY_PATTERN.search(normalized)
        recency_direction: str | None = None
        recency_limit: int | None = None
        if recency_match:
            recency_direction = "asc" if recency_match.group(1).lower() in {"first", "earliest"} else "desc"
            if recency_match.group(2):
                recency_limit = int(recency_match.group(2))
        return NLPAnalysis(
            original_question=original,
            normalized_question=normalized,
            lemmas=[token.lemma_ or token.lower_ for token in doc if not token.is_space],
            meaningful_tokens=meaningful,
            detected_domains=matches["domains"],
            detected_operations=matches["operations"],
            primary_operation=primary_operation,
            detected_measures=matches["measures"],
            detected_dimensions=matches["dimensions"],
            date_expressions=list(dict.fromkeys(dates)),
            numeric_expressions=numbers,
            ranking_limit=int(rank.group(1)) if rank else None,
            candidate_entity_spans=entity_spans,
            negations=negations,
            comparative_terms=comparative,
            ontology_matches=matches,
            confidence_signals={"matched_categories": matched_categories, "ontology_match_count": sum(len(v) for v in matches.values()), "has_date_expression": bool(dates)},
            has_explicit_time_grouping=has_explicit_time_grouping,
            time_grouping_granularity=time_grouping_granularity,
            quoted_entities=quoted_entities,
            contextual_identifiers=contextual_identifiers,
            recency_direction=recency_direction,
            recency_limit=recency_limit,
        )

    @staticmethod
    def _inside_date_expression(start: int, end: int, date_spans: list[tuple[int, int]]) -> bool:
        return any(date_start <= start and end <= date_end for date_start, date_end in date_spans)

    @staticmethod
    def _primary_operation(operations: list[str], rank: re.Match[str] | None, comparative: list[str]) -> str:
        """Choose one operation without letting generic detail wording dominate."""
        if comparative:
            return "comparison"
        if rank or "ranking" in operations:
            return "ranking"
        for operation in ("trend", "aggregate", "detail"):
            if operation in operations:
                return operation
        return "unknown"


_ANALYZER = SpacyQuestionAnalyzer()


def analyze_question_with_spacy(question: str) -> NLPAnalysis:
    """Analyze locally using the shared blank-English spaCy pipeline."""
    return _ANALYZER.analyze(question)
