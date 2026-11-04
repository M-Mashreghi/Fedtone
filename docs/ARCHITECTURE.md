# FedTone Architecture

**FedTone** is an LLM-powered monetary-policy intelligence and market-reaction research platform. It ingests official central-bank communications, produces evidence-grounded hawkish/dovish scores, compares language across meetings, and connects policy-tone shifts to deterministic market-reaction analytics.

This document defines the end-to-end system architecture. Implementation work should preserve these boundaries unless an explicit architecture decision changes them.

---

## High-Level Overview

```text
┌─────────────────────────┐
│ Streamlit Research UI   │
│ timeline / compare /    │
│ event-study dashboard   │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ FastAPI Core API        │
│ queries + orchestration │
└────────────┬────────────┘
             │
   ┌─────────┼───────────┬─────────────────┐
   ▼         ▼           ▼                 ▼
 SQLite   Ingestion   Tone Engine     Market Analytics
 /SQLA    Pipeline     (LLM + rules)    (deterministic)
   │         │           │                 │
   │         ▼           ▼                 ▼
   │   Official CB    Structured       Market-data
   │   sources        JSON output      providers
   │
   └──────────────► Audit / provenance store
```

---

## 1. Core Research Object & Lifecycle

Every central-bank communication is represented as a **PolicyDocument** with immutable source metadata, normalized text, analysis artifacts, model/prompt version, and optional market-event linkage.

### Document Lifecycle

| State | Meaning |
|---|---|
| `Discovered` | A supported official source exposed a new document. |
| `Fetched` | Raw HTML/PDF/text was retrieved and fingerprinted. |
| `Parsed` | Main policy text and metadata were extracted. |
| `Normalized` | Boilerplate removed; sections standardized; text ready for comparison. |
| `QueuedForAnalysis` | Document is waiting for the tone engine. |
| `Analyzed` | Structured LLM result validated and persisted. |
| `Compared` | Tone delta and textual change set computed against the prior comparable document. |
| `MarketLinked` | Optional market window and event metrics are attached. |
| `Published` | Results are visible in the research dashboard/API. |
| `NeedsReview` | Parsing, provenance, or LLM confidence is insufficient for automatic publication. |
| `Failed` | Processing stopped with structured failure metadata. |

### Rules

- Source content is immutable after ingestion; reparsing creates a new processing version, not silent mutation.
- A tone score is never stored without **evidence spans** and the model/prompt version that produced it.
- Financial calculations are deterministic Python functions; the LLM may explain them but must not calculate canonical metrics.
- Historical analysis must preserve **point-in-time integrity** and avoid look-ahead leakage.
- A failed or low-confidence LLM call never invents a score; it routes to `NeedsReview` or retry policy.

---

## 2. Official-Source Ingestion Layer

FedTone is **official-source first**. News articles may be future enrichment, but primary policy analysis should come from central-bank publications.

### Source Adapter Contract

`CentralBankSourceAdapter` implementations expose:

- document discovery,
- metadata extraction,
- canonical source URL,
- publication timestamp,
- document type,
- raw content fetch,
- stable source identifier where available.

Initial adapters:

1. **Federal Reserve / FOMC statement adapter** — MVP priority.
2. Federal Reserve speech/minutes adapter — later phase.
3. ECB adapter — multi-bank expansion.
4. SNB adapter — multi-bank expansion.
5. Bank of England adapter — multi-bank expansion.
6. Manual text/URL ingestion — development and evaluation support.

### Implementation (Phase 2)

- Contract and registry: `fedtone.application.sources` (`CentralBankSourceAdapter`,
  `SourceRegistry`, `TransientSourceError` / `PermanentSourceError`).
- Adapters: `fedtone.infrastructure.sources.fomc` (federalreserve.gov statements; discovery from
  the FOMC calendar, links `/newsevents/pressreleases/monetaryYYYYMMDDa.htm`, release time
  converted from America/New_York to UTC) and `...sources.manual` (plain text for development
  and evaluation sets). HTTP retry lives in `...sources.http.HttpFetcher`.
- Workflow: `fedtone.application.ingestion.IngestionService` — the single idempotent service
  used by `fedtone ingest` and, later, the scheduler.
