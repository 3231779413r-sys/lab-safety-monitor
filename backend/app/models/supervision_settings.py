from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.sql import func

from ..core.database import Base


def generate_uuid() -> str:
    return str(uuid4())


class SupervisionSettings(Base):
    __tablename__ = "supervision_settings"

    id = Column(String, primary_key=True, default=generate_uuid)
    other_person_scope = Column(String, nullable=True)
    area_missed_inspection_enabled = Column(Integer, nullable=False, default=0)
    area_missed_inspection_interval_hours = Column(Float, nullable=True)
    area_missed_inspection_start_time = Column(String, nullable=True)
    area_missed_inspection_camera_ids = Column(String, nullable=True)
    blind_spot_stay_enabled = Column(Integer, nullable=False, default=0)
    blind_spot_stay_threshold_seconds = Column(Integer, nullable=True)
    workshop_overcapacity_enabled = Column(Integer, nullable=False, default=0)
    workshop_overcapacity_limit = Column(Integer, nullable=True)
    alert_cooldown_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
