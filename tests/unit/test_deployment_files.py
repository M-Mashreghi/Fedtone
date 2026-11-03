"""Static checks of the Docker and CI files (the Docker build itself runs in CI)."""

import fnmatch
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]


def dockerignore_patterns() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def ignored(path: str) -> bool:
    result = False
    for pattern in dockerignore_patterns():
        negate = pattern.startswith("!")
        pattern = pattern.removeprefix("!").rstrip("/")
        if fnmatch.fnmatch(path, pattern) or path.startswith(pattern + "/"):
            result = not negate
    return result


@pytest.mark.parametrize(
    "path", [".env", ".env.production", "data/fedtone.db", "x.db", ".git/HEAD"]
)
def test_secrets_and_local_state_never_reach_the_build_context(path: str) -> None:
    assert ignored(path)


def test_dockerfile_copies_only_what_the_runtime_needs() -> None:
    copies = [
        line
        for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY")
    ]
    assert copies
    for line in copies:
        assert ".env" not in line
        assert not line.startswith("COPY . ")
    assert "USER fedtone" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_entrypoint_uses_lf_line_endings() -> None:
    assert b"\r\n" not in (ROOT / "docker" / "entrypoint.sh").read_bytes()


def test_compose_services_have_healthchecks_and_shared_volume() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"api", "ui", "scheduler"} <= set(services)
    for name in ("api", "ui"):
        assert services[name]["healthcheck"]["test"]
    assert services["ui"]["environment"]["FEDTONE_UI__API_BASE_URL"] == "http://api:8000"
    assert services["ui"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["scheduler"]["profiles"] == ["scheduler"]
    assert "fedtone-data" in compose["volumes"]


def test_ci_runs_lint_tests_audit_and_docker_smoke() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = " ".join(step.get("run", "") for job in ci["jobs"].values() for step in job["steps"])
    for command in (
        "ruff check",
        "ruff format --check",
        "pytest",
        "pip-audit",
        "docker compose up",
    ):
        assert command in steps
    assert ci["permissions"] == {"contents": "read"}


def test_cd_publishes_only_after_quality_gates() -> None:
    cd = yaml.safe_load((ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8"))
    assert cd["jobs"]["publish"]["needs"] == "quality"
    assert cd["jobs"]["quality"]["uses"] == "./.github/workflows/ci.yml"
