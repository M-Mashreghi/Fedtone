from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from fedtone import __version__
from fedtone.api.app import create_app
from fedtone.infrastructure.persistence.db import create_db_engine
from fedtone.settings import Settings
from tests.integration.conftest import alembic_config


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'api.db'}"


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(database={"url": database_url})


@pytest.fixture
def migrated(database_url: str) -> str:
    command.upgrade(alembic_config(database_url), "head")
    return database_url


def test_healthz_returns_ok(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_reports_database_schema_and_providers(settings: Settings, migrated) -> None:
    engine = create_db_engine(settings.database.url)
    client = TestClient(create_app(settings, engine=engine))

    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "database": "ok",
        "schema": "ok",
        "llm": "not_configured",
        "market_data": "not_configured",
        "scheduler": "disabled",
    }
    assert body["schema_revision"]
    engine.dispose()


def test_ready_creates_engine_lazily_from_settings(settings: Settings, migrated) -> None:
    client = TestClient(create_app(settings))

    assert client.get("/ready").status_code == 200


def test_ready_returns_503_for_unmigrated_database(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["schema"] == "missing"


def test_ready_returns_503_when_database_unreachable(tmp_path: Path, settings: Settings) -> None:
    missing_dir = tmp_path / "does-not-exist" / "db.sqlite"
    broken_engine = create_engine(f"sqlite:///{missing_dir}")
    client = TestClient(create_app(settings, engine=broken_engine))

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "unavailable"
    assert str(missing_dir) not in response.text


def test_ready_reports_configured_providers_without_secrets(
    tmp_path: Path, database_url: str, migrated
) -> None:
    fake_key = "sk-test-not-a-real-key-123456"
    settings = Settings(
        database={"url": database_url},
        llm={"api_key": fake_key, "model": "some-model"},
        market_data={"provider": "csv", "csv_directory": str(tmp_path / "nope")},
        scheduler={"enabled": True},
    )
    client = TestClient(create_app(settings))

    response = client.get("/ready")

    assert response.status_code == 200  # optional providers never fail readiness
    checks = response.json()["checks"]
    assert checks["llm"] == "configured"
    assert checks["market_data"] == "csv_directory_missing"
    assert checks["scheduler"] == "enabled"
    assert fake_key not in response.text
