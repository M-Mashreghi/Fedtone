# FedTone Implementation Phases

Exhaustive engineering breakdown for FedTone delivery. Each phase defines **Scope**, **Concrete Deliverables**, **Technical Challenges**, and **Definition of Done (DoD)** with automated test criteria.

**Related documents:** [ROADMAP.md](./ROADMAP.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [DIAGRAMS.md](./DIAGRAMS.md) · [TASKS.md](./TASKS.md)

---

## Phase 1: Foundation, Data Layer & Research Domain

**Status:** `[x]` Completed  
**Roadmap alignment:** M1 Alpha

### Scope

Create the Python project foundation, research-domain models, SQLite persistence, configuration system, logging, and test baseline. No LLM scoring or market analysis is required yet.

### Concrete Deliverables

1. **Project layout**
   - `src/fedtone/domain`, `application`, `infrastructure`, `api`, `ui`.
   - Python 3.12+, `pyproject.toml`, Ruff, pytest, optional mypy/pyright.
2. **Configuration**
   - Pydantic settings from environment.
   - `.env.example` with placeholders only.
3. **Domain entities**
   - `PolicyDocument`, `ProcessingRun`, `AnalysisVersion`, `ReviewItem`.
4. **SQLite + SQLAlchemy/Alembic**
   - Initial schema and migrations.
   - Unique constraint on canonical source/document identity.
5. **Lifecycle rules**
   - Centralized state transition service.
6. **FastAPI skeleton**
   - `/healthz`, `/ready`, minimal document query route.

### Technical Challenges

| Challenge | Approach |
|---|---|
| Keep domain logic independent of FastAPI/SQLAlchemy | Interfaces and DTO boundaries; ORM mapping outside domain. |
| Reproducibility from day one | Store content hashes and version metadata in core model. |
| SQLite evolution | Alembic migrations and integration tests on fresh DB. |
| Avoid premature complexity | One service, one DB, modular code; no message broker in MVP. |

### Definition of Done (DoD)

- [x] Clean install from documented Python version succeeds.
- [x] `pytest` passes on a clean checkout.
- [x] Database migration creates all Phase 1 tables.
- [x] Duplicate canonical document insertion is rejected/idempotent.
- [x] Invalid lifecycle transitions fail explicitly.
- [x] Health endpoints return expected status.
- [x] No secret value is committed.

### Automated Test Criteria

| Type | Criteria |
|---|---|
| Unit | Document lifecycle transition matrix. |
| Unit | Hash/canonical identity behavior. |
| Unit | Settings validation and missing-secret behavior. |
| Integration | Migrate fresh SQLite → insert/query/update document. |
| API | `/healthz` and basic document route smoke tests. |

---

## Phase 2: Official FOMC Ingestion & Document Normalization

**Status:** `[-]` In Progress — implemented; awaiting verification on captured official pages  
**Roadmap alignment:** M1 Alpha completion

### Scope

Build the official-source ingestion pipeline for FOMC statements, immutable raw snapshots, metadata parsing, normalized text, duplicate prevention, and previous-document linkage.

### Concrete Deliverables

1. `CentralBankSourceAdapter` interface.
2. Federal Reserve FOMC statement adapter.
3. Manual URL/text adapter for fixtures and development.
4. Raw snapshot + content hash persistence.
5. Document parser and boilerplate removal.
6. Paragraph/section preservation.
7. Comparable-document linker (`previous_document_id`).
8. Scheduled/manual ingestion command.

### Technical Challenges

| Challenge | Approach |
|---|---|
| Official HTML structure changes | Adapter-specific parser + saved fixtures. |
| Duplicate releases / revised pages | Canonical id + URL + content hash strategy. |
| Publication timestamp ambiguity | Preserve published/observed/fetched timestamps separately. |
| Text cleaning alters meaning | Conservative normalization; retain raw source for audit. |

### Definition of Done (DoD)

- [ ] Historical FOMC fixture set ingests successfully. *(passes on synthetic fixtures; official snapshots pending)*
- [x] Re-running ingestion creates no duplicate canonical records.
- [ ] Parsed text preserves policy wording and paragraph order. *(passes on synthetic fixtures; official snapshots pending)*
- [x] Each statement links to the correct previous comparable statement.
- [x] Raw source hash and canonical URL are stored.
- [x] Parser failure creates structured failure/review state.

### Automated Test Criteria

| Type | Criteria |
|---|---|
| Unit | HTML parser extracts expected title/date/body from fixtures. |
| Unit | Boilerplate removal does not remove policy paragraphs. |
| Unit | Previous-document linker orders by policy publication time. |
| Integration | Same document ingested twice remains one canonical record. |
| Integration | Adapter failure retries boundedly and preserves audit state. |

---

## Phase 3: LLM Hawkish/Dovish Tone Engine

**Status:** `[-]` In Progress — implemented and verified with a fake LLM; awaiting a human-labeled FOMC benchmark run against a real model.  
**Roadmap alignment:** M2 Beta

### Scope

Implement evidence-grounded structured LLM scoring, policy-dimension analysis, previous-statement comparison, validation, uncertainty routing and an offline evaluation harness.

### Concrete Deliverables

1. **Versioned analysis schema**
   - overall score `[-1, +1]`,
   - hawkish/dovish label,
   - confidence,
   - dimension scores,
   - evidence spans,
   - rationale and uncertainty flags.
2. **LLM provider abstraction**
   - OpenAI-compatible client.
3. **Prompt package**
   - current statement analysis,
   - previous-vs-current comparison,
   - prompt injection boundaries.
4. **Evidence validator**
   - verifies returned quote/spans exist in input source.
5. **Statement diff engine**
   - deterministic added/removed/changed blocks.
6. **Tone-delta calculator**
   - overall and dimension deltas.
7. **Review routing**
   - parse/evidence/confidence failure → `NeedsReview`.
8. **Evaluation harness**
   - labeled meeting set, agreement/stability metrics.

### Technical Challenges

| Challenge | Approach |
|---|---|
| LLM nondeterminism | Low temperature, structured outputs, repeated-run stability benchmark. |
| Hallucinated citations | Exact/fuzzy evidence validation against source text. |
| Label calibration | Human-labeled benchmark + score-distribution monitoring. |
| Prompt injection in source text | Treat document as quoted data, never instructions. |
| Model upgrades break comparability | Store model/prompt/schema version; benchmark before migration. |

### Definition of Done (DoD)

- [x] Valid statement fixture returns schema-valid analysis.
- [x] Published analyses have evidence for material dimensions.
- [x] Invalid evidence is rejected and never silently published.
- [x] Current vs previous statement produces a persisted tone delta.
- [ ] Labeled benchmark reports direction agreement and score stability. *(`fedtone benchmark` reports both; so far run only with a fake LLM on a synthetic dataset)*
- [x] All analyses persist model/prompt/schema/document hashes.

### Automated Test Criteria

| Type | Criteria |
|---|---|
| Unit | Schema accepts valid and rejects malformed LLM JSON. |
| Unit | Evidence validator passes real quotes and rejects hallucinated ones. |
| Unit | Tone delta calculation handles missing dimensions safely. |
| Unit | Diff engine fixtures detect added/removed/changed language. |
| Integration | Fake LLM pipeline reaches `Analyzed` / `NeedsReview` correctly. |
| Eval | Benchmark script outputs agreement, invalid-output and stability metrics. |

---

## Phase 4: Market Reaction & Historical Analogue Engine

**Status:** `[x]` Completed — verified with fake/fixture market data and a fake LLM; live FRED fetch to be checked locally.  
**Roadmap alignment:** M3 Release Candidate

### Scope

Attach deterministic market-data and event-study analytics to policy events while preserving point-in-time integrity and a strict separation between LLM explanation and numeric finance calculations.

### Concrete Deliverables

1. `MarketDataProvider` interface.
2. Free-first provider implementation(s) for MVP.
3. Trading-calendar/date alignment.
4. Configurable event windows (`T-1`, `T`, `T+1`, `T+5`).
5. Return and yield-basis-point calculators.
6. Missing-data and holiday handling.
7. Historical analogue search by tone level/delta/dimensions.
8. Research-summary composer using precomputed metrics only.
9. Point-in-time validation checks.

### Technical Challenges

| Challenge | Approach |
|---|---|
| Announcement dates on nontrading boundaries | Explicit exchange-calendar alignment policy. |
| Yield vs price units | Typed metric definitions; bps functions separately tested. |
| Provider gaps | Provider abstraction + missing-data flags; never interpolate silently. |
| Look-ahead leakage | Persist release timestamps and enforce window queries. |
| Overclaiming causality | UI/report language says reaction/association, not causal effect. |

### Definition of Done (DoD)

- [x] Meeting event links to market observations without future leakage.
- [x] Returns/yield changes reproduce known hand-calculated fixtures.
- [x] Missing observations are explicit, not silently filled.
- [x] Historical analogue selection uses only features available at event time. *(earlier meetings only; their tone scores come from today's model, see ARCHITECTURE)*
- [x] LLM summary receives computed metrics and cannot overwrite canonical values.

### Automated Test Criteria

| Type | Criteria |
|---|---|
| Unit | Return, log-return and basis-point calculations. |
| Unit | Weekend/holiday alignment fixture. |
| Unit | Event-window boundary tests prevent look-ahead. |
| Integration | Fake provider → event metrics persisted for one meeting. |
| Regression | Historical analogue query deterministic for fixed dataset. |

---

## Phase 5: Research API & Dashboard

**Status:** `[ ]` Not Started  
**Roadmap alignment:** M4 v1.0

### Scope

Expose the FedTone dataset and workflow through a documented API and a polished Streamlit research dashboard.

### Concrete Deliverables

1. **FastAPI endpoints**
   - document list/detail,
   - tone analysis,
   - comparison,
   - market reaction,
   - pipeline run/retry,
   - system telemetry.
2. **Streamlit dashboard**
   - policy tone timeline,
   - meeting detail,
   - previous-vs-current diff,
   - dimension chart,
   - evidence panel,
   - market reaction chart,
   - historical analogues.
3. **Filters**
   - date range, document type, central bank, model version.
4. **Exports**
   - CSV/JSON for signals,
   - Markdown research note.
5. **Audit view**
   - source URL, hashes, model, prompt, schema, run timestamp.

### Technical Challenges

| Challenge | Approach |
|---|---|
| UI tells a coherent research story | Meeting-centric layout with evidence beside scores. |
| Large historical comparisons | Precompute/cache lightweight aggregates. |
| API/UI business-rule divergence | Shared application services; thin routes and UI callbacks. |
| Misleading certainty | Always display confidence, evidence and missing-data flags. |

### Definition of Done (DoD)

- [ ] User can select a meeting and see source, score, diff, evidence and market reaction.
- [ ] Timeline filters and charts match API values.
- [ ] Export reproduces displayed canonical metrics.
- [ ] Audit metadata is visible for every published analysis.
- [ ] Primary dashboard flows work on desktop and narrow viewport.

### Automated Test Criteria

| Type | Criteria |
|---|---|
| API | Document/detail/comparison/market endpoints. |
| Integration | Pipeline run appears in API and dashboard query layer. |
| UI | Smoke tests for meeting selection and key components where practical. |
| Contract | Export values equal canonical database/API values. |

---

## Phase 6: Hardening, Dockerization & Production Readiness

**Status:** `[ ]` Not Started  
**Roadmap alignment:** M5 v1.1

### Scope

Add end-to-end coverage, scheduled ingestion, observability, Docker, CI/CD, backup/restore, security hygiene and operational documentation.

### Concrete Deliverables

1. E2E source → analysis → comparison → market → API flow.
2. Scheduler for new official releases.
3. Structured logs with correlation/run IDs.
4. Health/readiness endpoints with DB/provider checks.
5. Dockerfile + Compose.
6. GitHub Actions CI.
7. Optional GHCR release workflow.
8. SQLite online backup / restore procedure.
9. Operational runbook.

### Technical Challenges

| Challenge | Approach |
|---|---|
| External-service CI flakiness | Deterministic fakes/fixtures in CI; optional live smoke. |
| Scheduled duplicate processing | Idempotency keys and unique DB constraints. |
| LLM/provider outages | Bounded retries and resumable run states. |
| Secret exposure | Environment-only keys, log redaction, GitHub secret scanning. |
| SQLite corruption/loss | Scheduled snapshots + tested restore drill. |

### Definition of Done (DoD)

- [ ] Full fake-provider E2E path is green in CI.
- [ ] Scheduled ingestion can run twice without duplication.
- [ ] Docker Compose starts healthy API/UI.
- [ ] Logs include run/document correlation identifiers without secrets.
- [ ] Backup restore drill reproduces research records.
- [ ] Deployment and runbook docs are complete.

### Automated Test Criteria

| Type | Criteria |
|---|---|
| E2E | Fixture source → published research result. |
| E2E | Invalid LLM evidence → review state, no published score. |
| E2E | Market provider missing data → explicit partial result. |
| Integration | Scheduler idempotency. |
| Integration | Backup → restore → query known analysis. |
| CI | pytest + lint + type checks + dependency/security checks. |

---

## Phase 7: Multi-Bank, Speech Analytics & Agentic Research

**Status:** `[ ]` Future  
**Roadmap alignment:** M6 Future

### Scope

Extend the stable FOMC system into a broader central-bank research platform and add optional agentic workflows.

### Concrete Deliverables

1. ECB, SNB and BoE source adapters.
2. Bank-specific prompt profiles and dimension mapping.
3. Speeches/minutes ingestion with speaker identity.
4. Speaker-level hawkish/dovish history.
5. Agentic workflow:
   - Data Agent,
   - Policy Analyst,
   - Skeptic / Evidence Reviewer,
   - Market Analyst,
   - Research Note Composer.
6. RAG retrieval of historical analogues and prior analyses.
7. Optional alerting on material tone changes.
8. Optional intraday provider integration.

### Technical Challenges

| Challenge | Approach |
|---|---|
| Cross-bank language is not directly comparable | Shared normalized schema + bank-specific calibration. |
| Agent agreement can create false confidence | Independent evidence reviewer and deterministic final validation. |
| More document types increase noise | Separate document-type baselines and filters. |
| Intraday timestamps/timezones | UTC canonical timestamps and provider-specific market sessions. |

### Definition of Done (DoD)

- [ ] New banks use the same core domain contracts without forked pipelines.
- [ ] Bank/document-type calibration is explicit.
- [ ] Agentic report cannot publish unsupported claims without citations.
- [ ] Existing FOMC regression suite remains green.

---

## Cross-Phase Quality Gates

After every task/phase:

1. Automated tests added and passing.
2. `TASKS.md` status and verification updated.
3. Relevant architecture/diagram/API/config docs synchronized.
4. No secret committed.
5. No unversioned prompt/schema changes.
6. No finance metric moved from deterministic code into LLM reasoning.
7. No historical evaluation uses information unavailable at the event timestamp.

---

## Phase Dependency Graph

```mermaid
flowchart LR
    P1[Phase 1 Foundation] --> P2[Phase 2 FOMC Ingestion]
    P2 --> P3[Phase 3 Tone Engine]
    P3 --> P4[Phase 4 Market Analytics]
    P3 --> P5[Phase 5 Dashboard & API]
    P4 --> P5
    P5 --> P6[Phase 6 Production]
    P6 --> P7[Phase 7 Multi-Bank & Agents]
```
