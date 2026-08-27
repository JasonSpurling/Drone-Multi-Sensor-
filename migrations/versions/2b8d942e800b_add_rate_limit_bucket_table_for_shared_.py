"""add rate_limit_bucket table for shared multi-replica rate limiting

Revision ID: 2b8d942e800b
Revises: 4fe93e96f08f
Create Date: 2026-08-24 20:09:37.417202

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2b8d942e800b'
down_revision: str | Sequence[str] | None = '4fe93e96f08f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rate_limit_bucket',
        sa.Column('key', sa.String(length=150), nullable=False),
        sa.Column('tokens', sa.Float(), nullable=False),
        sa.Column('last_refill', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('rate_limit_bucket')
