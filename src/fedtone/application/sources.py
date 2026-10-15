"""Central-bank source adapter contract and registry.

An adapter knows how to discover, fetch and parse one (central bank, document type) pair.
Provider-specific HTTP and HTML details stay in ``fedtone.infrastructure.sources``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class SourceError(Exception):
    """Base class for source adapter failures."""


class TransientSourceError(SourceError):
    """A failure that may succeed on retry (network error, timeout, 5xx, 429)."""


class PermanentSourceError(SourceError):
    """A failure that retrying will not fix (404, unexpected page layout, parse failure)."""


class UnsupportedSourceError(LookupError):
    """No adapter is registered for the requested central bank and document type."""


@dataclass(frozen=True)
class DiscoveredDocument:
    central_bank: str
    document_type: str
    source_url: str
    observed_at: datetime
    title: str | None = None


@dataclass(frozen=True)
class FetchedContent:
    source_url: str
    content: bytes
    content_type: str
    fetched_at: datetime


@dataclass(frozen=True)
class ParsedDocument:
    """Policy text extracted from a fetched page, before normalization."""

    title: str
    published_at: datetime | None
    paragraphs: tuple[str, ...]
    parser_version: str


@runtime_checkable
class CentralBankSourceAdapter(Protocol):
    central_bank: str
    document_type: str

    def discover(self) -> Sequence[DiscoveredDocument]:
        """List documents currently exposed by the official source."""
        ...

    def fetch(self, document: DiscoveredDocument) -> FetchedContent:
        """Retrieve raw content. Raises TransientSourceError or PermanentSourceError."""
        ...

    def parse(self, content: FetchedContent) -> ParsedDocument:
        """Extract title, publication time and policy paragraphs.

        Raises PermanentSourceError when the page does not look like the expected document.
        """
        ...


class SourceRegistry:
    """Resolves adapters by (central bank, document type)."""

    def __init__(self, adapters: Iterable[CentralBankSourceAdapter] = ()) -> None:
        self._adapters: dict[tuple[str, str], CentralBankSourceAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: CentralBankSourceAdapter) -> None:
        key = (adapter.central_bank, adapter.document_type)
        if key in self._adapters:
            raise ValueError(f"an adapter is already registered for {key[0]}/{key[1]}")
        self._adapters[key] = adapter

    def get(self, central_bank: str, document_type: str) -> CentralBankSourceAdapter:
        try:
            return self._adapters[(central_bank, document_type)]
        except KeyError:
            raise UnsupportedSourceError(
                f"no source adapter for {central_bank}/{document_type}"
            ) from None

    def adapters(self) -> list[CentralBankSourceAdapter]:
        """All adapters ordered by central bank, then document type."""
        return [self._adapters[key] for key in sorted(self._adapters)]
