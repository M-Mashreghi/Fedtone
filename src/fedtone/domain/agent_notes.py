"""Agentic research notes: claim schemas and the deterministic evidence gate (Task 7.4).

Analyst agents propose short claims, each citing fact ids. A claim survives only if
``check_claim`` finds no problem AND the skeptic reviewer marks it supported:

- every cited fact id exists, and at least one is cited;
- market claims cite at least one market fact, policy claims at least one policy fact;
- every number in the claim is copied from a *cited* fact (sign included when written);
- quoted text ("...") is a verbatim part of a cited fact;
- no causal wording (reactions are associations, not effects).

Scores and metrics stay those of the existing services: agents can only point at facts.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from fedtone.domain.summary import CAUSAL_PATTERN, Fact, numbers_in

AGENT_SCHEMA_VERSION = "agent-claims-1.0"
_QUOTED = re.compile(r"[\"“]([^\"”]{3,})[\"”]")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ClaimDraft(_Strict):
    claim_id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=400)
    cited_fact_ids: list[str] = Field(min_length=1)


class AnalystOutput(_Strict):
    schema_version: Literal["agent-claims-1.0"]
    claims: list[ClaimDraft] = Field(min_length=1, max_length=8)


class Verdict(_Strict):
    claim_id: str
    supported: bool
    reason: str = Field(min_length=1, max_length=300)


class ReviewerOutput(_Strict):
    schema_version: Literal["agent-claims-1.0"]
    verdicts: list[Verdict]


class Section(StrEnum):
    POLICY = "policy"
    MARKET = "market"


def is_market_fact(fact_id: str) -> bool:
    """Market facts are the per-instrument metrics built by ``build_facts``."""
    return bool(
        re.match(r"^[A-Z0-9]+_(simple_return|log_return|yield_change|excess_return)_", fact_id)
    )


def check_claim(
    section: Section, text: str, cited: Sequence[str], facts: Mapping[str, Fact]
) -> list[str]:
    problems = []
    unknown = [i for i in cited if i not in facts]
    if unknown:
        problems.append(f"unknown fact ids: {', '.join(unknown)}")
    known = [facts[i] for i in cited if i in facts]
    if not known:
        problems.append("no valid citation")
        return problems
    market = [f for f in known if is_market_fact(f.id)]
    if section is Section.MARKET and not market:
        problems.append("market claim cites no market fact")
    if section is Section.POLICY and len(market) == len(known):
        problems.append("policy claim cites only market facts")

    allowed_signed: set[tuple[str, str]] = set()
    for fact in known:
        allowed_signed.update(numbers_in(fact.label))
        allowed_signed.update(numbers_in(fact.display))
    allowed_digits = {digits for _, digits in allowed_signed}
    for sign, digits in numbers_in(text):
        ok = (sign, digits) in allowed_signed if sign else digits in allowed_digits
        if not ok:
            problems.append(f"number not in cited facts: {sign}{digits}")

    cited_text = " ".join(f.display for f in known)
    for quote in _QUOTED.findall(text):
        if quote.strip() not in cited_text:
            problems.append(f"quote not in cited facts: {quote[:60]!r}")

    causal = sorted({m.group(0).lower() for m in CAUSAL_PATTERN.finditer(text)})
    if causal:
        problems.append(f"causal language: {', '.join(causal)}")
    return problems


@dataclass(frozen=True)
class NoteClaim:
    claim_id: str
    section: Section
    text: str
    cited_fact_ids: tuple[str, ...]
    accepted: bool
    problems: tuple[str, ...] = ()
    reviewer_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "section": self.section.value,
            "text": self.text,
            "cited_fact_ids": list(self.cited_fact_ids),
            "accepted": self.accepted,
            "problems": list(self.problems),
            "reviewer_reason": self.reviewer_reason,
        }


@dataclass(frozen=True)
class AgentNote:
    """A published agentic note: only accepted claims appear in ``markdown``."""

    document_id: UUID
    model: str
    prompt_version: str
    schema_version: str
    metric_version: str
    facts: tuple[Fact, ...]
    claims: tuple[NoteClaim, ...]  # accepted and rejected, for audit
    markdown: str
    created_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if not any(c.accepted for c in self.claims):
            raise ValueError("a note needs at least one accepted claim")

    @property
    def accepted(self) -> list[NoteClaim]:
        return [c for c in self.claims if c.accepted]

    @property
    def rejected(self) -> list[NoteClaim]:
        return [c for c in self.claims if not c.accepted]
