# FedTone — Task Progress Ledger

Structured delivery checklist for FedTone. Update this file after every completed task.

## Status Legend

| Marker | Meaning |
|---|---|
| `[ ]` | Pending |
| `[-]` | In Progress |
| `[x]` | Completed |

## Verification Log Format

For each milestone, record:

- **Tests:** what was added/run and pass/fail result.
- **Docs:** which files were updated.
- **Notes:** short verification evidence, commands and important decisions.

---

## Phase 0 — Project Governance & Baseline

Documentation and repository conventions. No production logic yet.

- [x] Create `docs/ARCHITECTURE.md`.
- [x] Create `docs/ROADMAP.md`.
- [x] Create `docs/PHASES.md`.
- [x] Create `docs/PROJECT_PHASES_AND_TASKS.md`.
- [x] Create `docs/TASKS.md`.
- [x] Create `docs/DIAGRAMS.md`.
- [x] Create `docs/DEPLOYMENT.md`.
- [x] Create `docs/RUNBOOK.md`.
- [ ] Create root `README.md` with product overview, screenshots placeholder and local quickstart.
- [ ] Create project engineering rules / contribution conventions.

### Verification — Phase 0

| Item | Detail |
|---|---|
| **Status** | Documentation baseline created. |
| **Tests** | N/A — documentation-only baseline. |
| **Docs** | Eight FedTone project templates created. |
| **Notes** | Implementation remains Not Started. |

---

## Phase 1 — Foundation, Data Layer & Research Domain

- [x] **Task 1.1** Python/FastAPI modular project scaffolding.
- [x] **Task 1.2** Pydantic settings, secret handling and environment templates.
- [x] **Task 1.3** Core domain models and lifecycle rules.
- [x] **Task 1.4** SQLite + SQLAlchemy + Alembic configuration.
- [x] **Task 1.5** Health endpoints, structured logging and baseline tests.

### Verification — Phase 1

| Item | Detail |
|---|---|
| **Status** | `[x]` Completed |
| **Tests** | `pytest`: 192 passed on Python 3.12 in a fresh virtual environment (`pip install -e ".[dev]"`). `ruff check` and `ruff format --check` clean. |
| **Task 1.1** | `/healthz` API test; layer import-boundary test (`tests/unit/test_architecture_boundaries.py`). |
| **Task 1.2** | `tests/unit/test_settings.py`: defaults, env + nested overrides, `.env` file, invalid values, production missing-secret errors, secrets absent from repr/dump/validation errors, redaction. |
| **Task 1.3** | `tests/unit/test_lifecycle.py`: full 9×11 allowed/forbidden transition matrix, publish requires source hash + analysis of the same document version with evidence, failure keeps `last_successful_status`, resume rules. `tests/unit/test_domain_models.py`: entity invariants. |
| **Task 1.4** | `tests/integration/test_migrations.py`: fresh `alembic upgrade head` creates all Phase 1 tables, indexes and canonical unique constraint; ORM ↔ migration diff is empty; downgrade works. `tests/integration/test_repositories.py`: insert/query/update, idempotent duplicate insert, DB-level duplicate rejection, UTC round-trip, list filters. |
| **Task 1.5** | `tests/api/test_health.py`: `/healthz`, `/ready` 200 and 503 (no internal detail leaked). `tests/unit/test_observability.py`: correlation ids in JSON logs, secret scrubbing of messages/args/exceptions and credential patterns. |
| **Manual check** | `alembic upgrade head` on a fresh directory created `data/fedtone.db`; `uvicorn fedtone.api.app:create_default_app --factory` served `/healthz` and `/ready` (200); API key set in env did not appear in logs. |
| **Docs** | README quickstart, ARCHITECTURE (layers, lifecycle guard, persistence), DEPLOYMENT and RUNBOOK config keys, PROJECT_STRUCTURE, PHASES, PROJECT_PHASES_AND_TASKS, this ledger. |
| **Notes** | Canonical identity = (`central_bank`, `document_type`, `source_url`); content revisions/versioning are deferred to Task 2.3. `policy_documents.published_analysis_id` has no DB foreign key (avoids a circular FK SQLite cannot add); the lifecycle guard validates it. |

---

## Phase 2 — Official FOMC Ingestion & Normalization

- [x] **Task 2.1** `CentralBankSourceAdapter` abstraction and registry.
- [-] **Task 2.2** Official FOMC statement discovery/fetch adapter. *(verify on captured official pages)*
- [x] **Task 2.3** Raw snapshot, hash and canonical metadata persistence.
- [-] **Task 2.4** Policy-text parser and conservative normalizer. *(verify on captured official pages)*
- [x] **Task 2.5** Previous comparable statement linker + idempotent ingestion workflow.

### Verification — Phase 2