- Storage: `document_snapshots` (raw bytes, `raw_hash`, version per distinct content) and
  `document_texts` (normalized paragraphs, `text_hash`, parser/normalizer versions). A
  document's `content_hash` is the SHA-256 of its normalized text, so page-chrome changes do
  not count as revisions. Timestamps: `discovered_at` (observed), `published_at` (official
  release time), `fetched_at` (retrieval) are kept separately.
- Linking: `fedtone.domain.linking.previous_comparable_links` picks the latest document of the
  same bank and type published strictly earlier. Links may change only before analysis;
  afterwards a change opens a review item instead.

New dependencies: `httpx` (HTTP client with transport mocking for tests), `beautifulsoup4`
(HTML parsing with the standard-library parser, no lxml needed), `tzdata` on Windows only
(time-zone database for `zoneinfo`).

### Implementation (Phase 7, Task 7.1)

More official sources use the same contract, ingestion service, linking and analysis:

| Bank / document type | Adapter | Discovery | Content | Release time (UTC-converted) |
|---|---|---|---|---|
| `ECB/monetary_policy_decision` | `sources.ecb.EcbDecisionAdapter` | yearly decision lists (`/press/govcdec/mopo/YYYY/html/index_include.en.html`, current + previous year) | press-release HTML (`div.section`) | 14:15 Frankfurt (13:45 before 2022) |
| `BOE/monetary_policy_summary` | `sources.boe.BoeSummaryAdapter` | news RSS feed | Monetary Policy Summary section of the summary-and-minutes page | 12:00 London |
| `SNB/monetary_policy_assessment` | `sources.snb.SnbAssessmentAdapter` | monetary policy decisions page | PDF linked from the press-release page; paragraphs rebuilt from wrapped lines | 09:30 Zurich |

Release times are the banks' published schedules (not stated on the pages); they only
matter for event alignment, and all fall before the European and US closes. The canonical
identity of an SNB document is its press-release page URL, while its snapshot stores the PDF
bytes. `FEDTONE_SOURCES__ENABLED` lists the `BANK/type` pairs the scheduler cycles over
(default: FOMC only). Shared helpers live in `sources.common`.

Limitation: market reactions use the configured instruments (US by default) for every bank.

### Implementation (Phase 7, Task 7.2) — Tone Profiles and Calibration Limits

`fedtone.application.tone_profiles` holds one explicit profile per bank and document type
(`fed-fomc-statement-v1`, `ecb-mp-decision-v1`, `boe-mps-v1`, `snb-mpa-v1`) plus
`generic-v1` for any other source. A profile adds institutional context to the system prompt:
institution, document, policy instrument, mandate/target, how the bank's concepts map to the
five shared dimensions (e.g. SNB FX interventions → `balance_sheet`; ECB wage growth →
`labor` only as an inflation driver) and reading notes (vote splits, standard guidance
phrases). It never changes the output schema, the -1..+1 scale or the label bands, and all
validators are unchanged. `GET /api/v1/system/tone-profiles` lists them.

The stored prompt version is `<shared prompt>+<profile id>`
(e.g. `tone-statement-v2+ecb-mp-decision-v1`; the signal record exposes `tone_profile`).
Because deltas, comparisons and analogue search only combine analyses with identical
model/prompt/schema versions, scores from different profiles are never mixed.

**Calibration limits.** Scores are calibrated within one bank and document type: a +0.3 from
the ECB and a +0.3 from the Fed each mean "moderately hawkish for that institution", not the
same stance. Cross-bank level comparisons are descriptive only; the dashboard draws one line
per bank and warns when a view mixes profiles. Profiles are prompt context, not a statistical
calibration; a per-bank labeled benchmark (as for the FOMC, Task 3.5) is needed before scores
are used quantitatively. Editing a profile requires bumping its version suffix.

New dependency: `pypdf` (pure Python) to read SNB assessment PDFs.

### Implementation (Phase 7, Task 7.3) — Speeches, Minutes and Speakers

New sources on the same contract: `FED/speech` (`sources.fed_speeches`, discovery from the
speeches RSS feed), `FED/fomc_minutes` (`sources.fomc_minutes`, discovery from the FOMC
calendar) and `BOE/mpc_minutes` (`sources.boe.BoeMinutesAdapter`, the minutes section of the
summary page). `PolicyDocument.speaker` (migration `0005`) holds the speaker's name with the
office stripped, so one person keeps one name across roles. Previous-document linking groups
by bank, type **and speaker**: a speech is compared with the same person's previous speech.

