# ruff: noqa: F811 - pytest fixtures are imported and requested by name
"""Alerts stage and historical search / grounded answers through the API (Task 7.5)."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from fedtone.api.app import create_app
from fedtone.application.historical_search import (
    AnswerOutput,
    HistoricalSearchService,
    check_answer,
    tokens,
)
from fedtone.application.llm import LLMRequest, LLMResponse
from fedtone.application.pipeline import PipelineStage
from fedtone.infrastructure.alerts import WebhookNotifier
from fedtone.infrastructure.pipeline import AdapterFactories
from fedtone.settings import Settings
from tests.api.conftest import harness  # noqa: F401
from tests.fake_llm import FakeLLM


def make_client(database_url, engine, *, webhook=None, llm=None, **alert_settings) -> TestClient:
    alerts = {"min_confidence": 0.0, **alert_settings}
    if webhook is not None:
        alerts["webhook_url"] = "https://hooks.example.test/fedtone?token=secret-token-1"
    factories = AdapterFactories(
        llm_client=(lambda s: llm) if llm else AdapterFactories.llm_client,
        alert_webhook=(
            (lambda client, url: WebhookNotifier(httpx.Client(transport=webhook), url))
            if webhook is not None
            else AdapterFactories.alert_webhook
        ),
    )
    settings = Settings(database={"url": database_url}, alerts=alerts)
    return TestClient(create_app(settings, engine=engine, adapter_factories=factories))


# -- alerts ----------------------------------------------------------------------------------


def test_alert_stage_fires_once_and_notifies(harness, database_url, migrated_engine) -> None:
    received = []

    def hook(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(204)

    client = make_client(database_url, migrated_engine, webhook=httpx.MockTransport(hook))

    first = client.post("/api/v1/pipeline/alerts").json()
    again = client.post("/api/v1/pipeline/alerts").json()
    alerts = client.get("/api/v1/alerts").json()

    # Harness tones: dec +0.5 (hawkish) -> jan +0.2 (hawkish) -> mar -0.1 (neutral).
    jan, mar = str(harness.doc("jan").id), str(harness.doc("mar").id)
    by_doc = {}
    for alert in alerts:
        by_doc.setdefault(alert["document_id"], set()).add(alert["rule"])
    assert by_doc == {jan: {"overall_delta"}, mar: {"overall_delta", "classification_change"}}
    assert first["outcomes"]["fired"] == 3 and again["outcomes"]["fired"] == 0
    assert len(received) == len(alerts)
    assert all(r["document_id"] for r in received)
    assert client.get("/api/v1/alerts", params={"document_id": jan}).json()


def test_thresholds_control_which_alerts_fire(harness, database_url, migrated_engine) -> None:
    client = make_client(
        database_url,
        migrated_engine,
        min_abs_overall_delta=0.5,
        classification_change=False,
        min_abs_dimension_delta=None,
    )

    result = client.post("/api/v1/pipeline/alerts").json()

    assert result["outcomes"]["fired"] == 0
    assert client.get("/api/v1/alerts").json() == []


def test_disabled_alerts_and_failing_webhook(harness, database_url, migrated_engine) -> None:
    disabled = make_client(database_url, migrated_engine, enabled=False)
    assert disabled.post("/api/v1/pipeline/alerts").status_code == 409

    broken = make_client(
        database_url, migrated_engine, webhook=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    response = broken.post("/api/v1/pipeline/alerts")

    assert response.status_code == 200  # a broken sink does not fail the stage
    assert response.json()["notes"] and "failed" in response.json()["notes"][0]
    assert broken.get("/api/v1/alerts").json()  # alerts are still stored
    assert "secret-token-1" not in response.text
    assert PipelineStage.ALERTS.value == "alerts"


# -- search ----------------------------------------------------------------------------------


def test_bm25_ranks_matching_paragraphs_and_cites_them(harness) -> None:
    service = HistoricalSearchService(
        documents=harness.documents, snapshots=harness.snapshots, analyses=harness.analyses
    )

    passages = service.search("cumulative tightening of policy")

    top = passages[0]
    assert "cumulative tightening" in top.text
    assert top.document_id == str(harness.doc("dec").id)
    assert top.passage_id == f"{top.document_id}:{top.paragraph_index}"
    assert top.net_tone_score == 0.5 and top.classification
    assert service.search("") == [] and tokens("The and of") == []


def test_search_is_point_in_time(harness, database_url, migrated_engine) -> None:
    client = make_client(database_url, migrated_engine)

    everything = client.get("/api/v1/search", params={"q": "inflation eased", "limit": 20})
    early = client.get(
        "/api/v1/search",
        params={"q": "inflation eased", "as_of": "2024-02-01T00:00:00+00:00", "limit": 20},
    )

    assert everything.status_code == 200
    mar = str(harness.doc("mar").id)
    assert any(p["document_id"] == mar for p in everything.json())
    cutoff = datetime(2024, 2, 1, tzinfo=UTC)
    assert all(
        datetime.fromisoformat(p["published_at"].replace("Z", "+00:00")) <= cutoff
        for p in early.json()
    )
    assert all(p["document_id"] != mar for p in early.json())
    naive = client.get("/api/v1/search", params={"q": "x y", "as_of": "2024-02-01T00:00:00"})
    assert naive.status_code == 422


class AnswerLLM(FakeLLM):
    def __init__(self, invent: bool = False) -> None:
        super().__init__()
        self.invent = invent

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = request.user.split("<<<PASSAGES>>>\n", 1)[1].split("\n<<<END_PASSAGES>>>")[0]
        passage = json.loads(payload)[0]
        quote = passage["text"].split(".")[0]
        answer = "Earlier statements referred to this language."
        if self.invent:
            answer += " Rates rose 75 basis points."
        body = {
            "schema_version": "historical-answer-1.0",
            "answer": answer,
            "citations": [{"passage_id": passage["passage_id"], "quote": quote}],
        }
        return LLMResponse(json.dumps(body), self.model)


def test_grounded_answer_and_refusal(harness, database_url, migrated_engine) -> None:
    good = make_client(database_url, migrated_engine, llm=AnswerLLM())
    bad = make_client(database_url, migrated_engine, llm=AnswerLLM(invent=True))
    none = make_client(database_url, migrated_engine)
    question = {"question": "What did the Committee say about cumulative tightening?"}

    answered = good.post("/api/v1/ask", json=question).json()
    refused = bad.post("/api/v1/ask", json=question).json()

    assert answered["refused"] is False and answered["citations"]
    cited = answered["citations"][0]
    passage = next(p for p in answered["passages"] if p["passage_id"] == cited["passage_id"])
    assert cited["quote"] in passage["text"]
    assert refused["refused"] is True and refused["answer"] is None
    assert any("75" in p for p in refused["problems"])
    assert none.post("/api/v1/ask", json=question).status_code == 409


def test_check_answer_rejects_unknown_passages_and_fake_quotes(harness) -> None:
    service = HistoricalSearchService(
        documents=harness.documents, snapshots=harness.snapshots, analyses=harness.analyses
    )
    passages = service.search("inflation")
    output = AnswerOutput.model_validate(
        {
            "schema_version": "historical-answer-1.0",
            "answer": "Inflation drove markets.",
            "citations": [
                {"passage_id": "nope:0", "quote": "abc"},
                {"passage_id": passages[0].passage_id, "quote": "not in the passage"},
            ],
        }
    )

    problems = check_answer(output, passages)

    assert any("unknown passage" in p for p in problems)
    assert any("quote not in passage" in p for p in problems)
    assert "causal language" in problems


@pytest.mark.parametrize(
    "command", [["search", "inflation"], ["search", "x", "--as-of", "2024-01-01"]]
)
def test_search_cli(harness, database_url, monkeypatch, capsys, command) -> None:
    from fedtone import cli
    from fedtone.settings import get_settings

    monkeypatch.setenv("FEDTONE_DATABASE__URL", database_url)
    get_settings.cache_clear()
    try:
        code = cli.main(command)
    finally:
        get_settings.cache_clear()
    out, err = capsys.readouterr()
    if "--as-of" in command:
        assert code == 2 and "timezone" in err
    else:
        assert code == 0 and "passage(s)" in out


def test_intraday_cli_is_off_by_default_and_point_in_time(
    harness, database_url, monkeypatch, capsys, tmp_path
) -> None:
    from fedtone import cli
    from fedtone.settings import get_settings

    mar = str(harness.doc("mar").id)  # released 2024-03-20 18:00 UTC
    (tmp_path / "SP500.csv").write_text(
        "timestamp,value\n2024-03-20T17:40:00+00:00,5000\n2024-03-20T18:30:00+00:00,5050\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FEDTONE_DATABASE__URL", database_url)
    get_settings.cache_clear()
    try:
        assert cli.main(["intraday", mar, "--symbol", "SP500"]) == 2
        assert "INTRADAY_DIRECTORY" in capsys.readouterr().err
        monkeypatch.setenv("FEDTONE_MARKET_DATA__INTRADAY_DIRECTORY", str(tmp_path))
        get_settings.cache_clear()
        assert cli.main(["intraday", mar, "--symbol", "SP500", "--after", "60"]) == 0
        assert "+1.00%" in capsys.readouterr().out
        early = ["intraday", mar, "--symbol", "SP500", "--as-of", "2024-03-20T18:45:00+00:00"]
        assert cli.main(early) == 0
        assert "window_not_closed" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
