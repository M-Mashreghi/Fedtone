"""Research summary contract and validator.

The LLM receives a list of facts (tone scores, deltas, market metrics, evidence quotes), each
with an id and an exact display string produced by deterministic code. Its summary may only
repeat those numbers: every number in the text must appear in the facts, and a number
written with a sign must match a fact's sign. Rounded, recomputed or invented numbers are
rejected. Causal wording ("caused", "drove", "led to" ...) is rejected because FedTone
reports association and historical reaction, not causal effects.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

SUMMARY_SCHEMA_VERSION = "research-summary-1.0"


@dataclass(frozen=True)
class Fact:
    id: str
    label: str
    display: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "display": self.display}


class SummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["research-summary-1.0"]
    headline: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    cited_fact_ids: list[str] = Field(min_length=1)
    uncertainty_flags: list[str] = Field(default_factory=list)


_NUMBER = re.compile(r"(?<![\w.])([+-]?)(\d+(?:\.\d+)?)")
_CAUSAL = re.compile(
    r"\b(caus(?:e|ed|es|ing)|dr(?:ove|iven by|ives)|trigger(?:ed|s)?|"
    r"(?:led|leads|lead) to|result(?:ed|s)? in|due to|because of|in response to|"
    r"sparked|prompted)\b",
    re.IGNORECASE,
)


def _numbers(text: str) -> list[tuple[str, str]]:
    text = text.replace("−", "-").replace("–", "-")
    return [(sign, digits) for sign, digits in _NUMBER.findall(text)]


def validate_summary(output: SummaryOutput, facts: Sequence[Fact]) -> list[str]:
    problems = []
    known = {f.id for f in facts}
    unknown = [i for i in output.cited_fact_ids if i not in known]
    if unknown:
        problems.append(f"unknown fact ids: {', '.join(unknown)}")

    allowed_signed: set[tuple[str, str]] = set()
    for fact in facts:
        allowed_signed.update(_numbers(fact.label))
        allowed_signed.update(_numbers(fact.display))
    allowed_digits = {digits for _, digits in allowed_signed}

    text = f"{output.headline}\n{output.summary}"
    for sign, digits in _numbers(text):
        ok = (sign, digits) in allowed_signed if sign else digits in allowed_digits
        if not ok:
            problems.append(f"number not in facts: {sign}{digits}")

    causal = sorted({m.group(0).lower() for m in _CAUSAL.finditer(text)})
    if causal:
        problems.append(f"causal language: {', '.join(causal)}")
    return problems


@dataclass(frozen=True)
class ResearchSummary:
    document_id: UUID
    model: str
    prompt_version: str
    schema_version: str
    metric_version: str
    facts: tuple[Fact, ...]
    headline: str
    summary: str
    cited_fact_ids: tuple[str, ...]
    created_at: datetime
    uncertainty_flags: tuple[str, ...] = ()
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        for name in ("model", "prompt_version", "schema_version", "metric_version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
