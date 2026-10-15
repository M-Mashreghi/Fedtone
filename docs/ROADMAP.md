# FedTone Roadmap

Strategic product roadmap for **FedTone** — an LLM-powered central-bank communication intelligence and market-reaction research platform.

**Related documents:** [ARCHITECTURE.md](./ARCHITECTURE.md) · [PHASES.md](./PHASES.md) · [DIAGRAMS.md](./DIAGRAMS.md) · [TASKS.md](./TASKS.md)

---

## 1. Product Vision & Value Proposition

### Vision

FedTone turns central-bank communication into a reproducible research signal. A researcher selects a meeting or document and the system automatically:

1. **Collects** the official statement or policy communication.
2. **Normalizes** and versions the source text.
3. **Scores** hawkish/dovish tone with a structured LLM output.
4. **Explains** the score using evidence from the document.
5. **Compares** the wording with the previous comparable meeting.
6. **Quantifies** the change in tone by policy dimension.
7. **Links** the policy event to deterministic market-reaction metrics.
8. **Visualizes** the historical tone timeline, statement diff and event-study results.

### Value Proposition

| Stakeholder | Value |
|---|---|
| **Macro / quant researchers** | Faster, reproducible extraction of policy-tone shifts and event-study features. |
| **Finance students / candidates** | A portfolio-grade project combining LLMs, NLP, macrofinance, data engineering and workflow automation. |
| **Portfolio / research teams** | Evidence-grounded monitoring of central-bank language changes with audit trails. |
| **Engineering** | Modular source, LLM and market-data adapters with independently testable stages. |

### Non-Goals (Near Term)

- Building a fully autonomous trading system.
- Issuing personalized buy/sell recommendations.
- Claiming causal market impact from simple event correlations.
- Supporting every central bank and document type in the MVP.
- Requiring paid institutional data for the first release.

---

## 2. Release Horizons

### M1 — FOMC Data Spine (Alpha)

**Theme:** Establish a clean research dataset and reproducible ingestion pipeline.

| Area | Outcome |
|---|---|
| Project foundation | Python/FastAPI structure, settings, tests, SQLite, migrations. |
| FOMC ingestion | Discover and fetch official FOMC statements. |
| Document model | Immutable source snapshot + metadata + hashes. |
| Normalization | Clean, comparable text with previous-document linking. |

**Business unlock:** A researcher can build a reliable historical corpus of FOMC statements without manual copy/paste.

**Exit criteria:** A curated historical set ingests idempotently and can be queried by meeting date with original source metadata preserved.

---

### M2 — Policy Tone Engine (Beta)

**Theme:** Turn official communication into evidence-grounded structured signals.

| Area | Outcome |
|---|---|
| LLM schema | Hawkish/dovish overall + dimension scores + confidence + evidence. |
| Comparison | Previous-statement diff and tone delta. |
| Validation | Pydantic schema, evidence validation, review path. |
| Evaluation | Labeled mini benchmark and stability tests. |

**Business unlock:** FOMC communications become queryable time-series research features rather than unstructured text.

**Exit criteria:** Historical statements produce reproducible structured outputs with evidence and acceptable agreement on a labeled evaluation set.

---

### M3 — Market Reaction Research (Release Candidate)

**Theme:** Connect policy-language changes to market behavior without letting the LLM perform finance math.

| Area | Outcome |
|---|---|
| Market adapters | Free-first pluggable price/yield data sources. |
| Event engine | Daily event windows and deterministic returns / basis-point changes. |
| Historical analogues | Similar tone shifts and their subsequent reactions. |
| Research summary | LLM explains computed metrics with citations to source data. |

**Business unlock:** Users can ask whether a given hawkish/dovish shift was unusual and how similar historical meetings behaved.

**Exit criteria:** A meeting detail page shows tone delta, evidence, market metrics and historical analogues from a point-in-time-safe dataset.

---

### M4 — Research Dashboard & API (v1.0)

**Theme:** Make the project usable and portfolio-ready.

| Area | Outcome |
|---|---|
| FastAPI | Stable research/query API. |
| Streamlit UI | Timeline, detail, statement diff, dimension radar/bar, event reaction. |
| Auditability | Model/prompt/schema/source versions visible. |
| Export | Downloadable CSV/JSON/Markdown research outputs. |

**Business unlock:** FedTone becomes an end-to-end research product rather than a notebook experiment.

**Exit criteria:** A fresh user can select an FOMC meeting and reproduce the complete source → score → diff → market-analysis flow from the UI.

---

### M5 — Production Hardening (v1.1)

**Theme:** Reliable scheduled operation and maintainability.

