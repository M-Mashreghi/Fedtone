"""Versioned structured-output schemas for LLM policy-tone analysis.

Scores use +1 = strongly hawkish, 0 = neutral/balanced, -1 = strongly dovish.
Any change to these models must bump the matching schema version, because stored analyses
are only comparable within one version.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fedtone.domain.models import ToneClassification

TONE_SCHEMA_VERSION = "tone-analysis-1.0"
COMPARISON_SCHEMA_VERSION = "tone-comparison-1.0"


class PolicyDimension(StrEnum):
    INFLATION = "inflation"
    LABOR = "labor"
    GROWTH = "growth"
    POLICY_GUIDANCE = "policy_guidance"
    BALANCE_SHEET = "balance_sheet"


Direction = Literal["hawkish", "dovish", "neutral"]

# Upper bounds (exclusive) of each label band; the last band runs to +1.
LABEL_BANDS: tuple[tuple[float, ToneClassification], ...] = (
    (-0.6, ToneClassification.STRONGLY_DOVISH),
    (-0.2, ToneClassification.DOVISH),
    (0.2, ToneClassification.NEUTRAL),
    (0.6, ToneClassification.HAWKISH),
)


def label_for_score(score: float) -> ToneClassification:
    for upper, label in LABEL_BANDS:
        if score < upper:
            return label
    return ToneClassification.STRONGLY_HAWKISH


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class EvidenceSpan(_Strict):
    quote: str = Field(min_length=3, max_length=800, description="Verbatim text from the source.")
    dimension: PolicyDimension
    direction: Direction


class DimensionScore(_Strict):
    dimension: PolicyDimension
    score: float | None = Field(
        ge=-1.0, le=1.0, description="None when the statement does not address the dimension."
    )
    rationale: str = Field(min_length=1, max_length=600)


class ToneAnalysisOutput(_Strict):
    """What the model must return for a single statement."""

    schema_version: Literal["tone-analysis-1.0"]
    overall_score: float = Field(ge=-1.0, le=1.0)
    label: ToneClassification
    confidence: float = Field(ge=0.0, le=1.0)
    dimensions: list[DimensionScore] = Field(min_length=1, max_length=len(PolicyDimension))
    evidence: list[EvidenceSpan] = Field(max_length=30)
    rationale: str = Field(min_length=1, max_length=2000)
    uncertainty_flags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("dimensions")
    @classmethod
    def _unique_dimensions(cls, value: list[DimensionScore]) -> list[DimensionScore]:
        seen = [d.dimension for d in value]
        if len(seen) != len(set(seen)):
            raise ValueError("each dimension may appear only once")
        return value

    @model_validator(mode="after")
    def _label_matches_score(self) -> "ToneAnalysisOutput":
        expected = label_for_score(self.overall_score)
        if self.label is not expected:
            raise ValueError(
                f"label {self.label} inconsistent with overall_score {self.overall_score} "
                f"(expected {expected})"
            )
        return self

    def dimension_scores(self) -> dict[str, float | None]:
        scores: dict[str, float | None] = dict.fromkeys(PolicyDimension, None)
        scores.update({d.dimension: d.score for d in self.dimensions})
        return {str(k): v for k, v in scores.items()}


ChangeKind = Literal["added", "removed", "strengthened", "softened", "unchanged"]


class LanguageChange(_Strict):
    kind: ChangeKind
    dimension: PolicyDimension
    direction: Direction
    current_quote: str | None = Field(default=None, max_length=800)
    previous_quote: str | None = Field(default=None, max_length=800)
    explanation: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def _quotes_match_kind(self) -> "LanguageChange":
        needs_current = self.kind != "removed"
        needs_previous = self.kind != "added"
        if needs_current and not self.current_quote:
            raise ValueError(f"{self.kind} change needs current_quote")
        if needs_previous and not self.previous_quote:
            raise ValueError(f"{self.kind} change needs previous_quote")
        return self


class ComparisonOutput(_Strict):
    """What the model must return when interpreting current vs previous statement."""

    schema_version: Literal["tone-comparison-1.0"]
    summary: str = Field(min_length=1, max_length=2000)
    changes: list[LanguageChange] = Field(max_length=30)
    uncertainty_flags: list[str] = Field(default_factory=list, max_length=10)
