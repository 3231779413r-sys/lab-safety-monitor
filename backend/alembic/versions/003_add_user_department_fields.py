"""Add department fields to users

Revision ID: 003_add_user_department_fields
Revises: 002_add_camera_id
Create Date: 2026-04-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003_add_user_department_fields"
down_revision: Union[str, None] = "002_add_camera_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("department", sa.String(length=100), nullable=True, server_default="碳化车间"),
    )
    op.add_column("users", sa.Column("job_title", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("responsibilities", sa.String(length=500), nullable=True))
    op.alter_column("users", "department", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "responsibilities")
    op.drop_column("users", "job_title")
    op.drop_column("users", "department")
