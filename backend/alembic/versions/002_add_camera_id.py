"""Add camera_id to compliance_events

Revision ID: 002_add_camera_id
Revises: 001_initial
Create Date: 2026-04-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_add_camera_id'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('compliance_events', sa.Column('camera_id', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('compliance_events', 'camera_id')
