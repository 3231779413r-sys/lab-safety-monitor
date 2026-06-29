from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.sql import func

from ..core.database import Base


def generate_uuid() -> str:
    return str(uuid4())


class InspectionWindowPatrolRecord(Base):
    __tablename__ = "inspection_window_patrol_records"
    __table_args__ = (
        UniqueConstraint("camera_id", "window_start", "window_end", name="uq_inspection_window_patrol_record"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    camera_id = Column(String, ForeignKey("video_sources.id"), nullable=False)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    first_patrol_at = Column(DateTime, nullable=True)
    last_patrol_at = Column(DateTime, nullable=True)
    person_id = Column(String, nullable=True)
    person_name = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
