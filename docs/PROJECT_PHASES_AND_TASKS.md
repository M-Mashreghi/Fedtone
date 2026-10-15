# FedTone — Project Phases & Atomic Tasks

**Single source of truth** for the FedTone delivery lifecycle.

This document decomposes the product into **granular, atomic, strictly testable** tasks. It captures the non-negotiable design principles: official-source provenance, evidence-grounded LLM scoring, versioned prompts/schemas, deterministic finance math, point-in-time integrity, provider modularity, human review for uncertainty, and mandatory testing/documentation.

| Meta | Value |
|---|---|
| **Project** | FedTone — LLM-Powered Central Bank Policy Analyzer |
| **Current status** | `[-]` Phase 2 in progress — 2.1, 2.3, 2.5 completed; 2.2, 2.4 await official-page fixtures |
| **Primary MVP** | FOMC statements → tone scoring → statement diff → market reaction → dashboard |
| **Status legend** | `[ ]` Not Started · `[-]` In Progress · `[x]` Completed |
| **Related docs** | [ARCHITECTURE](./ARCHITECTURE.md) · [ROADMAP](./ROADMAP.md) · [PHASES](./PHASES.md) · [DIAGRAMS](./DIAGRAMS.md) · [TASKS](./TASKS.md) · [DEPLOYMENT](./DEPLOYMENT.md) · [RUNBOOK](./RUNBOOK.md) |

### Global Definition of Done (applies to EVERY task)

Before any task may be marked `[x] Completed`:

1. **Automated tests** — unit/integration tests cover task DoD scenarios and pass.
2. **Quality** — format/lint checks pass; type checks pass where configured.
3. **Docs** — relevant `docs/` and `TASKS.md` are synchronized.
4. **Scope** — no unrelated refactors or hidden architecture changes.
5. **Secrets** — no hardcoded credentials/API keys; environment configuration only.
6. **Provenance** — new research outputs preserve source identifiers/hashes where applicable.
7. **Reproducibility** — LLM-related tasks persist model/prompt/schema version metadata.
8. **Evidence** — published LLM claims that require textual support include validated source evidence.
9. **Finance integrity** — numeric finance metrics remain deterministic code, not model arithmetic.
10. **Point-in-time safety** — historical-data tasks include explicit no-look-ahead tests.

---

## Phase 1: Foundation, Data Layer & Research Domain

**Goal:** Establish a lean Python/FastAPI application, research-domain model, SQLite persistence, configuration and test baseline.

**Roadmap:** M1 Alpha foundation

### Task 1.1: Python/FastAPI Modular Project Scaffolding

**Status:** `[x]` Completed

**Business Value / User Story**  
As the FedTone engineering team, we need a clean modular Python project so ingestion, LLM analysis, market analytics and UI can evolve independently without turning the repository into notebook-only research code.

**Technical Specification**

- Python 3.12+ package with `src/fedtone/` layout.
- Modules: `domain`, `application`, `infrastructure`, `api`, `ui`.
- `pyproject.toml`, pytest, Ruff; type checker optional but configured if adopted.
- FastAPI app with minimal health route.
- `.gitignore`, `.env.example`, test fixtures directory.

**Research Integrity / Safety Consideration**  
No API key or provider secret is committed; test configuration uses fakes.

**Definition of Done (DoD)**

- [x] Clean virtual environment installs project.
- [x] API starts locally.
- [x] `pytest` and lint checks pass.
- [x] Architecture/import boundary test or documented convention exists.
- [x] Docs synchronized.

---

### Task 1.2: Configuration, Secrets & Provider Options

**Status:** `[x]` Completed

**Business Value / User Story**  
As a developer, I need validated environment-driven configuration so LLM, central-bank and market-data providers can change without editing source code or leaking credentials.

**Technical Specification**

- Pydantic settings for application, database, LLM, scheduler and market providers.
- Safe defaults for local development.
- `.env.example` placeholder-only values.
- Secret values redacted from logs and error messages.

