# FedTone — Production Deployment & CI/CD

Production-oriented deployment guide for **FedTone** using Python, Docker Compose, GitHub Actions and optional GitHub Container Registry (GHCR).

Related: [ARCHITECTURE.md](./ARCHITECTURE.md) · [RUNBOOK.md](./RUNBOOK.md) · [ROADMAP.md](./ROADMAP.md)

---

## 1. Pipeline Overview

| Workflow | File | Triggers | Purpose |
|---|---|---|---|
| **CI** | `.github/workflows/ci.yml` | Pull requests and pushes to `main` | Install → lint → type check → pytest → dependency/security checks. |
| **CD** | `.github/workflows/cd.yml` | `v*.*.*` tag or manual dispatch | Re-run quality gates → build Docker image → push GHCR → create release. |

**Quality before publish:** CD image publishing must depend on the quality/test job. Failed tests block release.

CI jobs: **Lint and test** (ruff + pytest with fakes only), **Dependency audit**
(`pip-audit`) and **Docker Compose smoke test** (build, `compose up --wait`, curl `/ready`,
`/api/v1/system/telemetry` and the Streamlit health endpoint, run `fedtone backup` in the
container, check the image holds no `.env` or data). CD calls the CI workflow first, then
pushes `ghcr.io/<owner>/fedtone:<version>` and creates a GitHub Release.

```markdown
[![CI](https://github.com/M-Mashreghi/Fedtone/actions/workflows/ci.yml/badge.svg)](https://github.com/M-Mashreghi/Fedtone/actions/workflows/ci.yml)
```

---

## 2. GitHub Secrets & Permissions

Configure under **Settings → Secrets and variables → Actions**. Never commit real values.

| Name | Required | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | Automatic | GitHub Actions / GHCR permission where allowed. |
| `OPENAI_API_KEY` or provider equivalent | Only for optional live smoke | LLM provider access. Prefer fakes in normal CI. |
| `MARKET_DATA_API_KEY` | Optional | Paid/provider-specific market data. |
| `GHCR_TOKEN` | Optional | PAT if repository/org policy requires it. |
| `DEPLOY_HOST` | Optional deploy | Remote host name/IP. |
| `DEPLOY_USER` | Optional deploy | SSH user. |
| `DEPLOY_SSH_KEY` | Optional deploy | SSH private key. |
| `DEPLOY_PATH` | Optional deploy | Remote checkout/deploy directory. |

### Least Privilege

- CI: `contents: read`.
- CD quality: `contents: read`.
- CD publish: `contents: write`, `packages: write` only if releases/GHCR are used.
- Fork PRs must not receive production/provider secrets.
- Default CI uses mocked LLM and market providers.

### Branch Protection

CI runs on every pull request and push to `main`. To make a failing CI **block merges**, enable
it once in GitHub: *Settings → Branches → Add rule* for `main` → *Require status checks to pass*
→ select **Lint and test**, **Dependency audit** and **Docker Compose smoke test**. (This is a
repository setting; it cannot be committed as a file.)

---

## 3. Environment Configuration

Example `.env` shape:

```bash
# Implemented in Task 1.2 (see src/fedtone/settings.py and .env.example).
# Nested groups use a double underscore: FEDTONE_<GROUP>__<FIELD>.
FEDTONE_ENV=production
FEDTONE_LOG_LEVEL=INFO
FEDTONE_DATABASE__URL=sqlite:////app/data/fedtone.db

# LLM (API key and model are required when FEDTONE_ENV=production)
FEDTONE_LLM__MODEL=<MODEL_NAME>
FEDTONE_LLM__API_KEY=<REPLACE_ME>
FEDTONE_LLM__BASE_URL=<OPTIONAL>

# Scheduler
FEDTONE_SCHEDULER__ENABLED=true
FEDTONE_SCHEDULER__INGESTION_INTERVAL_MINUTES=360

# Market data
FEDTONE_MARKET_DATA__PROVIDER=<PROVIDER>
FEDTONE_MARKET_DATA__API_KEY=<OPTIONAL>
```

Host/port values for the API and Streamlit are passed to their servers at launch (Phase 6 Docker work).

**Never** bake `.env` into the Docker image.

---

## 4. Docker Packaging

Repository artifacts: `Dockerfile` (one image for every role, Python 3.12-slim, non-root
user `fedtone`, uid 10001), `docker/entrypoint.sh`, `docker-compose.yml`, `.dockerignore`
(excludes `.env*`, `data/`, databases, tests and docs from the build context) and
`.env.example`. Secrets are read from the environment at run time only.

The entrypoint takes a role: `api` (runs `alembic upgrade head`, then uvicorn on 8000), `ui`
(Streamlit on 8501), `scheduler` (migrates, then `fedtone schedule`), or any command
(`fedtone backup`, `fedtone ingest`, ...).

