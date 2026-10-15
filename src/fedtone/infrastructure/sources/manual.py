"""Manual text adapter for development, evaluation sets and tests.

Documents are supplied in memory as plain text; paragraphs are separated by blank lines.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from fedtone.application.sources import (
    DiscoveredDocument,
    FetchedContent,
    ParsedDocument,
    PermanentSourceError,
)

PARSER_VERSION = "manual-text-1"


@dataclass(frozen=True)
class ManualDocument:
    source_url: str
    title: str
    published_at: datetime | None
    text: str


class ManualSourceAdapter:
    def __init__(
        self,
        central_bank: str,
        document_type: str,
        documents: Sequence[ManualDocument],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.central_bank = central_bank
        self.document_type = document_type
        self._documents = {doc.source_url: doc for doc in documents}
        self._clock = clock

    def discover(self) -> Sequence[DiscoveredDocument]:
        now = self._clock()
        return [
            DiscoveredDocument(
                self.central_bank, self.document_type, doc.source_url, now, doc.title
            )
            for doc in self._documents.values()
        ]

    def fetch(self, document: DiscoveredDocument) -> FetchedContent:
        try:
            doc = self._documents[document.source_url]
        except KeyError:
            raise PermanentSourceError(f"unknown manual document {document.source_url}") from None
        return FetchedContent(
            source_url=doc.source_url,
            content=doc.text.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            fetched_at=self._clock(),
        )

    def parse(self, content: FetchedContent) -> ParsedDocument:
        doc = self._documents.get(content.source_url)
        if doc is None:
            raise PermanentSourceError(f"unknown manual document {content.source_url}")
        text = content.content.decode("utf-8")
        paragraphs = tuple(p for p in (block.strip() for block in text.split("\n\n")) if p)
        if not paragraphs:
            raise PermanentSourceError(f"manual document {content.source_url} has no text")
        return ParsedDocument(doc.title, doc.published_at, paragraphs, PARSER_VERSION)