**Definition of Done (DoD)**

- [x] Missing required production secrets fail with clear validation errors.
- [x] Unit tests cover environment overrides and redaction.
- [x] No real credential in repository history for this task.

---

### Task 1.3: Core Domain Models & Lifecycle State Machine

**Status:** `[x]` Completed

**Business Value / User Story**  
As a researcher, I need every document and processing run to have auditable lifecycle state so failed and published analyses can be reproduced and distinguished.

**Technical Specification**

- `PolicyDocument`, `ProcessingRun`, `ToneAnalysis`, `DocumentComparison`, `ReviewItem`.
- Explicit lifecycle states: Discovered → Fetched → Parsed → Normalized → QueuedForAnalysis → Analyzed → Compared → MarketLinked → Published; review/failure branches.
- Central transition guard.

**Definition of Done (DoD)**

- [x] Allowed/forbidden transition matrix unit tested.
- [x] Published result requires source hash and valid analysis reference.
- [x] Failure retains previous successful stage metadata.

---

### Task 1.4: SQLite / SQLAlchemy / Alembic Persistence

**Status:** `[x]` Completed

**Business Value / User Story**  
FedTone needs a lightweight system of record for source documents, analysis versions and research metrics that can be queried from API and UI.

**Technical Specification**

- SQLAlchemy models and repository interfaces.
- Alembic migrations.
- Unique canonical document identity.
- Indexes on bank/date/type/status.

**Definition of Done (DoD)**

- [x] Fresh database migration succeeds.
- [x] Insert/query/update integration test passes.
- [x] Duplicate canonical record is prevented/idempotently resolved.

---

### Task 1.5: Baseline Observability & Health Endpoints

**Status:** `[x]` Completed

**Business Value / User Story**  
As an operator, I need structured logs and basic health status from the first phase so pipeline debugging does not depend on ad-hoc print statements.

**Technical Specification**

- Structured logging with run/document correlation ids.
- `/healthz`, `/ready`.
- Basic DB readiness check.
- Secret scrubbing utility.

**Definition of Done (DoD)**

- [x] Health API tests pass.
- [x] Correlation fields appear in processing logs.
- [x] Secret-scrubbing test passes.
- [x] Completing Task 1.5 closes Phase 1.

---

## Phase 2: Official FOMC Ingestion & Document Normalization

**Goal:** Build a reproducible corpus of official FOMC statements with immutable raw snapshots and comparable normalized text.

**Roadmap:** M1 Alpha completion

### Task 2.1: Central Bank Source Adapter Abstraction & Registry

**Status:** `[x]` Completed

**Business Value / User Story**  
As the platform, FedTone needs replaceable source adapters so adding ECB/SNB/BoE later does not rewrite the ingestion core.

**Technical Specification**

- `CentralBankSourceAdapter` protocol/interface.
- Discover, fetch, parse-metadata capability contracts.
- Source registry ordered by bank/document type.
- Manual fixture adapter for tests.

**Definition of Done (DoD)**

- [x] Registry resolves adapter by bank/type.
- [x] Unsupported type returns explicit error.
- [x] Fake adapter contract tests pass.

---

### Task 2.2: Official FOMC Statement Discovery & Fetch Adapter

**Status:** `[-]` In Progress — implemented; pending official-page fixture verification

**Business Value / User Story**  
As a macro researcher, I want official FOMC statements automatically collected so the research corpus does not depend on manual copy/paste or news summaries.

**Technical Specification**

- Discover historical/current FOMC statement links from official source pages.
- Fetch with `httpx` and bounded retry/backoff.
- Capture canonical URL, bank, document type, meeting/publication date, fetched timestamp.
- Fixture-driven parser tests; live network not required in CI.

**Definition of Done (DoD)**

- [ ] Curated FOMC fixtures discover/fetch correctly. *(passes on synthetic layout fixtures; official snapshots not yet captured)*
- [x] Transient failure retry boundedly.
- [x] Permanent parsing failure becomes structured error/review record.

