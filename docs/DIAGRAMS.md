# FedTone Diagrams

Mermaid diagrams for the FedTone architecture, processing workflow, state machine and research-integrity boundaries.

**Related documents:** [ARCHITECTURE.md](./ARCHITECTURE.md) · [ROADMAP.md](./ROADMAP.md) · [PHASES.md](./PHASES.md)

---

## 1. System Architecture (C4-Style Flowchart)

```mermaid
flowchart TB
    USER[Researcher / Analyst]
    UI[Streamlit Research Dashboard]
    API[FastAPI Core API]
    DB[(SQLite / SQLAlchemy)]
    SCHED[Scheduler / Workflow Runner]
    SRC[Official Central-Bank Sources]
    ING[Ingestion + Normalization]
    LLM[LLM Tone Engine]
    VAL[Evidence / Schema Validator]
    DIFF[Statement Diff + Tone Delta]
    MKT[Market Data Providers]
    EVT[Deterministic Event-Study Engine]
    REP[Research Summary / Export]

    USER --> UI
    UI --> API
    API --> DB
    API --> SCHED
    SCHED --> SRC
    SRC --> ING
    ING --> DB
    ING --> LLM
    LLM --> VAL
    VAL --> DB
    VAL --> DIFF
    DIFF --> DB
    SCHED --> MKT
    MKT --> EVT
    EVT --> DB
    DB --> REP
    REP --> UI
```

---

## 2. End-to-End FOMC Research Lifecycle (Sequence)

```mermaid
sequenceDiagram
    autonumber
    actor R as Researcher
    participant UI as Streamlit
    participant API as FastAPI
    participant SRC as FOMC Source Adapter
    participant DB as SQLite
    participant LLM as Tone Engine
    participant VAL as Evidence Validator
    participant MKT as Market Provider
    participant EVT as Event Analytics

    R->>UI: Select / ingest FOMC meeting
    UI->>API: Run or fetch analysis
    API->>SRC: Discover/fetch official statement
    SRC-->>API: Source URL + raw content + metadata
    API->>DB: Persist snapshot + hash (Fetched)
    API->>API: Parse + normalize
    API->>DB: Persist normalized text (Normalized)
    API->>DB: Resolve previous comparable statement

    API->>LLM: Current + previous text + schema
    LLM-->>API: Structured tone JSON + evidence
    API->>VAL: Validate schema + evidence

    alt Valid and confident
        VAL-->>API: Approved
        API->>DB: Persist analysis (Analyzed)
        API->>API: Compute statement diff + tone delta
        API->>DB: Persist comparison (Compared)
    else Invalid / low confidence
        VAL-->>API: Needs review
        API->>DB: Persist NeedsReview state
    end

    opt Market analysis enabled
        API->>MKT: Fetch event-window observations
        MKT-->>API: Prices / yields
        API->>EVT: Compute returns / bps / analogues
        EVT-->>API: Deterministic metrics
        API->>DB: Persist market reaction (MarketLinked)
    end

    API->>DB: Publish result
    API-->>UI: Scores + evidence + diff + market metrics
    UI-->>R: Research dashboard
```

---

## 3. Policy Document State Machine (FSM)

```mermaid
stateDiagram-v2
    [*] --> Discovered
    Discovered --> Fetched: raw snapshot stored
    Fetched --> Parsed: main content extracted
    Parsed --> Normalized: comparable text + content hash
    Normalized --> QueuedForAnalysis: previous comparable document linked
    QueuedForAnalysis --> Analyzed: schema + evidence valid
    Analyzed --> Compared: diff vs previous (or baseline if none)
    Compared --> MarketLinked: market analysis enabled
    Compared --> Published: research text only
    MarketLinked --> Published

    note right of Published
        Publishing requires a SHA-256 source hash and an
        evidence-backed analysis of the same document version.
    end note

    Discovered --> NeedsReview
    Discovered --> Failed
    Fetched --> NeedsReview
    Fetched --> Failed
    Parsed --> NeedsReview
    Normalized --> NeedsReview
    QueuedForAnalysis --> NeedsReview
    QueuedForAnalysis --> Failed
    Analyzed --> NeedsReview
    Compared --> NeedsReview
    MarketLinked --> NeedsReview

    NeedsReview --> Failed: rejected / unusable
    NeedsReview --> LastSuccessfulStage: reviewer resumes
    Failed --> LastSuccessfulStage: retry when safe

    Published --> [*]
```

Every non-terminal stage may move to `NeedsReview` or `Failed` (only some arrows are drawn).
`LastSuccessfulStage` stands for the stage recorded in `last_successful_status`: a document
resumes exactly there, so the pipeline re-attempts the step that broke. `Published` is
terminal; reprocessing creates new versions instead. Enforced by
`fedtone.domain.models.transition()` (Task 1.3).

### State Reference

