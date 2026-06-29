"""Initial migration with PostgreSQL

Revision ID: 001_initial
Revises:
Create Date: 2026-04-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create persons table
    op.create_table(
        'persons',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('face_embedding', sa.LargeBinary(), nullable=True),
        sa.Column('thumbnail', sa.LargeBinary(), nullable=True),
        sa.Column('first_seen', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_seen', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('total_events', sa.Integer(), nullable=True),
        sa.Column('violation_count', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create compliance_events table
    op.create_table(
        'compliance_events',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('person_id', sa.String(), nullable=True),
        sa.Column('track_id', sa.Integer(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('video_source', sa.String(), nullable=True),
        sa.Column('frame_number', sa.Integer(), nullable=True),
        sa.Column('detected_ppe', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('missing_ppe', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('action_violations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_violation', sa.Boolean(), nullable=True),
        sa.Column('detection_confidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('snapshot_path', sa.String(), nullable=True),
        sa.Column('start_frame', sa.Integer(), nullable=True),
        sa.Column('end_frame', sa.Integer(), nullable=True),
        sa.Column('end_timestamp', sa.DateTime(), nullable=True),
        sa.Column('duration_frames', sa.Integer(), nullable=True),
        sa.Column('is_ongoing', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create video_sources table
    op.create_table(
        'video_sources',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('source_type', sa.String(), nullable=False),
        sa.Column('path', sa.String(), nullable=True),
        sa.Column('vendor', sa.String(), nullable=True),
        sa.Column('protocol', sa.String(), nullable=True),
        sa.Column('host', sa.String(), nullable=True),
        sa.Column('port', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('password', sa.String(), nullable=True),
        sa.Column('channel', sa.Integer(), nullable=True),
        sa.Column('stream_type', sa.String(), nullable=True),
        sa.Column('rtsp_path', sa.String(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=True),
        sa.Column('last_test_status', sa.String(), nullable=True),
        sa.Column('last_test_error', sa.String(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('total_frames', sa.Integer(), nullable=True),
        sa.Column('processed_frames', sa.Integer(), nullable=True),
        sa.Column('total_violations', sa.Integer(), nullable=True),
        sa.Column('total_persons_detected', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('video_resolution', sa.String(), nullable=True),
        sa.Column('frame_rate', sa.Integer(), nullable=True),
        sa.Column('max_bitrate', sa.Integer(), nullable=True),
        sa.Column('video_encoding', sa.String(), nullable=True),
        sa.Column('transport_mode', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('video_sources')
    op.drop_table('compliance_events')
    op.drop_table('persons')
