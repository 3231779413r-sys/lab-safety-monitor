"""Add person management fields and shift schedules

Revision ID: 006_add_person_management_fields
Revises: 005_add_danger_event_types
Create Date: 2026-05-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_add_person_management_fields"
down_revision: Union[str, None] = "005_add_danger_event_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("persons", sa.Column("workshop", sa.String(), nullable=True))
    op.add_column("persons", sa.Column("job_title", sa.String(), nullable=True))
    op.add_column("persons", sa.Column("supervision_scope", sa.String(), nullable=True))

    op.create_table(
        "shift_schedules",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("shift_date", sa.Date(), nullable=False),
        sa.Column("day_person_id", sa.String(), nullable=True),
        sa.Column("night_person_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shift_date"),
    )


def downgrade() -> None:
    op.drop_table("shift_schedules")
    op.drop_column("persons", "supervision_scope")
    op.drop_column("persons", "job_title")
    op.drop_column("persons", "workshop")