Point-in-time: a speech's page states only its date, so `published_at` is 23:59 New York on
that day (never earlier than the speech). Minutes are published three weeks after the policy
decision at 14:00 ET; discovery skips minutes whose release time is still in the future.

Speeches never enter committee views by default: `/documents`, `/timeline` and exports
exclude `speech` unless `document_type=speech`, `speaker=...` or `include_speeches=true` is
given. `GET /api/v1/speakers` lists speech counts, first/last dates, latest score and the
plain mean of stored scores per speaker. Speeches and minutes have their own tone profiles
(`fed-speech-v1`, `fed-fomc-minutes-v1`, `boe-mpc-minutes-v1`), so their scores form separate
baselines.

### Implementation (Phase 7, Task 7.4) — Agentic Research Notes

`fedtone.application.agentic_research` runs five roles in a fixed order (no agent framework):

| Agent | Kind | Output |
|---|---|---|
| Data Agent | deterministic | facts with ids and canonical display strings (`build_facts`) from stored analysis, comparison and metrics |
| Policy Analyst | LLM | up to 4 claims about the policy language, each citing fact ids |
| Market Analyst | LLM (market facts only) | up to 4 claims about the observed reaction |
| Skeptic / Evidence Reviewer | deterministic gate + LLM verdict | `check_claim`: known cited ids, market claims cite market facts, every number copied from a *cited* fact (sign included), quotes verbatim, no causal wording; then a reviewer verdict per surviving claim |
| Research Note Composer | deterministic | Markdown from accepted claims only, each with its citations, plus a table of the cited facts' canonical values |

A claim is published only if the gate finds nothing and the reviewer marks it supported (a
missing verdict counts as unsupported). No accepted claim → nothing stored, review item
opened. Rejected claims are stored with their reasons for audit (`agent_research_notes`,
migration `0006`). Scores and metrics are never produced by agents. Entry points:
`fedtone agent-notes`, optional pipeline stage `agent_notes` (not in the default cycle),
`POST`/`GET /api/v1/documents/{id}/agent-note`. Prompt version `agent-research-v1`, schema
`agent-claims-1.0`.

### Implementation (Phase 7, Task 7.5) — Historical Search, Alerts, Intraday

- **Historical retrieval (RAG)** — `fedtone.application.historical_search`: BM25 over the
  paragraphs of each document's latest normalized text, no embeddings or vector store.
  Every hit is a citation (`<document_id>:<paragraph>`, exact text, source URL) with the
  stored tone record. `as_of` limits the corpus to documents published by then.
  `POST /api/v1/ask` sends only retrieved passages to the LLM; the answer is returned only
  if every cited passage exists, every quote is verbatim from it, every number appears in a
  cited passage and there is no causal wording — otherwise it is refused (`answer: null`).
- **Alerts** — `fedtone.domain.alerts` (deterministic rules: overall delta, label change,
  dimension delta, confidence floor; `FEDTONE_ALERTS__*`) and the `alerts` stage
  (`fedtone.application.alerting`, after `analyze` in the cycle). Each (document, rule) fires
  once (`alerts` table, migration `0007`); notifications go to the log and optionally a
  webhook (`FEDTONE_ALERTS__WEBHOOK_URL`, treated as a secret). A failing webhook is counted
  and does not stop the stage. `GET /api/v1/alerts`, `fedtone alerts`.
- **Intraday (optional, off by default)** — `fedtone.domain.intraday` computes `[-b,+a]`
  minute windows from timezone-aware observations in UTC; observations after `as_of` are
  never read and windows not yet closed are reported as such. `IntradayCsvProvider` rejects
  naive timestamps and duplicates. Enabled by `FEDTONE_MARKET_DATA__INTRADAY_DIRECTORY`;
  `fedtone intraday DOCUMENT_ID --symbol S`. Results are computed on demand, not stored.

### Ingestion Guarantees

- Canonical URLs and content hashes prevent duplicate ingestion.
- Raw snapshots are retained for reproducibility.
- Network failures use bounded retries with exponential backoff.
- Parsing failures preserve source metadata and move the item to review rather than dropping it silently.

---