| State | Role |
|---|---|
| `Discovered` | Official source item found. |
| `Fetched` | Raw source retrieved and fingerprinted. |
| `Parsed` | Main text extracted. |
| `Normalized` | Comparable policy text available. |
| `QueuedForAnalysis` | Waiting for LLM analysis. |
| `Analyzed` | Valid structured tone output persisted. |
| `Compared` | Previous-document diff and tone delta available. |
| `MarketLinked` | Deterministic event metrics attached. |
| `NeedsReview` | Confidence/evidence/parser issue requires inspection. |
| `Published` | Visible to API/UI. |
| `Failed` | Processing stopped with auditable error. |

---

## 4. Tone Analysis Flow

```mermaid
flowchart LR
    CUR[Current Statement]
    PREV[Previous Statement]
    NORM[Normalized Text]
    DIFF[Deterministic Diff]
    PROMPT[Versioned Prompt]
    LLM[LLM Structured Output]
    SCHEMA[Pydantic Schema]
    EVID[Evidence Validator]
    SCORE[(Canonical Tone Analysis)]
    REVIEW[NeedsReview]

    CUR --> NORM
    PREV --> NORM
    NORM --> DIFF
    NORM --> PROMPT
    DIFF --> PROMPT
    PROMPT --> LLM
    LLM --> SCHEMA
    SCHEMA --> EVID
    EVID -->|valid| SCORE
    EVID -->|invalid / low confidence| REVIEW
```

### Score Convention

```text
-1.00                  0.00                  +1.00
Strongly Dovish ------- Neutral ------- Strongly Hawkish
```

Dimensions:

```text
Overall
├── Inflation
├── Labor
├── Growth
├── Policy Guidance
└── Balance Sheet
```

---

## 5. Statement Comparison Flow

```mermaid
flowchart TB
    A[Previous FOMC Statement]
    B[Current FOMC Statement]
    D[Paragraph / Sentence Diff]
    ADD[Added Language]
    REM[Removed Language]
    CHG[Changed Language]
    TA[Previous Tone Scores]
    TB[Current Tone Scores]
    DELTA[Tone Delta by Dimension]
    EXPL[Evidence-Grounded Explanation]

    A --> D
    B --> D
    D --> ADD
    D --> REM
    D --> CHG
    TA --> DELTA
    TB --> DELTA
    ADD --> EXPL
    REM --> EXPL
    CHG --> EXPL
    DELTA --> EXPL
```

---

## 6. Market Reaction Research Flow

```mermaid
flowchart LR
    EVENT[FOMC Event Timestamp]
    TONE[Tone Level + Delta]
    PROVIDER[Market Data Provider]
    WINDOW[Point-in-Time Event Window]
    CALC[Deterministic Metrics]
    ANALOG[Historical Analogues]
    SUMMARY[Research Summary]

    EVENT --> WINDOW
    PROVIDER --> WINDOW
    WINDOW --> CALC
    TONE --> ANALOG
    CALC --> ANALOG
    CALC --> SUMMARY
    ANALOG --> SUMMARY
```

### Numeric Boundary

```mermaid
flowchart TB
    DATA[Prices / Yields]
    CODE[Python Analytics Code]
    METRIC[(Canonical Returns / bps)]
    LLM[LLM Explanation]
    TEXT[Research Narrative]

    DATA --> CODE --> METRIC
    METRIC --> LLM --> TEXT

    X[LLM must NOT invent canonical metrics]
    X -. guardrail .-> LLM
```

---

## 7. Research Integrity & Reproducibility Chain

```mermaid
flowchart LR
    URL[Canonical Source URL]
    RAW[Raw Snapshot]
    HASH[Content Hash]
    PROMPT[Prompt Version]
    MODEL[Model ID]
    SCHEMA[Schema Version]
    RUN[Processing Run ID]
    OUTPUT[Published Analysis]

    URL --> RAW --> HASH --> OUTPUT
    PROMPT --> OUTPUT
    MODEL --> OUTPUT
    SCHEMA --> OUTPUT
    RUN --> OUTPUT
```

### Operational Guarantees

1. **No score without provenance:** every published analysis resolves to an official source and content hash.
2. **No unsupported evidence:** evidence strings are validated against source text.
3. **No LLM finance arithmetic:** canonical returns/yield changes come from deterministic functions.
4. **No look-ahead:** historical analogue/event-study queries enforce event-time cutoffs.
5. **No silent model migration:** model/prompt/schema versions remain attached to every analysis.
6. **No duplicate canonical documents:** ingestion uses stable identity/hash checks.

---

## 8. Phase Dependency Diagram

```mermaid
flowchart LR
    P1[1 Foundation]
    P2[2 FOMC Ingestion]
    P3[3 Tone Engine]
    P4[4 Market Analytics]
    P5[5 API + Dashboard]
    P6[6 Production]
    P7[7 Multi-Bank + Agents]

    P1 --> P2 --> P3
    P3 --> P4
    P3 --> P5
    P4 --> P5
    P5 --> P6 --> P7
```

---

## Diagram Maintenance

- Keep diagrams aligned with `ARCHITECTURE.md` and `PHASES.md`.
- When lifecycle states change, update the FSM and state reference together.
- When a new provider/agent is introduced, show whether it owns canonical data or only interpretation.
- Prefer Mermaid source in-repo over binary diagrams for reviewability.
