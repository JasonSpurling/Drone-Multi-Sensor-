"""add camera_stream_url to sensor_registry for live view proxying

Revision ID: 7a2e9c14b8d3
Revises: 2b8d942e800b
Create Date: 2026-09-06 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7a2e9c14b8d3'
down_revision: str | Sequence[str] | None = '2b8d942e800b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('sensor_registry') as batch_op:
        batch_op.add_column(sa.Column('camera_stream_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sensor_registry') as batch_op:
        batch_op.drop_column('camera_stream_url')