| Item | Detail |
|---|---|
| **Status** | `[-]` In Progress — all tasks implemented; 2.2/2.4 await official-page fixtures. |
| **Tests** | `pytest`: 252 passed, 2 skipped (live-fixture tests, no captured pages yet) on Python 3.12 in a fresh virtual environment. `ruff check` / `ruff format --check` clean. |
| **Task 2.1** | `tests/unit/test_source_registry.py`: resolve by bank/type, explicit `UnsupportedSourceError`, duplicate registration rejected, ordered listing, adapter contract suite (manual adapter). |
| **Task 2.2** | `tests/unit/test_fomc_adapter.py` (synthetic layout fixtures): calendar discovery excludes PDFs/implementation notes/minutes/duplicates/off-site links; EST and EDT release times → UTC; wrong-page, missing-container and date-mismatch → `PermanentSourceError`. `tests/unit/test_http_fetcher.py`: exponential backoff, bounded retries, 4xx not retried. |
| **Task 2.3** | `tests/integration/test_ingestion.py`: raw bytes + SHA-256 stored; identical reingestion adds nothing; changed text → snapshot v2 + review item, stored text kept; chrome-only change → new snapshot, no revision. Migration `0002` checked against ORM. |
| **Task 2.4** | `tests/unit/test_text_normalization.py`: NFC, NBSP/whitespace collapse, punctuation/quotes preserved, idempotent. Ingestion test checks paragraph order and boilerplate exclusion. |
| **Task 2.5** | `tests/unit/test_linking.py`: publication-order links, strictly-earlier (no look-ahead), backfill relinks, bank/type grouping. Ingestion tests: two runs → one record set; transient failure → `Failed` then resumed and relinked; permanent failure → one review item; post-analysis link change → review item, no rewrite. `tests/integration/test_cli.py`: `fedtone ingest` twice is idempotent. |
| **Open item** | federalreserve.gov was unreachable from the build environment (proxy 403), so parser fixtures are synthetic pages modeled on the site layout. Run `python scripts/capture_fomc_fixtures.py`, then `pytest tests/unit/test_fomc_live_fixtures.py`; if they pass, mark 2.2/2.4 completed and close Phase 2. |
| **Docs** | ARCHITECTURE (Phase 2 implementation, new dependencies), RUNBOOK (commands, failure table, config keys), DIAGRAMS (state machine aligned with the Task 1.3 guard), README, PROJECT_STRUCTURE, PHASES, PROJECT_PHASES_AND_TASKS, this ledger. |
| **Notes** | Documents without a parsed publication time are not linked and get a review item. A document whose fetch failed is skipped as a link anchor until it succeeds; affected successors are relinked automatically while not yet analyzed. |

---

## Phase 3 — LLM Policy Tone Engine

- [x] **Task 3.1** Versioned hawkish/dovish analysis schema.
- [x] **Task 3.2** OpenAI-compatible LLM client with structured outputs.
- [x] **Task 3.3** Evidence validator + prompt-injection guardrails.
- [x] **Task 3.4** Deterministic statement diff + tone delta engine.
- [-] **Task 3.5** Evaluation harness, confidence routing and review state. *(awaits a human-labeled FOMC benchmark and a real-model run)*

### Verification — Phase 3

