from sqlalchemy import Column, Date, DateTime, String
from sqlalchemy.sql import func
from uuid import uuid4

from ..core.database import Base


def generate_uuid():
    return str(uuid4())


class ShiftSchedule(Base):
    __tablename__ = "shift_schedules"

    id = Column(String, primary_key=True, default=generate_uuid)
    shift_date = Column(Date, nullable=False, unique=True)
    day_person_id = Column(String, nullable=True)
    night_person_id = Column(String, nullable=True)
    day_person_ids = Column(String, nullable=True)
    night_person_ids = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