## 3. Document Normalization & Comparison Engine

The normalization layer converts heterogeneous policy documents into comparable research objects.

### Processing Stages

1. Remove navigation, footer, legal and repeated boilerplate.
2. Preserve paragraphs and section boundaries.
3. Normalize Unicode/whitespace without altering semantic wording.
4. Detect policy-relevant sections such as inflation, labor, activity, policy guidance, and balance-sheet language.
5. Link each document to its **previous comparable communication**.
6. Generate a sentence/paragraph-level diff.

### Comparison Output

The comparator stores:

- added language,
- removed language,
- changed phrases,
- semantic similarity,
- section-level tone deltas,
- overall hawkish/dovish delta.

The comparison itself is deterministic where possible. LLM interpretation is layered on top and must cite the changed text.

---

## 4. LLM Policy Tone Engine

FedTone uses the LLM as a structured research classifier/explainer, not as an unconstrained chat model.

### Score Convention

- **+1.00** = strongly hawkish
- **0.00** = neutral / balanced
- **−1.00** = strongly dovish

### Core Dimensions

| Dimension | What the engine evaluates |
|---|---|
| `inflation` | Concern about inflation persistence, upside risk, price pressure. |
| `labor` | Tightness/weakness, unemployment risk, labor rebalancing. |
| `growth` | Strength/weakness of economic activity. |
| `policy_guidance` | Tightening/easing bias, rate-path language, patience/urgency. |
| `balance_sheet` | QT/QE and balance-sheet stance when relevant. |
| `overall` | Calibrated overall hawkish/dovish policy tone. |

### Required Structured Output

Every analysis must return schema-valid JSON containing:

- overall score,
- dimension scores,
- classification label,
- confidence,
- evidence quotes/spans,
- concise rationale,
- uncertainty flags,
- model identifier,
- prompt version.

### Guardrails

- Evidence must come from the analyzed document or explicit previous-document diff.
- Prompt injection inside source text is treated as untrusted content.
- Model output is schema validated with Pydantic.
- Parse failure or missing evidence triggers retry once, then `NeedsReview`.
- LLM-generated investment advice is outside FedTone's core scope.

### Implementation (Phase 3)

- `fedtone.domain.tone_schema` — versioned LLM output contracts (`tone-analysis-1.0`,
  `tone-comparison-1.0`). Unknown fields are rejected; the label must match the score band
  (< -0.6 strongly dovish, < -0.2 dovish, <= 0.2 neutral, < 0.6 hawkish, else strongly hawkish).
- `fedtone.domain.evidence` — every quote must appear in the source on word boundaries, either
  exactly or ignoring only punctuation/case (at least 4 words). Ratio-based fuzzy matching is
  not used because it accepts negation flips. Any unsupported quote rejects the output; every
  material dimension (abs score >= 0.2) needs at least one supported quote.
- `fedtone.domain.diff` — deterministic sentence diff (added/removed/changed) and tone deltas
  computed from stored scores. No previous document means a baseline, not an error.
- `fedtone.application.prompts` — versioned prompts (`tone-statement-v2`, `tone-comparison-v2`;
  v1 had no profile section).
  Source text is wrapped once in delimiters it cannot close; instruction-like text is flagged
  and blocks publication.
- `fedtone.application.tone_analysis` — `AnalysisService`: analyze queued documents, retry
  invalid output once, then route to `NeedsReview`; low confidence (below
  `FEDTONE_LLM__MIN_CONFIDENCE`) is stored but routed to review. The comparison step runs only
  once the previous document has an accepted analysis, and calls the LLM only when wording
  changed.
- `fedtone.application.evaluation` — benchmark of direction agreement, repeat-run score drift,
  schema and evidence failure rates.
- `fedtone.application.review` — resolve review items and resume at the last good stage, or
  reject.
- `fedtone.infrastructure.llm.openai_compatible` — httpx client for any OpenAI-compatible
  `/chat/completions` endpoint with JSON-schema responses and bounded retry; the API key is
  never included in errors or logs.

---

## 5. Market-Reaction Analytics

Market analytics are optional enrichment layered after policy analysis.

### MVP Assets / Proxies

A configurable set may include:

- broad U.S. equities (e.g. S&P 500 proxy),
- U.S. dollar proxy,
- gold proxy,
- 2-year Treasury yield,
- 10-year Treasury yield.