| Item | Detail |
|---|---|
| **Status** | `[-]` In Progress — all tasks implemented and tested with a fake LLM; M2 Beta closes after a real-model benchmark. |
| **Tests** | `pytest`: 348 passed, 2 skipped (Phase 2 live-fixture tests) on Python 3.12 in a fresh virtual environment. `ruff check` / `ruff format --check` clean. No test calls a real LLM. |
| **Task 3.1** | `tests/unit/test_tone_schema.py`: valid JSON accepted; out-of-range, malformed, unknown-field, duplicate-dimension and label/score mismatches rejected; label bands; comparison quote rules per change kind. Schema versions `tone-analysis-1.0` / `tone-comparison-1.0` persisted (migration `0003`, `tests/integration/test_migrations.py`). |
| **Task 3.2** | `tests/unit/test_llm_client.py` (mock transport): structured request/response, bounded retry with backoff on 408/409/425/429/5xx and transport errors, other 4xx permanent, unexpected shape transient, API key never in errors or logs. `tests/integration/test_analysis_pipeline.py`: valid output maps to `ToneAnalysis` with model, prompt version (`tone-statement-v1`, `tone-comparison-v1`), schema version and temperature stored; malformed output retried once. |
| **Task 3.3** | `tests/unit/test_evidence.py`: real quotes match exactly; punctuation-only differences match (≥ 4 words); partial words, altered, fabricated and negation-flipped quotes fail; material dimensions (abs score ≥ 0.2) need supported evidence; comparison quotes checked against the correct document. `tests/unit/test_prompts.py`: source wrapped once in delimiters that the document cannot close; system contract independent of document text; instruction-like text detected. Pipeline test: an injection fixture cannot publish a score. |
| **Task 3.4** | `tests/unit/test_diff.py`: abbreviation-aware sentence split; known fixture diff finds the intended added/removed/changed sentences; identical statements → no changes; deterministic and serializable; deltas from stored scores; no previous document → baseline (no delta), not an error. Pipeline: comparison LLM not called when wording is unchanged. |
| **Task 3.5** | `tests/unit/test_evaluation.py`: direction agreement, repeat-run score drift, schema and evidence failure counts, JSON + Markdown report. `tests/integration/test_analysis_pipeline.py`: low confidence stored but routed to `NeedsReview` and blocks the successor's comparison; invalid output twice → review, nothing published; provider outage → `Failed`, next run resumes; permanent provider error aborts the run without touching documents. `tests/integration/test_review_service.py`: resolve with resume/reject; document waits for all its review items. `tests/integration/test_cli.py`: `analyze` (missing config explained), `benchmark` writes reports. |
| **Eval** | `fedtone benchmark` verified with a fake LLM on `tests/fixtures/eval/mini_benchmark.json` (synthetic, not real FOMC text). Open item: build a human-labeled FOMC dataset and run it against a real model (`FEDTONE_LLM__API_KEY`, `FEDTONE_LLM__MODEL`) to record the baseline. |
| **Docs** | ARCHITECTURE (Phase 3 implementation), RUNBOOK (`analyze`, `resolve`, `benchmark`, new config keys), README, PROJECT_STRUCTURE, PHASES, PROJECT_PHASES_AND_TASKS, this ledger. |
| **Notes** | The LLM never calculates deltas or metrics; diffs, deltas and benchmark statistics are deterministic code. Evidence uses exact or punctuation-insensitive word matching only; ratio-based fuzzy matching was dropped because it accepted negation flips. |

---

## Phase 4 — Market Reaction & Historical Analogues

- [x] **Task 4.1** Market data provider abstraction + MVP provider. *(live FRED fetch still to be checked locally)*
- [x] **Task 4.2** Trading calendar & event window engine.
- [x] **Task 4.3** Deterministic return & yield change calculator.
- [x] **Task 4.4** Historical analogue search with point-in-time guards.
- [x] **Task 4.5** Research summary composer.

### Verification — Phase 4

| Item | Detail |
|---|---|
| **Status** | `[x]` Completed — verified with fake/fixture market data and a fake LLM. M3 RC also waits on the open Phase 2/3 items. |
| **Tests** | `pytest`: 455 passed, 2 skipped (Phase 2 live-fixture tests) on Python 3.12 in a fresh virtual environment. `ruff check` / `ruff format --check` clean. No test calls the network or a real LLM. |
| **Task 4.1** | `tests/unit/test_market_providers.py`: one contract suite run against the fake, FRED CSV (mocked HTTP) and local CSV providers: sorted, in range, aware timestamps, listed-but-empty dates kept as `None`; `DATE` and `observation_date` headers; unknown series, HTML, bad values, bad dates and duplicate dates are permanent errors; 5xx retried then transient. |
| **Task 4.2** | `tests/unit/test_trading_calendar.py`: Easter/Good Friday, NYSE 2024 holidays (252 sessions), weekend observance rules, Juneteenth from 2022, one-off closures, Treasury calendar adds Columbus/Veterans Day, early closes; alignment before/at/after the observation time, weekend, holiday, early close, daylight saving; `[0,0]`, `[0,+1]`, `[0,+5]` and `[-1,+1]` boundaries across Good Friday. |
| **Task 4.3** | `tests/unit/test_metrics.py`: hand-calculated simple/log/cumulative/excess returns and bps changes; missing dates never filled; windows not yet closed at `as_of` are reported and later observations are invisible; benchmark on a different calendar is not mixed; metric modules import no LLM code. |
| **Task 4.4** | `tests/unit/test_analogues.py`: RMS distance by hand, deterministic ranking independent of input order, future meeting excluded, earlier backtest cutoff, tie-breaks, insufficient-history flag. `tests/integration/test_analogue_search.py`: later meetings never returned; reactions whose window ended after the cutoff are withheld. |
| **Task 4.5** | `tests/unit/test_summary_validation.py`: copied values pass; invented, rounded, sign-flipped, recomputed and filled-in numbers fail; causal wording fails. `tests/integration/test_research_summary.py`: stored summary contains the canonical display strings; injected number → retry → review, nothing stored; outage → Failed → resumed; reviewer rejection is final. |
| **Integration** | `tests/integration/test_market_reaction.py`: fake provider → metrics persisted for each meeting; recent meeting waits until its windows close; `--as-of` backtest never fetches later prices; missing data stored explicitly; outage keeps documents `Compared`; permanent error aborts. `tests/integration/test_cli.py`: `reactions`, `documents`, `analogues`, `summarize` end to end on CSV data. |
| **Open item** | fred.stlouisfed.org was unreachable from the build environment (proxy 403), so the FRED provider is tested against its documented CSV layout. Run `fedtone reactions` with `FEDTONE_MARKET_DATA__PROVIDER=fred` once locally to confirm. |
| **Docs** | ARCHITECTURE (Phase 4 implementation), RUNBOOK (commands, config keys), README, PROJECT_STRUCTURE, PHASES, PROJECT_PHASES_AND_TASKS, this ledger. |
| **Notes** | Default instruments are FRED series SP500, DGS2, DGS10 and DTWEXBGS. FRED serves latest values, not vintages; daily closes are rarely revised. Phase 3 fix found while wiring Phase 4: a document a reviewer rejected was re-analyzed on the next run; now only provider outages are resumed, including outages during comparison. |

