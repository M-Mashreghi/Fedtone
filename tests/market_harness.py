"""Three ingested and analyzed FOMC-like statements, ready for market linking (tests only)."""

from datetime import UTC, date, datetime, time, timedelta
from itertools import count

from fedtone.application.ingestion import IngestionService
from fedtone.application.market_reaction import MarketReactionService
from fedtone.application.sources import SourceRegistry
from fedtone.application.tone_analysis import AnalysisService, ToneAnalyzer
from fedtone.domain.market import Instrument, ValueKind
from fedtone.domain.metrics import DEFAULT_WINDOWS
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyComparisonRepository,
    SqlAlchemyEventMetricRepository,
    SqlAlchemyMarketObservationRepository,
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyResearchSummaryRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemySourceSnapshotRepository,
    SqlAlchemyToneAnalysisRepository,
)
from fedtone.infrastructure.sources.manual import ManualDocument, ManualSourceAdapter
from tests.fake_llm import FakeLLM
from tests.fake_market import FakeMarketProvider, weekday_series

# FOMC statements are released at 14:00 New York time.
RELEASES = {
    "dec": datetime(2023, 12, 13, 19, 0, tzinfo=UTC),
    "jan": datetime(2024, 1, 31, 19, 0, tzinfo=UTC),
    "mar": datetime(2024, 3, 20, 18, 0, tzinfo=UTC),  # EDT
}
TEXTS = {
    "dec": "Growth of economic activity has slowed from its strong pace. Inflation remains "
    "elevated.\n\nThe Committee will take into account the cumulative tightening of policy.",
    "jan": "Economic activity has been expanding at a solid pace. Inflation remains elevated."
    "\n\nThe risks to achieving the Committee's goals are moving into better balance.",
    "mar": "Economic activity has been expanding at a solid pace. Inflation has eased but "
    "remains elevated.\n\nThe risks are moving into better balance.",
}
SCORES = {"slowed": (0.5, 0.9), "solid pace. Inflation remains": (0.2, 0.8), "eased": (-0.1, 0.8)}
NOW = datetime(2024, 6, 3, tzinfo=UTC)

SPX = Instrument("SP500", "S&P 500 index", ValueKind.PRICE, "nyse", time(16, 0))
DGS10 = Instrument(
    "DGS10", "10-Year Treasury yield", ValueKind.YIELD_PERCENT, "us_treasury", time(15, 30)
)
INSTRUMENTS = (SPX, DGS10)


def market_data() -> dict[str, dict[date, float | None]]:
    start, end = date(2023, 11, 1), date(2024, 5, 31)
    return {
        "SP500": weekday_series(start, end, 4500.0, 5.0),
        "DGS10": weekday_series(start, end, 4.80, -0.01),
    }


class MarketHarness:
    def __init__(self, session_factory, releases=RELEASES) -> None:
        self.documents = SqlAlchemyPolicyDocumentRepository(session_factory)
        self.snapshots = SqlAlchemySourceSnapshotRepository(session_factory)
        self.reviews = SqlAlchemyReviewItemRepository(session_factory)
        self.analyses = SqlAlchemyToneAnalysisRepository(session_factory)
        self.comparisons = SqlAlchemyComparisonRepository(session_factory)
        self.observations = SqlAlchemyMarketObservationRepository(session_factory)
        self.metrics = SqlAlchemyEventMetricRepository(session_factory)
        self.summaries = SqlAlchemyResearchSummaryRepository(session_factory)
        ticks = count()
        self.clock = lambda: NOW + timedelta(seconds=next(ticks))
        self.urls = {name: f"manual://{name}" for name in releases}
        docs = [
            ManualDocument(self.urls[name], f"Statement {name}", when, TEXTS[name])
            for name, when in releases.items()
        ]
        IngestionService(
            registry=SourceRegistry([ManualSourceAdapter("FED", "fomc_statement", docs)]),
            documents=self.documents,
            snapshots=self.snapshots,
            reviews=self.reviews,
            runs=SqlAlchemyProcessingRunRepository(session_factory),
            clock=self.clock,
        ).run("FED", "fomc_statement")
        AnalysisService(
            analyzer=ToneAnalyzer(FakeLLM(scores=SCORES)),
            documents=self.documents,
            snapshots=self.snapshots,
            analyses=self.analyses,
            comparisons=self.comparisons,
            reviews=self.reviews,
            clock=self.clock,
        ).run("FED", "fomc_statement")

    def doc(self, name: str):
        return self.documents.get_by_canonical_identity("FED", "fomc_statement", self.urls[name])

    def reaction_service(self, provider=None, **kwargs) -> MarketReactionService:
        options = {"instruments": INSTRUMENTS, "windows": DEFAULT_WINDOWS} | kwargs
        return MarketReactionService(
            provider=provider or FakeMarketProvider(market_data()),
            documents=self.documents,
            observations=self.observations,
            metrics=self.metrics,
            clock=self.clock,
            **options,
        )
