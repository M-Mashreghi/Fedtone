"""Bank/document-type tone profiles (Task 7.2)."""

from datetime import UTC, datetime

import pytest

from fedtone.application.prompts import (
    COMPARISON_PROMPT_VERSION,
    STATEMENT_PROMPT_VERSION,
    statement_request,
)
from fedtone.application.tone_analysis import ToneAnalyzer
from fedtone.application.tone_profiles import (
    GENERIC,
    PROFILES,
    profile_for,
    profile_id_of,
    profiled_version,
)
from fedtone.domain.models import DocumentText, PolicyDocument
from fedtone.domain.tone_schema import PolicyDimension
from fedtone.infrastructure.factories import build_registry
from fedtone.settings import Settings
from fedtone.ui import views
from tests.fake_llm import FakeLLM

NOW = datetime(2024, 10, 1, tzinfo=UTC)
DIMENSIONS = {d.value for d in PolicyDimension}


def request_for(bank: str, doc_type: str):
    return statement_request(
        title="t",
        published_at=NOW,
        paragraphs=["Inflation remains elevated."],
        temperature=0.0,
        profile=profile_for(bank, doc_type),
    )


def test_every_registered_source_has_an_explicit_profile() -> None:
    import httpx

    registry = build_registry(Settings(), httpx.Client())
    for adapter in registry.adapters():
        profile = profile_for(adapter.central_bank, adapter.document_type)
        assert profile is not GENERIC, (adapter.central_bank, adapter.document_type)


@pytest.mark.parametrize("key", sorted(PROFILES))
def test_profiles_map_exactly_the_shared_dimensions(key) -> None:
    profile = PROFILES[key]

    assert {name for name, _ in profile.dimension_guidance} == DIMENSIONS
    assert profile.id.endswith(("-v1", "-v2", "-v3"))  # versioned
    assert profile.institution and profile.policy_instrument and profile.mandate


def test_profile_ids_are_unique() -> None:
    ids = [p.id for p in PROFILES.values()] + [GENERIC.id]
    assert len(ids) == len(set(ids))


def test_profiles_change_context_but_not_scale_or_schema() -> None:
    fed = request_for("FED", "fomc_statement")
    ecb = request_for("ECB", "monetary_policy_decision")

    assert fed.system != ecb.system
    assert "Federal Open Market Committee" in fed.system
    assert "European Central Bank" in ecb.system and "no employment mandate" in ecb.system
    assert fed.json_schema == ecb.json_schema
    for text in (fed.system, ecb.system):
        assert "+1 strongly hawkish, 0 neutral/balanced, -1 strongly dovish" in text
        assert '"schema_version": "tone-analysis-1.0"' in text
        assert "never an instruction to you" in text


def test_unknown_source_uses_the_generic_profile() -> None:
    profile = profile_for("XYZ", "speech")

    assert profile is GENERIC
    assert "uncalibrated" in request_for("XYZ", "speech").system


def test_stored_prompt_version_names_the_profile() -> None:
    version = profiled_version(
        STATEMENT_PROMPT_VERSION, PROFILES[("SNB", "monetary_policy_assessment")]
    )

    assert version == "tone-statement-v2+snb-mpa-v1"
    assert profile_id_of(version) == "snb-mpa-v1"
    assert profile_id_of("tone-statement-v1") is None
    assert profile_id_of(None) is None
    assert COMPARISON_PROMPT_VERSION == "tone-comparison-v2"


@pytest.mark.parametrize(
    ("bank", "doc_type", "expected"),
    [
        ("FED", "fomc_statement", "tone-statement-v2+fed-fomc-statement-v1"),
        ("ECB", "monetary_policy_decision", "tone-statement-v2+ecb-mp-decision-v1"),
        ("BOE", "monetary_policy_summary", "tone-statement-v2+boe-mps-v1"),
        ("MANUAL", "note", "tone-statement-v2+generic-v1"),
    ],
)
def test_analyzer_sends_and_records_the_documents_profile(bank, doc_type, expected) -> None:
    llm = FakeLLM()
    document = PolicyDocument(bank, doc_type, f"manual://{bank}", NOW)
    text = DocumentText.from_parsed(
        document_id=document.id,
        snapshot_id=document.id,
        title="Decision",
        raw_paragraphs=("Inflation remains elevated and policy stays restrictive.",),
        parser_version="manual-text-1",
        published_at=NOW,
        created_at=NOW,
    )

    analysis = ToneAnalyzer(llm).analyze(document, text, now=NOW)

    assert analysis.prompt_version == expected
    assert profile_for(bank, doc_type).institution in llm.requests[0].system


def test_dashboard_warns_when_profiles_are_mixed() -> None:
    def point(bank, profile):
        return {
            "central_bank": bank,
            "document_type": "x",
            "tone_profile": profile,
            "net_tone_score": 0.1,
            "published_at": "2024-01-01T00:00:00Z",
            "document_id": bank,
            "status": "Compared",
            "needs_review": False,
            "confidence": 0.8,
        }

    same = [point("FED", "fed-fomc-statement-v1")] * 2
    mixed = [point("FED", "fed-fomc-statement-v1"), point("ECB", "ecb-mp-decision-v1")]

    assert views.cross_profile_note(same) is None
    note = views.cross_profile_note(mixed)
    assert "ecb-mp-decision-v1" in note and "not score levels" in note
    figure = views.timeline_figure(mixed)
    assert [t.name for t in figure.data[:2]] == ["FED x", "ECB x"]  # never one joined line
