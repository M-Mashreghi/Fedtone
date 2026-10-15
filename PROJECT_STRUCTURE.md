# FedTone Claude Code Starter Structure

```text
FedTone/
├── CLAUDE.md
├── README.md
├── PROJECT_STRUCTURE.md
├── pyproject.toml
├── alembic.ini
├── .gitignore
├── .env.example
├── migrations/             # Alembic env + versions/0001–0004
├── scripts/
│   └── capture_fomc_fixtures.py  # saves official pages into tests/fixtures/fomc/live
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
│       ├── cli.py          # ingest, reviews, analyze, resolve, benchmark, reactions, analogues, summarize
│       ├── domain/         # lifecycle, models (+ guard), text, linking, tone_schema, evidence, diff, market, calendar, metrics, analogues, summary
│       ├── application/    # ports, sources, ingestion, llm, prompts, tone_analysis, review, evaluation,
│       │                   # market_data, market_reaction, analogue_search, research_summary
│       ├── infrastructure/
│       │   ├── persistence/ # db.py, orm.py, repositories.py
│       │   ├── sources/    # http.py (retry), fomc.py, manual.py
│       │   ├── llm/        # openai_compatible.py
│       │   └── market/     # fred.py (FRED CSV + local CSV providers)
│       ├── api/            # app.py: FastAPI factory, /healthz, /ready
│       └── ui/
├── tests/
│   ├── api/
│   ├── unit/               # includes layer import-boundary test
│   ├── integration/        # migrations + repositories on fresh SQLite
│   ├── fake_llm.py         # scripted LLM used by tests (no network)
│   ├── fake_market.py      # in-memory market-data provider
│   ├── market_harness.py   # analyzed statements ready for market linking
│   └── fixtures/           # fomc/ pages; eval/ benchmark; market/ FRED-format CSVs
└── docs/
    ├── ARCHITECTURE.md
    ├── ROADMAP.md
    ├── PHASES.md
    ├── PROJECT_PHASES_AND_TASKS.md
    ├── TASKS.md
    ├── DIAGRAMS.md
    ├── DEPLOYMENT.md
    └── RUNBOOK.md
```
