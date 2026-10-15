"""Idempotent ingestion: discover → fetch → snapshot → parse → normalize → link.

Manual commands and the scheduler call the same ``IngestionService.run``. Running it twice
over the same source leaves one canonical record set: documents are keyed by canonical
identity, snapshots by raw-content hash and parsed texts by processing version.

Failure routing:
- transient fetch errors (after bounded retries) → document ``Failed``; the next run resumes it;
- permanent fetch/parse errors → ``NeedsReview`` plus a review item; skipped until resolved;
- changed normalized text for an already-ingested document → review item, never an overwrite.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from fedtone.application.ports import (
    PolicyDocumentRepository,
    ProcessingRunRepository,
    ReviewItemRepository,
    SourceSnapshotRepository,
)
from fedtone.application.sources import (
    CentralBankSourceAdapter,
    DiscoveredDocument,
    FetchedContent,
    PermanentSourceError,
    SourceError,
    SourceRegistry,
    TransientSourceError,
)
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.linking import previous_comparable_links
from fedtone.domain.models import (
    DocumentText,
    DomainValidationError,
    PolicyDocument,
    ProcessingRun,
    ReviewItem,
    RunStatus,
    RunTrigger,
    transition,
)
from fedtone.observability import correlation_context

logger = logging.getLogger(__name__)

# Statuses whose previous-document link may still change freely (not yet analyzed).
RELINKABLE = frozenset({DocumentStatus.NORMALIZED, DocumentStatus.QUEUED_FOR_ANALYSIS})
# Statuses that already hold an accepted normalized text (candidates for revision checks).
NORMALIZED_OR_LATER = frozenset(
    {
        DocumentStatus.NORMALIZED,
        DocumentStatus.QUEUED_FOR_ANALYSIS,
        DocumentStatus.ANALYZED,
        DocumentStatus.COMPARED,
        DocumentStatus.MARKET_LINKED,
        DocumentStatus.PUBLISHED,
    }
)


class Outcome(StrEnum):
    INGESTED = "ingested"
    UNCHANGED = "unchanged"
    REVISION_DETECTED = "revision_detected"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


@dataclass
class IngestionReport:
    run: ProcessingRun
    discovered: int = 0
    outcomes: dict[Outcome, int] = field(default_factory=lambda: dict.fromkeys(Outcome, 0))
    relinked: int = 0

    def count(self, outcome: Outcome) -> int:
        return self.outcomes[outcome]


class IngestionService:
    def __init__(
        self,
        *,
        registry: SourceRegistry,
        documents: PolicyDocumentRepository,
        snapshots: SourceSnapshotRepository,
        reviews: ReviewItemRepository,
        runs: ProcessingRunRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._registry = registry
        self._documents = documents
        self._snapshots = snapshots
        self._reviews = reviews
        self._runs = runs
        self._clock = clock

    def run(
        self,
        central_bank: str,
        document_type: str,
        *,
        trigger: RunTrigger = RunTrigger.MANUAL,
        refetch_existing: bool = False,
    ) -> IngestionReport:
        """Ingest everything the source currently lists.

        ``refetch_existing`` re-downloads already-ingested documents to detect revisions.
        """
        adapter = self._registry.get(central_bank, document_type)
        run = ProcessingRun(trigger=trigger, started_at=self._clock())
        self._runs.add(run)
        report = IngestionReport(run=run)

        with correlation_context(run_id=run.id):
            try:
                discovered = adapter.discover()
            except SourceError as exc:
                logger.error("discovery failed for %s/%s: %s", central_bank, document_type, exc)
                run.finish(RunStatus.FAILED, self._clock(), error=f"discovery failed: {exc}")
                self._runs.update(run)
                return report

            report.discovered = len(discovered)
            for item in discovered:
                outcome = self._ingest(adapter, item, refetch_existing)
                report.outcomes[outcome] += 1

            report.relinked = self._link(central_bank, document_type)

            problems = report.count(Outcome.NEEDS_REVIEW) + report.count(Outcome.FAILED)
            status = RunStatus.PARTIAL if problems else RunStatus.SUCCEEDED
            error = f"{problems} document(s) need attention" if problems else None
            run.finish(status, self._clock(), error=error)
            self._runs.update(run)
            logger.info(
                "ingestion finished: %s",
                ", ".join(f"{k}={v}" for k, v in report.outcomes.items()),
            )
        return report

    # -- per document -------------------------------------------------------------------

    def _ingest(
        self, adapter: CentralBankSourceAdapter, item: DiscoveredDocument, refetch: bool
    ) -> Outcome:
        document, _ = self._documents.add(
            PolicyDocument(
                central_bank=item.central_bank,
                document_type=item.document_type,
                source_url=item.source_url,
                discovered_at=item.observed_at,
            )
        )
        with correlation_context(document_id=document.id):
            if document.status is DocumentStatus.NEEDS_REVIEW:
                return Outcome.NEEDS_REVIEW
            if document.status is DocumentStatus.FAILED:
                logger.info("resuming failed document at %s", document.last_successful_status)
                transition(document, document.last_successful_status, at=self._clock())
                self._documents.update(document)

            if document.status is DocumentStatus.DISCOVERED:
                outcome = self._fetch(adapter, item, document)
                if outcome is not None:
                    return outcome
            elif document.status in NORMALIZED_OR_LATER and refetch:
                return self._check_revision(adapter, item, document)

            if document.status in (DocumentStatus.FETCHED, DocumentStatus.PARSED):
                return self._parse_and_normalize(adapter, document)
            return Outcome.UNCHANGED

    def _fetch(
        self, adapter: CentralBankSourceAdapter, item: DiscoveredDocument, document: PolicyDocument
    ) -> Outcome | None:
        try:
            fetched = adapter.fetch(item)
        except TransientSourceError as exc:
            return self._fail(document, f"fetch failed: {exc}")
        except PermanentSourceError as exc:
            return self._needs_review(document, f"fetch failed: {exc}")
        self._snapshots.add_snapshot(
            document_id=document.id,
            source_url=fetched.source_url,
            content=fetched.content,
            content_type=fetched.content_type,
            fetched_at=fetched.fetched_at,
        )
        document.fetched_at = fetched.fetched_at
        transition(document, DocumentStatus.FETCHED, at=self._clock())
        self._documents.update(document)
        return None

    def _parse_and_normalize(
        self, adapter: CentralBankSourceAdapter, document: PolicyDocument
    ) -> Outcome:
        snapshot = self._snapshots.latest_snapshot(document.id)
        if snapshot is None:
            return self._needs_review(document, "no raw snapshot stored for fetched document")
        fetched = FetchedContent(
            snapshot.source_url, snapshot.content, snapshot.content_type, snapshot.fetched_at
        )
        try:
            text = self._to_text(adapter, document, snapshot.id, fetched)
        except (PermanentSourceError, DomainValidationError) as exc:
            return self._needs_review(document, f"parse failed: {exc}")
        self._snapshots.add_text(text)

        document.content_hash = text.text_hash
        document.parser_version = text.parser_version
        document.published_at = text.published_at
        if document.status is DocumentStatus.FETCHED:
            transition(document, DocumentStatus.PARSED, at=self._clock())
        transition(document, DocumentStatus.NORMALIZED, at=self._clock())
        self._documents.update(document)
        if text.published_at is None:
            self._open_review(document, "publication time not found; cannot link previous document")
        logger.info("normalized document %s", document.source_url)
        return Outcome.INGESTED

    def _to_text(
        self,
        adapter: CentralBankSourceAdapter,
        document: PolicyDocument,
        snapshot_id: UUID,
        fetched: FetchedContent,
    ) -> DocumentText:
        parsed = adapter.parse(fetched)
        return DocumentText.from_parsed(
            document_id=document.id,
            snapshot_id=snapshot_id,
            title=parsed.title,
            raw_paragraphs=parsed.paragraphs,
            parser_version=parsed.parser_version,
            published_at=parsed.published_at,
            created_at=self._clock(),
        )

    def _check_revision(
        self, adapter: CentralBankSourceAdapter, item: DiscoveredDocument, document: PolicyDocument
    ) -> Outcome:
        try:
            fetched = adapter.fetch(item)
        except SourceError as exc:
            logger.warning("revision check fetch failed: %s", exc)
            return Outcome.UNCHANGED
        snapshot, created = self._snapshots.add_snapshot(
            document_id=document.id,
            source_url=fetched.source_url,
            content=fetched.content,
            content_type=fetched.content_type,
            fetched_at=fetched.fetched_at,
        )
        if not created:
            return Outcome.UNCHANGED
        try:
            text = self._to_text(adapter, document, snapshot.id, fetched)
        except (PermanentSourceError, DomainValidationError) as exc:
            self._open_review(document, f"new raw snapshot v{snapshot.version} unparseable: {exc}")
            return Outcome.REVISION_DETECTED
        self._snapshots.add_text(text)
        if text.text_hash == document.content_hash:
            # Page chrome changed; policy text did not.
            return Outcome.UNCHANGED
        self._open_review(
            document,
            f"source text revised (snapshot v{snapshot.version}): "
            f"{document.content_hash} -> {text.text_hash}; stored version kept",
        )
        return Outcome.REVISION_DETECTED

    # -- linking --------------------------------------------------------------------------

    def _link(self, central_bank: str, document_type: str) -> int:
        documents = self._documents.list(
            central_bank=central_bank, document_type=document_type, limit=None
        )
        links = previous_comparable_links(d for d in documents if d.status not in _UNLINKABLE)
        changed = 0
        for document in documents:
            if document.id not in links:
                continue
            previous_id = links[document.id]
            with correlation_context(document_id=document.id):
                if document.status in RELINKABLE:
                    if document.previous_document_id != previous_id:
                        document.previous_document_id = previous_id
                        changed += 1
                    if document.status is DocumentStatus.NORMALIZED:
                        transition(document, DocumentStatus.QUEUED_FOR_ANALYSIS, at=self._clock())
                    self._documents.update(document)
                elif document.previous_document_id != previous_id:
                    # Already analyzed against another baseline: flag, do not rewrite history.
                    self._open_review(
                        document,
                        f"previous comparable document is now {previous_id} "
                        f"(was {document.previous_document_id})",
                    )
        return changed

    # -- failure helpers ------------------------------------------------------------------

    def _fail(self, document: PolicyDocument, reason: str) -> Outcome:
        logger.warning("document failed: %s", reason)
        transition(document, DocumentStatus.FAILED, at=self._clock(), reason=reason)
        self._documents.update(document)
        return Outcome.FAILED

    def _needs_review(self, document: PolicyDocument, reason: str) -> Outcome:
        logger.warning("document needs review: %s", reason)
        stage = document.status
        transition(document, DocumentStatus.NEEDS_REVIEW, at=self._clock(), reason=reason)
        self._documents.update(document)
        self._reviews.add(ReviewItem(document.id, stage, reason, self._clock()))
        return Outcome.NEEDS_REVIEW

    def _open_review(self, document: PolicyDocument, reason: str) -> None:
        """Open a review item unless an identical one is still open (keeps reruns idempotent)."""
        if any(item.reason == reason for item in self._reviews.list_open(document_id=document.id)):
            return
        logger.warning("review item opened: %s", reason)
        self._reviews.add(ReviewItem(document.id, document.status, reason, self._clock()))


# Documents that have not produced trustworthy normalized text cannot anchor a link.
_UNLINKABLE = frozenset(
    {
        DocumentStatus.DISCOVERED,
        DocumentStatus.FETCHED,
        DocumentStatus.PARSED,
        DocumentStatus.FAILED,
        DocumentStatus.NEEDS_REVIEW,
    }
)
