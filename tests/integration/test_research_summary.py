import json

import pytest

from fedtone.application.llm import LLMPermanentError, LLMRequest, LLMTransientError
from fedtone.application.research_summary import (
    SUMMARY_PROMPT_VERSION,
    ResearchSummaryService,
    SummaryOutcome,
)
from fedtone.application.review import ReviewService
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.metrics import METRIC_VERSION
from tests.fake_llm import FakeLLM
from tests.market_harness import MarketHarness

NAMES = {"SP500": "S&P 500 index", "DGS10": "10-Year Treasury yield"}


def facts_of(request: LLMRequest) -> dict[str, dict]:
    payload = request.user.split("<<<FACTS>>>\n", 1)[1].rsplit("\n<<<END_FACTS>>>", 1)[0]
    return {f["id"]: f for f in json.loads(payload)}


def faithful(request: LLMRequest) -> str:
    facts = facts_of(request)
    spx = facts["SP500_simple_return_[0,+1]"]["display"]
    return json.dumps(
        {
            "schema_version": "research-summary-1.0",
            "headline": f"Tone score {facts['tone']['display']}",
            "summary": f"Over the [0,+1] window the S&P 500 return was {spx}, "
            "which coincided with the statement.",
            "cited_fact_ids": ["tone", "SP500_simple_return_[0,+1]"],
            "uncertainty_flags": [],
        }
    )


def injected(request: LLMRequest) -> str:
    answer = json.loads(faithful(request))
    answer["summary"] += " Stocks rallied 2.75% the following week."
    return json.dumps(answer)


@pytest.fixture
def harness(session_factory) -> MarketHarness:
    h = MarketHarness(session_factory)
    h.reaction_service().run("FED", "fomc_statement")
    return h


def run(h: MarketHarness, llm) -> object:
    return ResearchSummaryService(
        client=llm,
        documents=h.documents,
        analyses=h.analyses,
        comparisons=h.comparisons,
        metrics=h.metrics,
        summaries=h.summaries,
        reviews=h.reviews,
        instrument_names=NAMES,
        clock=h.clock,
    ).run("FED", "fomc_statement")


def test_summary_references_canonical_metrics_exactly(harness) -> None:
    llm = FakeLLM(responder=faithful)

    report = run(harness, llm)

    assert report.outcomes[SummaryOutcome.SUMMARIZED] == 3
    jan = harness.doc("jan")
    summary = harness.summaries.latest_for_document(jan.id)
    facts = {f.id: f for f in summary.facts}
    spx = [
        m
        for m in harness.metrics.for_document(jan.id)
        if (m.symbol, m.window, m.kind.value) == ("SP500", "[0,+1]", "simple_return")
    ][0]
    assert facts["SP500_simple_return_[0,+1]"].display == f"{spx.value * 100:+.2f}%"
    assert facts["SP500_simple_return_[0,+1]"].display in summary.summary
    assert (summary.prompt_version, summary.metric_version) == (
        SUMMARY_PROMPT_VERSION,
        METRIC_VERSION,
    )
    assert summary.model == "fake-model-1"
    assert jan.status is DocumentStatus.MARKET_LINKED
    # The prompt carries facts only; it never asks the model to compute anything.
    assert "Never round, convert, add, subtract or compute" in llm.requests[0].system


def test_existing_summary_is_not_regenerated(harness) -> None:
    run(harness, FakeLLM(responder=faithful))
    llm = FakeLLM(responder=faithful)
    assert run(harness, llm).outcomes[SummaryOutcome.SUMMARIZED] == 0
    assert llm.requests == []


def test_unsupported_number_injection_fails_validation(harness) -> None:
    llm = FakeLLM(responder=injected)

    report = run(harness, llm)

    assert report.outcomes[SummaryOutcome.REJECTED] == 3
    jan = harness.doc("jan")
    assert harness.summaries.latest_for_document(jan.id) is None
    assert jan.status is DocumentStatus.NEEDS_REVIEW
    assert jan.last_successful_status is DocumentStatus.MARKET_LINKED
    (item,) = harness.reviews.list_open(document_id=jan.id)
    assert "number not in facts: 2.75" in item.reason
    assert len(llm.requests) == 6  # one retry per document


def test_invalid_first_answer_is_retried_once(harness) -> None:
    llm = FakeLLM(script=["not json"], responder=faithful)
    report = run(harness, llm)
    assert report.outcomes[SummaryOutcome.SUMMARIZED] == 3


def test_provider_outage_fails_and_next_run_resumes(harness) -> None:
    report = run(harness, FakeLLM(script=[LLMTransientError("timeout")], responder=faithful))
    assert report.outcomes[SummaryOutcome.FAILED] == 1
    failed = [d for d in harness.documents.list(limit=None) if d.status is DocumentStatus.FAILED]
    assert len(failed) == 1

    again = run(harness, FakeLLM(responder=faithful))
    assert again.outcomes[SummaryOutcome.SUMMARIZED] == 1
    assert harness.documents.get(failed[0].id).status is DocumentStatus.MARKET_LINKED


def test_permanent_provider_error_aborts(harness) -> None:
    report = run(harness, FakeLLM(script=[LLMPermanentError("401")]))
    assert report.aborted == "401"
    assert all(d.status is DocumentStatus.MARKET_LINKED for d in harness.documents.list(limit=None))


def test_summary_rejected_by_reviewer_is_not_regenerated(harness) -> None:
    run(harness, FakeLLM(responder=injected))
    jan = harness.doc("jan")
    (item,) = harness.reviews.list_open(document_id=jan.id)
    ReviewService(harness.documents, harness.reviews, clock=harness.clock).resolve(
        item.id, "numbers unreliable", resume=False
    )

    llm = FakeLLM(responder=faithful)
    run(harness, llm)

    assert harness.documents.get(jan.id).status is DocumentStatus.FAILED
    assert harness.summaries.latest_for_document(jan.id) is None
