from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fedtone.domain.lifecycle import DocumentStatus as S
from fedtone.domain.lifecycle import InvalidTransitionError
from fedtone.domain.models import (
    DomainValidationError,
    EvidenceItem,
    PolicyDocument,
    ToneAnalysis,
    ToneClassification,
    transition,
)

NOW = datetime(2024, 1, 31, 19, 0, tzinfo=UTC)
HASH = "a" * 64
OTHER_HASH = "b" * 64

# Written out by hand (not derived from the implementation) so the test is an independent spec.
EXPECTED_FORWARD: dict[S, set[S]] = {
    S.DISCOVERED: {S.FETCHED},
    S.FETCHED: {S.PARSED},
    S.PARSED: {S.NORMALIZED},
    S.NORMALIZED: {S.QUEUED_FOR_ANALYSIS},
    S.QUEUED_FOR_ANALYSIS: {S.ANALYZED},
    S.ANALYZED: {S.COMPARED},
    S.COMPARED: {S.MARKET_LINKED, S.PUBLISHED},
    S.MARKET_LINKED: {S.PUBLISHED},
    S.PUBLISHED: set(),
}
PIPELINE_ORDER = list(EXPECTED_FORWARD)


def make_document(status: S = S.DISCOVERED, content_hash: str | None = HASH) -> PolicyDocument:
    doc = PolicyDocument(
        central_bank="FED",
        document_type="fomc_statement",
        source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20240131a.htm",
        discovered_at=NOW,
        content_hash=content_hash,
    )
    doc.status = status
    doc.last_successful_status = status
    return doc


def make_analysis(doc: PolicyDocument, *, evidence: bool = True, **overrides) -> ToneAnalysis:
    values = dict(
        document_id=doc.id,
        document_hash=doc.content_hash or HASH,
        model="fake-model",
        prompt_version="tone-v1",
        schema_version="1.0",
        net_tone_score=0.3,
        classification=ToneClassification.HAWKISH,
        confidence=0.8,
        evidence=(EvidenceItem("inflation remains elevated", "inflation"),) if evidence else (),
        explanation="Inflation language is hawkish.",
        created_at=NOW,
    )
    values.update(overrides)
    return ToneAnalysis(**values)


def all_pipeline_pairs():
    for current in PIPELINE_ORDER:
        for target in S:
            yield current, target


@pytest.mark.parametrize(("current", "target"), list(all_pipeline_pairs()))
def test_transition_matrix(current: S, target: S) -> None:
    doc = make_document(current)
    analysis = make_analysis(doc)
    if current is S.PUBLISHED:
        allowed = False
    else:
        allowed = target in EXPECTED_FORWARD[current] or target in {S.NEEDS_REVIEW, S.FAILED}

    if allowed:
        transition(doc, target, at=NOW, reason="test", analysis=analysis)
        assert doc.status is target
        assert doc.history[-1].from_status is current
    else:
        with pytest.raises(InvalidTransitionError):
            transition(doc, target, at=NOW, reason="test", analysis=analysis)
        assert doc.status is current
        assert doc.history == []


def test_full_happy_path_records_history() -> None:
    doc = make_document()
    for target in (S.FETCHED, S.PARSED, S.NORMALIZED, S.QUEUED_FOR_ANALYSIS, S.ANALYZED):
        transition(doc, target, at=NOW)
    transition(doc, S.COMPARED, at=NOW)
    transition(doc, S.MARKET_LINKED, at=NOW)
    analysis = make_analysis(doc)
    transition(doc, S.PUBLISHED, at=NOW, analysis=analysis)

    assert doc.status is S.PUBLISHED
    assert doc.published_analysis_id == analysis.id
    assert [c.to_status for c in doc.history][-1] is S.PUBLISHED
    assert len(doc.history) == 8


@pytest.mark.parametrize("branch", [S.NEEDS_REVIEW, S.FAILED])
def test_failure_retains_last_successful_stage(branch: S) -> None:
    doc = make_document(S.NORMALIZED)

    transition(doc, branch, at=NOW, reason="llm returned malformed JSON")

    assert doc.status is branch
    assert doc.last_successful_status is S.NORMALIZED
    assert doc.failure_reason == "llm returned malformed JSON"


@pytest.mark.parametrize("branch", [S.NEEDS_REVIEW, S.FAILED])
def test_exception_branch_requires_reason(branch: S) -> None:
    doc = make_document(S.PARSED)

    with pytest.raises(InvalidTransitionError, match="reason"):
        transition(doc, branch, at=NOW, reason="  ")


@pytest.mark.parametrize("branch", [S.NEEDS_REVIEW, S.FAILED])
def test_resume_only_at_last_successful_stage(branch: S) -> None:
    doc = make_document(S.ANALYZED)
    transition(doc, branch, at=NOW, reason="evidence missing")

    for target in S:
        if target in {S.ANALYZED, S.FAILED}:
            continue
        with pytest.raises(InvalidTransitionError):
            transition(doc, target, at=NOW, reason="x", analysis=make_analysis(doc))

    transition(doc, S.ANALYZED, at=NOW)
    assert doc.status is S.ANALYZED
    assert doc.failure_reason is None


def test_needs_review_can_escalate_to_failed_but_not_back() -> None:
    doc = make_document(S.PARSED)
    transition(doc, S.NEEDS_REVIEW, at=NOW, reason="ambiguous date")
    transition(doc, S.FAILED, at=NOW, reason="reviewer rejected source")

    assert doc.last_successful_status is S.PARSED
    with pytest.raises(InvalidTransitionError):
        transition(doc, S.NEEDS_REVIEW, at=NOW, reason="again")


def test_publish_requires_source_hash() -> None:
    doc = make_document(S.COMPARED, content_hash=None)
    analysis = make_analysis(doc)

    with pytest.raises(InvalidTransitionError, match="content hash"):
        transition(doc, S.PUBLISHED, at=NOW, analysis=analysis)


def test_publish_requires_analysis_reference() -> None:
    doc = make_document(S.COMPARED)

    with pytest.raises(InvalidTransitionError, match="analysis reference"):
        transition(doc, S.PUBLISHED, at=NOW)


def test_publish_rejects_analysis_of_another_document() -> None:
    doc = make_document(S.COMPARED)
    analysis = make_analysis(doc, document_id=uuid4())

    with pytest.raises(InvalidTransitionError, match="different document"):
        transition(doc, S.PUBLISHED, at=NOW, analysis=analysis)


def test_publish_rejects_analysis_of_another_document_version() -> None:
    doc = make_document(S.COMPARED)
    analysis = make_analysis(doc, document_hash=OTHER_HASH)

    with pytest.raises(InvalidTransitionError, match="different document version"):
        transition(doc, S.PUBLISHED, at=NOW, analysis=analysis)


def test_publish_rejects_analysis_without_evidence() -> None:
    doc = make_document(S.MARKET_LINKED)
    analysis = make_analysis(doc, evidence=False)

    with pytest.raises(InvalidTransitionError, match="evidence"):
        transition(doc, S.PUBLISHED, at=NOW, analysis=analysis)
    assert doc.status is S.MARKET_LINKED


def test_transition_requires_timezone_aware_timestamp() -> None:
    doc = make_document()

    with pytest.raises(DomainValidationError):
        transition(doc, S.FETCHED, at=NOW.replace(tzinfo=None))


def test_history_keeps_timestamps_in_order() -> None:
    doc = make_document()
    transition(doc, S.FETCHED, at=NOW)
    transition(doc, S.PARSED, at=NOW + timedelta(seconds=5))

    assert [c.at for c in doc.history] == [NOW, NOW + timedelta(seconds=5)]
