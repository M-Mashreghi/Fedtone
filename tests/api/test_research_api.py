"""Contract tests for the /api/v1 research routes."""

import csv
import io
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fedtone.api.app import create_app
from fedtone.application.pipeline import PipelineResult, PipelineStage
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.models import ReviewItem, transition
from tests.market_harness import NOW


def _parse_time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_document_list_returns_stored_signals_in_release_order(client, harness) -> None:
    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    names = ["dec", "jan", "mar"]
    assert [i["document_id"] for i in body["items"]] == [str(harness.doc(n).id) for n in names]
    for item, name in zip(body["items"], names, strict=True):
        doc = harness.doc(name)
        analysis = harness.analyses.latest_for_document(doc.id)
        comparison = harness.comparisons.latest_for_document(doc.id)
        assert item["net_tone_score"] == analysis.net_tone_score
        assert item["confidence"] == analysis.confidence
        assert item["classification"] == analysis.classification.value
        assert item["overall_delta"] == comparison.overall_delta
        assert item["dim_inflation"] == analysis.dimension_scores["inflation"]
        assert item["model"] == analysis.model
        assert item["prompt_version"] == analysis.prompt_version
        assert item["content_hash"] == doc.content_hash
        assert item["status"] == "MarketLinked"
        assert item["needs_review"] is False
    # The first statement is a baseline: no delta, not an error.
    assert body["items"][0]["overall_delta"] is None


