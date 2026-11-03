"""End to end: FOMC fixture pages → parsed documents → tone analysis → comparison → market
metrics → research summary → published result in the API. All providers are fakes."""

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from fedtone.api.app import create_app
from fedtone.application.pipeline import CYCLE
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemyToneAnalysisRepository,
)
from tests.e2e_support import PipelineLLM, e2e_settings, fixture_factories
from tests.fake_llm import comparison_json, documents, tone_json
from tests.market_harness import market_data

JANUARY = "solid pace. Job gains have moderated"  # text only in the 2024-01-31 fixture


def run_all(client: TestClient) -> dict[str, dict]:
    results = {}
    for stage in CYCLE:
        response = client.post(f"/api/v1/pipeline/{stage}")
        assert response.status_code == 200, (stage, response.text)
        results[stage.value] = response.json()["outcomes"]
    return results


def make_client(database_url, engine, **factory_args) -> TestClient:
    settings = e2e_settings(database_url)
    return TestClient(
        create_app(settings, engine=engine, adapter_factories=fixture_factories(**factory_args))
    )


def test_fixture_source_reaches_published_research_result(
    database_url, migrated_engine, session_factory
) -> None:
    client = make_client(database_url, migrated_engine)

    outcomes = run_all(client)

    assert outcomes["ingest"]["ingested"] == 3
    assert outcomes["analyze"]["compared"] == 3
    assert outcomes["reactions"]["linked"] == 3
    assert outcomes["summarize"]["summarized"] == 3
    assert outcomes["publish"]["published"] == 3

    points = client.get("/api/v1/timeline").json()["points"]
    assert [p["published_at"][:10] for p in points] == ["2023-12-13", "2024-01-31", "2024-03-20"]
    assert {p["status"] for p in points} == {"Published"}
    assert all(p["net_tone_score"] is not None and p["evidence_count"] > 0 for p in points)
    assert points[0]["overall_delta"] is None and points[1]["overall_delta"] is not None

    detail = client.get(f"/api/v1/documents/{points[1]['document_id']}").json()
    assert detail["audit"]["source_url"].startswith("https://www.federalreserve.gov/")
    assert detail["audit"]["content_hash"] == detail["audit"]["analysis_document_hash"]
    assert detail["audit"]["parser_version"].startswith("fomc-statement-html")
    assert detail["comparison"]["diff"]["added"]
    assert detail["market_reaction"]["metrics"] and detail["market_reaction"]["missing"] == 0
    assert detail["summary"]["headline"]

    # The published analysis is the exact analysis shown by the API.
    docs = SqlAlchemyPolicyDocumentRepository(session_factory)
    stored = docs.get(UUID(detail["signal"]["document_id"]))
    assert str(stored.published_analysis_id) == detail["analysis"]["id"]

    # A second full cycle changes nothing.
    again = run_all(client)
    assert again["ingest"]["unchanged"] == 3
    assert again["publish"]["published"] == 0
    assert client.get("/api/v1/documents").json()["total"] == 3


def test_low_confidence_analysis_reaches_review_and_is_not_published(
    database_url, migrated_engine, session_factory
) -> None:
    llm = PipelineLLM(scores={JANUARY: (0.4, 0.3)})  # below min_confidence 0.6
    client = make_client(database_url, migrated_engine, llm=llm)

    run_all(client)

    points = {p["published_at"][:10]: p for p in client.get("/api/v1/timeline").json()["points"]}
    january = points["2024-01-31"]
    assert january["status"] == "NeedsReview" and january["needs_review"] is True
    assert points["2023-12-13"]["status"] == "Published"
    assert points["2024-03-20"]["status"] != "Published"  # its comparison waits for January
    reviews = client.get("/api/v1/reviews").json()
    assert [r["document_id"] for r in reviews] == [january["document_id"]]
    assert "confidence" in reviews[0]["reason"]


def test_invalid_evidence_blocks_publication(
    database_url, migrated_engine, session_factory
) -> None:
    def respond(request):
        if request.schema_name == "tone_comparison":
            return comparison_json(request)
        text = documents(request)["current"]
        if JANUARY in text:
            return tone_json(text, 0.6, 0.9, quote="The Committee will raise rates aggressively.")
        return tone_json(text)

    client = make_client(database_url, migrated_engine, llm=PipelineLLM(responder=respond))

    run_all(client)

    january = next(
        p
        for p in client.get("/api/v1/timeline").json()["points"]
        if p["published_at"].startswith("2024-01-31")
    )
    assert january["status"] == "NeedsReview"
    assert january["net_tone_score"] is None  # nothing unvalidated is stored
    analyses = SqlAlchemyToneAnalysisRepository(session_factory)
    assert analyses.latest_for_document(UUID(january["document_id"])) is None
    reviews = SqlAlchemyReviewItemRepository(session_factory).list_open(limit=None)
    assert any("evidence" in r.reason for r in reviews)


def test_missing_market_data_gives_explicit_partial_result(database_url, migrated_engine) -> None:
    data = market_data()
    data["DGS10"][date(2024, 2, 1)] = None  # provider row present, value empty
    client = make_client(database_url, migrated_engine, market=data)

    run_all(client)

    january = next(
        p
        for p in client.get("/api/v1/timeline").json()["points"]
        if p["published_at"].startswith("2024-01-31")
    )
    reaction = client.get(f"/api/v1/documents/{january['document_id']}/market-reaction").json()
    missing = [m for m in reaction["metrics"] if m["value"] is None]
    assert reaction["missing"] == len(missing) == 1
    assert missing[0]["symbol"] == "DGS10" and missing[0]["window"] == "[0,+1]"
    assert missing[0]["missing_reason"] == "provider_reported_missing"
    assert missing[0]["display"].startswith("not available")
    present = [m for m in reaction["metrics"] if m["value"] is not None]
    assert len(present) == 8  # the rest of the reaction is still reported
    note = client.get(f"/api/v1/documents/{january['document_id']}/research-note").text
    assert "not available (provider_reported_missing)" in note


@pytest.mark.parametrize("stage", ["analyze", "reactions", "summarize", "publish"])
def test_stages_on_an_empty_database_do_nothing(database_url, migrated_engine, stage) -> None:
    client = make_client(database_url, migrated_engine)

    response = client.post(f"/api/v1/pipeline/{stage}")

    assert response.status_code == 200
    assert not any(response.json()["outcomes"].values())
