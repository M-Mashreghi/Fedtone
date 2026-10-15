"""Core research-domain entities.

These are plain dataclasses with no persistence or framework dependencies.
All timestamps must be timezone-aware.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from fedtone.domain.lifecycle import (
    EXCEPTION_STATES,
    TERMINAL_STATES,
    DocumentStatus,
    InvalidTransitionError,
    is_forward_transition_allowed,
)
from fedtone.domain.text import (
    NORMALIZER_VERSION,
    join_paragraphs,
    normalize_paragraph,
    normalize_paragraphs,
    sha256_hex,
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class DomainValidationError(ValueError):
    """Raised when an entity would violate a domain invariant."""


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and value.utcoffset() is None:
        raise DomainValidationError(f"{name} must be timezone-aware")


def _require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise DomainValidationError(f"{name} must not be empty")


def is_sha256_hex(value: str | None) -> bool:
    return value is not None and bool(_SHA256_HEX.match(value))


class ToneClassification(StrEnum):
    STRONGLY_DOVISH = "strongly_dovish"
    DOVISH = "dovish"
    NEUTRAL = "neutral"
    HAWKISH = "hawkish"
    STRONGLY_HAWKISH = "strongly_hawkish"


@dataclass(frozen=True)
class StatusChange:
    from_status: DocumentStatus
    to_status: DocumentStatus
    at: datetime
    reason: str | None = None


@dataclass(frozen=True)
class EvidenceItem:
    text: str
    dimension: str | None = None
    direction: str | None = None
    match: str | None = None  # "exact" or "fuzzy" once validated against the source


@dataclass(frozen=True)
class ToneAnalysis:
    """A validated policy-tone assessment of one document version."""

    document_id: UUID
    document_hash: str
    model: str
    prompt_version: str
    schema_version: str
    net_tone_score: float
    classification: ToneClassification
    confidence: float
    evidence: tuple[EvidenceItem, ...]
    explanation: str
    created_at: datetime
    id: UUID = field(default_factory=uuid4)
    dimension_scores: dict[str, float | None] = field(default_factory=dict)
    uncertainty_flags: tuple[str, ...] = ()
    temperature: float | None = None
    raw_output: str | None = None

    def __post_init__(self) -> None:
        if not is_sha256_hex(self.document_hash):
            raise DomainValidationError("document_hash must be a SHA-256 hex digest")
        for name in ("model", "prompt_version", "schema_version", "explanation"):
            _require_text(name, getattr(self, name))
        if not -1.0 <= self.net_tone_score <= 1.0:
            raise DomainValidationError("net_tone_score must be within [-1, 1]")
        if not 0.0 <= self.confidence <= 1.0:
            raise DomainValidationError("confidence must be within [0, 1]")
        for name, value in self.dimension_scores.items():
            if value is not None and not -1.0 <= value <= 1.0:
                raise DomainValidationError(f"dimension score {name} must be within [-1, 1]")
        _require_aware("created_at", self.created_at)

    @property
    def has_evidence(self) -> bool:
        return any(item.text.strip() for item in self.evidence)


@dataclass
class PolicyDocument:
    """An official central-bank communication and its lifecycle state."""

    central_bank: str
    document_type: str
    source_url: str
    discovered_at: datetime
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    content_hash: str | None = None
    parser_version: str | None = None
    previous_document_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    status: DocumentStatus = DocumentStatus.DISCOVERED
    last_successful_status: DocumentStatus = DocumentStatus.DISCOVERED
    published_analysis_id: UUID | None = None
    failure_reason: str | None = None
    history: list[StatusChange] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in ("central_bank", "document_type", "source_url"):
            _require_text(name, getattr(self, name))
        for name in ("discovered_at", "published_at", "fetched_at"):
            _require_aware(name, getattr(self, name))
        if self.content_hash is not None and not is_sha256_hex(self.content_hash):
            raise DomainValidationError("content_hash must be a SHA-256 hex digest")


@dataclass
class DocumentComparison:
    """Comparison of a document with its previous comparable document.

    ``previous_document_id`` is None for a baseline (first) document.
    """

    document_id: UUID
    current_analysis_id: UUID
    created_at: datetime
    previous_document_id: UUID | None = None
    previous_analysis_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    # Deterministic results (diff and score deltas). Deltas are None when not computable.
    diff: dict | None = None
    overall_delta: float | None = None
    dimension_deltas: dict[str, float | None] = field(default_factory=dict)
    # Optional validated LLM interpretation of the changes, with its provenance.
    interpretation: dict | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    uncertainty_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_aware("created_at", self.created_at)
        if self.interpretation is not None and not (
            self.model and self.prompt_version and self.schema_version
        ):
            raise DomainValidationError("an interpretation needs model, prompt and schema versions")
        if (self.previous_document_id is None) != (self.previous_analysis_id is None):
            raise DomainValidationError(
                "previous_document_id and previous_analysis_id must be set together"
            )

    @property
    def is_baseline(self) -> bool:
        return self.previous_document_id is None


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


@dataclass
class ProcessingRun:
    trigger: RunTrigger
    started_at: datetime
    id: UUID = field(default_factory=uuid4)
    status: RunStatus = RunStatus.RUNNING
    finished_at: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _require_aware("started_at", self.started_at)

    def finish(self, status: RunStatus, at: datetime, error: str | None = None) -> None:
        if self.status is not RunStatus.RUNNING:
            raise InvalidTransitionError(f"run already finished with status {self.status}")
        if status is RunStatus.RUNNING:
            raise InvalidTransitionError("a run cannot finish as running")
        _require_aware("finished_at", at)
        if at < self.started_at:
            raise DomainValidationError("finished_at must not precede started_at")
        if status is RunStatus.FAILED and not error:
            raise DomainValidationError("a failed run requires an error description")
        self.status, self.finished_at, self.error = status, at, error


@dataclass
class ReviewItem:
    document_id: UUID
    stage: DocumentStatus
    reason: str
    created_at: datetime
    id: UUID = field(default_factory=uuid4)
    resolved_at: datetime | None = None
    resolution: str | None = None

    def __post_init__(self) -> None:
        _require_text("reason", self.reason)
        _require_aware("created_at", self.created_at)

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    def resolve(self, resolution: str, at: datetime) -> None:
        if not self.is_open:
            raise InvalidTransitionError("review item already resolved")
        _require_text("resolution", resolution)
        _require_aware("resolved_at", at)
        self.resolution, self.resolved_at = resolution, at


def transition(
    document: PolicyDocument,
    target: DocumentStatus,
    *,
    at: datetime,
    reason: str | None = None,
    analysis: ToneAnalysis | None = None,
) -> None:
    """The single guard through which every document status change must pass.

    - Forward moves follow ``FORWARD_TRANSITIONS``.
    - Moving to NeedsReview/Failed requires a reason and keeps ``last_successful_status``.
    - From NeedsReview/Failed a document may only resume at ``last_successful_status``
      (or escalate NeedsReview → Failed).
    - Publishing requires a source content hash and a publishable analysis of that exact
      document version.
    """
    _require_aware("at", at)
    current = document.status

    if current in TERMINAL_STATES:
        raise InvalidTransitionError(f"{current} is terminal")

    if current in EXCEPTION_STATES:
        allowed = {document.last_successful_status}
        if current is DocumentStatus.NEEDS_REVIEW:
            allowed.add(DocumentStatus.FAILED)
        if target not in allowed:
            raise InvalidTransitionError(
                f"{current} may only resume at {document.last_successful_status}"
            )
    elif not is_forward_transition_allowed(current, target):
        raise InvalidTransitionError(f"{current} → {target} is not allowed")

    if target in EXCEPTION_STATES and not (reason and reason.strip()):
        raise InvalidTransitionError(f"moving to {target} requires a reason")

    if target is DocumentStatus.PUBLISHED:
        _check_publishable(document, analysis)

    document.history.append(StatusChange(current, target, at, reason))
    document.status = target
    if target in EXCEPTION_STATES:
        document.failure_reason = reason
    else:
        document.last_successful_status = target
        document.failure_reason = None
    if target is DocumentStatus.PUBLISHED and analysis is not None:
        document.published_analysis_id = analysis.id


def _check_publishable(document: PolicyDocument, analysis: ToneAnalysis | None) -> None:
    if not is_sha256_hex(document.content_hash):
        raise InvalidTransitionError("publishing requires a source content hash")
    if analysis is None:
        raise InvalidTransitionError("publishing requires an analysis reference")
    if analysis.document_id != document.id:
        raise InvalidTransitionError("analysis belongs to a different document")
    if analysis.document_hash != document.content_hash:
        raise InvalidTransitionError("analysis was produced from a different document version")
    if not analysis.has_evidence:
        raise InvalidTransitionError("analysis without evidence cannot be published")


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable raw content of a document exactly as fetched (one row per distinct content)."""

    document_id: UUID
    version: int
    source_url: str
    content: bytes
    content_type: str
    raw_hash: str
    fetched_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainValidationError("snapshot version starts at 1")
        if self.raw_hash != sha256_hex(self.content):
            raise DomainValidationError("raw_hash does not match content")
        _require_text("source_url", self.source_url)
        _require_aware("fetched_at", self.fetched_at)

    @classmethod
    def create(
        cls,
        *,
        document_id: UUID,
        version: int,
        source_url: str,
        content: bytes,
        content_type: str,
        fetched_at: datetime,
    ) -> "SourceSnapshot":
        return cls(
            document_id=document_id,
            version=version,
            source_url=source_url,
            content=content,
            content_type=content_type,
            raw_hash=sha256_hex(content),
            fetched_at=fetched_at,
        )