### Compose Services

| Service | Role | Health |
|---|---|---|
| `api` | Research API; applies migrations on start. | `GET /ready` |
| `ui` | Streamlit dashboard, talks to `http://api:8000`; starts after the API is healthy. | `GET /_stcore/health` |
| `scheduler` | Pipeline cycles every `FEDTONE_SCHEDULER__INGESTION_INTERVAL_MINUTES`. Opt-in profile: `docker compose --profile scheduler up -d`. Run only one. | — |

### Durable Storage

| Volume | Purpose |
|---|---|
| `fedtone-data` → `/app/data` | SQLite database (`fedtone.db`), `backups/`, optional `market/` CSV files. |

Raw source snapshots live inside SQLite. A named volume avoids host permission problems with the
non-root user; to use a host folder instead, mount `./data:/app/data` and
`chown 10001 ./data` first.

---

## 5. Local Production-Shaped Start

```bash
cp .env.example .env              # optional; fill in LLM / market-data settings
docker compose up -d --build --wait
docker compose ps                 # api and ui report "healthy"
curl -fsS http://localhost:8000/ready
docker compose --profile scheduler up -d   # optional: scheduled pipeline cycles
```

Dashboard: `http://localhost:8501`. API docs: `http://localhost:8000/docs`.

---

## 6. CI Quality Gates

`ci.yml` steps:

1. Checkout and Python 3.12.
2. `pip install -e ".[dev,dashboard]"` (no lock file yet; versions are lower-bounded in `pyproject.toml`).
3. `ruff check .` (includes the flake8-bandit `S` security rules) and `ruff format --check .`.
4. `pytest -q` — unit, integration, API, UI (Streamlit `AppTest`) and fake-provider E2E tests.
5. `pip-audit` over the installed runtime dependencies.
6. Docker Compose smoke test (see section 1).

A static type checker is not adopted yet.

### Required Test Policy

CI must **not** depend on live:

- Federal Reserve website,
- LLM provider,
- market-data provider.

Use fixtures/fakes for deterministic CI. Live smoke tests can be manual/nightly and opt-in.

---

## 7. Releasing a Version

```bash
# Ensure main is green
git checkout main
git pull

# Tag a release
git tag -a v0.1.0 -m "FedTone v0.1.0"
git push origin v0.1.0
```

CD may then:

1. Run quality gates.
2. Build Docker image.
3. Push `ghcr.io/<USER>/fedtone:v0.1.0`.
4. Push `latest` only according to repository policy.
5. Create a GitHub Release with generated notes.
6. Optionally deploy via SSH/webhook.

---

## 8. Production Host Update

Example pull/update path:

```bash
cd /opt/fedtone
git pull

docker compose pull
docker compose up -d

curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/ready
```

If building from source on the host:

```bash
docker compose up -d --build
```

---

## 9. Database Backup Before Deploy

Before schema-changing releases:

```bash
docker compose run --rm api fedtone backup       # or: fedtone backup (without Docker)
```

The backup is taken with SQLite's online backup API, verified (integrity check, schema
revision, record counts) and pruned to `FEDTONE_BACKUP__RETENTION_COUNT`. Restore and drill
procedures: RUNBOOK sections 10 and 11.

---

## 10. Operator Checklist

- [ ] CI green on `main`.
- [ ] `.env` exists only on trusted host / secret store.
- [ ] LLM key configured and not visible in logs.
- [ ] Database backup completed before migration-heavy release.
- [ ] Docker services healthy.
- [ ] `/healthz` and `/ready` healthy.
- [ ] Streamlit loads the latest published meeting.
- [ ] A fixture or controlled analysis run succeeds after deploy.
- [ ] Audit metadata contains model/prompt/schema/source hash.

---

## 11. Troubleshooting

| Symptom | Check |
|---|---|
| API unhealthy | DB path/mount, migration, environment validation. |
| UI loads but no meetings | API base URL and ingestion/database state. |
| LLM 401/403 | Provider key/base URL/model permissions. |
| LLM schema failures | Model compatibility, prompt/schema version, structured-output mode. |
| Official source fetch fails | Network, adapter parser, source structure drift. |
| Market metrics missing | Provider range/symbol/calendar alignment. |
| Duplicate meetings | Canonical identity/hash uniqueness and scheduler idempotency. |
| GHCR push denied | Actions package permission/PAT/visibility. |

---

## Change Log

| Date | Change |
|---|---|
| 2026-10-13 | Initial FedTone deployment and CI/CD plan created. |
| 2026-11-03 | Phase 6: Dockerfile, Compose (api/ui/scheduler), CI (lint, tests, audit, Docker smoke), CD to GHCR, backup command. |
