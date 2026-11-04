"""Manual pipeline runs triggered through the API.

Each stage is the same application service the CLI runs; this module only dispatches to a
configured runner and normalizes its report. Stages whose provider is not configured (no
LLM key, no market-data provider) are reported as unavailable instead of failing midway.

Retrying is the normal pipeline behavior: documents that failed because a provider was
unavailable resume at their last successful stage on the next run of that stage, and a
resolved review item (``ReviewService.resolve(..., resume=True)``) lets its document continue.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from fedtone.domain.models import RunTrigger


class PipelineStage(StrEnum):
    INGEST = "ingest"
    ANALYZE = "analyze"
    REACTIONS = "reactions"
    SUMMARIZE = "summarize"
    PUBLISH = "publish"
    # Material tone-change alerts (Task 7.5).
    ALERTS = "alerts"
    # Optional: agentic research notes (Task 7.4); not part of the default cycle.
    AGENT_NOTES = "agent_notes"


# Order of one full pipeline cycle.
CYCLE = (
    PipelineStage.INGEST,
    PipelineStage.ANALYZE,
    PipelineStage.ALERTS,
    PipelineStage.REACTIONS,
    PipelineStage.SUMMARIZE,
    PipelineStage.PUBLISH,
)


class StageUnavailableError(RuntimeError):
    """The stage cannot run with the current configuration."""


@dataclass(frozen=True)
class PipelineResult:
    stage: PipelineStage
    outcomes: dict[str, int]
    aborted: str | None = None
    run_id: UUID | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.aborted is None


StageRunner = Callable[[str, str, RunTrigger], PipelineResult]


class PipelineService:
    def __init__(self, runners: Mapping[PipelineStage, StageRunner]) -> None:
        self._runners = dict(runners)

    def run(
        self,
        stage: PipelineStage,
        central_bank: str,
        document_type: str,
        *,
        trigger: RunTrigger = RunTrigger.MANUAL,
    ) -> PipelineResult:
        runner = self._runners.get(stage)
        if runner is None:
            raise StageUnavailableError(f"stage {stage} is not configured")
        return runner(central_bank, document_type, trigger)