---

## Phase 5 — Research API & Streamlit Dashboard

- [ ] **Task 5.1** Core research REST API.
- [ ] **Task 5.2** Policy Tone Timeline dashboard.
- [ ] **Task 5.3** Meeting detail + evidence + statement diff view.
- [ ] **Task 5.4** Market reaction + historical analogue view.
- [ ] **Task 5.5** Audit metadata, filters and CSV/JSON/Markdown export.

### Verification — Phase 5

| Item | Detail |
|---|---|
| **Status** | `[ ]` Not Started |
| **Tests** | API contract tests + UI smoke where practical. |
| **Docs** | API routes, screenshots and quickstart. |
| **Notes** | M4 v1.0 closes here. |

---

## Phase 6 — Hardening, Deployment & Operations

- [ ] **Task 6.1** End-to-end pipeline test suite.
- [ ] **Task 6.2** Scheduled ingestion, retries and resumable processing runs.
- [ ] **Task 6.3** Observability, health/readiness and secret scrubbing.
- [ ] **Task 6.4** Docker + GitHub Actions CI/CD.
- [ ] **Task 6.5** SQLite backup/restore + operational runbook verification.

### Verification — Phase 6

| Item | Detail |
|---|---|
| **Status** | `[ ]` Not Started |
| **Tests** | E2E, scheduler idempotency, backup/restore. |
| **Deployment** | Docker Compose healthy; CI green. |
| **Notes** | M5 v1.1 closes here. |

---

## Phase 7 — Multi-Bank & Agentic Research Expansion

- [ ] **Task 7.1** ECB/SNB/BoE source adapters.
- [ ] **Task 7.2** Bank-specific scoring profiles and calibration.
- [ ] **Task 7.3** Speeches/minutes + speaker-level tone analytics.
- [ ] **Task 7.4** Agentic research workflow with skeptic/evidence reviewer.
- [ ] **Task 7.5** RAG historical memory, alerts and optional intraday extension.

### Verification — Phase 7

| Item | Detail |
|---|---|
| **Status** | `[ ]` Future |
| **Tests** | Cross-bank regression, evidence support and agent-review gates. |
| **Notes** | Must not regress FOMC v1 behavior. |

---

## Cross-Cutting Research Integrity Checklist

- [ ] Every canonical document has source URL + content hash.
- [x] Every published LLM analysis has model + prompt + schema versions.
- [x] Every material score has source evidence.
- [x] Invalid/hallucinated evidence blocks publication.
- [x] Returns/yields/statistics come from deterministic tested code.
- [x] Historical tests enforce point-in-time data boundaries.
- [x] Missing data is explicit.
- [ ] No secret/API key is committed or logged.
- [ ] No automated trade execution is introduced without a separate approved scope.

---

## Change Log

| Date | Change |
|---|---|
| 2026-10-13 | Initial FedTone task ledger created from project planning templates; implementation status set to Not Started. |
| 2026-10-13 | Task 1.1 completed: `src/fedtone` layered package, FastAPI `/healthz`, pytest + Ruff, import-boundary test, `.gitignore`, `.env.example`. |
| 2026-10-13 | Tasks 1.2–1.5 completed; Phase 1 closed. |
| 2026-10-13 | Phase 2 implemented (2.1, 2.3, 2.5 completed; 2.2, 2.4 pending official-page fixtures); DIAGRAMS state machine synced with Task 1.3 guard. |
| 2026-10-13 | Phase 3 implemented (3.1–3.4 completed; 3.5 pending a human-labeled FOMC benchmark and a real-model run). |
| 2026-10-13 | Phase 4 completed (4.1–4.5); Phase 3 review-rejection and comparison-outage resume fixes. |
