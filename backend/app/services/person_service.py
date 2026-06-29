from typing import Optional, Tuple, List
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.person import Person
from ..ml.face_recognition import FaceRecognizer
from ..core.config import settings


class PersonService:
    """Service for creating and updating person records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_embeddings(self) -> List[Tuple[str, bytes]]:
        """Fetch all stored person embeddings."""
        result = await self.session.execute(
            select(Person.id, Person.face_embedding).where(
                Person.face_embedding.isnot(None)
            )
        )
        return result.all()

    async def get_person(self, person_id: str) -> Optional[Person]:
        """Get a person by ID."""
        return await self.session.get(Person, person_id)

    async def get_or_create_person(
        self,
        person_id: str,
        embedding: Optional[object],
        name: Optional[str] = None,
        thumbnail: Optional[bytes] = None,
    ) -> Person:
        """Get or create a person record, updating embedding if provided."""
        person = await self.get_person(person_id)

        embedding_bytes = None
        if embedding is not None:
            embedding_bytes = FaceRecognizer.serialize_embedding(embedding)

        if person is None:
            person = Person(
                id=person_id,
                name=name,
                is_employee=False,
                face_embedding=embedding_bytes,
                thumbnail=thumbnail,
                first_seen=datetime.now(),
                last_seen=datetime.now(),
                total_events=0,
                violation_count=0,
            )
            self.session.add(person)
        else:
            now = datetime.now()
            update_data = {}
            last_seen = person.last_seen
            should_touch_last_seen = (
                last_seen is None
                or now - last_seen
                >= timedelta(seconds=settings.PERSON_LAST_SEEN_UPDATE_SECONDS)
            )
            if should_touch_last_seen:
                update_data["last_seen"] = now
            # Employee profile names are edited in personnel management and
            # should not be overwritten by runtime face-match labels.
            if name and not person.is_employee:
                update_data["name"] = name
            # Preserve the enrolled face template once it exists. Runtime
            # recognition frames are often blurrier/partial and should not
            # drift the canonical face embedding used for future matching.
            if embedding_bytes is not None and person.face_embedding is None:
                update_data["face_embedding"] = embedding_bytes
            if thumbnail is not None and person.thumbnail is None:
                update_data["thumbnail"] = thumbnail

            if update_data:
                await self.session.execute(
                    update(Person).where(Person.id == person_id).values(**update_data)
                )

        return person

    async def increment_event_counts(self, person_id: str, is_violation: bool):
        """Increment total events and violations for a person."""
        person = await self.get_person(person_id)
        if person is None:
            return

        total_events = (person.total_events or 0) + 1
        violation_count = (person.violation_count or 0) + (1 if is_violation else 0)

        await self.session.execute(
            update(Person)
            .where(Person.id == person_id)
            .values(
                total_events=total_events,
                violation_count=violation_count,
                last_seen=datetime.now(),
            )
        )