---

### Task 2.3: Immutable Raw Snapshot, Hash & Canonical Identity

**Status:** `[x]` Completed

**Business Value / User Story**  
As a researcher, I must be able to prove exactly which source content produced a score.

**Technical Specification**

- Persist raw content snapshot or canonical raw text representation.
- SHA-256 content hash.
- Canonical source id/url.
- `observed_at`, `published_at`, `fetched_at` kept distinct.

**Definition of Done (DoD)**

- [x] Hash deterministic for identical content.
- [x] Reingestion of identical canonical content creates no duplicate document.
- [x] Revised content is versioned or explicitly detected.

---

### Task 2.4: Policy Text Parser & Conservative Normalizer

**Status:** `[-]` In Progress — implemented; pending official-page fixture verification

**Business Value / User Story**  
The LLM and comparator need clean policy text without losing wording that may carry monetary-policy meaning.

**Technical Specification**

- Remove site navigation/footer/boilerplate.
- Preserve paragraph ordering and punctuation.
- Normalize Unicode and whitespace conservatively.
- Persist raw + normalized forms.

**Definition of Done (DoD)**

- [ ] Fixture expected paragraphs preserved. *(passes on synthetic layout fixtures; official snapshots not yet captured)*
- [ ] Boilerplate excluded. *(passes on synthetic layout fixtures; official snapshots not yet captured)*
- [x] Raw input remains recoverable for audit.

---

### Task 2.5: Previous Comparable Statement Linker & Idempotent Ingestion Workflow

**Status:** `[x]` Completed

**Business Value / User Story**  
FedTone's core value is detecting change, so each statement must reliably link to the correct prior comparable communication.

**Technical Specification**

- Previous statement lookup by bank/type/publication order.
- Ingestion service: discover → fetch → snapshot → parse → normalize → link.
- Manual and scheduled triggers share the same idempotent service.

**Definition of Done (DoD)**

- [x] Historical ordering fixtures link correctly.
- [x] Two identical ingestion runs leave one canonical record set.
- [ ] Completing Task 2.5 closes Phase 2 / M1 Alpha. *(Phase 2 closes when Tasks 2.2 and 2.4 are verified on official pages)*

---

## Phase 3: LLM Policy Tone Engine

**Goal:** Produce evidence-grounded hawkish/dovish scores and explainable tone changes.

**Roadmap:** M2 Beta

### Task 3.1: Versioned Policy Tone Analysis Schema

**Status:** `[x]` Completed

**Business Value / User Story**  
As a researcher, I need machine-readable scores rather than free-form chatbot prose.

**Technical Specification**

Required fields:

- `overall_score` in `[-1, +1]`,
- `label` (`strongly_dovish`, `dovish`, `neutral`, `hawkish`, `strongly_hawkish`),
- dimension scores for inflation/labor/growth/policy guidance/balance sheet,
- confidence,
- evidence items with text + dimension,
- rationale,
- uncertainty flags,
- schema version.

**Definition of Done (DoD)**

- [x] Pydantic model accepts valid and rejects out-of-range/malformed values.
- [x] Schema version persisted.

---

### Task 3.2: Structured LLM Analysis Client & Prompt Package

**Status:** `[x]` Completed

**Business Value / User Story**  
As the analysis engine, FedTone needs a provider-independent client that returns validated structured output and keeps prompt/model metadata for reproducibility.

**Technical Specification**

- OpenAI-compatible client interface.
- Low-temperature structured response path.
- Current-statement prompt + current-vs-previous prompt.
- Model/prompt config persisted per run.
- Bounded retry on transient/API parse failures.

**Definition of Done (DoD)**

- [x] Fake client tests pass.
- [x] Valid structured response maps to domain model.
- [x] Model + prompt version stored.
- [x] API key never logged.

---

