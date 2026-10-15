# FedTone Operational Runbook

Production incident and maintenance procedures for **FedTone**. Companion docs: [DEPLOYMENT.md](./DEPLOYMENT.md) · [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## 1. Architecture Summary (Operator View)

| Component | Role | Durable State |
|---|---|---|
| FastAPI | Research API + pipeline commands | None directly |
| Streamlit | Research dashboard | None |
| SQLite (`fedtone.db`) | Documents, hashes, analyses, comparisons, event metrics, run history | `./data` / `/app/data` |
| Scheduler | Periodic official-source discovery/processing | Job/run state in DB |
| Official source adapters | Fetch central-bank communications | Raw/normalized snapshot metadata persisted |
| LLM provider | Structured tone analysis | Output persisted with model/prompt/schema version |
| Market provider | Price/yield observations | Retrieved canonical observations persisted as configured |
| Backup directory | Rotated database backups | `./data/backups` |

---

## 2. Health Diagnostics

| Probe | URL | Meaning |
|---|---|---|
| Liveness | `GET /healthz` | Process is running. |
| Readiness | `GET /ready` | Database is reachable (`SELECT 1`); returns 503 with `{"checks": {"database": "unavailable"}}` otherwise. Provider checks are added in Task 6.3. |
| Detailed | `GET /health/detailed` | Per-component diagnostic state if implemented. |

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/ready
docker compose ps
docker compose logs --tail 200 api
```

If API is healthy but dashboard is empty, inspect document counts and latest processing runs before rerunning ingestion.

---

## 3. Official FOMC Ingestion Failure

**Symptoms:** new statement missing, source adapter errors, parsing failures, repeated `Fetched`/`Parsed` failures.

**Recovery**

1. Confirm external network access from the application host.
2. Inspect latest `ProcessingRun` error and source URL.
3. Determine whether failure is:
   - transient HTTP error,
   - official page structure change,
   - metadata date parse issue,
   - text extraction/normalization issue.
4. Re-run against the saved/raw source snapshot when possible before hitting the live site repeatedly.
5. Add/update a fixture reproducing the changed official page structure.
6. Fix adapter/parser and run tests.
7. Retry the failed document; verify the canonical id/hash prevents duplication.

**Commands (Phase 2)**

```bash
alembic upgrade head
fedtone ingest                 # discover, fetch, snapshot, parse, normalize, link
fedtone ingest --refetch       # also re-download known documents to detect revisions
fedtone reviews                # list open review items
```

How failures surface:

| Failure | Document state | Next run |
|---|---|---|
| Network error / 5xx / 429 after bounded retries | `Failed` (reason stored) | Resumed automatically at the last successful stage. |
| 404 / unexpected layout / not an FOMC statement | `NeedsReview` + review item | Skipped until a reviewer resumes it. |
| Listing page unreachable | Run `failed`, no documents touched | Retried on next run. |
| Same URL, changed policy text (`--refetch`) | Stored text kept; new snapshot + parsed text saved; review item `source text revised` | Not overwritten. |
| Same URL, only page chrome changed | New raw snapshot, no review | — |

Raw snapshots (`document_snapshots`) are append-only, so a parser fix can be re-run against
stored bytes without hitting the live site.

**Never** delete a canonical document merely to make ingestion succeed. Preserve audit/version history.

---

## 4. LLM Provider Failure / Model Retirement

**Symptoms:** 401/403, model-not-found, 429, timeout, malformed structured output.

**Recovery**

1. Check configured provider, base URL and model identifier.
2. Confirm API key exists in environment and has not expired.
3. For 429/5xx, respect bounded backoff; do not create tight retry loops.
4. For model retirement, choose a replacement model in configuration.
5. **Before bulk reanalysis**, run the fixed evaluation benchmark on the new model.
6. Create a new model/prompt analysis version; do not overwrite historical outputs silently.
7. Resume `NeedsReview` / failed processing runs only after validation.

### Commands (Phase 3)

```bash
fedtone analyze                                  # analyze queued statements, then compare with the previous one
fedtone reviews                                  # open review items with their ids
fedtone resolve ITEM_ID --note "checked" --resume  # continue from the last good stage
fedtone resolve ITEM_ID --note "bad output"        # reject: document goes to Failed
fedtone benchmark --dataset tests/fixtures/eval/mini_benchmark.json --repeats 3 --output reports/
```

`analyze` exits with code 2 and names the missing keys when `FEDTONE_LLM__API_KEY` or
`FEDTONE_LLM__MODEL` is not set. A provider outage marks the document `Failed`; the next
`analyze` run resumes it. A permanent provider error (for example 401) stops the run without
changing any document. A document stays in review until all of its open items are resolved.

### Schema Failures

If the model returns invalid structured output:

- retry once using the same versioned schema,
- if still invalid, route to `NeedsReview`,
- never parse an arbitrary prose answer into a fabricated score.

---

## 5. Hallucinated / Invalid Evidence Incident

**Symptoms:** evidence quote cannot be found in the statement; researcher reports unsupported rationale.

**Immediate Actions**

1. Mark affected analysis as `NeedsReview` or unpublished.
2. Preserve model output and source hash for debugging.
3. Run evidence validator manually on the affected analysis.
4. Check normalization differences before assuming hallucination.
5. If truly unsupported, inspect prompt/model changes and benchmark neighboring documents.
6. Patch validator/prompt and rerun the evaluation set before republishing.

**Integrity rule:** a material score should not be public without source-grounded evidence.

---

## 6. Tone Score Looks Implausible

**Symptoms:** sudden extreme ±1 scores, contradictory dimension scores, major drift after model change.

**Actions**

1. Confirm correct current and previous statements were linked.
2. Inspect evidence spans and diff output.
3. Check model/prompt/schema versions.
4. Compare with the fixed benchmark and recent score distribution.
5. Repeat the analysis 3–5 times in evaluation mode to measure nondeterministic drift.
6. If calibration is broken, do not mass-update historical records until a new analysis version is approved.

---

## 7. Market Data Missing or Wrong

**Symptoms:** event metrics absent, zero/NaN returns, yields off by orders of magnitude, wrong event date.

**Recovery**

1. Verify symbol/series mapping and provider response.
2. Check whether the event date was a trading day.
3. Inspect timezone/date alignment rules.
4. Confirm units:
   - equity/ETF return: decimal or percent per documented output,
   - yield changes: basis points.
5. Compare one event manually with raw provider observations.
6. If provider history is incomplete, mark the event as partial; do not silently interpolate.

### Commands (Phase 4)

```bash
fedtone reactions                                   # market reactions for compared statements
fedtone reactions --as-of 2024-03-01T00:00:00+00:00 # backtest: only data fixed by then
fedtone documents                                   # ids, release times, status, tone
fedtone analogues DOCUMENT_ID --top-k 5 --min-sample 3
fedtone summarize                                   # LLM research summaries (needs LLM config)
```

`reactions` exits with code 2 when `FEDTONE_MARKET_DATA__PROVIDER` is `none`. A meeting whose
`[0,+5]` window has not closed stays `Compared` and is linked on a later run. A provider outage
leaves documents `Compared`; a permanent error (unknown series, unexpected file) stops the run.
For `csv`, put FRED-format files named `<SYMBOL>.csv` in `FEDTONE_MARKET_DATA__CSV_DIRECTORY`.
Missing values appear as metrics with a `missing_reason`; never edit them in by hand.

---

## 8. Point-in-Time / Look-Ahead Audit

**Trigger:** backtest/analogue results appear too good or a data leak is suspected.

**Audit Procedure**

1. Pick a historical event date.
2. Record event publication timestamp and analysis cutoff.
3. Inspect every feature used in analogue ranking.
4. Verify each feature's timestamp is `<= event cutoff`.
5. Verify market outcome windows are used only as labels/results, not ranking inputs.
6. Run dedicated no-look-ahead tests.
7. Invalidate/recompute any derived research set created by the faulty code path.

**Do not** publish performance claims until the audit is clean.

---

## 9. Duplicate Document / Duplicate Analysis

**Symptoms:** same meeting appears twice, scheduled job creates duplicate canonical record.

**Actions**

1. Compare canonical URL/source id and content hashes.
2. Confirm unique DB constraint and idempotency key logic.
3. Determine whether the source was genuinely revised.
4. If exact duplicate, merge/delete only through a documented maintenance path.
5. If revised, preserve as a version linked to the same logical publication where supported.
6. Add regression fixture for the duplicate pattern.

---

## 10. Database Disaster Recovery

**Symptoms:** SQLite corruption, missing file, volume loss, failed migration with unusable DB.

### Recovery Procedure

1. Stop application writes:

```bash
docker compose stop
```

2. Locate newest verified backup under `./data/backups` or off-site copy.
3. Keep the damaged DB as forensic copy; do not overwrite it immediately.
4. Restore backup as `fedtone.db`.
5. Run integrity check:

```bash
sqlite3 data/fedtone.db 'PRAGMA integrity_check;'
```

6. Start services:

```bash
docker compose up -d
```

7. Verify `/ready`.
8. Spot-check:
   - a known source document,
   - its source hash,
   - one tone analysis,
   - one comparison,
   - one market reaction record.
9. Re-run ingestion since backup time; idempotency should prevent duplicate historical documents.

---

## 11. Backup Verification (Routine)

1. Confirm backup schedule ran.
2. Confirm archive count respects retention policy.
3. Restore a backup periodically into a temporary test DB.
4. Run `PRAGMA integrity_check`.
5. Query a known analysis and verify audit metadata.
6. Record restore drill date in operations notes.

---

## 12. Scheduler Incident

**Symptoms:** no new releases discovered, repeated jobs, duplicate jobs, CPU/network spike.

**Actions**

1. Check scheduler process/health.
2. Check last successful scheduled run timestamp.
3. Confirm only one scheduler instance owns the schedule in single-node deployment.
4. Pause schedule if it is creating repeated failures.
5. Run one manual ingestion cycle.
6. Confirm idempotency before re-enabling recurring schedule.

---

## 13. Related Configuration Keys

| Key | Purpose |
|---|---|
| `FEDTONE_ENV` | `development`, `test` or `production`; production requires LLM key and model. |
| `FEDTONE_DATABASE__URL` | SQLite/PostgreSQL database connection. |
| `FEDTONE_LLM__MODEL` | Model used for new analyses. |
| `FEDTONE_LLM__API_KEY` | Provider credential; never log (redacted by the JSON log formatter). |
| `FEDTONE_LLM__BASE_URL` | Optional OpenAI-compatible endpoint. |
| `FEDTONE_LLM__TEMPERATURE` / `__TIMEOUT_SECONDS` / `__MAX_RETRIES` | LLM call parameters. |
| `FEDTONE_LLM__BACKOFF_SECONDS` | Base delay for bounded exponential retry of LLM calls. |
| `FEDTONE_LLM__MIN_CONFIDENCE` | Analyses below this confidence are stored but routed to review (default 0.6). |
| `FEDTONE_SCHEDULER__ENABLED` | Enable scheduled discovery. |
| `FEDTONE_SCHEDULER__INGESTION_INTERVAL_MINUTES` | Recurring discovery interval. |
| `FEDTONE_SOURCES__USER_AGENT` / `__TIMEOUT_SECONDS` | HTTP identity and timeout for official sources. |
| `FEDTONE_SOURCES__MAX_RETRIES` / `__BACKOFF_SECONDS` | Bounded retry with exponential backoff. |
| `FEDTONE_SOURCES__FOMC_LISTING_URLS` | JSON list of pages scanned for statement links. |
| `FEDTONE_MARKET_DATA__PROVIDER` | Market-data backend. |
| `FEDTONE_MARKET_DATA__API_KEY` | Optional provider key (FRED CSV needs none). |
| `FEDTONE_MARKET_DATA__CSV_DIRECTORY` | Folder of `<SYMBOL>.csv` files for the `csv` provider. |
| `FEDTONE_MARKET_DATA__TIMEOUT_SECONDS` / `__MAX_RETRIES` / `__BACKOFF_SECONDS` | HTTP behaviour of the FRED provider. |
| `FEDTONE_MARKET_DATA__INSTRUMENTS` | JSON list of `{symbol, name, kind, calendar, observation_time}`; defaults SP500, DGS2, DGS10, DTWEXBGS. |
| `FEDTONE_MARKET_DATA__WINDOWS` | JSON list of event windows, default `["[0,0]","[0,+1]","[0,+5]"]`. |
| `FEDTONE_MARKET_DATA__BENCHMARK_SYMBOL` | Optional benchmark for excess returns (same calendar). |
| `FEDTONE_MARKET_DATA__ANALOGUE_TOP_K` / `__ANALOGUE_MIN_SAMPLE` | Analogue search defaults. |
| `FEDTONE_LOG_LEVEL` | Application log verbosity. |
| `BACKUP_RETENTION_COUNT` | Number of local DB backups to retain (planned, Task 6.5). |

---

## Change Log

| Date | Change |
|---|---|
| 2026-10-13 | Initial FedTone operational runbook created. |
| 2026-10-13 | Phase 3: `analyze`, `resolve`, `benchmark` commands and LLM config keys. |
| 2026-10-13 | Phase 4: `reactions`, `documents`, `analogues`, `summarize` commands and market-data keys. |
