"""Scheduled pipeline cycles: idempotent, resumable after transient failures."""

import threading

from sqlalchemy import func, select

from fedtone.application.pipeline import (
    PipelineResult,
    PipelineService,
    PipelineStage,
    StageUnavailableError,
)
from fedtone.application.scheduler import PipelineScheduler
from fedtone.domain.models import RunTrigger
from fedtone.infrastructure.persistence.orm import (
    PolicyDocumentRow,
    SourceSnapshotRow,
    ToneAnalysisRow,
)
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
)
from fedtone.infrastructure.pipeline import default_stage_runners
from tests.e2e_support import PipelineLLM, e2e_settings, fixture_factories


def scheduler_for(database_url, session_factory, llm=None) -> PipelineScheduler:
    runners = default_stage_runners(
        e2e_settings(database_url), session_factory, fixture_factories(llm=llm)
    )
    return PipelineScheduler(PipelineService(runners))


def count(session_factory, row) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(row))


def outcomes_of(report) -> dict:
    return {r.stage: r.outcomes for r in report.results}


def test_repeated_cycles_create_no_duplicates(database_url, session_factory) -> None:
    scheduler = scheduler_for(database_url, session_factory)

    first = scheduler.run_cycle()
    second = scheduler.run_cycle()

    assert first.healthy and second.healthy
    assert outcomes_of(first)[PipelineStage.PUBLISH]["published"] == 3
    assert count(session_factory, PolicyDocumentRow) == 3
    assert count(session_factory, SourceSnapshotRow) == 3
    assert count(session_factory, ToneAnalysisRow) == 3
    again = outcomes_of(second)
    assert again[PipelineStage.INGEST]["unchanged"] == 3
    assert not any(again[PipelineStage.ANALYZE].values())
    runs = SqlAlchemyProcessingRunRepository(session_factory).list_recent()
    assert len(runs) == 2 and {r.trigger for r in runs} == {RunTrigger.SCHEDULED}


def test_transient_llm_outage_resumes_on_next_cycle(database_url, session_factory) -> None:
    llm = PipelineLLM(outage=True)
    scheduler = scheduler_for(database_url, session_factory, llm=llm)

    first = scheduler.run_cycle()
    documents = SqlAlchemyPolicyDocumentRepository(session_factory).list(limit=None)
    llm.outage = False
    second = scheduler.run_cycle()

    assert outcomes_of(first)[PipelineStage.ANALYZE]["failed"] >= 1
    assert {d.status.value for d in documents} <= {"Failed", "QueuedForAnalysis"}
    assert outcomes_of(second)[PipelineStage.PUBLISH]["published"] == 3
    assert count(session_factory, ToneAnalysisRow) == 3


def result(stage, **outcomes) -> PipelineResult:
    return PipelineResult(stage, outcomes)


def test_unconfigured_stage_is_skipped_and_crash_does_not_stop_cycle() -> None:
    calls = []

    def ok(stage):
        def run(bank, doc_type, trigger):
            calls.append((stage, trigger))
            return result(stage, done=1)

        return run

    def unavailable(bank, doc_type, trigger):
        raise StageUnavailableError("no market-data provider configured")

    def crash(bank, doc_type, trigger):
        raise RuntimeError("boom")

    scheduler = PipelineScheduler(
        PipelineService(
            {
                PipelineStage.INGEST: ok(PipelineStage.INGEST),
                PipelineStage.ANALYZE: crash,
                PipelineStage.REACTIONS: unavailable,
                PipelineStage.SUMMARIZE: ok(PipelineStage.SUMMARIZE),
            }
        )
    )

    report = scheduler.run_cycle()

    assert [c[0] for c in calls] == [PipelineStage.INGEST, PipelineStage.SUMMARIZE]
    assert {c[1] for c in calls} == {RunTrigger.SCHEDULED}
    assert report.errors == {PipelineStage.ANALYZE: "RuntimeError: boom"}
    assert set(report.skipped) == {PipelineStage.REACTIONS, PipelineStage.PUBLISH}
    assert not report.healthy


def test_cycles_never_overlap() -> None:
    entered, release = threading.Event(), threading.Event()

    def slow(bank, doc_type, trigger):
        entered.set()
        release.wait(5)
        return result(PipelineStage.INGEST)

    scheduler = PipelineScheduler(
        PipelineService({PipelineStage.INGEST: slow}), stages=[PipelineStage.INGEST]
    )
    worker = threading.Thread(target=scheduler.run_cycle)
    worker.start()
    entered.wait(5)

    assert scheduler.run_cycle() is None
    release.set()
    worker.join(5)
    assert scheduler.run_cycle() is not None


def test_run_forever_stops_on_event_and_max_cycles() -> None:
    ticks = []

    def tick(bank, doc_type, trigger):
        ticks.append(trigger)
        return result(PipelineStage.INGEST)

    scheduler = PipelineScheduler(
        PipelineService({PipelineStage.INGEST: tick}), stages=[PipelineStage.INGEST]
    )

    assert scheduler.run_forever(0.01, stop=threading.Event(), max_cycles=3) == 3
    stopped = threading.Event()
    stopped.set()
    assert scheduler.run_forever(0.01, stop=stopped) == 0
    assert len(ticks) == 3
