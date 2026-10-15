"""Historical analogue search over stored tone features, with a point-in-time cutoff.

Distance is the root-mean-square difference over the features both meetings have: the
overall score, each dimension score, and the overall tone delta (halved, because a delta
spans [-2, 2] while scores span [-1, 1]). Only meetings published strictly before the cutoff
are candidates, so a backtest never sees a later meeting. Ties are broken by publication
time (most recent first) and then by document id, which makes the ranking deterministic.

Limitation: tone features of past meetings are produced by today's model and prompt, not by
a model available at the time. The text they are computed from was public at the time.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class MeetingFeatures:
    document_id: UUID
    published_at: datetime
    overall: float
    dimensions: Mapping[str, float | None]
    overall_delta: float | None = None

    def vector(self) -> dict[str, float]:
        features = {"overall": self.overall}
        features.update({f"dim:{k}": v for k, v in self.dimensions.items() if v is not None})
        if self.overall_delta is not None:
            features["delta"] = self.overall_delta / 2.0
        return features


@dataclass(frozen=True)
class Analogue:
    document_id: UUID
    published_at: datetime
    distance: float
    shared_features: tuple[str, ...]


@dataclass(frozen=True)
class AnalogueResult:
    target_id: UUID
    cutoff: datetime
    matches: tuple[Analogue, ...]
    sample_size: int
    min_sample: int

    @property
    def insufficient_history(self) -> bool:
        return self.sample_size < self.min_sample


def distance(a: MeetingFeatures, b: MeetingFeatures) -> tuple[float, tuple[str, ...]]:
    va, vb = a.vector(), b.vector()
    shared = tuple(sorted(set(va) & set(vb)))
    total = sum((va[k] - vb[k]) ** 2 for k in shared)
    return math.sqrt(total / len(shared)), shared


def find_analogues(
    target: MeetingFeatures,
    candidates: Iterable[MeetingFeatures],
    *,
    cutoff: datetime | None = None,
    top_k: int = 5,
    min_sample: int = 3,
) -> AnalogueResult:
    """Rank earlier meetings by tone similarity to ``target``.

    ``cutoff`` defaults to the target's publication time; it may be earlier but never later,
    because features after the target's own release are not available at event time.
    """
    if top_k < 1 or min_sample < 1:
        raise ValueError("top_k and min_sample must be positive")
    cutoff = cutoff or target.published_at
    if cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    if cutoff > target.published_at:
        raise ValueError("cutoff cannot be later than the target meeting")

    eligible = [
        c for c in candidates if c.document_id != target.document_id and c.published_at < cutoff
    ]
    scored = []
    for candidate in eligible:
        d, shared = distance(target, candidate)
        scored.append(Analogue(candidate.document_id, candidate.published_at, d, shared))
    scored.sort(key=lambda a: (a.distance, -a.published_at.timestamp(), str(a.document_id)))
    return AnalogueResult(
        target_id=target.document_id,
        cutoff=cutoff,
        matches=tuple(scored[:top_k]),
        sample_size=len(eligible),
        min_sample=min_sample,
    )
