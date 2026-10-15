"""LLM tone analysis, deterministic comparison and confidence routing.

``ToneAnalyzer`` turns one normalized document into a validated ``ToneAnalysis`` or a
rejection: the model output must parse against the versioned schema and pass the evidence
validator. A failed attempt is retried once with the same prompt; a second failure is a
rejection, never a guessed score.

``AnalysisService`` moves documents through the lifecycle:

    QueuedForAnalysis ─ valid ───────────────────────→ Analyzed ─→ Compared
                      ├ valid, low confidence → Analyzed → NeedsReview (resumes at Analyzed)
                      ├ invalid twice ──────────────────→ NeedsReview (resumes at Queued)
                      └ provider unavailable ───────────→ Failed (resumed next run)
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import ValidationError

from fedtone.application.llm import LLMClient, LLMPermanentError, LLMTransientError
from fedtone.application.ports import (
    ComparisonRepository,
    PolicyDocumentRepository,
    ReviewItemRepository,
    SourceSnapshotRepository,
    ToneAnalysisRepository,
)
from fedtone.application.prompts import (
    COMPARISON_PROMPT_VERSION,
    STATEMENT_PROMPT_VERSION,
    comparison_request,
    statement_request,
    suspicious_instructions,
)
from fedtone.domain.diff import StatementDiff, diff_statements, tone_delta
from fedtone.domain.evidence import validate_comparison_evidence, validate_tone_evidence
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.models import (
    DocumentComparison,
    DocumentText,
    EvidenceItem,
    PolicyDocument,
    ReviewItem,
    ToneAnalysis,
    transition,
)
from fedtone.domain.tone_schema import (
    COMPARISON_SCHEMA_VERSION,
    TONE_SCHEMA_VERSION,
    ComparisonOutput,
    ToneAnalysisOutput,
)
from fedtone.observability import correlation_context

logger = logging.getLogger(__name__)

MAX_OUTPUT_ATTEMPTS = 2
PROMPT_INJECTION_FLAG = "possible_prompt_injection_in_source"
# Statuses at which a document has a usable analysis for comparisons.
HAS_ANALYSIS = frozenset(
    {
        DocumentStatus.ANALYZED,
        DocumentStatus.COMPARED,
        DocumentStatus.MARKET_LINKED,
        DocumentStatus.PUBLISHED,
    }
)


LLM_FAILURE_PREFIX = "LLM: "


def is_retryable_failure(document: PolicyDocument, stage: DocumentStatus) -> bool:
    """Failed at ``stage`` because the provider was unavailable (not rejected by a reviewer)."""
    return (
        document.status is DocumentStatus.FAILED
        and document.last_successful_status is stage
        and (document.failure_reason or "").startswith(LLM_FAILURE_PREFIX)
    )


@dataclass(frozen=True)
class Rejection:
    """All attempts produced unusable output."""

    problems: tuple[str, ...]
    attempts: int
    last_output: str | None


class ToneAnalyzer:
    def __init__(self, client: LLMClient, *, temperature: float = 0.0) -> None:
        self._client = client
        self._temperature = temperature

    def analyze(
        self, document: PolicyDocument, text: DocumentText, *, now: datetime
    ) -> ToneAnalysis | Rejection:
        request = statement_request(
            title=text.title,
            published_at=text.published_at,
            paragraphs=text.paragraphs,
            temperature=self._temperature,
        )
        problems: list[str] = []
        content = None
        for attempt in range(1, MAX_OUTPUT_ATTEMPTS + 1):
            response = self._client.complete(request)
            content = response.content
            try:
                output = ToneAnalysisOutput.model_validate_json(content)
            except ValidationError as exc:
                problems.append(f"attempt {attempt}: schema: {_summarize(exc)}")
                continue
            report = validate_tone_evidence(output, text.normalized_text)
            if not report.valid:
                problems.append(f"attempt {attempt}: evidence: {'; '.join(report.problems)}")
                continue
            flags = list(output.uncertainty_flags)
            if suspicious_instructions(text.title + "\n" + text.normalized_text):
                flags.append(PROMPT_INJECTION_FLAG)
            return ToneAnalysis(
                document_id=document.id,
                document_hash=text.text_hash,
                model=response.model,
                prompt_version=STATEMENT_PROMPT_VERSION,
                schema_version=TONE_SCHEMA_VERSION,
                net_tone_score=output.overall_score,
                classification=output.label,
                confidence=output.confidence,
                evidence=tuple(
                    EvidenceItem(span.quote, span.dimension, span.direction, match.kind.value)
                    for span, match in zip(output.evidence, report.matches, strict=True)
                ),
                explanation=output.rationale,
                created_at=now,
                dimension_scores=output.dimension_scores(),
                uncertainty_flags=tuple(flags),
                temperature=self._temperature,
                raw_output=content,
            )
        return Rejection(tuple(problems), MAX_OUTPUT_ATTEMPTS, content)

    def interpret(
        self,
        current: DocumentText,
        previous: DocumentText,
        diff: StatementDiff,
    ) -> tuple[ComparisonOutput, str] | Rejection:
        """Validated LLM interpretation of a diff, plus the model id that produced it."""
        request = comparison_request(
            current_title=current.title,
            current_published_at=current.published_at,
            current_paragraphs=current.paragraphs,
            previous_title=previous.title,
            previous_published_at=previous.published_at,
            previous_paragraphs=previous.paragraphs,
            diff=diff,
            temperature=self._temperature,
        )
        problems: list[str] = []
        content = None
        for attempt in range(1, MAX_OUTPUT_ATTEMPTS + 1):
            response = self._client.complete(request)
            content = response.content
            try:
                output = ComparisonOutput.model_validate_json(content)
            except ValidationError as exc:
                problems.append(f"attempt {attempt}: schema: {_summarize(exc)}")
                continue
            report = validate_comparison_evidence(
                output, current.normalized_text, previous.normalized_text
            )
            if not report.valid:
                problems.append(f"attempt {attempt}: evidence: {'; '.join(report.problems)}")
                continue
            return output, response.model
        return Rejection(tuple(problems), MAX_OUTPUT_ATTEMPTS, content)


def _summarize(exc: ValidationError) -> str:
    errors = exc.errors()[:3]
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in errors)


class Outcome(StrEnum):
    ANALYZED = "analyzed"
    LOW_CONFIDENCE = "low_confidence"
    INJECTION_SUSPECTED = "injection_suspected"
    REJECTED = "rejected"
    FAILED = "failed"
    COMPARED = "compared"
    WAITING_FOR_PREVIOUS = "waiting_for_previous"
    COMPARISON_REJECTED = "comparison_rejected"


@dataclass
class AnalysisReport:
    outcomes: dict[Outcome, int] = field(default_factory=lambda: dict.fromkeys(Outcome, 0))
    aborted: str | None = None

    def count(self, outcome: Outcome) -> int:
        return self.outcomes[outcome]


class AnalysisService:
    def __init__(
        self,
        *,
        analyzer: ToneAnalyzer,
        documents: PolicyDocumentRepository,
        snapshots: SourceSnapshotRepository,
        analyses: ToneAnalysisRepository,
        comparisons: ComparisonRepository,
        reviews: ReviewItemRepository,
        min_confidence: float = 0.6,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._analyzer = analyzer
        self._documents = documents
        self._snapshots = snapshots
        self._analyses = analyses
        self._comparisons = comparisons
        self._reviews = reviews
        self._min_confidence = min_confidence
        self._clock = clock

    def run(self, central_bank: str, document_type: str) -> AnalysisReport:
        report = AnalysisReport()
        documents = self._documents.list(
            central_bank=central_bank, document_type=document_type, limit=None
        )
        try:
            for document in documents:
                for stage in (DocumentStatus.QUEUED_FOR_ANALYSIS, DocumentStatus.ANALYZED):
                    if is_retryable_failure(document, stage):
                        transition(document, stage, at=self._clock())
                        self._documents.update(document)
                if document.status is DocumentStatus.QUEUED_FOR_ANALYSIS:
                    report.outcomes[self._analyze(document)] += 1
            # A successor waits until its predecessor has an accepted analysis; documents
            # parked in NeedsReview (e.g. low confidence) hold back their successors.
            for document in self._documents.list(
                central_bank=central_bank, document_type=document_type, limit=None
            ):
                if document.status is DocumentStatus.ANALYZED:
                    report.outcomes[self._compare(document)] += 1
        except LLMPermanentError as exc:
            # Bad credentials or request format: stop instead of failing every document.
            logger.error("analysis aborted: %s", exc)
            report.aborted = str(exc)
        return report

    def _text(self, document: PolicyDocument) -> DocumentText | None:
        texts = self._snapshots.texts_for(document.id)
        matching = [t for t in texts if t.text_hash == document.content_hash]
        return matching[-1] if matching else None

    def _analyze(self, document: PolicyDocument) -> Outcome:
        with correlation_context(document_id=document.id):
            text = self._text(document)
            if text is None:
                self._needs_review(document, "no normalized text matches the document hash")
                return Outcome.REJECTED
            try:
                result = self._analyzer.analyze(document, text, now=self._clock())
            except LLMTransientError as exc:
                transition(
                    document,
                    DocumentStatus.FAILED,
                    at=self._clock(),
                    reason=f"{LLM_FAILURE_PREFIX}{exc}",
                )
                self._documents.update(document)
                return Outcome.FAILED

            if isinstance(result, Rejection):
                reason = "LLM output rejected: " + " | ".join(result.problems)
                self._needs_review(document, reason[:2000])
                return Outcome.REJECTED

            self._analyses.add(result)
            transition(document, DocumentStatus.ANALYZED, at=self._clock())
            self._documents.update(document)
            logger.info(
                "analyzed: score=%.2f label=%s confidence=%.2f",
                result.net_tone_score,
                result.classification,
                result.confidence,
            )
            if PROMPT_INJECTION_FLAG in result.uncertainty_flags:
                self._needs_review(document, "source contains instruction-like text")
                return Outcome.INJECTION_SUSPECTED
            if result.confidence < self._min_confidence:
                threshold = self._min_confidence
                self._needs_review(
                    document, f"confidence {result.confidence:.2f} below threshold {threshold:.2f}"
                )
                return Outcome.LOW_CONFIDENCE
            return Outcome.ANALYZED

    def _compare(self, document: PolicyDocument) -> Outcome:
        with correlation_context(document_id=document.id):
            current = self._analyses.latest_for_document(document.id)
            current_text = self._text(document)
            if current is None or current_text is None:
                self._needs_review(document, "analyzed document has no analysis or text")
                return Outcome.COMPARISON_REJECTED

            if document.previous_document_id is None:
                comparison = DocumentComparison(
                    document_id=document.id,
                    current_analysis_id=current.id,
                    created_at=self._clock(),
                    dimension_deltas=dict.fromkeys(current.dimension_scores, None),
                    uncertainty_flags=("baseline_no_previous_document",),
                )
            else:
                built = self._compare_with_previous(document, current, current_text)
                if isinstance(built, Outcome):
                    return built
                comparison = built

            self._comparisons.add(comparison)
            transition(document, DocumentStatus.COMPARED, at=self._clock())
            self._documents.update(document)
            return Outcome.COMPARED

    def _compare_with_previous(
        self, document: PolicyDocument, current: ToneAnalysis, current_text: DocumentText
    ) -> DocumentComparison | Outcome:
        previous_doc = self._documents.get(document.previous_document_id)
        if previous_doc is None or previous_doc.status not in HAS_ANALYSIS:
            return Outcome.WAITING_FOR_PREVIOUS
        previous = self._analyses.latest_for_document(previous_doc.id)
        previous_text = self._text(previous_doc)
        if previous is None or previous_text is None:
            return Outcome.WAITING_FOR_PREVIOUS

        flags: list[str] = []
        comparable = (previous.schema_version, previous.prompt_version, previous.model) == (
            current.schema_version,
            current.prompt_version,
            current.model,
        )
        if comparable:
            delta = tone_delta(
                current.net_tone_score,
                previous.net_tone_score,
                current.dimension_scores,
                previous.dimension_scores,
            )
        else:
            # Scores from different model/prompt/schema versions are not on the same scale.
            flags.append("analysis_versions_differ_delta_not_computed")
            delta = tone_delta(current.net_tone_score, None, current.dimension_scores, None)

        diff = diff_statements(previous_text.paragraphs, current_text.paragraphs)
        interpretation = model = None
        if diff.has_changes:
            try:
                result = self._analyzer.interpret(current_text, previous_text, diff)
            except LLMTransientError as exc:
                transition(
                    document,
                    DocumentStatus.FAILED,
                    at=self._clock(),
                    reason=f"{LLM_FAILURE_PREFIX}{exc}",
                )
                self._documents.update(document)
                return Outcome.FAILED
            if isinstance(result, Rejection):
                reason = "comparison output rejected: " + " | ".join(result.problems)
                self._needs_review(document, reason[:2000])
                return Outcome.COMPARISON_REJECTED
            output, model = result
            interpretation = output.model_dump(mode="json")
            flags.extend(output.uncertainty_flags)
        else:
            flags.append("no_language_change")

        return DocumentComparison(
            document_id=document.id,
            current_analysis_id=current.id,
            previous_document_id=previous_doc.id,
            previous_analysis_id=previous.id,
            created_at=self._clock(),
            diff=diff.to_dict(),
            overall_delta=delta.overall,
            dimension_deltas=delta.dimensions,
            interpretation=interpretation,
            model=model,
            prompt_version=COMPARISON_PROMPT_VERSION if interpretation else None,
            schema_version=COMPARISON_SCHEMA_VERSION if interpretation else None,
            uncertainty_flags=tuple(flags),
        )

    def _needs_review(self, document: PolicyDocument, reason: str) -> None:
        logger.warning("needs review: %s", reason)
        stage = document.status
        transition(document, DocumentStatus.NEEDS_REVIEW, at=self._clock(), reason=reason)
        self._documents.update(document)
        self._reviews.add(ReviewItem(document.id, stage, reason, self._clock()))
