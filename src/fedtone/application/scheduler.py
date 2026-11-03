"""Recurring pipeline cycles for new official releases.

One cycle runs every stage in order (ingest → analyze → reactions → summarize → publish).
Running it again is safe:

- ingestion is idempotent on the canonical identity (bank, type, URL) and the content hash,
  so a repeated discovery creates no document or snapshot twice;
- every later stage only picks documents at its input status, so finished work is skipped;
- documents that failed on a provider outage resume at their last successful stage on the
  next cycle (bounded HTTP/LLM retries happen inside each adapter first).

A stage that is not configured (e.g. no market-data provider) is skipped, and an unexpected
error in one stage is logged and does not stop the following stages. Cycles never overlap
within one process; run a single scheduler process per database.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fedtone.application.pipeline import (
    CYCLE,
    PipelineResult,
    PipelineService,
    PipelineStage,
    StageUnavailableError,
)
from fedtone.domain.models import RunTrigger
from fedtone.observability import correlation_context

logger = logging.getLogger(__name__)


@dataclass
class CycleReport:
    started_at: datetime
    id: UUID = field(default_factory=uuid4)
    finished_at: datetime | None = None
    results: list[PipelineResult] = field(default_factory=list)
    skipped: dict[PipelineStage, str] = field(default_factory=dict)
    errors: dict[PipelineStage, str] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return not self.errors and all(r.succeeded for r in self.results)


class PipelineScheduler:
    def __init__(
        self,
        pipeline: PipelineService,
        *,
        central_bank: str = "FED",
        document_type: str = "fomc_statement",
        stages: Sequence[PipelineStage] = CYCLE,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pipeline = pipeline
        self._bank = central_bank
        self._type = document_type
        self._stages = tuple(stages)
        self._clock = clock
        self._lock = threading.Lock()

    def run_cycle(self) -> CycleReport | None:
        """Run all stages once; returns None if a cycle is already in progress."""
        if not self._lock.acquire(blocking=False):
            logger.warning("previous pipeline cycle still running; skipping this tick")
            return None
        report = CycleReport(started_at=self._clock())
        try:
            with correlation_context(run_id=report.id):
                self._run_stages(report)
            return report
        finally:
            self._lock.release()

    def _run_stages(self, report: CycleReport) -> None:
        for stage in self._stages:
            try:
                result = self._pipeline.run(
                    stage, self._bank, self._type, trigger=RunTrigger.SCHEDULED
                )
            except StageUnavailableError as exc:
                logger.info("stage %s skipped: %s", stage, exc)
                report.skipped[stage] = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001 - one stage must not stop the cycle
                logger.exception("stage %s crashed", stage)
                report.errors[stage] = f"{exc.__class__.__name__}: {exc}"
                continue
            report.results.append(result)
            if not result.succeeded:
                logger.error("stage %s aborted: %s", stage, result.aborted)
        report.finished_at = self._clock()
        logger.info(
            "pipeline cycle finished: %s",
            ", ".join(f"{r.stage}={r.outcomes}" for r in report.results) or "nothing ran",
        )

    def run_forever(
        self,
        interval_seconds: float,
        *,
        stop: threading.Event,
        max_cycles: int | None = None,
    ) -> int:
        """Run cycles until ``stop`` is set (or ``max_cycles`` ran). Returns cycles run."""
        if interval_seconds <= 0:
            raise ValueError("interval must be positive")
        cycles = 0
        while not stop.is_set():
            self.run_cycle()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            stop.wait(interval_seconds)
        return cycles