### Task 3.3: Evidence Validator & Prompt-Injection Guardrails

**Status:** `[x]` Completed

**Business Value / User Story**  
A hawkish/dovish score is only useful if users can inspect the exact wording that supports it.

**Technical Specification**

- Verify evidence text exists in normalized/raw source using exact then controlled fuzzy matching.
- Reject unsupported evidence.
- Source text wrapped as untrusted quoted content.
- Low evidence coverage → retry once or `NeedsReview`.

**Definition of Done (DoD)**

- [x] Real source quote passes.
- [x] Fabricated quote fails.
- [x] Prompt-injection fixture cannot alter system analysis contract.

---

### Task 3.4: Statement Diff & Tone Delta Engine

**Status:** `[x]` Completed

**Business Value / User Story**  
Researchers care more about **what changed** than a standalone score.

**Technical Specification**

- Paragraph/sentence-level deterministic diff.
- Added/removed/changed groups.
- Overall and dimension tone deltas.
- Evidence for major changes.

**Definition of Done (DoD)**

- [x] Known fixture diff detects intended changes.
- [x] Tone deltas computed deterministically from stored scores.
- [x] No previous document produces valid baseline behavior rather than error.

---

### Task 3.5: Evaluation Harness, Confidence Routing & Review Queue

**Status:** `[-]` In Progress *(harness, routing and review queue done; awaits a human-labeled FOMC benchmark and a real-model run)*

**Business Value / User Story**  
The project needs measurable quality, not screenshots of plausible LLM output.

**Technical Specification**

- Labeled FOMC mini benchmark.
- Direction agreement, score drift, schema failure, evidence failure metrics.
- Confidence thresholds.
- `NeedsReview` queue for uncertain/invalid analysis.

**Definition of Done (DoD)**

- [x] Benchmark command produces metrics report. *(verified with a fake LLM on a synthetic dataset; the labeled FOMC benchmark is still to be built)*
- [x] Low-confidence output routes to review.
- [x] Repeated-run stability is measurable.
- [ ] Completing Task 3.5 closes M2 Beta. *(M2 Beta closes after a real-model run on a human-labeled FOMC benchmark)*

---

## Phase 4: Market Reaction & Historical Analogue Engine

**Goal:** Quantify how markets behaved around policy communications without look-ahead bias.

**Roadmap:** M3 Release Candidate

### Task 4.1: Market Data Provider Abstraction & MVP Provider

**Status:** `[x]` Completed

**Business Value / User Story**  
As a researcher, I need market observations connected to policy dates without coupling FedTone to one vendor.

**Technical Specification**

- `MarketDataProvider` interface.
- Daily OHLC/close and/or yield observations.
- Provider metadata and retrieval timestamp.
- Free-first adapter for portfolio/demo use.

**Definition of Done (DoD)**

- [x] Fake provider contract suite passes.
- [x] MVP provider fetches a small fixture/date range. *(FRED CSV provider tested against a mocked HTTP response; a live FRED fetch is still to be checked locally)*
- [x] Missing observation is explicit.

---

### Task 4.2: Trading Calendar & Event Window Engine

**Status:** `[x]` Completed

**Business Value / User Story**  
Announcement dates must map consistently to tradable observations, including weekends/holidays.

**Technical Specification**

- Event timestamp/date normalization.
- Window definitions: T-1, T, T+1, T+5.
- Explicit calendar alignment rules.

**Definition of Done (DoD)**

- [x] Weekend/holiday fixtures produce documented alignment.
- [x] Window boundaries unit tested.

---

### Task 4.3: Deterministic Return & Yield Change Calculator

**Status:** `[x]` Completed

**Business Value / User Story**  
Canonical finance metrics must be numerically correct and reproducible.

**Technical Specification**

- simple/log returns,
- cumulative event-window returns,
- yield basis-point changes,
- optional benchmark-adjusted return,
- metric version metadata.

**Definition of Done (DoD)**

