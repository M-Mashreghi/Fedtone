# ruff: noqa: F811 - pytest fixtures are imported and requested by name
"""Backup → restore drill: research records survive a lost database (Task 6.5)."""

from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fedtone import cli
from fedtone.api.app import create_app
from fedtone.infrastructure.persistence.backup import (
    BackupError,
    backup_database,
    list_backups,
    restore_database,
    sqlite_path,
    verify_backup,
)
from fedtone.infrastructure.persistence.db import create_db_engine
from fedtone.settings import Settings, get_settings
from tests.api.conftest import harness  # noqa: F401


def ticking_clock():
    ticks = count()
    return lambda: datetime(2026, 11, 3, tzinfo=UTC) + timedelta(minutes=next(ticks))


def snapshot_api(database_url: str) -> dict:
    engine = create_db_engine(database_url)
    try:
        client = TestClient(create_app(Settings(database={"url": database_url}), engine=engine))
        points = client.get("/api/v1/timeline").json()["points"]
        details = {
            p["document_id"]: client.get(f"/api/v1/documents/{p['document_id']}").json()
            for p in points
        }
    finally:
        engine.dispose()
    return {"points": points, "details": details}


def test_backup_of_live_database_restores_known_analysis_records(
    harness,
    database_url,
    migrated_engine,
    tmp_path,  # noqa: F811
) -> None:
    before = snapshot_api(database_url)
    # The API engine stays connected while the backup runs (online backup).
    with migrated_engine.connect():
        info = backup_database(database_url, tmp_path / "backups", clock=ticking_clock())
    assert info.record_counts["policy_documents"] == 3
    assert info.record_counts["tone_analyses"] == 3
    assert info.record_counts["event_metrics"] > 0
    assert info.record_counts["research_summaries"] == 3

    migrated_engine.dispose()
    sqlite_path(database_url).unlink()  # the host lost the database
    restored, safety = restore_database(info.path, database_url)

    assert safety is None
    assert restored.record_counts == info.record_counts
    after = snapshot_api(database_url)
    assert after == before  # same scores, evidence, metrics, summaries and audit metadata


def test_restore_keeps_a_copy_of_the_replaced_database(
    harness,
    database_url,
    migrated_engine,
    tmp_path,  # noqa: F811
) -> None:
    info = backup_database(database_url, tmp_path / "backups", clock=ticking_clock())
    migrated_engine.dispose()

    restored, safety = restore_database(info.path, database_url, clock=ticking_clock())

    assert safety is not None and safety.is_file()
    assert verify_backup(safety).record_counts == restored.record_counts


def test_retention_keeps_newest_backups(harness, database_url, tmp_path) -> None:  # noqa: F811
    clock, directory = ticking_clock(), tmp_path / "backups"

    paths = [
        backup_database(database_url, directory, retention_count=2, clock=clock).path
        for _ in range(4)
    ]

    assert list_backups(directory) == paths[-2:]


def test_corrupt_backup_is_rejected_and_database_untouched(
    harness,
    database_url,
    migrated_engine,
    tmp_path,  # noqa: F811
) -> None:
    bad = tmp_path / "fedtone-20260101T000000Z.db"
    bad.write_bytes(b"not a database at all" * 100)
    migrated_engine.dispose()
    original = sqlite_path(database_url).read_bytes()

    with pytest.raises(BackupError):
        restore_database(bad, database_url)

    assert sqlite_path(database_url).read_bytes() == original


def test_unmigrated_database_is_not_a_valid_backup(tmp_path) -> None:
    import sqlite3

    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()

    with pytest.raises(BackupError, match="schema revision"):
        verify_backup(empty)


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "postgresql://h/db"])
def test_only_file_sqlite_is_supported(url: str, tmp_path: Path) -> None:
    with pytest.raises(BackupError):
        backup_database(url, tmp_path)


def test_backup_and_restore_commands(
    harness,
    database_url,
    migrated_engine,
    tmp_path,
    monkeypatch,
    capsys,  # noqa: F811
) -> None:
    monkeypatch.setenv("FEDTONE_DATABASE__URL", database_url)
    monkeypatch.setenv("FEDTONE_BACKUP__DIRECTORY", str(tmp_path / "backups"))
    get_settings.cache_clear()
    try:
        assert cli.main(["backup"]) == 0
        out = capsys.readouterr().out
        assert "policy_documents=3" in out
        (path,) = list_backups(tmp_path / "backups")

        assert cli.main(["backup", "--verify", str(path)]) == 0
        assert cli.main(["restore", str(path)]) == 2
        assert "--yes" in capsys.readouterr().err

        migrated_engine.dispose()
        assert cli.main(["restore", str(path), "--yes"]) == 0
        assert "previous database kept at" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
