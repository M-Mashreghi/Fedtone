"""Speaker of a document (speeches, Phase 7.3).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04 03:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("policy_documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("speaker", sa.String(length=128), nullable=True))
        batch_op.create_index("ix_policy_documents_speaker", ["speaker"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("policy_documents", schema=None) as batch_op:
        batch_op.drop_index("ix_policy_documents_speaker")
        batch_op.drop_column("speaker")
