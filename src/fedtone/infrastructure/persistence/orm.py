"""SQLAlchemy table mappings. Kept separate from domain dataclasses on purpose."""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """Stores timezone-aware datetimes as naive UTC; returns them as aware UTC.

    Keeps behavior identical on SQLite (no native timezone support) and other databases.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("naive datetime cannot be stored; use timezone-aware values")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class PolicyDocumentRow(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint(
            "central_bank",
            "document_type",
            "source_url",
            name="uq_policy_documents_canonical_identity",
        ),
        Index(
            "ix_policy_documents_bank_type_published",
            "central_bank",
            "document_type",
            "published_at",
        ),
        Index("ix_policy_documents_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    central_bank: Mapped[str] = mapped_column(String(32))
    document_type: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(String(2048))
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime())
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    content_hash: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    previous_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("policy_documents.id"))
    status: Mapped[str] = mapped_column(String(32))
    last_successful_status: Mapped[str] = mapped_column(String(32))
    # No FK: tone_analyses already references policy_documents, and a cycle would need ALTER
    # TABLE support that SQLite lacks. The lifecycle guard validates this reference.
    published_analysis_id: Mapped[UUID | None] = mapped_column()
    failure_reason: Mapped[str | None] = mapped_column(Text)

    history: Mapped[list["DocumentStatusChangeRow"]] = relationship(
        order_by="DocumentStatusChangeRow.sequence",
        cascade="all, delete-orphan",
    )


class DocumentStatusChangeRow(Base):
    __tablename__ = "document_status_changes"
    __table_args__ = (
        UniqueConstraint("document_id", "sequence", name="uq_document_status_changes_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    reason: Mapped[str | None] = mapped_column(Text)


class ToneAnalysisRow(Base):
    __tablename__ = "tone_analyses"
    __table_args__ = (Index("ix_tone_analyses_document_id", "document_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"))
    document_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    net_tone_score: Mapped[float] = mapped_column(Float)
    classification: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list[dict]] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    dimension_scores: Mapped[dict | None] = mapped_column(JSON)
    uncertainty_flags: Mapped[list[str] | None] = mapped_column(JSON)
    temperature: Mapped[float | None] = mapped_column(Float)
    raw_output: Mapped[str | None] = mapped_column(Text)


class DocumentComparisonRow(Base):
    __tablename__ = "document_comparisons"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "current_analysis_id", name="uq_document_comparisons_analysis"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"), index=True)
    current_analysis_id: Mapped[UUID] = mapped_column(ForeignKey("tone_analyses.id"))
    previous_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("policy_documents.id"))
    previous_analysis_id: Mapped[UUID | None] = mapped_column(ForeignKey("tone_analyses.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    diff: Mapped[dict | None] = mapped_column(JSON)
    overall_delta: Mapped[float | None] = mapped_column(Float)
    dimension_deltas: Mapped[dict | None] = mapped_column(JSON)
    interpretation: Mapped[dict | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    uncertainty_flags: Mapped[list[str] | None] = mapped_column(JSON)


class ProcessingRunRow(Base):
    __tablename__ = "processing_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)


class ReviewItemRow(Base):
    __tablename__ = "review_items"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    resolution: Mapped[str | None] = mapped_column(Text)


class SourceSnapshotRow(Base):
    __tablename__ = "document_snapshots"
    __table_args__ = (
        UniqueConstraint("document_id", "raw_hash", name="uq_document_snapshots_raw_hash"),
        UniqueConstraint("document_id", "version", name="uq_document_snapshots_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"))
    version: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(String(2048))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String(255))
    raw_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DocumentTextRow(Base):
    __tablename__ = "document_texts"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "parser_version",
            "normalizer_version",
            name="uq_document_texts_processing_version",
        ),
        Index("ix_document_texts_document_id", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("document_snapshots.id"))
    title: Mapped[str] = mapped_column(Text)
    paragraphs: Mapped[list[str]] = mapped_column(JSON)
    parser_version: Mapped[str] = mapped_column(String(64))
    normalizer_version: Mapped[str] = mapped_column(String(64))
    text_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MarketObservationRow(Base):
    """Append-only: a re-fetched date gets a new row only when its value changed."""

    __tablename__ = "market_observations"
    __table_args__ = (Index("ix_market_observations_series_date", "provider", "symbol", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(64))
    date: Mapped[date] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Float)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime())


class EventMetricRow(Base):
    __tablename__ = "event_metrics"
    __table_args__ = (Index("ix_event_metrics_document_id", "document_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"))
    symbol: Mapped[str] = mapped_column(String(64))
    window: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    unit: Mapped[str] = mapped_column(String(32))
    value: Mapped[float | None] = mapped_column(Float)
    missing_reason: Mapped[str | None] = mapped_column(String(64))
    event_day: Mapped[date] = mapped_column(Date)
    alignment_rule: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    start_value: Mapped[float | None] = mapped_column(Float)
    end_value: Mapped[float | None] = mapped_column(Float)
    end_observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    provider: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(UTCDateTime())
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    metric_version: Mapped[str] = mapped_column(String(32))
    benchmark_symbol: Mapped[str | None] = mapped_column(String(64))


class ResearchSummaryRow(Base):
    __tablename__ = "research_summaries"
    __table_args__ = (Index("ix_research_summaries_document_id", "document_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("policy_documents.id"))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    metric_version: Mapped[str] = mapped_column(String(32))
    facts: Mapped[list[dict]] = mapped_column(JSON)
    headline: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    cited_fact_ids: Mapped[list[str]] = mapped_column(JSON)
    uncertainty_flags: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
