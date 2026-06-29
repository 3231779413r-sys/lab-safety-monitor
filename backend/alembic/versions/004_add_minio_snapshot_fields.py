"""Add MinIO snapshot fields to compliance events

Revision ID: 004_add_minio_snapshot_fields
Revises: 003_add_user_department_fields
Create Date: 2026-05-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_add_minio_snapshot_fields"
down_revision: Union[str, None] = "003_add_user_department_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("compliance_events", sa.Column("snapshot_storage", sa.String(), nullable=True))
    op.add_column("compliance_events", sa.Column("snapshot_bucket", sa.String(), nullable=True))
    op.add_column("compliance_events", sa.Column("snapshot_object_key", sa.String(), nullable=True))
    op.add_column("compliance_events", sa.Column("snapshot_content_type", sa.String(), nullable=True))
    op.add_column("compliance_events", sa.Column("snapshot_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("compliance_events", "snapshot_size_bytes")
    op.drop_column("compliance_events", "snapshot_content_type")
    op.drop_column("compliance_events", "snapshot_object_key")
    op.drop_column("compliance_events", "snapshot_bucket")
    op.drop_column("compliance_events", "snapshot_storage")
