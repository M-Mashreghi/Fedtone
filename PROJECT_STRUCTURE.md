# FedTone Claude Code Starter Structure

```text
FedTone/
├── CLAUDE.md
├── README.md
├── PROJECT_STRUCTURE.md
├── pyproject.toml
├── Dockerfile               # one image: api | ui | scheduler | any command
├── docker-compose.yml       # api + ui (+ scheduler profile), named data volume
├── .dockerignore
├── docker/entrypoint.sh
├── .github/workflows/       # ci.yml (lint, tests, audit, Docker smoke), cd.yml (GHCR)
├── alembic.ini
├── .gitignore
├── .env.example
├── migrations/             # Alembic env + versions/0001–0004
├── scripts/
│   ├── capture_fomc_fixtures.py  # saves official pages into tests/fixtures/fomc/live
│   └── capture_bank_fixtures.py  # ECB/BoE/SNB pages into tests/fixtures/<bank>/live
├── .claude/
│   └── rules/
│       ├── architecture.md
│       ├── finance-integrity.md
│       ├── llm-analysis.md
│       ├── testing.md
│       └── documentation.md
├── src/
│   └── fedtone/
│       ├── settings.py     # Pydantic settings + secret redaction
│       ├── observability.py # JSON logging, correlation ids, secret scrubbing
│       ├── cli.py          # ingest … summarize, publish, schedule, backup, restore
│       ├── domain/         # lifecycle, models (+ guard), text, linking, tone_schema, evidence, diff, market, calendar, metrics, analogues, summary
│       ├── application/    # ports, sources, ingestion, llm, prompts, tone_analysis, review, evaluation,
│       │                   # market_data, market_reaction, analogue_search, research_summary,
│       │                   # research_queries, research_export, pipeline, publication, scheduler
│       ├── infrastructure/
│       │   ├── persistence/ # db.py, orm.py, repositories.py, backup.py
│       │   ├── sources/    # http.py (retry), fomc.py, ecb.py, boe.py, snb.py (PDF), common.py, manual.py
│       │   ├── llm/        # openai_compatible.py
│       │   ├── market/     # fred.py (FRED CSV + local CSV providers)
│       │   ├── factories.py # adapter construction shared by CLI and API
│       │   └── pipeline.py  # stage runners shared by API, CLI and scheduler
│       ├── api/            # app.py (factory, /healthz, /ready), routes.py (/api/v1), schemas.py
│       └── ui/             # dashboard.py (Streamlit), client.py (API client), views.py
├── tests/
│   ├── api/                # research API contract tests
│   ├── ui/                 # dashboard view helpers + Streamlit AppTest smoke tests
│   ├── e2e/                # fixture source → published result through the API
│   ├── unit/               # includes layer import-boundary test
│   ├── integration/        # migrations + repositories on fresh SQLite
│   ├── fake_llm.py         # scripted LLM used by tests (no network)
│   ├── fake_market.py      # in-memory market-data provider
│   ├── market_harness.py   # analyzed statements ready for market linking
│   └── fixtures/           # fomc/ (+ live captures), ecb/ boe/ snb/, eval/ benchmark, market/ CSVs
└── docs/
    ├── ARCHITECTURE.md
    ├── ROADMAP.md
    ├── PHASES.md
    ├── PROJECT_PHASES_AND_TASKS.md
    ├── TASKS.md
    ├── DIAGRAMS.md
    ├── DEPLOYMENT.md
    ├── RUNBOOK.md
    └── images/             # dashboard screenshots
```
