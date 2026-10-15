from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from fedtone.infrastructure.persistence.db import create_db_engine
from fedtone.infrastructure.persistence.orm import Base
from tests.integration.conftest import alembic_config

PHASE_1_TABLES = {
    "document_snapshots",
    "document_texts",
    "policy_documents",
    "document_status_changes",
    "tone_analyses",
    "document_comparisons",
    "processing_runs",
    "review_items",
}
PHASE_4_TABLES = {"market_observations", "event_metrics", "research_summaries"}


def test_fresh_migration_creates_phase_1_tables(migrated_engine: Engine) -> None:
    tables = set(inspect(migrated_engine).get_table_names())

    assert PHASE_1_TABLES <= tables
    assert PHASE_4_TABLES <= tables
    assert "alembic_version" in tables


def test_policy_documents_indexes_and_canonical_constraint(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    indexes = {ix["name"]: ix["column_names"] for ix in inspector.get_indexes("policy_documents")}
    uniques = {
        uq["name"]: uq["column_names"]
        for uq in inspector.get_unique_constraints("policy_documents")
    }

    assert indexes["ix_policy_documents_status"] == ["status"]
    assert indexes["ix_policy_documents_bank_type_published"] == [
        "central_bank",
        "document_type",
        "published_at",
    ]
    assert uniques["uq_policy_documents_canonical_identity"] == [
        "central_bank",
        "document_type",
        "source_url",
    ]


def test_migrations_match_orm_models(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert diff == []


def test_downgrade_to_base_removes_tables(database_url: str) -> None:
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_db_engine(database_url)
    try:
        assert not PHASE_1_TABLES & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_engine_creates_missing_sqlite_directory(tmp_path) -> None:
    db_path = tmp_path / "nested" / "dir" / "fedtone.db"
    config = alembic_config(f"sqlite:///{db_path}")

    command.upgrade(config, "head")

    assert db_path.is_file()
