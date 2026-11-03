"""SQLite online backup, verification, retention and restore.

Backups use SQLite's online backup API, so they are consistent even while the API or the
scheduler is writing. Every backup is checked with ``PRAGMA integrity_check`` and must carry
an Alembic revision. A restore first verifies the backup, then saves the current database as a
``pre-restore`` copy, and only then replaces it, so a bad restore can be undone.
"""

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import make_url

BACKUP_PREFIX = "fedtone-"
_BACKUP_NAME = re.compile(r"^fedtone-\d{8}T\d{6}\d*Z\.db$")
# Tables whose row counts identify the research history in a backup.
RECORD_TABLES = (
    "policy_documents",
    "document_status_changes",
    "document_snapshots",
    "document_texts",
    "tone_analyses",
    "document_comparisons",
    "market_observations",
    "event_metrics",
    "research_summaries",
    "review_items",
    "processing_runs",
)


class BackupError(RuntimeError):
    """The backup or restore could not be completed safely."""


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    schema_revision: str
    record_counts: dict[str, int]


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        raise BackupError("backups are supported for file-based SQLite databases only")
    return Path(url.database)


def _connect(path: Path, *, must_exist: bool = True) -> sqlite3.Connection:
    if must_exist and not path.is_file():
        raise BackupError(f"database file not found: {path}")
    return sqlite3.connect(path)


def verify_backup(path: Path) -> BackupInfo:
    """Integrity check plus schema revision and record counts; raises BackupError if unusable."""
    connection = _connect(path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError(f"integrity check failed for {path.name}: {result}")
        try:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"{path.name} has no schema revision") from exc
        if not revision:
            raise BackupError(f"{path.name} has no schema revision")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]  # noqa: S608
            for table in RECORD_TABLES
            if table in tables
        }
        return BackupInfo(path, revision[0], counts)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"{path.name} is not a readable SQLite database: {exc}") from exc
    finally:
        connection.close()


def _copy(source: Path, target: Path) -> None:
    src = _connect(source)
    dst = _connect(target, must_exist=False)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def backup_database(
    database_url: str,
    backup_dir: Path,
    *,
    retention_count: int = 7,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BackupInfo:
    """Write a verified snapshot to ``backup_dir`` and prune the oldest beyond retention."""
    if retention_count < 1:
        raise BackupError("retention must keep at least one backup")
    source = sqlite_path(database_url)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{BACKUP_PREFIX}{clock():%Y%m%dT%H%M%S%fZ}.db"
    if target.exists():
        raise BackupError(f"backup {target.name} already exists")
    _copy(source, target)
    try:
        info = verify_backup(target)
    except BackupError:
        target.unlink(missing_ok=True)
        raise
    for old in list_backups(backup_dir)[:-retention_count]:
        old.unlink()
    return info


def list_backups(backup_dir: Path) -> list[Path]:
    """Backups oldest first (names sort chronologically)."""
    if not backup_dir.is_dir():
        return []
    return sorted(p for p in backup_dir.iterdir() if _BACKUP_NAME.match(p.name))


def restore_database(
    backup_path: Path,
    database_url: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[BackupInfo, Path | None]:
    """Replace the database with a verified backup.

    Returns the restored backup's info and the path of the ``pre-restore`` copy of the
    database that was replaced (None if there was no database). Stop the API and the
    scheduler before restoring.
    """
    info = verify_backup(backup_path)
    target = sqlite_path(database_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    safety = None
    if target.exists():
        safety = target.with_name(f"{target.stem}.pre-restore-{clock():%Y%m%dT%H%M%S%fZ}.db")
        _copy(target, safety)
    _copy(backup_path, target)
    restored = verify_backup(target)
    if restored.record_counts != info.record_counts:
        raise BackupError("restored database does not match the backup's record counts")
    return restored, safety
