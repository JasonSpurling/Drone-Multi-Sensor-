"""add ignored to track for operator alert suppression

Revision ID: c3f1a8d92e47
Revises: 7a2e9c14b8d3
Create Date: 2026-09-06 13:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3f1a8d92e47'
down_revision: str | Sequence[str] | None = '7a2e9c14b8d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('track') as batch_op:
        batch_op.add_column(sa.Column('ignored', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('track') as batch_op:
        batch_op.drop_column('ignored')
