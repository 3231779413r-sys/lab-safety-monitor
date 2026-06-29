"""Add danger event types to compliance events

Revision ID: 005_add_danger_event_types
Revises: 004_add_minio_snapshot_fields
Create Date: 2026-05-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "005_add_danger_event_types"
down_revision: Union[str, None] = "004_add_minio_snapshot_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "compliance_events",
        sa.Column(
            "danger_event_types",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("compliance_events", "danger_event_types")
