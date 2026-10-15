# FedTone — Claude Code Project Instructions

FedTone is an LLM-powered central-bank communication analysis and monetary-policy research platform.

## Source of Truth

Before implementing a task, read:

1. `docs/PROJECT_PHASES_AND_TASKS.md`
2. `docs/ARCHITECTURE.md`
3. `docs/TASKS.md`
4. The relevant phase/task specification in `docs/PHASES.md`

Do not invent requirements that conflict with these documents.

## Development Workflow

For every implementation task:

1. Read the task specification and its Definition of Done.
2. Inspect the existing implementation before editing.
3. Implement only the requested scope.
4. Add or update automated tests.
5. Run the relevant tests and fix failures.
6. Run broader regression tests when the change touches shared code.
7. Update `docs/TASKS.md` and `docs/PROJECT_PHASES_AND_TASKS.md` only after verification succeeds.
8. Do not begin the next task unless explicitly requested.

## Architecture

Maintain clear separation between:

- official-source ingestion
- document normalization and storage
- LLM policy-tone analysis
- evidence validation
- statement comparison
- market-data retrieval
- deterministic financial analytics
- API/application services
- dashboard/UI

Business and research logic must not live directly in UI code.
External services must be accessed through explicit adapters/interfaces where practical.

## Finance Integrity

LLMs must NOT calculate financial returns, yield changes, basis-point moves, event-study statistics, abnormal returns, p-values, or other numerical market metrics.

LLMs may:

- classify hawkish/dovish language
- identify supporting evidence
- explain monetary-policy communication
- summarize language changes
- interpret already-computed financial results

Deterministic Python code must calculate:

- returns
- basis-point changes
- event-window statistics
- abnormal and cumulative abnormal returns
- descriptive statistics
- historical analog comparisons

Never introduce look-ahead bias. Historical analysis must use only information available at the relevant historical timestamp.

## LLM Requirements

Application-facing LLM outputs must use structured schemas.

Every policy-tone assessment must include at least:

- `net_tone_score`
- `classification`
- `confidence`
- evidence spans or evidence references
- explanation

Unsupported scores without evidence must be rejected or marked uncertain.
Track prompt/model versions for reproducibility.

## Data Provenance

Prefer official central-bank sources.
Persist provenance fields when applicable:

- source URL
- publication timestamp
- retrieval timestamp
- document hash
- central bank
- document type
- parser version

Never silently overwrite historical source documents.

## Testing

Every implementation task requires tests appropriate to its scope.

Critical test categories include:

- official-source parser fixtures
- document normalization tests
- structured LLM schema validation
- missing-evidence and malformed-output handling
- statement-diff logic
- deterministic financial calculation fixtures
- no-look-ahead / point-in-time tests
- event-study tests
- API tests

Do not weaken correct tests merely to make an implementation pass.

## Secrets

Never hardcode or commit API keys, tokens, credentials, or private endpoints.
Use environment variables and `.env.example`.
Never print secrets in logs or exception messages.

## Scope Discipline

- Do not perform unrelated refactors.
- Do not add infrastructure that the current task does not need.
- Prefer the simplest implementation that satisfies the task and architecture.
- Avoid unnecessary microservices for the MVP.
- New external dependencies should be justified before addition.

## Documentation

Repository documentation and code comments should be in English.
After architectural, configuration, API, or workflow changes, update the relevant docs in the same task.

## Completion Rule

Never claim a task is complete unless:

- implementation exists
- required tests pass
- Definition of Done is satisfied
- affected documentation is synchronized
