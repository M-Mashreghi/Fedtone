"""Material tone-change alerts (Task 7.5).

Alerts are evaluated by deterministic code over stored analyses and comparisons; the LLM is
not involved. A rule fires only when its configured condition holds, and only for documents
whose comparison used analyses of the same model/prompt/schema (otherwise the delta is not
computed and no delta rule can fire).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from fedtone.domain.models import DocumentComparison, ToneAnalysis


class AlertRule(StrEnum):
    OVERALL_DELTA = "overall_delta"
    CLASSIFICATION_CHANGE = "classification_change"
    DIMENSION_DELTA = "dimension_delta"


@dataclass(frozen=True)
class AlertConfig:
    """Thresholds; ``None`` disables a rule."""

    min_abs_overall_delta: float | None = 0.3
    classification_change: bool = True
    min_abs_dimension_delta: float | None = 0.5
    min_confidence: float = 0.0

    def __post_init__(self) -> None:
        for name in ("min_abs_overall_delta", "min_abs_dimension_delta"):
            value = getattr(self, name)
            if value is not None and not 0 < value <= 2:
                raise ValueError(f"{name} must be in (0, 2]")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be in [0, 1]")


@dataclass(frozen=True)
class Alert:
    document_id: UUID
    rule: AlertRule
    message: str
    values: dict[str, object]
    created_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")


def evaluate_alerts(
    config: AlertConfig,
    current: ToneAnalysis,
    previous: ToneAnalysis | None,
    comparison: DocumentComparison | None,
    *,
    now: datetime,
) -> list[Alert]:
    if comparison is None or comparison.is_baseline or previous is None:
        return []
    if min(current.confidence, previous.confidence) < config.min_confidence:
        return []
    alerts = []
    delta = comparison.overall_delta
    threshold = config.min_abs_overall_delta
    if threshold is not None and delta is not None and abs(delta) >= threshold:
        alerts.append(
            Alert(
                current.document_id,
                AlertRule.OVERALL_DELTA,
                f"overall tone moved {delta:+.2f} (threshold {threshold:.2f})",
                {"overall_delta": delta, "threshold": threshold},
                now,
            )
        )
    comparable = (current.model, current.prompt_version, current.schema_version) == (
        previous.model,
        previous.prompt_version,
        previous.schema_version,
    )
    if (
        config.classification_change
        and comparable
        and current.classification != previous.classification
    ):
        alerts.append(
            Alert(
                current.document_id,
                AlertRule.CLASSIFICATION_CHANGE,
                f"label changed {previous.classification} -> {current.classification}",
                {
                    "previous": previous.classification.value,
                    "current": current.classification.value,
                },
                now,
            )
        )
    dim_threshold = config.min_abs_dimension_delta
    if dim_threshold is not None:
        moved = {
            name: value
            for name, value in sorted(comparison.dimension_deltas.items())
            if value is not None and abs(value) >= dim_threshold
        }
        if moved:
            listing = ", ".join(f"{name} {value:+.2f}" for name, value in moved.items())
            alerts.append(
                Alert(
                    current.document_id,
                    AlertRule.DIMENSION_DELTA,
                    f"dimension tone moved: {listing} (threshold {dim_threshold:.2f})",
                    {"dimension_deltas": moved, "threshold": dim_threshold},
                    now,
                )
            )
    return alerts