- [x] Hand-calculated fixtures match.
- [x] LLM is not called by metric calculator.
- [x] Unit semantics (percent, decimal, bps) explicit.

---

### Task 4.4: Historical Analogue Search with Point-in-Time Guards

**Status:** `[x]` Completed

**Business Value / User Story**  
Users should see prior meetings with similar policy-tone levels or shifts and the reactions that followed.

**Technical Specification**

- Similarity on overall/dimension scores and tone delta.
- Date cutoff prevents future events from entering a historical query when backtesting.
- Configurable top-k and minimum sample size.

**Definition of Done (DoD)**

- [x] Fixed dataset returns deterministic analogue ranking.
- [x] Explicit test proves future meeting exclusion.

---

### Task 4.5: Research Summary Composer

**Status:** `[x]` Completed

**Business Value / User Story**  
FedTone should explain a meeting in readable macro-finance language while preserving the deterministic source of all numbers.

**Technical Specification**

- LLM receives structured tone + computed market metrics + evidence.
- No new numeric metric may be introduced by model output.
- Language distinguishes association from causality.

**Definition of Done (DoD)**

- [x] Summary references canonical metrics exactly.
- [x] Unsupported number injection test fails validation.
- [ ] Completing Task 4.5 closes M3 RC. *(M3 RC also needs the open Phase 2 and Phase 3 items: official FOMC pages and a real-model benchmark)*

---

## Phase 5: Research API & Streamlit Dashboard

**Goal:** Turn the pipeline into a usable research product.

**Roadmap:** M4 v1.0

### Task 5.1: Core Research REST API

**Status:** `[ ]` Not Started

**Business Value / User Story**  
UI, notebooks and future agents need one stable access layer for FedTone data.

**Technical Specification**

- documents, analyses, comparisons, event metrics, runs, system routes.
- pagination and filters.
- generated OpenAPI docs.

**Definition of Done (DoD)**

- [ ] Core endpoint contract tests pass.
- [ ] Route handlers contain no duplicated analysis logic.

---

### Task 5.2: Policy Tone Timeline Dashboard

**Status:** `[ ]` Not Started

**Business Value / User Story**  
A user should understand the historical direction of Fed communication at a glance.

**Technical Specification**

- Streamlit timeline of overall score.
- dimension selector.
- date/model filters.
- click/select meeting to open detail.

**Definition of Done (DoD)**

- [ ] Chart values equal API values.
- [ ] Missing/review meetings visibly distinguished.

---

### Task 5.3: Meeting Detail, Evidence & Statement Diff View

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Users need to inspect why the score changed, not just see a number.

**Technical Specification**

- current score + dimension cards,
- confidence,
- evidence quotes,
- previous score/delta,
- added/removed/changed language panel,
- source link + audit metadata.

**Definition of Done (DoD)**

- [ ] Selected meeting displays all persisted evidence/deltas correctly.
- [ ] No score shown without source/audit context.

---

### Task 5.4: Market Reaction & Historical Analogue View

**Status:** `[ ]` Not Started

**Business Value / User Story**  
A user should connect communication changes to the market-data context around the event.

**Technical Specification**

- event window table/chart,
- yields in bps and asset returns in clear units,
- analogue ranking with subsequent reactions,
- missing-data warnings.

**Definition of Done (DoD)**

- [ ] Displayed metrics exactly match canonical analytics records.
- [ ] Point-in-time warning shown for historical/backtest mode.

---

### Task 5.5: Audit, Filters & Research Export

**Status:** `[ ]` Not Started

**Business Value / User Story**  
A portfolio project should be reproducible and easy to inspect/export.

**Technical Specification**

- filters by date/document/model/status.
- CSV/JSON signal exports.
- Markdown research-note export.
- source/model/prompt/schema/run audit view.

**Definition of Done (DoD)**

- [ ] Export values equal API/database records.
- [ ] Audit panel shows required reproducibility metadata.
- [ ] Completing Task 5.5 closes v1.0.

