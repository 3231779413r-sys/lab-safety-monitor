from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String
from sqlalchemy.sql import func

from ..core.database import Base


def generate_uuid() -> str:
    return str(uuid4())


class VisitorRegistration(Base):
    __tablename__ = "visitor_registrations"

    id = Column(String, primary_key=True, default=generate_uuid)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    visiting_company = Column(String, nullable=False)
    total_people = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ExternalPersonnelRegistration(Base):
    __tablename__ = "external_personnel_registrations"

    id = Column(String, primary_key=True, default=generate_uuid)
    external_person_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    organization = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    visit_reason = Column(String, nullable=False)
    face_image = Column(LargeBinary, nullable=True)
    face_embedding = Column(LargeBinary, nullable=True)
    face_image_storage = Column(String, nullable=True)
    face_image_bucket = Column(String, nullable=True)
    face_image_object_key = Column(String, nullable=True)
    face_image_content_type = Column(String, nullable=True)
    face_image_size_bytes = Column(Integer, nullable=True)
    supervision_events = Column(String, nullable=True)
    allowed_camera_ids = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