@dataclass(frozen=True)
class DocumentText:
    """Parsed and normalized policy text derived from one snapshot by one parser version.

    ``text_hash`` (SHA-256 of the normalized text) is the document's canonical content hash.
    Reparsing with a new parser or normalizer creates a new DocumentText, never an update.
    """

    document_id: UUID
    snapshot_id: UUID
    title: str
    paragraphs: tuple[str, ...]
    parser_version: str
    normalizer_version: str
    text_hash: str
    published_at: datetime | None
    created_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.paragraphs:
            raise DomainValidationError("document text needs at least one paragraph")
        if self.text_hash != sha256_hex(join_paragraphs(self.paragraphs)):
            raise DomainValidationError("text_hash does not match paragraphs")
        for name in ("parser_version", "normalizer_version"):
            _require_text(name, getattr(self, name))
        _require_aware("published_at", self.published_at)
        _require_aware("created_at", self.created_at)

    @property
    def normalized_text(self) -> str:
        return join_paragraphs(self.paragraphs)

    @classmethod
    def from_parsed(
        cls,
        *,
        document_id: UUID,
        snapshot_id: UUID,
        title: str,
        raw_paragraphs: tuple[str, ...],
        parser_version: str,
        published_at: datetime | None,
        created_at: datetime,
    ) -> "DocumentText":
        paragraphs = normalize_paragraphs(raw_paragraphs)
        return cls(
            document_id=document_id,
            snapshot_id=snapshot_id,
            title=normalize_paragraph(title),
            paragraphs=paragraphs,
            parser_version=parser_version,
            normalizer_version=NORMALIZER_VERSION,
            text_hash=sha256_hex(join_paragraphs(paragraphs)),
            published_at=published_at,
            created_at=created_at,
        )
