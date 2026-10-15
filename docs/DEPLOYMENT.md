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

Recommended badges after workflows exist:

```markdown
[![CI](https://github.com/<USER>/fedtone/actions/workflows/ci.yml/badge.svg)](https://github.com/<USER>/fedtone/actions/workflows/ci.yml)
[![CD](https://github.com/<USER>/fedtone/actions/workflows/cd.yml/badge.svg)](https://github.com/<USER>/fedtone/actions/workflows/cd.yml)
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

Require CI to pass before merge to `main`.

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

Recommended repository artifacts:

```text
Dockerfile
Dockerfile.ui            # optional; may share one image initially
docker-compose.yml
.env.example
.dockerignore
```

### Suggested Compose Services

| Service | Role |
|---|---|
| `api` | FastAPI application + ingestion/analysis commands. |
| `ui` | Streamlit dashboard. |
| `scheduler` | Optional separate scheduler process after MVP; may live in API initially. |

For the simplest v1 deployment, API and scheduler may share one container/process model, while Streamlit is a second service.

### Durable Mount

| Mount | Purpose |
|---|---|
| `./data` → `/app/data` | SQLite database + local backups. |

Raw source snapshots may be stored in SQLite or a documented data subdirectory. If files are used, their retention/backups must be explicit.

---

## 5. Local Production-Shaped Start

```bash
cp .env.example .env
mkdir -p data

docker compose build
docker compose up -d

docker compose ps
curl -fsS http://localhost:8000/healthz
```

Open the Streamlit UI at the configured port, typically:

```text
http://localhost:8501
```

---

## 6. CI Quality Gates

Suggested `ci.yml` steps:

1. Checkout.
2. Setup Python 3.12.
3. Install locked dependencies.
4. `ruff check .`
5. `ruff format --check .`
6. type check (`mypy`/`pyright` if adopted).
7. `pytest -q --cov=src/fedtone`.
8. dependency/security audit.
9. optional Docker build smoke.

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
mkdir -p data/backups
sqlite3 data/fedtone.db ".backup 'data/backups/fedtone-predeploy.db'"
```

In production automation, use the backup service/runbook rather than relying only on manual commands.

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