---

## Phase 6: Hardening, Dockerization & Production Readiness

**Goal:** Make FedTone reliable enough to run automatically and demonstrate engineering quality.

**Roadmap:** M5 v1.1

### Task 6.1: End-to-End Pipeline Integration Test

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Prove the full research promise: official fixture → parsed document → structured tone → comparison → market metrics → API result.

**Definition of Done (DoD)**

- [ ] Happy path green with fakes/fixtures.
- [ ] Low-confidence LLM path reaches `NeedsReview`.
- [ ] Missing market data produces partial, explicit result.

---

### Task 6.2: Scheduled Ingestion, Retry & Resumable Runs

**Status:** `[ ]` Not Started

**Business Value / User Story**  
New statements should be discovered automatically without duplicate analysis or manual recovery from transient faults.

**Technical Specification**

- APScheduler or approved equivalent.
- idempotent recurring discovery.
- retry policies for HTTP/LLM/provider boundaries.
- resumable `ProcessingRun` stages.

**Definition of Done (DoD)**

- [ ] Repeated schedule run creates no duplicate document.
- [ ] Transient failure can retry/resume safely.

---

### Task 6.3: Observability, Health & Secret Scrubbing

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Operators need fast diagnosis without exposing provider secrets or raw sensitive configuration.

**Technical Specification**

- structured JSON logs,
- run/document correlation ids,
- detailed readiness checks,
- provider latency/error counters,
- secret redaction.

**Definition of Done (DoD)**

- [ ] Health tests cover DB and provider readiness behavior.
- [ ] Log capture tests contain no secret value.

---

### Task 6.4: Docker & GitHub Actions CI/CD

**Status:** `[ ]` Not Started

**Business Value / User Story**  
A clean checkout should build, test and run reproducibly, and releases should be easy to publish.

**Technical Specification**

- Dockerfile.
- Compose for API/UI/database volume.
- CI: install, lint, test, type/security checks.
- CD: optional GHCR image on tag.

**Definition of Done (DoD)**

- [ ] `docker compose up` healthy locally.
- [ ] CI blocks merge on failed tests.
- [ ] No `.env` secret copied into image.

---

### Task 6.5: SQLite Backup, Disaster Recovery & Operational Runbook

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Research history, prompt/model metadata and human review decisions must be recoverable after host/database failure.

**Technical Specification**

- consistent SQLite snapshot.
- retention policy.
- restore instructions and verification.
- production runbook for data source, LLM and scheduler incidents.

**Definition of Done (DoD)**

- [ ] Backup created from live/staged DB.
- [ ] Restore test reproduces known analysis records.
- [ ] Runbook procedures verified.
- [ ] Completing Task 6.5 closes M5 v1.1.

---

## Phase 7: Multi-Bank, Speech Analytics & Agentic Research

**Goal:** Extend a proven FOMC platform into a differentiated central-bank research system.

**Roadmap:** M6 Future

### Task 7.1: ECB, SNB & BoE Official Source Adapters

**Status:** `[ ]` Future

**Definition of Done (DoD)**

- [ ] Each bank uses the shared adapter contract.
- [ ] Fixture suites cover discovery/parser drift.
- [ ] No FOMC pipeline fork.

---

### Task 7.2: Bank-Specific Tone Calibration Profiles

**Status:** `[ ]` Future

**Definition of Done (DoD)**

- [ ] Bank/document-type prompt profile explicit.
- [ ] Common overall scale retained with documented calibration limits.

---

### Task 7.3: Speeches, Minutes & Speaker-Level Tone Analytics

**Status:** `[ ]` Future

**Definition of Done (DoD)**

- [ ] Speaker identity and document type persisted.
- [ ] Speech scores not mixed with statement baseline without explicit filter.

---

### Task 7.4: Agentic Research Workflow with Evidence Reviewer

**Status:** `[ ]` Future

**Technical Specification**

