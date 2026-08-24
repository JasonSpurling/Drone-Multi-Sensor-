"""add related_track_id to incident for shadowing pair dedup

Revision ID: 4fe93e96f08f
Revises: 05a71b7465a5
Create Date: 2026-08-24 19:28:42.043033

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4fe93e96f08f'
down_revision: str | Sequence[str] | None = '05a71b7465a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('incident') as batch_op:
        batch_op.add_column(sa.Column('related_track_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_incident_related_track_id', 'track', ['related_track_id'], ['id']
        )
    op.create_index(
        'idx_incident_related_track_id', 'incident', ['related_track_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_incident_related_track_id', table_name='incident')
    with op.batch_alter_table('incident') as batch_op:
        batch_op.drop_constraint('fk_incident_related_track_id', type_='foreignkey')
        batch_op.drop_column('related_track_id')
