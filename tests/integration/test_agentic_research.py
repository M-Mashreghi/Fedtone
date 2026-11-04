# ruff: noqa: F811 - pytest fixtures are imported and requested by name
"""Agentic research workflow: only reviewed, evidence-backed claims are published (Task 7.4)."""

import json

from fastapi.testclient import TestClient

from fedtone.api.app import create_app
from fedtone.application.agentic_research import AgentOutcome, AgentResearchService
from fedtone.application.llm import LLMRequest, LLMTransientError
from fedtone.application.pipeline import CYCLE, PipelineStage
from fedtone.application.research_summary import metric_display
from fedtone.infrastructure.persistence.repositories import SqlAlchemyAgentNoteRepository
from fedtone.infrastructure.pipeline import AdapterFactories
from fedtone.settings import Settings
from tests.api.conftest import harness  # noqa: F401
from tests.fake_llm import FakeLLM
from tests.integration.test_research_summary import NAMES

SPX = "SP500_simple_return_[0,+1]"


def facts_of(request: LLMRequest) -> dict[str, dict]:
    payload = request.user.split("<<<FACTS>>>\n", 1)[1].split("\n<<<END_FACTS>>>", 1)[0]
    return {f["id"]: f for f in json.loads(payload)}


def claims_of(request: LLMRequest) -> list[dict]:
    payload = request.user.split("<<<CLAIMS>>>\n", 1)[1].split("\n<<<END_CLAIMS>>>", 1)[0]
    return json.loads(payload)


class AgentLLM(FakeLLM):
    """Scripted agents. ``extra_policy`` adds a claim; ``reject`` lists claim ids the skeptic
    marks unsupported; ``broken_review`` returns malformed reviewer output."""

    def __init__(self, *, extra_policy=None, reject=(), broken_review=False, outage=False):
        super().__init__()
        self.extra_policy = extra_policy
        self.reject = set(reject)
        self.broken_review = broken_review
        self.outage = outage

    def _respond(self, request: LLMRequest) -> str:
        if self.outage:
            raise LLMTransientError("503")
        if request.schema_name == "agent_policy_claims":
            facts = facts_of(request)
            claims = [
                {
                    "claim_id": "p1",
                    "text": f"The overall tone score was {facts['tone']['display']}.",
                    "cited_fact_ids": ["tone"],
                }
            ]
            if self.extra_policy:
                claims.append(self.extra_policy)
            return json.dumps({"schema_version": "agent-claims-1.0", "claims": claims})
        if request.schema_name == "agent_market_claims":
            spx = facts_of(request)[SPX]["display"]
            claim = {
                "claim_id": "m1",
                "text": f"The S&P 500 return over [0,+1] was {spx} after the release.",
                "cited_fact_ids": [SPX],
            }
            return json.dumps({"schema_version": "agent-claims-1.0", "claims": [claim]})
        if request.schema_name == "agent_review":
            if self.broken_review:
                return "not json"
            verdicts = [
                {
                    "claim_id": c["claim_id"],
                    "supported": c["claim_id"] not in self.reject,
                    "reason": "checked against cited facts",
                }
                for c in claims_of(request)
            ]
            return json.dumps({"schema_version": "agent-claims-1.0", "verdicts": verdicts})
        raise AssertionError(request.schema_name)

    def complete(self, request):
        from fedtone.application.llm import LLMResponse

        self.requests.append(request)
        return LLMResponse(self._respond(request), self.model)


def notes(harness) -> SqlAlchemyAgentNoteRepository:
    return SqlAlchemyAgentNoteRepository(harness.session_factory)


def service(harness, llm) -> AgentResearchService:
    return AgentResearchService(
        client=llm,
        documents=harness.documents,
        analyses=harness.analyses,
        comparisons=harness.comparisons,
        metrics=harness.metrics,
        notes=notes(harness),
        reviews=harness.reviews,
        instrument_names=NAMES,
        clock=harness.clock,
    )


