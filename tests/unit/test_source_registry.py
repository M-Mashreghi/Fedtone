from datetime import UTC, datetime

import pytest

from fedtone.application.sources import (
    CentralBankSourceAdapter,
    DiscoveredDocument,
    PermanentSourceError,
    SourceRegistry,
    UnsupportedSourceError,
)
from fedtone.infrastructure.sources.manual import ManualDocument, ManualSourceAdapter

NOW = datetime(2024, 2, 1, tzinfo=UTC)


def manual(bank: str = "FED", doc_type: str = "fomc_statement", docs=()) -> ManualSourceAdapter:
    return ManualSourceAdapter(bank, doc_type, list(docs), clock=lambda: NOW)


def test_registry_resolves_adapter_by_bank_and_type() -> None:
    fed, ecb = manual(), manual("ECB", "monetary_policy_decision")
    registry = SourceRegistry([fed, ecb])

    assert registry.get("FED", "fomc_statement") is fed
    assert registry.get("ECB", "monetary_policy_decision") is ecb


def test_unsupported_bank_or_type_raises_explicit_error() -> None:
    registry = SourceRegistry([manual()])

    with pytest.raises(UnsupportedSourceError, match="FED/fomc_minutes"):
        registry.get("FED", "fomc_minutes")
    with pytest.raises(UnsupportedSourceError, match="SNB/fomc_statement"):
        registry.get("SNB", "fomc_statement")


def test_duplicate_registration_is_rejected() -> None:
    registry = SourceRegistry([manual()])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(manual())


def test_adapters_are_listed_by_bank_then_type() -> None:
    registry = SourceRegistry(
        [manual("SNB", "b"), manual("ECB", "z"), manual("ECB", "a"), manual("FED", "a")]
    )

    keys = [(a.central_bank, a.document_type) for a in registry.adapters()]
    assert keys == [("ECB", "a"), ("ECB", "z"), ("FED", "a"), ("SNB", "b")]


# --- Contract suite: every adapter must satisfy these (run here against the manual adapter).


@pytest.fixture
def contract_adapter() -> CentralBankSourceAdapter:
    return manual(
        docs=[
            ManualDocument(
                source_url="manual://fed/2024-01-31",
                title="Synthetic FOMC statement",
                published_at=datetime(2024, 1, 31, 19, tzinfo=UTC),
                text="First paragraph.\n\nSecond paragraph.\n\n\n",
            )
        ]
    )


def test_contract_adapter_satisfies_protocol(contract_adapter) -> None:
    assert isinstance(contract_adapter, CentralBankSourceAdapter)


def test_contract_discover_fetch_parse_round_trip(contract_adapter) -> None:
    (discovered,) = contract_adapter.discover()
    assert discovered.central_bank == contract_adapter.central_bank
    assert discovered.document_type == contract_adapter.document_type
    assert discovered.observed_at.utcoffset() is not None

    fetched = contract_adapter.fetch(discovered)
    assert fetched.source_url == discovered.source_url
    assert fetched.content
    assert fetched.fetched_at.utcoffset() is not None

    parsed = contract_adapter.parse(fetched)
    assert parsed.paragraphs == ("First paragraph.", "Second paragraph.")
    assert parsed.parser_version


def test_contract_unknown_document_is_permanent_error(contract_adapter) -> None:
    unknown = DiscoveredDocument("FED", "fomc_statement", "manual://missing", NOW)

    with pytest.raises(PermanentSourceError):
        contract_adapter.fetch(unknown)