Agents:

- Data Agent,
- Policy Analyst,
- Market Analyst,
- Skeptic / Evidence Reviewer,
- Research Note Composer.

**Definition of Done (DoD)**

- [ ] Final note cannot publish unsupported claim after reviewer gate.
- [ ] Canonical scores/metrics remain from existing services, not agent consensus.

---

### Task 7.5: Historical RAG, Alerts & Optional Intraday Extension

**Status:** `[ ]` Future

**Definition of Done (DoD)**

- [ ] RAG retrieves cited historical FedTone records.
- [ ] Alerts fire only on configurable material-change conditions.
- [ ] Intraday provider preserves timezone/point-in-time rules if enabled.

---

## Cross-Cutting Traceability Matrix

| Concern | Primary tasks |
|---|---|
| Official-source provenance | 2.1–2.5, 5.3, 5.5 |
| Idempotent ingestion | 1.4, 2.3, 2.5, 6.2 |
| Structured LLM output | 3.1–3.2 |
| Evidence grounding | 3.3, 3.5, 5.3, 7.4 |
| Prompt/model/schema versioning | 1.3, 3.1–3.2, 5.5 |
| Statement change detection | 2.5, 3.4 |
| Deterministic finance math | 4.2–4.3, 5.4 |
| Point-in-time integrity | 4.2–4.4, 6.1 |
| Human review | 1.3, 3.3, 3.5, 6.1 |
| API/UI parity | 5.1–5.5 |
| Observability / health | 1.5, 6.2–6.3 |
| Docker / CI/CD | 6.4 |
| Backup / recovery | 6.5 |
| Multi-bank expansion | 7.1–7.3 |
| Agentic research | 7.4–7.5 |

---

## Progress Summary

| Phase | Tasks | Not Started | In Progress | Completed |
|---|---|---:|---:|---:|
| 1 Foundation | 1.1–1.5 | 0 | 0 | 5 |
| 2 FOMC Ingestion | 2.1–2.5 | 0 | 2 | 3 |
| 3 Tone Engine | 3.1–3.5 | 0 | 1 | 4 |
| 4 Market Analytics | 4.1–4.5 | 0 | 0 | 5 |
| 5 Dashboard & API | 5.1–5.5 | 5 | 0 | 0 |
| 6 Production | 6.1–6.5 | 5 | 0 | 0 |
| 7 Expansion | 7.1–7.5 | 5 | 0 | 0 |
| **Total** | **35** | **15** | **3** | **17** |

**Project status:** Phase 1 completed; Phase 2 implemented, awaiting verification against captured official FOMC pages; Phase 3 implemented and verified with a fake LLM, awaiting a human-labeled FOMC benchmark and a real-model run; Phase 4 completed (verified with fake/fixture market data and a fake LLM).

---

## Change Log

| Date | Change |
|---|---|
| 2026-10-13 | Initial FedTone SSOT created with 35 atomic tasks across 7 phases. |
| 2026-10-13 | Task 1.1 completed (modular scaffolding, `/healthz`, pytest/Ruff, import-boundary test). |
| 2026-10-13 | Tasks 1.2–1.5 completed; Phase 1 closed (settings/secrets, domain lifecycle, SQLite/Alembic persistence, JSON logging, `/ready`). |
| 2026-10-13 | Phase 2 implemented: 2.1, 2.3, 2.5 completed; 2.2 and 2.4 in progress pending official-page fixtures (federalreserve.gov unreachable from the build environment). |
| 2026-10-13 | Phase 3 implemented: 3.1–3.4 completed; 3.5 in progress pending a human-labeled FOMC benchmark and a real-model run. |
| 2026-10-13 | Phase 4 completed: market-data providers (FRED CSV, local CSV), trading calendars and event windows, deterministic metrics, point-in-time analogue search, validated research summaries. Phase 3 fix: documents rejected by a reviewer are no longer re-analyzed; provider outages during comparison now resume. |