def test_reviewed_claims_are_published_with_canonical_facts(harness) -> None:
    jan = harness.doc("jan")

    outcome, note = service(harness, AgentLLM()).run_document(jan.id)

    assert outcome is AgentOutcome.PUBLISHED
    assert [c.claim_id for c in note.accepted] == ["policy:p1", "market:m1"]
    spx = next(
        m
        for m in harness.metrics.for_document(jan.id)
        if (m.symbol, m.window, m.kind.value) == ("SP500", "[0,+1]", "simple_return")
    )
    analysis = harness.analyses.latest_for_document(jan.id)
    # The note's numbers are the canonical displays of stored records.
    assert f"| {SPX} |" in note.markdown and metric_display(spx) in note.markdown
    assert f"{analysis.net_tone_score:+.2f}" in note.markdown
    assert "association" in note.markdown.lower() or "associations" in note.markdown.lower()
    stored = notes(harness)
    assert stored.latest_for_document(jan.id).markdown == note.markdown


def test_invented_number_is_blocked_by_the_gate(harness) -> None:
    invented = {"claim_id": "p2", "text": "Tone rose 0.75 points.", "cited_fact_ids": ["tone"]}

    _, note = service(harness, AgentLLM(extra_policy=invented)).run_document(harness.doc("jan").id)

    (rejected,) = note.rejected
    assert rejected.claim_id == "policy:p2"
    assert any("0.75" in p for p in rejected.problems)
    assert "0.75" not in note.markdown and "Tone rose" not in note.markdown


def test_skeptic_can_veto_a_claim_that_passed_the_gate(harness) -> None:
    llm = AgentLLM(reject={"market:m1"})

    _, note = service(harness, llm).run_document(harness.doc("jan").id)

    assert [c.claim_id for c in note.accepted] == ["policy:p1"]
    assert note.rejected[0].problems == ("reviewer: not supported",)
    assert "S&P 500 return" not in note.markdown
    # The reviewer only saw claims that passed the deterministic gate.
    review = next(r for r in llm.requests if r.schema_name == "agent_review")
    assert {c["claim_id"] for c in claims_of(review)} == {"policy:p1", "market:m1"}


def test_no_reviewer_verdict_means_nothing_is_published(harness) -> None:
    jan = harness.doc("jan")

    outcome, note = service(harness, AgentLLM(broken_review=True)).run_document(jan.id)

    assert outcome is AgentOutcome.REJECTED and note is None
    assert notes(harness).latest_for_document(jan.id) is None
    (item,) = harness.reviews.list_open(document_id=jan.id)
    assert "evidence review" in item.reason


def test_provider_outage_stores_nothing(harness) -> None:
    jan = harness.doc("jan")

    outcome, note = service(harness, AgentLLM(outage=True)).run_document(jan.id)

    assert outcome is AgentOutcome.FAILED and note is None
    assert harness.reviews.list_open(document_id=jan.id) == []


def test_batch_run_skips_documents_that_already_have_a_note(harness) -> None:
    first = service(harness, AgentLLM()).run("FED", "fomc_statement")
    second = service(harness, AgentLLM()).run("FED", "fomc_statement")

    assert first.outcomes[AgentOutcome.PUBLISHED] == 3
    assert not any(second.outcomes.values())
    assert PipelineStage.AGENT_NOTES not in CYCLE


def test_agent_note_api(harness, database_url, migrated_engine) -> None:
    settings = Settings(database={"url": database_url})
    with_llm = TestClient(
        create_app(
            settings,
            engine=migrated_engine,
            adapter_factories=AdapterFactories(llm_client=lambda s: AgentLLM()),
        )
    )
    without_llm = TestClient(create_app(settings, engine=migrated_engine))
    jan = harness.doc("jan").id

    assert with_llm.get(f"/api/v1/documents/{jan}/agent-note").status_code == 404
    assert without_llm.post(f"/api/v1/documents/{jan}/agent-note").status_code == 409
    created = with_llm.post(f"/api/v1/documents/{jan}/agent-note").json()
    fetched = with_llm.get(f"/api/v1/documents/{jan}/agent-note").json()

    assert created["outcome"] == "published"
    assert fetched["markdown"] == created["note"]["markdown"]
    assert [c["claim_id"] for c in fetched["accepted"]] == ["policy:p1", "market:m1"]
    assert fetched["prompt_version"] == "agent-research-v1"
    stage = with_llm.post("/api/v1/pipeline/agent_notes")
    assert stage.status_code == 200 and stage.json()["outcomes"]["published"] == 2
