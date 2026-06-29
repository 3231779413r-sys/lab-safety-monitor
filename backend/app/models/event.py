from sqlalchemy import (
    Column,
    String,
    DateTime,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    JSON,
)
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from uuid import uuid4
from ..core.database import Base

# Use JSON for SQLite/dev and JSONB only when running on PostgreSQL.
JSONType = JSON().with_variant(JSONB, "postgresql")


def generate_uuid():
    return str(uuid4())


class ComplianceEvent(Base):
    __tablename__ = "compliance_events"

    id = Column(String, primary_key=True, default=generate_uuid)
    person_id = Column(String, ForeignKey("persons.id"), nullable=True)
    person_name = Column(String, nullable=True)
    track_id = Column(Integer, nullable=True)
    timestamp = Column(DateTime, server_default=func.now())
    video_source = Column(String, nullable=True)
    camera_id = Column(String, nullable=True)  # 逻辑外键，关联摄像头
    camera_ids = Column(JSONType, default=list)
    camera_name = Column(String, nullable=True)
    frame_number = Column(Integer, default=0)

    # Detection results (JSONB for PostgreSQL, JSON for other databases)
    detected_ppe = Column(JSONType, default=list)  # ["goggles", "hardhat"]
    missing_ppe = Column(JSONType, default=list)  # ["mask", "safety_vest"]
    action_violations = Column(JSONType, default=list)  # ["fall_detected"]
    danger_event_types = Column(JSONType, default=list)  # ["hardhat", "mask"]

    # Violation info
    is_violation = Column(Boolean, default=False)

    # Confidence scores
    detection_confidence = Column(JSONType, default=dict)
    snapshot_overlay = Column(JSONType, nullable=True)

    # Optional snapshot
    snapshot_path = Column(String, nullable=True)
    snapshot_storage = Column(String, nullable=True)
    snapshot_bucket = Column(String, nullable=True)
    snapshot_object_key = Column(String, nullable=True)
    snapshot_content_type = Column(String, nullable=True)
    snapshot_size_bytes = Column(Integer, nullable=True)

    # Optional event video clip
    video_path = Column(String, nullable=True)
    video_storage = Column(String, nullable=True)
    video_bucket = Column(String, nullable=True)
    video_object_key = Column(String, nullable=True)
    video_content_type = Column(String, nullable=True)
    video_size_bytes = Column(Integer, nullable=True)

    # Event deduplication fields - track violation duration
    start_frame = Column(Integer, nullable=True)  # Frame when violation started
    end_frame = Column(Integer, nullable=True)  # Frame when violation ended (null if ongoing)
    end_timestamp = Column(DateTime, nullable=True)  # When violation ended
    duration_frames = Column(Integer, default=1)  # Total frames violation lasted
    is_ongoing = Column(Boolean, default=True)  # Whether violation is still active
