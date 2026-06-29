"""Add snapshot overlay metadata to compliance events

Revision ID: 008_add_event_snapshot_overlay
Revises: 007_drop_rtsp_camera_fields
Create Date: 2026-06-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008_add_event_snapshot_overlay"
down_revision: Union[str, None] = "007_drop_rtsp_camera_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("compliance_events", sa.Column("snapshot_overlay", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("compliance_events", "snapshot_overlay")
