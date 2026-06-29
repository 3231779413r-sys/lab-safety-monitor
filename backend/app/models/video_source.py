from sqlalchemy import Column, String, DateTime, Integer, Boolean
from sqlalchemy.sql import func
from uuid import uuid4
from ..core.database import Base


def generate_uuid():
    return str(uuid4())


class VideoSource(Base):
    """
    Tracks video sources processed by the system.

    This model stores metadata about uploaded videos, webcam sessions,
    or camera streams that have been processed for PPE detection.
    """

    __tablename__ = "video_sources"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)  # Display name (e.g., "Lab Camera 1")
    floor = Column(String, nullable=True)
    name_suffix = Column(String, nullable=True)
    source_type = Column(String, nullable=False, default="camera")  # "camera", "uploaded", "webcam"
    path = Column(String, nullable=True)  # File path or stream URL
    vendor = Column(String, nullable=True, default="hikvision")
    host = Column(String, nullable=True)
    port = Column(Integer, nullable=True, default=8000)
    username = Column(String, nullable=True)
    password = Column(String, nullable=True)
    channel = Column(Integer, nullable=True, default=1)
    stream_type = Column(String, nullable=True, default="main")
    enabled = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    last_test_status = Column(String, nullable=True, default="unknown")
    last_test_error = Column(String, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    # Processing stats
    total_frames = Column(Integer, default=0)
    processed_frames = Column(Integer, default=0)
    total_violations = Column(Integer, default=0)
    total_persons_detected = Column(Integer, default=0)

    # Processing status
    status = Column(
        String, default="pending"
    )  # "pending", "processing", "completed", "failed"

    # Video quality / encoding settings (fetched from Hikvision SDK)
    video_resolution = Column(String, nullable=True)  # e.g. "1920x1080"
    frame_rate = Column(Integer, nullable=True)      # e.g. 25
    max_bitrate = Column(Integer, nullable=True)     # kbps, e.g. 4096
    video_encoding = Column(String, nullable=True)  # "H.265" / "H.264"
    transport_mode = Column(String, nullable=True)  # "UDP" / "TCP"
    camera_detection_scope = Column(String, nullable=True)
    backend_detection_scope = Column(String, nullable=True)
    area_overcapacity_polygon = Column(String, nullable=True)
    area_overcapacity_limit = Column(Integer, nullable=True)
    is_patrol_area = Column(Boolean, nullable=False, default=False)
    last_patrol_at = Column(DateTime, nullable=True)
    last_patrol_person_id = Column(String, nullable=True)
    last_patrol_person_name = Column(String, nullable=True)
    last_patrol_evaluated_window_end = Column(DateTime, nullable=True)
