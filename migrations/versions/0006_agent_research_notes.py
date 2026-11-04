"""Agentic research notes (Phase 7.4).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agent_research_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("metric_version", sa.String(length=32), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["policy_documents.id"],
            name=op.f("fk_agent_research_notes_document_id_policy_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_research_notes")),
    )
    with op.batch_alter_table("agent_research_notes", schema=None) as batch_op:
        batch_op.create_index("ix_agent_research_notes_document_id", ["document_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent_research_notes", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_research_notes_document_id")
    op.drop_table("agent_research_notes")