| Area | Outcome |
|---|---|
| Automated ingestion | Scheduled official-source checks with idempotent jobs. |
| Observability | Structured logs, health probes, pipeline run history. |
| Docker | Reproducible local/host deployment. |
| CI/CD | Tests, lint, type checks and image builds on GitHub Actions. |
| Backups | SQLite backup/restore runbook. |

**Business unlock:** The platform can monitor new releases with minimal manual intervention.

---

### M6 — Multi-Bank & Agentic Research (Future)

Directional expansion after the FOMC workflow is stable:

| Horizon | Description |
|---|---|
| **ECB / SNB / BoE** | Add official adapters and bank-specific policy dimensions. |
| **Speeches & minutes** | Separate communication types and compare tone dispersion. |
| **Speaker analytics** | Track policymaker hawkish/dovish profiles over time. |
| **Agentic research note** | Data agent + policy analyst + skeptic/reviewer + report composer. |
| **RAG research memory** | Retrieve historical analogues, related statements and prior analyses. |
| **Intraday event study** | Provider-backed minute data around policy announcements. |
| **Alerts** | Notify only on material tone changes or unusual market reactions. |

---

## 3. Success Metrics & KPIs

### Core KPIs

| KPI | Initial Target | Definition |
|---|---:|---|
| **Official-source ingestion success** | > 98% | Supported FOMC statements discovered, fetched and parsed correctly. |
| **Duplicate ingestion rate** | 0% | Same canonical document never creates multiple canonical records. |
| **Structured-output validity** | > 99% | LLM responses that pass the versioned Pydantic schema after bounded retry. |
| **Evidence coverage** | > 95% | Published score dimensions contain at least one valid source evidence span when applicable. |
| **Tone-label agreement** | ≥ 85% | Model direction/class agrees with a human-labeled evaluation set. |
| **Reproducibility** | 100% | Published analyses store source hash, model, prompt and schema version. |
| **Finance-metric determinism** | 100% | Canonical event metrics are produced by tested code, never free-form LLM arithmetic. |

### Supporting Research Metrics

| Metric | Purpose |
|---|---|
| Mean absolute score drift across repeated runs | Model stability. |
| Low-confidence / `NeedsReview` rate | Human-review burden. |
| Average source-to-dashboard latency | Workflow efficiency. |
| Tone delta distribution | Detect calibration saturation. |
| Dimension/evidence disagreement rate | Prompt or schema quality issue. |
| Missing market-data rate | Provider reliability. |
| Historical analogue sample size | Research interpretability. |

### KPI Governance

- Keep a fixed labeled evaluation set in-repo or in a reproducible fixture package.
- Version prompt and schema changes; never overwrite historical evaluation evidence.
- A major model/provider change requires rerunning the benchmark before release.
- Market-reaction metrics must have tests for dates, timezone conventions and window boundaries.
- KPI regressions block milestone completion unless explicitly documented.

---

## 4. Milestone → Phase Mapping

| Horizon | Primary engineering phases |
|---|---|
| M1 Alpha | Phases 1–2 |
| M2 Beta | Phase 3 |
| M3 RC | Phase 4 |
| M4 v1.0 | Phase 5 |
| M5 v1.1 | Phase 6 |
| M6 Future | Phase 7 |

---

## 5. Risk Register (Strategic)

| Risk | Impact | Mitigation |
|---|---|---|
| Official page structure changes | Ingestion breaks | Adapter isolation, fixtures, parser tests, raw snapshot retention. |
| LLM score instability | Research signal becomes noisy | Low temperature, structured schema, eval set, prompt/model versioning. |
| Hallucinated evidence | Trust failure | Validate quotes/spans against source text; reject missing evidence. |
| Look-ahead bias | Invalid historical conclusions | Point-in-time joins, release timestamps, explicit event-window tests. |
| LLM performing finance math | Silent numeric errors | Deterministic analytics service; LLM receives computed values only. |
| Free market-data gaps | Incomplete event study | Provider abstraction, clear missing-data flags, paid/intraday provider later. |
| Scope creep into trading | Delays research MVP | Keep automated execution and investment advice out of v1 scope. |
| Multi-bank semantics differ | Invalid cross-bank comparison | Bank-specific prompts/dimensions with a common normalized interface. |

---

## 6. Roadmap Change Control

- Update this file when milestone scope, KPI targets or ordering changes.
- Reflect implementation progress in `TASKS.md` after each completed task.
- Update `ARCHITECTURE.md` and `DIAGRAMS.md` when contracts or state flows change.
- New paid data providers or major infrastructure dependencies require an explicit architecture decision.
