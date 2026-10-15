# FedTone

**FedTone** turns Federal Reserve communication into a reproducible research signal. It collects
official FOMC statements, scores how hawkish or dovish they are with an LLM that must quote its
evidence, shows what changed versus the previous statement, and links each meeting to market
reactions that are computed by deterministic Python code, never by the model.

> FedTone reports association and historical reaction, not causal proof, and it does not give
> trading or investment advice.

```text
federalreserve.gov ─► Collect & normalize ─► LLM tone analysis ─► Evidence validation
                                                                        │
   Research summary ◄─ Historical analogues ◄─ Market reaction ◄─ Statement-to-statement diff
```

## What you get out of it

| Output | What it is | Command |
|---|---|---|
| **Document corpus** | Official FOMC statements with source URL, publication and retrieval time, SHA-256 hash and parser version. Snapshots are immutable; a changed page adds a version instead of overwriting. | `fedtone ingest`, `fedtone documents` |
| **Tone score** | `overall_score` from −1 (strongly dovish) to +1 (strongly hawkish), a label (`strongly_dovish` … `strongly_hawkish`), a confidence, per-dimension scores (inflation, labor, growth, policy guidance, balance sheet), verbatim evidence quotes and a rationale. Quotes that are not in the source are rejected. | `fedtone analyze` |
| **Statement comparison** | Language that was added, removed, strengthened or softened since the previous meeting, with the tone delta. | `fedtone analyze` |
| **Market reaction** | Simple and log returns for prices (S&P 500, broad dollar index) and basis-point changes for yields (2y, 10y Treasury) over `[0,0]`, `[0,+1]` and `[0,+5]` trading-day windows, aligned so the pre-event close never contains the announcement. Optional excess return versus a benchmark. | `fedtone reactions` |
| **Historical analogues** | The earlier meetings whose tone profile is most similar, and how markets reacted then. Only information available before the meeting is used. | `fedtone analogues DOCUMENT_ID` |
| **Research summary** | A short LLM-written note that may only quote numbers already computed by FedTone (copied verbatim, sign included). Causal wording is rejected. | `fedtone summarize` |
| **Review queue** | Anything uncertain (parse problems, missing evidence, invalid model output) goes to a human review list instead of being silently accepted. | `fedtone reviews`, `fedtone resolve` |
| **Model benchmark** | Agreement, stability and evidence quality of the configured model against a labeled dataset, as a Markdown/JSON report. | `fedtone benchmark` |

Every stored result records its model, prompt version, schema version and metric version, so an
analysis can be traced and re-run.

## Install

Requires Python 3.12+.

```bash
git clone https://github.com/M-Mashreghi/Fedtone.git
cd Fedtone
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env          # then fill in the values you need (never commit .env)
alembic upgrade head          # creates ./data/fedtone.db by default
```

Configuration comes from `FEDTONE_*` environment variables or `.env`; nested groups use `__`
(for example `FEDTONE_LLM__API_KEY`). The most important settings:

| Setting | Needed for |
|---|---|
| `FEDTONE_LLM__API_KEY`, `FEDTONE_LLM__MODEL` (and `FEDTONE_LLM__BASE_URL` for non-OpenAI hosts) | `analyze`, `summarize`, `benchmark`. Any OpenAI-compatible endpoint works. |
| `FEDTONE_MARKET_DATA__PROVIDER=fred` | `reactions`, `analogues`. Public FRED CSV downloads, no key needed. Use `csv` plus `FEDTONE_MARKET_DATA__CSV_DIRECTORY` for local files. |
| `FEDTONE_DATABASE__URL` | Optional; defaults to SQLite in `./data/`. |

See [`.env.example`](.env.example) for the full list, including event windows, instruments and
analogue settings.

## Workflows

### 1. Build a clean FOMC statement corpus (no LLM, no market data)

```bash
fedtone ingest              # discover, fetch, parse and link statements; safe to re-run
fedtone ingest --refetch    # re-check known pages for silent edits
fedtone reviews             # documents that need a human look
fedtone resolve ITEM_ID --note "checked against the PDF" --resume
fedtone documents           # list what is stored
```

### 2. Score tone and see what changed

```bash
fedtone analyze             # scores queued statements and compares each with the previous one
fedtone documents           # now shows tone=... per document
```

Low-confidence or unsupported results land in `fedtone reviews` rather than in the dataset.

### 3. Measure the market reaction