def test_document_list_paginates_and_filters(client, harness) -> None:
    page = client.get("/api/v1/documents", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3
    assert [i["document_id"] for i in page["items"]] == [str(harness.doc("jan").id)]

    ranged = client.get(
        "/api/v1/documents",
        params={"published_from": "2024-01-01T00:00:00+00:00"},
    ).json()
    assert {i["document_id"] for i in ranged["items"]} == {
        str(harness.doc("jan").id),
        str(harness.doc("mar").id),
    }
    assert client.get("/api/v1/documents", params={"model": "other"}).json()["total"] == 0
    assert client.get("/api/v1/documents", params={"central_bank": "ECB"}).json()["total"] == 0
    assert client.get("/api/v1/documents", params={"status": "Failed"}).json()["total"] == 0


def test_document_filters_reject_bad_input(client) -> None:
    assert client.get("/api/v1/documents", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/documents", params={"status": "Nope"}).status_code == 422
    naive = client.get("/api/v1/documents", params={"published_from": "2024-01-01T00:00:00"})
    assert naive.status_code == 422


def test_document_detail_shows_source_score_diff_evidence_and_reaction(client, harness) -> None:
    jan = harness.doc("jan")

    body = client.get(f"/api/v1/documents/{jan.id}").json()

    analysis = harness.analyses.latest_for_document(jan.id)
    assert body["title"] == "Statement jan"
    assert body["paragraphs"]
    assert body["analysis"]["net_tone_score"] == analysis.net_tone_score
    assert [e["text"] for e in body["analysis"]["evidence"]] == [e.text for e in analysis.evidence]
    comparison = body["comparison"]
    assert comparison["is_baseline"] is False
    assert comparison["previous_document_id"] == str(harness.doc("dec").id)
    assert comparison["previous_net_tone_score"] == 0.5
    assert comparison["overall_delta"] == pytest.approx(-0.3)
    assert comparison["diff"]["added"] and comparison["diff"]["removed"]
    reaction = body["market_reaction"]
    assert len(reaction["metrics"]) == 9 and reaction["missing"] == 0
    assert body["summary"]["headline"]
    audit = body["audit"]
    assert audit["source_url"] == jan.source_url
    assert audit["content_hash"] == jan.content_hash == audit["analysis_document_hash"]
    assert audit["model"] == "fake-model-1"
    assert audit["prompt_version"] == "tone-statement-v1"
    assert audit["schema_version"] == "tone-analysis-1.0"
    assert audit["parser_version"] == "manual-text-1"
    assert audit["raw_hash"] and audit["snapshot_version"] == 1
    assert audit["metric_version"] == "market-metrics-1.0"
    assert audit["history"][-1]["to_status"] == "MarketLinked"


def test_market_reaction_values_equal_canonical_metrics(client, harness) -> None:
    jan = harness.doc("jan")
    stored = {(m.symbol, m.window, m.kind.value): m for m in harness.metrics.for_document(jan.id)}

    body = client.get(f"/api/v1/documents/{jan.id}/market-reaction").json()

    assert body["backtest_mode"] is False
    assert len(body["metrics"]) == len(stored)
    for metric in body["metrics"]:
        canonical = stored[(metric["symbol"], metric["window"], metric["kind"])]
        assert metric["value"] == canonical.value  # exact, not approx
        assert metric["unit"] == canonical.unit.value
        assert metric["start_date"] == canonical.start_date.isoformat()
        assert metric["end_date"] == canonical.end_date.isoformat()
    units = {m["kind"]: m["unit"] for m in body["metrics"]}
    assert units["yield_change"] == "bps" and units["simple_return"] == "decimal_return"
    spx = next(
        m
        for m in body["metrics"]
        if (m["symbol"], m["window"]) == ("SP500", "[0,+1]") and m["kind"] == "simple_return"
    )
    assert spx["display"].endswith("%")
    # Windows are ordered by their definition, not alphabetically.
    assert [m["window"] for m in body["metrics"] if m["symbol"] == "DGS10"] == [
        "[0,0]",
        "[0,+1]",
        "[0,+5]",
    ]


def test_backtest_metrics_are_flagged(session_factory, api_settings, migrated_engine) -> None:
    from tests.market_harness import MarketHarness

    h = MarketHarness(session_factory)
    h.reaction_service().run("FED", "fomc_statement", as_of=datetime(2024, 2, 5, tzinfo=UTC))
    client = TestClient(create_app(api_settings, engine=migrated_engine))

    dec = client.get(f"/api/v1/documents/{h.doc('dec').id}/market-reaction").json()
    jan = client.get(f"/api/v1/documents/{h.doc('jan').id}/market-reaction").json()

    assert dec["backtest_mode"] is True
    assert dec["metrics"] and all(m["backtest_mode"] for m in dec["metrics"])
    assert {_parse_time(m["as_of"]) for m in dec["metrics"]} == {datetime(2024, 2, 5, tzinfo=UTC)}
    # The January [0,+5] window had not closed by the cut-off, so nothing was linked yet.
    assert jan["metrics"] == [] and jan["backtest_mode"] is False


def test_analysis_and_comparison_routes(client, harness) -> None:
    dec = harness.doc("dec")

    analysis = client.get(f"/api/v1/documents/{dec.id}/analysis").json()
    comparison = client.get(f"/api/v1/documents/{dec.id}/comparison").json()

    assert analysis["net_tone_score"] == 0.5
    assert comparison["is_baseline"] is True
    assert comparison["overall_delta"] is None


def test_unknown_document_returns_404(client) -> None:
    missing = uuid4()
    for path in ("", "/analysis", "/comparison", "/market-reaction", "/audit", "/analogues"):
        assert client.get(f"/api/v1/documents/{missing}{path}").status_code == 404
    assert client.get("/api/v1/documents/not-a-uuid").status_code == 422


def test_analogues_use_only_earlier_meetings(client, harness) -> None:
    mar = harness.doc("mar")

    body = client.get(f"/api/v1/documents/{mar.id}/analogues").json()

    assert _parse_time(body["cutoff"]) == mar.published_at
    assert {m["document_id"] for m in body["matches"]} == {
        str(harness.doc("jan").id),
        str(harness.doc("dec").id),
    }
    assert body["insufficient_history"] is True  # 2 earlier meetings < default min sample 3
    for match in body["matches"]:
        assert all(_parse_time(r["end_observed_at"]) < mar.published_at for r in match["reactions"])

    earlier = client.get(
        f"/api/v1/documents/{mar.id}/analogues", params={"cutoff": "2024-01-01T00:00:00+00:00"}
    ).json()
    assert [m["document_id"] for m in earlier["matches"]] == [str(harness.doc("dec").id)]
    later = client.get(
        f"/api/v1/documents/{mar.id}/analogues", params={"cutoff": "2024-12-01T00:00:00+00:00"}
    )
    assert later.status_code == 422


def test_timeline_points_equal_document_list(client) -> None:
    timeline = client.get("/api/v1/timeline").json()
    documents = client.get("/api/v1/documents", params={"limit": 500}).json()

    assert timeline["dimensions"] == [
        "inflation",
        "labor",
        "growth",
        "policy_guidance",
        "balance_sheet",
    ]
    assert timeline["points"] == documents["items"]


def test_csv_and_json_exports_reproduce_api_values(client) -> None:
    points = client.get("/api/v1/timeline").json()["points"]

    exported = json.loads(client.get("/api/v1/exports/signals", params={"format": "json"}).text)
    response = client.get("/api/v1/exports/signals")
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert response.headers["content-type"].startswith("text/csv")
    assert len(exported) == len(rows) == len(points) == 3
    for point, record, row in zip(points, exported, rows, strict=True):
        assert set(point) == set(record) == set(row)
        for key, value in point.items():
            if key in ("published_at", "analyzed_at"):
                assert _parse_time(record[key]) == _parse_time(value)
                assert _parse_time(row[key]) == _parse_time(value)
            elif isinstance(value, bool):
                assert record[key] is value and row[key] == str(value)
            elif isinstance(value, float):
                assert record[key] == value and float(row[key]) == value
            elif value is None:
                assert record[key] is None and row[key] == ""
            else:
                assert record[key] == value and row[key] == str(value)


def test_export_rejects_unknown_format(client) -> None:
    assert client.get("/api/v1/exports/signals", params={"format": "xml"}).status_code == 422


def test_research_note_contains_canonical_values_and_audit(client, harness) -> None:
    jan = harness.doc("jan")
    detail = client.get(f"/api/v1/documents/{jan.id}").json()

    response = client.get(f"/api/v1/documents/{jan.id}/research-note")

    assert response.headers["content-type"].startswith("text/markdown")
    note = response.text
    assert note.startswith("# FED Statement jan (2024-01-31)")
    for metric in detail["market_reaction"]["metrics"]:
        assert f"| {metric['window']} | {metric['display']} |" in note
    assert detail["summary"]["headline"] in note
    assert "association, not causation" in note
    for key in ("content_hash", "source_url", "model", "prompt_version", "schema_version"):
        assert str(detail["audit"][key]) in note
    assert detail["analysis"]["evidence"][0]["text"] in note


def test_reviews_can_be_listed_and_resolved_to_resume(
    session_factory, api_settings, migrated_engine
) -> None:
    from tests.market_harness import MarketHarness

    h = MarketHarness(session_factory)
    mar = h.doc("mar")
    transition(mar, DocumentStatus.NEEDS_REVIEW, at=NOW, reason="check evidence")
    h.documents.update(mar)
    item = ReviewItem(mar.id, DocumentStatus.COMPARED, "check evidence", NOW)
    h.reviews.add(item)
    client = TestClient(create_app(api_settings, engine=migrated_engine))

    listed = client.get("/api/v1/reviews").json()
    flagged = client.get(f"/api/v1/documents/{mar.id}").json()
    resolved = client.post(
        f"/api/v1/reviews/{item.id}/resolve", json={"note": "evidence fine", "resume": True}
    )

    assert [r["id"] for r in listed] == [str(item.id)]
    assert flagged["signal"]["needs_review"] is True
    assert flagged["open_reviews"][0]["reason"] == "check evidence"
    assert resolved.status_code == 200
    assert resolved.json()["resolution"] == "evidence fine"
    assert h.doc("mar").status is DocumentStatus.COMPARED
    assert client.get("/api/v1/reviews").json() == []
    again = client.post(f"/api/v1/reviews/{item.id}/resolve", json={"note": "x"})
    assert again.status_code == 409
    assert client.post(f"/api/v1/reviews/{uuid4()}/resolve", json={"note": "x"}).status_code == 404


def test_pipeline_stage_dispatches_to_runner(api_settings, migrated_engine) -> None:
    calls = []

    def analyze(bank: str, doc_type: str) -> PipelineResult:
        calls.append((bank, doc_type))
        return PipelineResult(PipelineStage.ANALYZE, {"analyzed": 2})

    def failing(bank: str, doc_type: str) -> PipelineResult:
        return PipelineResult(PipelineStage.SUMMARIZE, {}, aborted="provider rejected the key")

    client = TestClient(
        create_app(
            api_settings,
            engine=migrated_engine,
            stage_runners={PipelineStage.ANALYZE: analyze, PipelineStage.SUMMARIZE: failing},
        )
    )

    ok = client.post("/api/v1/pipeline/analyze", params={"central_bank": "FED"})
    aborted = client.post("/api/v1/pipeline/summarize")
    missing = client.post("/api/v1/pipeline/ingest")
    unknown = client.post("/api/v1/pipeline/deploy")

    assert ok.status_code == 200 and ok.json()["outcomes"] == {"analyzed": 2}
    assert calls == [("FED", "fomc_statement")]
    assert aborted.status_code == 502 and aborted.json()["aborted"] == "provider rejected the key"
    assert missing.status_code == 409
    assert unknown.status_code == 422


def test_unconfigured_llm_stage_is_unavailable_without_leaking_config(client) -> None:
    response = client.post("/api/v1/pipeline/analyze")

    assert response.status_code == 409
    assert "FEDTONE_LLM__API_KEY" in response.json()["detail"]


def test_telemetry_reports_counts_runs_and_versions(client) -> None:
    body = client.get("/api/v1/system/telemetry").json()

    assert body["documents_by_status"]["MarketLinked"] == 3
    assert body["open_reviews"] == 0
    assert body["recent_runs"][0]["status"] == "succeeded"
    assert body["versions"]["tone_schema"] == "tone-analysis-1.0"
    assert client.get("/api/v1/runs").json() == body["recent_runs"]


def test_openapi_documents_research_routes(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    for path in (
        "/api/v1/documents",
        "/api/v1/documents/{document_id}",
        "/api/v1/documents/{document_id}/comparison",
        "/api/v1/documents/{document_id}/market-reaction",
        "/api/v1/timeline",
        "/api/v1/exports/signals",
        "/api/v1/pipeline/{stage}",
        "/api/v1/system/telemetry",
    ):
        assert path in paths
