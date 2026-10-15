"""Deterministic evidence validation: does a quoted span really occur in the source?

Matching is exact first (after case, typography and whitespace folding), then a controlled
fuzzy match that ignores punctuation only. Word-level differences are never tolerated: a
single changed word can flip meaning ("does not expect" vs "does expect"), so a similarity
ratio is deliberately not used. Quotes shorter than a few words are never fuzzy-matched.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from fedtone.domain.tone_schema import ComparisonOutput, ToneAnalysisOutput

MIN_FUZZY_WORDS = 4

_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)
_SPACE = re.compile(r"\s+")


def fold(text: str) -> str:
    """Case, typography and whitespace folding used for matching only."""
    text = unicodedata.normalize("NFKC", text).translate(_FOLD).casefold()
    return _SPACE.sub(" ", text).strip()


class MatchKind(StrEnum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class EvidenceMatch:
    quote: str
    kind: MatchKind
    ratio: float
    matched_text: str | None = None

    @property
    def supported(self) -> bool:
        return self.kind is not MatchKind.UNSUPPORTED


def _words(text: str) -> str:
    """Folded text with all punctuation removed, as space-separated words."""
    stripped = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in text)
    return _SPACE.sub(" ", stripped).strip()


def _find_on_word_boundaries(needle: str, haystack: str) -> int | None:
    """Index of ``needle`` in ``haystack`` that does not cut a word in half, if any."""
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        cuts_before = start > 0 and haystack[start - 1].isalnum() and needle[0].isalnum()
        cuts_after = end < len(haystack) and haystack[end].isalnum() and needle[-1].isalnum()
        if not cuts_before and not cuts_after:
            return start
        start = haystack.find(needle, start + 1)
    return None


def match_quote(quote: str, source: str) -> EvidenceMatch:
    needle, haystack = fold(quote).strip(" .\"'"), fold(source)
    if not needle:
        return EvidenceMatch(quote, MatchKind.UNSUPPORTED, 0.0)
    start = _find_on_word_boundaries(needle, haystack)
    if start is not None:
        return EvidenceMatch(quote, MatchKind.EXACT, 1.0, haystack[start : start + len(needle)])

    needle_words, haystack_words = _words(needle), _words(haystack)
    if len(needle_words.split(" ")) >= MIN_FUZZY_WORDS and (
        f" {needle_words} " in f" {haystack_words} "
    ):
        return EvidenceMatch(quote, MatchKind.FUZZY, 1.0, needle_words)
    return EvidenceMatch(quote, MatchKind.UNSUPPORTED, 0.0)


# A dimension (or the overall score) at or beyond this magnitude is "material" and must be
# backed by at least one supported quote for that dimension.
MATERIAL_SCORE = 0.2


@dataclass(frozen=True)
class EvidenceReport:
    matches: tuple[EvidenceMatch, ...]
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.problems

    @property
    def supported_count(self) -> int:
        return sum(m.supported for m in self.matches)


def validate_tone_evidence(output: ToneAnalysisOutput, source_text: str) -> EvidenceReport:
    """Every quote must occur in the source; every material dimension needs a supported quote."""
    matches = tuple(match_quote(span.quote, source_text) for span in output.evidence)
    problems = [f"unsupported quote: {m.quote!r}" for m in matches if not m.supported]

    supported_dimensions = {
        span.dimension
        for span, match in zip(output.evidence, matches, strict=True)
        if match.supported
    }
    if not supported_dimensions:
        problems.append("no supported evidence")
    for dim in output.dimensions:
        if (
            dim.score is not None
            and abs(dim.score) >= MATERIAL_SCORE
            and dim.dimension not in supported_dimensions
        ):
            problems.append(f"material dimension {dim.dimension} has no supported evidence")
    return EvidenceReport(matches, tuple(problems))


def validate_comparison_evidence(
    output: ComparisonOutput, current_text: str, previous_text: str
) -> EvidenceReport:
    """Current quotes must occur in the current text and previous quotes in the previous one."""
    matches: list[EvidenceMatch] = []
    problems: list[str] = []
    for change in output.changes:
        for quote, source, label in (
            (change.current_quote, current_text, "current"),
            (change.previous_quote, previous_text, "previous"),
        ):
            if quote is None:
                continue
            match = match_quote(quote, source)
            matches.append(match)
            if not match.supported:
                problems.append(f"unsupported {label} quote: {quote!r}")
    return EvidenceReport(tuple(matches), tuple(problems))