```bash
fedtone reactions                                   # returns and bps changes per window
fedtone reactions --as-of 2024-03-01T00:00:00+00:00 # only data available at that moment
```

A window that had not closed at `--as-of` is never computed, which keeps backtests free of
look-ahead bias.

### 4. Find historical analogues

```bash
fedtone analogues DOCUMENT_ID --top-k 5 --min-sample 3
```

Only meetings published before the target, analyzed by the same model and prompt, and reactions
fixed before the cutoff are used. With fewer than `--min-sample` earlier meetings the ranking is
flagged as anecdotal.

### 5. Write a research note

```bash
fedtone summarize
```

### 6. Evaluate a model before trusting it

```bash
fedtone benchmark --dataset tests/fixtures/eval/mini_benchmark.json --repeats 3 --output reports/
```

### 7. Run the API

```bash
uvicorn fedtone.api.app:create_default_app --factory --reload
# GET http://127.0.0.1:8000/healthz   liveness
# GET http://127.0.0.1:8000/ready     database readiness
```

Research endpoints and the dashboard are planned for Phase 5.

### 8. Develop and test

```bash
pytest                                  # all tests use fixtures and a fake LLM; no network needed
ruff check . && ruff format --check .
python scripts/capture_fomc_fixtures.py # capture real FOMC pages for the live-fixture tests
```

## Design rules

1. **Evidence-backed LLM output.** Every score must cite source text, or it is marked uncertain.
2. **Deterministic finance math.** Returns, bps moves and event statistics are computed in Python.
3. **Point-in-time integrity.** Historical analysis uses only what was known at the time.
4. **Official-source provenance.** URL, timestamps, hashes and parser version are kept.
5. **Reproducibility.** Model, prompt, schema and metric versions are stored with each result.
6. **Source text is untrusted.** Instructions embedded in documents are never followed.

## Project layout

```text
src/fedtone/
  domain/           pure research logic: tone schema, diff, evidence, calendars, metrics, analogues
  application/      services and ports: ingestion, tone analysis, market reaction, summaries
  infrastructure/   adapters: FOMC source, OpenAI-compatible LLM, FRED/CSV market data, SQLite
  api/              FastAPI app
  cli.py            the `fedtone` command
migrations/         Alembic schema migrations
tests/              unit and integration tests with saved fixtures
docs/               architecture, roadmap, phases, task ledger, runbook
```

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Foundation, data layer, research domain | Done |
| 2 | Official FOMC ingestion and normalization | Implemented; closes once captured live pages pass the fixture tests |
| 3 | LLM hawkish/dovish tone engine | Implemented with a fake LLM; closes after a human-labeled benchmark on a real model |
| 4 | Market reaction, historical analogues, research summaries | Done with fixture market data; live FRED fetch still to be checked |
| 5 | Research API and dashboard | Planned |
| 6 | Hardening, Docker, production readiness | Planned |
| 7 | Multi-bank, speeches, agentic research | Planned |

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md) · [Phases](docs/PHASES.md) ·
[Task plan](docs/PROJECT_PHASES_AND_TASKS.md) · [Progress ledger](docs/TASKS.md) ·
[Diagrams](docs/DIAGRAMS.md) · [Deployment](docs/DEPLOYMENT.md) · [Runbook](docs/RUNBOOK.md)

Contributors using Claude Code should start with [`CLAUDE.md`](CLAUDE.md) and `.claude/rules/`.

## خلاصه فارسی

FedTone بیانیه‌های رسمی فدرال رزرو (FOMC) را جمع‌آوری می‌کند، با یک مدل زبانی میزان «انقباضی» یا
«انبساطی» بودن لحن آن‌ها را از −۱ تا +۱ می‌سنجد و برای هر امتیاز، جمله‌های مستند از متن اصلی ارائه
می‌دهد. سپس تغییرات زبان نسبت به جلسه‌ی قبل را نشان می‌دهد، واکنش بازار (بازده سهام، تغییر بازده
اوراق خزانه به واحد bps و شاخص دلار) را با کد قطعی پایتون محاسبه می‌کند، جلسه‌های مشابه تاریخی را
بدون سوگیری نگاه به آینده پیدا می‌کند و یک یادداشت پژوهشی کوتاه می‌نویسد که فقط اعداد محاسبه‌شده را
نقل می‌کند. هیچ عدد مالی توسط مدل زبانی ساخته نمی‌شود و نتایج توصیه‌ی سرمایه‌گذاری نیستند.
