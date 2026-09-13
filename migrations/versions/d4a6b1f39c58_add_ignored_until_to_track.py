"""add ignored_until to track for timed operator alert suppression

Revision ID: d4a6b1f39c58
Revises: c3f1a8d92e47
Create Date: 2026-09-06 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4a6b1f39c58'
down_revision: str | Sequence[str] | None = 'c3f1a8d92e47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('track') as batch_op:
        batch_op.add_column(sa.Column('ignored_until', sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('track') as batch_op:
        batch_op.drop_column('ignored_until')
