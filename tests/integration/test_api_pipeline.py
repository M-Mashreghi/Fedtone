"""A pipeline run triggered through the API appears in the API's read routes."""

from fastapi.testclient import TestClient

from fedtone.api.app import create_app
from fedtone.api.pipeline import AdapterFactories
from fedtone.application.sources import SourceRegistry
from fedtone.infrastructure.sources.manual import ManualDocument, ManualSourceAdapter
from fedtone.settings import Settings
from tests.fake_llm import FakeLLM
from tests.fake_market import FakeMarketProvider
from tests.market_harness import RELEASES, SCORES, TEXTS, market_data

INSTRUMENTS = [
    {
        "symbol": "SP500",
        "name": "S&P 500 index",
        "kind": "price",
        "calendar": "nyse",
        "observation_time": "16:00",
    },
    {
        "symbol": "DGS10",
        "name": "10-Year Treasury yield",
        "kind": "yield_percent",
        "calendar": "us_treasury",
        "observation_time": "15:30",
    },
]


def factories() -> AdapterFactories:
    docs = [
        ManualDocument(f"manual://{name}", f"Statement {name}", when, TEXTS[name])
        for name, when in RELEASES.items()
    ]
    return AdapterFactories(
        registry=lambda settings, client: SourceRegistry(
            [ManualSourceAdapter("FED", "fomc_statement", docs)]
        ),
        llm_client=lambda settings: FakeLLM(scores=SCORES),
        market_provider=lambda settings, client: FakeMarketProvider(market_data()),
    )


def test_pipeline_run_through_api_is_visible_in_api(database_url, migrated_engine) -> None:
    settings = Settings(
        database={"url": database_url},
        market_data={"provider": "csv", "instruments": INSTRUMENTS},
    )
    client = TestClient(create_app(settings, engine=migrated_engine, adapter_factories=factories()))

    ingest = client.post("/api/v1/pipeline/ingest")
    analyze = client.post("/api/v1/pipeline/analyze")
    reactions = client.post("/api/v1/pipeline/reactions")

    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["outcomes"]["ingested"] == 3
    assert analyze.json()["outcomes"]["compared"] == 3
    assert reactions.json()["outcomes"]["linked"] == 3
    runs = client.get("/api/v1/runs").json()
    assert [r["id"] for r in runs] == [ingest.json()["run_id"]]

    timeline = client.get("/api/v1/timeline").json()["points"]
    assert [p["net_tone_score"] for p in timeline] == [0.5, 0.2, -0.1]
    assert {p["status"] for p in timeline} == {"MarketLinked"}
    detail = client.get(f"/api/v1/documents/{timeline[1]['document_id']}").json()
    assert detail["market_reaction"]["metrics"]
    assert detail["comparison"]["overall_delta"] is not None

    # Running a stage again retries nothing that already succeeded.
    again = client.post("/api/v1/pipeline/ingest").json()
    assert again["outcomes"]["unchanged"] == 3
