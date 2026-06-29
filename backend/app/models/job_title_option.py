from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func
from uuid import uuid4

from ..core.database import Base


def generate_uuid():
    return str(uuid4())


class JobTitleOption(Base):
    __tablename__ = "job_title_options"

    id = Column(String, primary_key=True, default=generate_uuid)
    code = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
