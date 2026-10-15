from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from fedtone import __version__
from fedtone.api.app import create_app
from fedtone.infrastructure.persistence.db import create_db_engine
from fedtone.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database={"url": f"sqlite:///{tmp_path / 'api.db'}"})


def test_healthz_returns_ok(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_reports_database_ok(settings: Settings) -> None:
    engine = create_db_engine(settings.database.url)
    client = TestClient(create_app(settings, engine=engine))

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}
    engine.dispose()


def test_ready_creates_engine_lazily_from_settings(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    assert client.get("/ready").status_code == 200


def test_ready_returns_503_when_database_unreachable(tmp_path: Path, settings: Settings) -> None:
    missing_dir = tmp_path / "does-not-exist" / "db.sqlite"
    broken_engine = create_engine(f"sqlite:///{missing_dir}")
    client = TestClient(create_app(settings, engine=broken_engine))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "unavailable"}}
    assert str(missing_dir) not in response.text
