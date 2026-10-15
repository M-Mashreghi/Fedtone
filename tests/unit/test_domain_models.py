from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fedtone.domain.lifecycle import DocumentStatus, InvalidTransitionError
from fedtone.domain.models import (
    DocumentComparison,
    DomainValidationError,
    EvidenceItem,
    PolicyDocument,
    ProcessingRun,
    ReviewItem,
    RunStatus,
    RunTrigger,
    ToneAnalysis,
    ToneClassification,
)

NOW = datetime(2024, 1, 31, 19, 0, tzinfo=UTC)


def analysis_kwargs(**overrides):
    values = dict(
        document_id=uuid4(),
        document_hash="c" * 64,
        model="fake-model",
        prompt_version="tone-v1",
        schema_version="1.0",
        net_tone_score=-0.4,
        classification=ToneClassification.DOVISH,
        confidence=0.7,
        evidence=(EvidenceItem("risks have moved into better balance"),),
        explanation="Balanced risk language.",
        created_at=NOW,
    )
    values.update(overrides)
    return values


@pytest.mark.parametrize("score", [-1.0, 0.0, 1.0])
def test_tone_analysis_accepts_boundary_scores(score: float) -> None:
    assert ToneAnalysis(**analysis_kwargs(net_tone_score=score)).net_tone_score == score


@pytest.mark.parametrize(
    "overrides",
    [
        {"net_tone_score": 1.01},
        {"net_tone_score": -1.5},
        {"confidence": 1.2},
        {"confidence": -0.1},
        {"document_hash": "not-a-hash"},
        {"model": ""},
        {"prompt_version": " "},
        {"schema_version": ""},
        {"explanation": ""},
        {"created_at": datetime(2024, 1, 31)},
    ],
)
def test_tone_analysis_rejects_invalid_values(overrides) -> None:
    with pytest.raises(DomainValidationError):
        ToneAnalysis(**analysis_kwargs(**overrides))


def test_tone_analysis_evidence_detection() -> None:
    assert ToneAnalysis(**analysis_kwargs()).has_evidence
    assert not ToneAnalysis(**analysis_kwargs(evidence=())).has_evidence
    assert not ToneAnalysis(**analysis_kwargs(evidence=(EvidenceItem("  "),))).has_evidence


@pytest.mark.parametrize(
    "overrides",
    [
        {"central_bank": ""},
        {"source_url": " "},
        {"discovered_at": datetime(2024, 1, 31)},
        {"content_hash": "XYZ"},
    ],
)
def test_policy_document_rejects_invalid_values(overrides) -> None:
    values = dict(
        central_bank="FED",
        document_type="fomc_statement",
        source_url="https://www.federalreserve.gov/x.htm",
        discovered_at=NOW,
    )
    values.update(overrides)
    with pytest.raises(DomainValidationError):
        PolicyDocument(**values)


def test_new_policy_document_starts_discovered() -> None:
    doc = PolicyDocument(
        central_bank="FED",
        document_type="fomc_statement",
        source_url="https://www.federalreserve.gov/x.htm",
        discovered_at=NOW,
    )
    assert doc.status is DocumentStatus.DISCOVERED
    assert doc.history == []


def test_comparison_baseline_and_linked() -> None:
    baseline = DocumentComparison(document_id=uuid4(), current_analysis_id=uuid4(), created_at=NOW)
    linked = DocumentComparison(
        document_id=uuid4(),
        current_analysis_id=uuid4(),
        created_at=NOW,
        previous_document_id=uuid4(),
        previous_analysis_id=uuid4(),
    )
    assert baseline.is_baseline
    assert not linked.is_baseline


def test_comparison_requires_previous_ids_together() -> None:
    with pytest.raises(DomainValidationError):
        DocumentComparison(
            document_id=uuid4(),
            current_analysis_id=uuid4(),
            created_at=NOW,
            previous_document_id=uuid4(),
        )


def test_processing_run_finishes_once() -> None:
    run = ProcessingRun(trigger=RunTrigger.MANUAL, started_at=NOW)
    run.finish(RunStatus.SUCCEEDED, at=NOW + timedelta(minutes=1))

    assert run.status is RunStatus.SUCCEEDED
    with pytest.raises(InvalidTransitionError):
        run.finish(RunStatus.FAILED, at=NOW + timedelta(minutes=2), error="late")


def test_processing_run_failure_requires_error_and_valid_time() -> None:
    run = ProcessingRun(trigger=RunTrigger.SCHEDULED, started_at=NOW)

    with pytest.raises(DomainValidationError):
        run.finish(RunStatus.FAILED, at=NOW + timedelta(minutes=1))
    with pytest.raises(DomainValidationError):
        run.finish(RunStatus.SUCCEEDED, at=NOW - timedelta(minutes=1))
    with pytest.raises(InvalidTransitionError):
        run.finish(RunStatus.RUNNING, at=NOW)
    assert run.status is RunStatus.RUNNING


def test_review_item_resolves_once() -> None:
    item = ReviewItem(
        document_id=uuid4(),
        stage=DocumentStatus.ANALYZED,
        reason="confidence below threshold",
        created_at=NOW,
    )
    assert item.is_open

    item.resolve("approved after manual check", at=NOW + timedelta(hours=1))

    assert not item.is_open
    with pytest.raises(InvalidTransitionError):
        item.resolve("again", at=NOW + timedelta(hours=2))


def test_tone_analysis_rejects_out_of_range_dimension_score() -> None:
    with pytest.raises(DomainValidationError, match="labor"):
        ToneAnalysis(**analysis_kwargs(dimension_scores={"labor": 1.5}))


def test_comparison_interpretation_requires_provenance() -> None:
    with pytest.raises(DomainValidationError, match="model, prompt and schema"):
        DocumentComparison(
            document_id=uuid4(),
            current_analysis_id=uuid4(),
            created_at=NOW,
            interpretation={"summary": "x"},
        )
