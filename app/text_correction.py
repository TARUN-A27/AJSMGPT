"""Conservative offline spelling correction for development NLP endpoints."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel
from symspellpy import SymSpell, Verbosity


class TextCorrectionError(ValueError):
    """Raised when there is no usable question text to correct."""


class TokenCorrection(BaseModel):
    original_token: str
    corrected_token: str
    edit_distance: int
    reason: str


class TextCorrectionResult(BaseModel):
    original_question: str
    corrected_question: str
    corrections: list[TokenCorrection]
    was_corrected: bool


_DICTIONARY_PATH = Path(__file__).resolve().parent / "resources" / "text_correction_dictionary.txt"
_WORD_PATTERN = re.compile(r"[A-Za-z]+")
_PROTECTED_CHUNK_PATTERN = re.compile(r"[@:/\\;`()'\"]|--|\b(?:select|from|where|join|insert|update|delete)\b", re.IGNORECASE)
_DATE_WORDS = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june",
    "jul", "july", "aug", "august", "sep", "sept", "september", "oct", "october", "nov", "november",
    "dec", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}


def _load_symspell() -> tuple[SymSpell, set[str]]:
    symspell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    if not symspell.load_dictionary(str(_DICTIONARY_PATH), term_index=0, count_index=1):
        raise RuntimeError("Text correction dictionary could not be loaded.")
    trusted = set(symspell.words)
    return symspell, trusted


_SYMSPELL, _TRUSTED_TERMS = _load_symspell()


def _is_protected_word(token: str, chunk: str) -> bool:
    lowered = token.lower()
    date_suggestions = _SYMSPELL.lookup(lowered, Verbosity.CLOSEST, max_edit_distance=2)
    return (
        token.lower() in _TRUSTED_TERMS
        or lowered in _DATE_WORDS
        or any(suggestion.term in _DATE_WORDS for suggestion in date_suggestions)
        or len(token) < 4
        or token.isupper()
        or token[0].isupper()
        or any(char.isdigit() for char in chunk)
        or bool(_PROTECTED_CHUNK_PATTERN.search(chunk))
    )


def _replacement_for(token: str, chunk: str, corrections: list[TokenCorrection]) -> str:
    if _is_protected_word(token, chunk):
        return token
    max_distance = 1 if len(token) <= 5 else 2
    suggestions = _SYMSPELL.lookup(token.lower(), Verbosity.CLOSEST, max_edit_distance=max_distance)
    if len(suggestions) != 1:
        return token
    suggestion = suggestions[0]
    if suggestion.term == token.lower() or suggestion.distance > max_distance:
        return token
    corrections.append(
        TokenCorrection(
            original_token=token,
            corrected_token=suggestion.term,
            edit_distance=suggestion.distance,
            reason="unambiguous trusted dictionary suggestion",
        )
    )
    return suggestion.term


def correct_question_text(question: str) -> TextCorrectionResult:
    """Correct only low-risk alphabetic natural-language tokens offline."""
    if not isinstance(question, str) or not question.strip():
        raise TextCorrectionError("Question cannot be empty.")
    corrections: list[TokenCorrection] = []
    corrected_chunks: list[str] = []
    inside_quoted_span = False
    for chunk in re.split(r"(\s+)", question):
        if not chunk or chunk.isspace():
            corrected_chunks.append(chunk)
            continue
        # A chunk between an opening and closing `"` carries no quote char of
        # its own, so _PROTECTED_CHUNK_PATTERN never sees it; track quote
        # state across chunks so a multi-word quoted value ("dell system") is
        # protected word-for-word, not just at its two boundary chunks.
        protected = inside_quoted_span or bool(_PROTECTED_CHUNK_PATTERN.search(chunk))
        if chunk.count('"') % 2 == 1:
            inside_quoted_span = not inside_quoted_span
        if protected:
            corrected_chunks.append(chunk)
            continue
        corrected_chunks.append(_WORD_PATTERN.sub(lambda match: _replacement_for(match.group(0), chunk, corrections), chunk))
    corrected_question = "".join(corrected_chunks)
    return TextCorrectionResult(
        original_question=question,
        corrected_question=corrected_question,
        corrections=corrections,
        was_corrected=bool(corrections),
    )
