"""Deterministic statement diff and tone-delta calculation.

The diff works on sentences (paragraph order preserved) and classifies each sentence as
unchanged, added, removed or changed (a rewritten sentence paired with its predecessor).
Whether a change is hawkish or dovish is not decided here; that interpretation is layered on
top by the LLM comparison step and must cite these sentences.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

DIFF_VERSION = "sentence-diff-1"
CHANGED_SENTENCE_RATIO = 0.6

# Abbreviations common in central-bank text that end with a period but not a sentence.
_ABBREVIATIONS = (
    "U.S.",
    "U.K.",
    "e.g.",
    "i.e.",
    "No.",
    "Mr.",
    "Ms.",
    "Dr.",
    "Jan.",
    "Feb.",
    "Aug.",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
    "a.m.",
    "p.m.",
)
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
_PLACEHOLDER = "\u0000"


def split_sentences(paragraphs: Sequence[str]) -> list[str]:
    sentences: list[str] = []
    for paragraph in paragraphs:
        protected = paragraph
        for abbreviation in _ABBREVIATIONS:
            protected = protected.replace(abbreviation, abbreviation.replace(".", _PLACEHOLDER))
        for part in _SENTENCE_END.split(protected):
            part = part.replace(_PLACEHOLDER, ".").strip()
            if part:
                sentences.append(part)
    return sentences


@dataclass(frozen=True)
class ChangedSentence:
    previous: str
    current: str
    similarity: float


@dataclass(frozen=True)
class StatementDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[ChangedSentence, ...]
    unchanged: tuple[str, ...]
    similarity: float
    version: str = DIFF_VERSION

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "similarity": self.similarity,
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": [
                {"previous": c.previous, "current": c.current, "similarity": c.similarity}
                for c in self.changed
            ],
            "unchanged": list(self.unchanged),
        }


def diff_statements(previous: Sequence[str], current: Sequence[str]) -> StatementDiff:
    """Diff two statements given as paragraph lists."""
    old, new = split_sentences(previous), split_sentences(current)
    added: list[str] = []
    removed: list[str] = []
    changed: list[ChangedSentence] = []
    unchanged: list[str] = []

    matcher = SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged.extend(new[j1:j2])
        elif tag == "delete":
            removed.extend(old[i1:i2])
        elif tag == "insert":
            added.extend(new[j1:j2])
        else:
            _pair_replacements(old[i1:i2], new[j1:j2], added, removed, changed)

    similarity = SequenceMatcher(None, " ".join(old), " ".join(new), autojunk=False).ratio()
    return StatementDiff(
        tuple(added), tuple(removed), tuple(changed), tuple(unchanged), round(similarity, 4)
    )


def _pair_replacements(
    old: list[str],
    new: list[str],
    added: list[str],
    removed: list[str],
    changed: list[ChangedSentence],
) -> None:
    """Greedily pair rewritten sentences; unpaired ones are plain additions/removals."""
    remaining = list(old)
    for sentence in new:
        best, best_ratio = None, 0.0
        for candidate in remaining:
            ratio = SequenceMatcher(None, candidate, sentence, autojunk=False).ratio()
            if ratio > best_ratio:
                best, best_ratio = candidate, ratio
        if best is not None and best_ratio >= CHANGED_SENTENCE_RATIO:
            changed.append(ChangedSentence(best, sentence, round(best_ratio, 4)))
            remaining.remove(best)
        else:
            added.append(sentence)
    removed.extend(remaining)


@dataclass(frozen=True)
class ToneDelta:
    """Current minus previous score. None where either side is missing (never imputed)."""

    overall: float | None
    dimensions: dict[str, float | None]


def tone_delta(
    current_overall: float,
    previous_overall: float | None,
    current_dimensions: Mapping[str, float | None],
    previous_dimensions: Mapping[str, float | None] | None,
) -> ToneDelta:
    if previous_overall is None or previous_dimensions is None:
        return ToneDelta(None, dict.fromkeys(current_dimensions, None))
    dims: dict[str, float | None] = {}
    for name in sorted(set(current_dimensions) | set(previous_dimensions)):
        cur, prev = current_dimensions.get(name), previous_dimensions.get(name)
        dims[name] = None if cur is None or prev is None else round(cur - prev, 6)
    return ToneDelta(round(current_overall - previous_overall, 6), dims)
