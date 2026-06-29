from sqlalchemy import Boolean, Column, DateTime, Integer, LargeBinary, String
from sqlalchemy.sql import func
from uuid import uuid4
from ..core.database import Base


def generate_uuid():
    return str(uuid4())


class Person(Base):
    __tablename__ = "persons"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=True)
    is_employee = Column(Boolean, nullable=False, default=False)
    workshop = Column(String, nullable=True)
    job_title = Column(String, nullable=True)
    supervision_scope = Column(String, nullable=True)
    face_embedding = Column(LargeBinary, nullable=True)  # Stored as bytes
    thumbnail = Column(LargeBinary, nullable=True)
    face_image_storage = Column(String, nullable=True)
    face_image_bucket = Column(String, nullable=True)
    face_image_object_key = Column(String, nullable=True)
    face_image_content_type = Column(String, nullable=True)
    face_image_size_bytes = Column(Integer, nullable=True)
    first_seen = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now(), onupdate=func.now())
    total_events = Column(Integer, default=0)
    violation_count = Column(Integer, default=0)

    @property
    def compliance_rate(self) -> float:
        """
        Calculate individual compliance rate for this person.

        Returns:
            Percentage of events where the person was compliant (0-100).
            100.0 if no events recorded (benefit of the doubt).

        Example:
            - 80 compliant events + 20 violation events = 80% compliance
            - 0 events = 100% compliance (no violations recorded)
        """
        if self.total_events == 0:
            return 100.0
        return ((self.total_events - self.violation_count) / self.total_events) * 100