### Event Windows

MVP uses daily data and configurable windows such as:

- `T-1 → T+1`,
- `T → T+1`,
- `T → T+5`.

A later provider may add intraday windows around announcement timestamps.

### Deterministic Metrics

- raw return,
- basis-point yield change,
- volatility around event,
- optional market-adjusted / benchmark-adjusted return,
- historical percentile,
- historical analogue summary.

### Research Constraint

FedTone reports **association and historical reaction**, not causal proof. The LLM can summarize the metrics, but the numeric result must come from tested analytics code.

### Implementation (Phase 4)

- `fedtone.application.market_data.MarketDataProvider` — fetches raw daily observations only.
  Adapters: `FredCsvProvider` (public FRED CSV download, no key) and `CsvFileProvider` (local
  files in the same layout). A date listed without a value is kept as `None`; nothing is filled.
- `fedtone.domain.calendar` — rule-based `nyse` and `us_treasury` calendars and event alignment.
  Each instrument has a New York observation time (SP500 16:00, Treasury yields 15:30 per
  H.15, dollar index 12:00 noon rates). The event session T is the first session whose
  observation is fixed after the release, so close(T-1) never contains the announcement.
- `fedtone.domain.metrics` (`market-metrics-1.0`) — windows in return days, `[a,b]` = observation
  of T+a-1 to T+b (`[0,0]`, `[0,+1]`, `[0,+5]` by default). Prices give simple and log returns
  (decimal), yields give bps; optional excess return versus a benchmark on the same calendar.
  Each metric stores its endpoints, dates, alignment rule, provider, `as_of` and the time its
  end observation was fixed. A window not closed at `as_of` is never computed.
- `fedtone.application.market_reaction` — `Compared` → `MarketLinked` once every window has
  closed; observations are stored append-only (a changed value adds a row).
- `fedtone.domain.analogues` / `fedtone.application.analogue_search` — RMS distance over
  overall, dimension and delta features; only meetings published before the cutoff, only
  analyses from the same model/prompt/schema, and only analogue reactions fixed before the
  cutoff. Limitation: historical tone scores come from the current model, not one available
  at the time.
- `fedtone.domain.summary` / `fedtone.application.research_summary` (`research-summary-v1`,
  `research-summary-1.0`) — the LLM sees facts with exact display strings; every number in its
  text must be copied from a fact (sign included) and causal wording is rejected. Invalid
  output is retried once, then routed to review.

---

## 6. Application & API Layer

A single **FastAPI** application exposes research data and orchestration commands.

API groups (OpenAPI tags): `documents`, `analysis`, `comparisons`, `market-reactions`,
`exports` and `system`, all under `/api/v1` (see Implementation (Phase 5) below).

Business logic lives in services/modules, not route handlers.

### UI

The initial UI is **Streamlit** for fast iteration and portfolio presentation.

Primary screens:

1. Policy Tone Timeline
2. Meeting Detail
3. Statement Diff
4. Dimension Scores
5. Market Reaction
6. Historical Analogues
7. Analysis Audit / Evidence

A React/Next.js UI can replace Streamlit later without changing domain contracts.

### Implementation (Phase 5)

- **Read model** — `fedtone.application.research_queries.ResearchQueryService` assembles
  stored records (document, latest analysis, comparison, metrics, summary, open reviews,
  snapshot/text) and never recalculates them. `signal_record()` is the single flat record
  behind `/documents`, `/timeline` and `/exports/signals`, so a chart, a list and an export
  cannot disagree. `metric_record()` adds the canonical `metric_display()` text used by
  research summaries and flags `backtest_mode` when `as_of` is more than one hour before
  `computed_at` (an explicit `--as-of` cut-off).
- **Exports** — `fedtone.application.research_export`: CSV/JSON of signal records at full
  precision and a Markdown research note built from the same stored records.
- **Pipeline runs** — `fedtone.application.pipeline.PipelineService` dispatches
  `ingest | analyze | reactions | summarize | publish` to runners built in
  `fedtone.infrastructure.pipeline` from
  the same services and adapters as the CLI (`fedtone.infrastructure.factories`). A stage
  whose provider is not configured returns 409; an aborted run returns 502. Retrying is the
  normal behavior of each stage (provider failures resume at the last good stage); review
  items are resolved with `POST /reviews/{id}/resolve`.
