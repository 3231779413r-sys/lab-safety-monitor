from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String
from sqlalchemy.sql import func

from ..core.database import Base


def generate_uuid() -> str:
    return str(uuid4())


class ExternalPerson(Base):
    __tablename__ = "external_persons"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    organization = Column(String, nullable=False)
    supervision_scope = Column(String, nullable=True)
    allowed_camera_ids = Column(String, nullable=True)
    face_embedding = Column(LargeBinary, nullable=True)
    thumbnail = Column(LargeBinary, nullable=True)
    face_image_storage = Column(String, nullable=True)
    face_image_bucket = Column(String, nullable=True)
    face_image_object_key = Column(String, nullable=True)
    face_image_content_type = Column(String, nullable=True)
    face_image_size_bytes = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
