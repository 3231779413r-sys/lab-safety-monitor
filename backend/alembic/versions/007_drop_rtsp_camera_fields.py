"""Drop legacy RTSP camera fields

Revision ID: 007_drop_rtsp_camera_fields
Revises: 006_add_person_management_fields
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007_drop_rtsp_camera_fields"
down_revision: Union[str, None] = "006_add_person_management_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("video_sources", "rtsp_path")
    op.drop_column("video_sources", "protocol")


def downgrade() -> None:
    op.add_column(
        "video_sources",
        sa.Column("protocol", sa.String(), nullable=True, server_default="hikvision_sdk"),
    )
    op.add_column(
        "video_sources",
        sa.Column("rtsp_path", sa.String(), nullable=True),
    )