- **Routes** (`fedtone.api.routes`, schemas in `fedtone.api.schemas`), all under `/api/v1`:
  `GET /documents`, `GET /documents/{id}` and its `/analysis`, `/comparison`,
  `/market-reaction`, `/analogues`, `/audit`, `/research-note`; `GET /timeline`;
  `GET /exports/signals?format=csv|json`; `POST /pipeline/{stage}`; `GET /runs`;
  `GET /reviews`, `POST /reviews/{id}/resolve`; `GET /system/telemetry`. Filters:
  `central_bank`, `document_type`, `status`, `model`, `published_from`, `published_to`
  (timezone-aware). OpenAPI at `/docs`.
- **Dashboard** — `fedtone.ui.dashboard` (Streamlit) reads only the API through
  `fedtone.ui.client` (httpx), so it imports no infrastructure and shows exactly the API's
  numbers. `fedtone.ui.views` arranges payloads into tables and Plotly figures without
  computing anything; market values carry the API's display text and units (yields in bps,
  returns in %). Meetings in review or without a score are drawn as separate markers;
  analogues always carry a point-in-time note, backtest metrics a warning.

---

## 7. Data Layer

### SQLite + SQLAlchemy

SQLite is the MVP database. Tables should cover:

- `policy_documents`,
- `document_versions`,
- `tone_analyses`,
- `tone_dimensions`,
- `document_comparisons`,
- `market_events`,
- `market_observations`,
- `processing_runs`,
- `review_items`.

PostgreSQL is a later scaling option; no core business logic may depend on SQLite-specific behavior.

### Reproducibility Metadata

For every model run persist:

- input document hash,
- previous-document hash where applicable,
- model name,
- prompt version,
- analysis schema version,
- temperature/config,
- created timestamp,
- code/app version if available.

---

## 8. Workflow & Background Processing

MVP should remain lightweight:

- FastAPI command endpoint for manual runs,
- scheduled ingestion via APScheduler or equivalent,
- idempotent pipeline service,
- retry policy around network/LLM boundaries.

### Implementation (Phase 6)

- **Stages** — `ingest → analyze → reactions → summarize → publish`
  (`fedtone.application.pipeline.CYCLE`). `PublicationService` publishes `MarketLinked`
  documents that have a validated research summary; the lifecycle guard still requires the
  source hash and an evidence-backed analysis of that exact version, and a document that fails
  it goes to review.
- **Scheduler** — `fedtone.application.scheduler.PipelineScheduler` runs one cycle per
  interval (`fedtone schedule`, Compose profile `scheduler`) without a new dependency: a
  `threading.Event` loop, no overlapping cycles, unconfigured stages skipped, one crashing
  stage does not stop the next. Idempotency comes from the canonical document identity and
  content hashes plus status-driven stages; provider outages resume at the last good stage.
  Runners for the API, CLI and scheduler are built in `fedtone.infrastructure.pipeline`.
- **Observability** — JSON logs carry `run_id` (cycle or ingestion run) and `document_id`;
  secrets are scrubbed by the formatter. `fedtone.observability.PROVIDER_METRICS` counts
  requests, errors and latency per provider (`http:<host>` for official sources and FRED,
  `llm`), exposed in `/api/v1/system/telemetry`. `/ready` requires a reachable, migrated
  database and reports provider configuration without contacting providers.
- **Backup** — `fedtone.infrastructure.persistence.backup`: SQLite online backup, integrity
  check, schema revision and record counts, retention, restore with a `pre-restore` copy.
- **Packaging** — one Docker image (roles `api`, `ui`, `scheduler`, or any command), Compose
  with health checks and a named data volume, GitHub Actions CI and a GHCR CD workflow.

Conceptual workflow:

```text
Discover
  → Fetch
  → Parse
  → Normalize
  → Link Previous
  → LLM Tone Analysis
  → Validate Evidence
  → Compute Tone Delta
  → Fetch Market Data
  → Compute Event Metrics
  → Publish Dashboard Result
```

A later agentic layer may use LangGraph, but it is **not required for the MVP**.

---

## 9. Production Deployment

Production-shaped packaging:

