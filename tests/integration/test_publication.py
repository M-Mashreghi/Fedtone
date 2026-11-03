from fedtone.application.publication import PublicationOutcome, PublicationService
from fedtone.application.research_summary import ResearchSummaryService
from fedtone.domain.lifecycle import DocumentStatus
from tests.fake_llm import FakeLLM
from tests.integration.test_research_summary import NAMES, faithful
from tests.market_harness import MarketHarness


def publisher(h: MarketHarness) -> PublicationService:
    return PublicationService(
        documents=h.documents,
        analyses=h.analyses,
        summaries=h.summaries,
        reviews=h.reviews,
        clock=h.clock,
    )


def summarize(h: MarketHarness) -> None:
    ResearchSummaryService(
        client=FakeLLM(responder=faithful),
        documents=h.documents,
        analyses=h.analyses,
        comparisons=h.comparisons,
        metrics=h.metrics,
        summaries=h.summaries,
        reviews=h.reviews,
        instrument_names=NAMES,
        clock=h.clock,
    ).run("FED", "fomc_statement")


def test_documents_wait_for_a_summary_then_publish(session_factory) -> None:
    h = MarketHarness(session_factory)
    h.reaction_service().run("FED", "fomc_statement")

    waiting = publisher(h).run("FED", "fomc_statement")
    summarize(h)
    done = publisher(h).run("FED", "fomc_statement")

    assert waiting.outcomes[PublicationOutcome.AWAITING_SUMMARY] == 3
    assert done.outcomes[PublicationOutcome.PUBLISHED] == 3
    jan = h.doc("jan")
    assert jan.status is DocumentStatus.PUBLISHED
    assert jan.published_analysis_id == h.analyses.latest_for_document(jan.id).id
    assert publisher(h).run("FED", "fomc_statement").outcomes[PublicationOutcome.PUBLISHED] == 0


def test_analysis_of_another_document_version_is_not_published(session_factory) -> None:
    h = MarketHarness(session_factory)
    h.reaction_service().run("FED", "fomc_statement")
    summarize(h)
    jan = h.doc("jan")
    jan.content_hash = "0" * 64  # source changed after the analysis was produced
    h.documents.update(jan)

    report = publisher(h).run("FED", "fomc_statement")

    assert report.outcomes[PublicationOutcome.REJECTED] == 1
    assert report.outcomes[PublicationOutcome.PUBLISHED] == 2
    jan = h.doc("jan")
    assert jan.status is DocumentStatus.NEEDS_REVIEW
    assert jan.last_successful_status is DocumentStatus.MARKET_LINKED
    (item,) = h.reviews.list_open(document_id=jan.id)
    assert "different document version" in item.reason