| Artifact | Role |
|---|---|
| `Dockerfile` | Python application build and runtime image. |
| `docker-compose.yml` | API/UI plus persistent database volume. |
| `.env.example` | Placeholder-only configuration. |
| `.github/workflows/ci.yml` | Tests, lint, type checks, security checks. |
| `.github/workflows/cd.yml` | Build/publish container on releases. |

Secrets such as LLM API keys and market-data provider keys are environment variables only.

---

## Code Layout & Import Rules

Source lives in `src/fedtone/`, split into five layers. Imports may only point "inward":

| Layer | Responsibility | May import |
|---|---|---|
| `domain` | Entities, value objects, lifecycle rules | nothing internal |
| `application` | Use cases / services orchestrating the domain | `domain` |
| `infrastructure` | Persistence, source, LLM and market-data adapters | `domain`, `application` |
| `api` | FastAPI routes (thin) | `domain`, `application`, `infrastructure` |
| `ui` | Dashboard presentation only | `domain`, `application` |

These rules are enforced by `tests/unit/test_architecture_boundaries.py`.

Cross-cutting modules at the package root, usable by every layer:

- `fedtone.settings` — Pydantic settings (`FEDTONE_*`, nested with `__`), `SecretStr` secrets,
  production secret validation and `redact()`.
- `fedtone.observability` — JSON log formatter, `correlation_context(run_id=, document_id=)`,
  `scrub_secrets()`.

### Lifecycle Guard

All document status changes go through `fedtone.domain.models.transition()`. It enforces the
transition table in `fedtone.domain.lifecycle`, requires a reason for `NeedsReview`/`Failed`,
keeps `last_successful_status` so a failed document resumes only at the stage that last
succeeded, treats `Published` as terminal, and allows publishing only with a SHA-256 source
hash plus an evidence-backed analysis of that exact document version.

### Persistence (Phase 1)

SQLAlchemy mappings live in `fedtone.infrastructure.persistence.orm`, separate from domain
dataclasses; repositories implement the `fedtone.application.ports` protocols. Alembic
migrations live in `migrations/`. Canonical document identity is the unique key
(`central_bank`, `document_type`, `source_url`); repeated inserts return the existing record.
Datetimes are stored as naive UTC and returned timezone-aware. Status history is append-only
in `document_status_changes`.

---

## Architectural Invariants (Non-Negotiable)

1. **Official-source provenance** — every analyzed claim traces back to a canonical source document.
2. **Evidence-grounded LLM output** — no score without supporting spans.
3. **Structured outputs** — model responses must validate against versioned schemas.
4. **Deterministic finance math** — returns, yield changes, event windows, and statistics are code, not LLM arithmetic.
5. **Point-in-time integrity** — no future information in historical tests.
6. **Reproducibility** — model, prompt, schema and document hashes are persisted.
7. **Idempotent ingestion** — the same source document cannot create duplicate canonical records.
8. **Human-review path** — low-confidence or invalid analyses do not silently publish.
9. **Replaceable providers** — central-bank, LLM and market-data providers sit behind interfaces.
10. **Research, not automated investment advice** — FedTone explains policy communication and observed market reactions.

---

## Technology Stack (Baseline)

| Area | Technology |
|---|---|
| Runtime | Python 3.12+ |
| API | FastAPI + Pydantic |
| Database | SQLite + SQLAlchemy / Alembic |
| UI | Streamlit + Plotly |
| HTTP | httpx |
| Parsing | BeautifulSoup / lxml / PDF parser as needed |
| LLM | OpenAI-compatible structured-output client |
| Data | pandas / numpy |
| Market providers | Pluggable adapters (free-first for MVP) |
| Tests | pytest |
| Quality | Ruff + mypy/pyright as adopted |
| Deployment | Docker + GitHub Actions |

---

## Related Documents

- `docs/ROADMAP.md` — product vision, milestones and KPIs.
- `docs/PHASES.md` — implementation phases, deliverables and test criteria.
- `docs/PROJECT_PHASES_AND_TASKS.md` — atomic delivery source of truth.
- `docs/TASKS.md` — execution ledger.
- `docs/DIAGRAMS.md` — architecture, pipeline and state diagrams.
- `docs/DEPLOYMENT.md` — CI/CD and production deployment.
- `docs/RUNBOOK.md` — operational and recovery procedures.
