import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session
from app.models.external_person import ExternalPerson
from app.models.person import Person
from app.models.supervision import ExternalPersonnelRegistration
from app.services.face_registry_service import get_face_registry_service
from app.services.object_storage import get_object_storage


@dataclass
class RebuildStats:
    scanned: int = 0
    rebuilt: int = 0
    skipped: int = 0
    failed: int = 0


async def _rebuild_model_embeddings(model_cls, label: str, stats: RebuildStats) -> None:
    storage = get_object_storage()
    registry = get_face_registry_service()

    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(model_cls).where(
                        model_cls.face_image_storage == "minio",
                        model_cls.face_image_bucket.is_not(None),
                        model_cls.face_image_object_key.is_not(None),
                    )
                )
            ).scalars().all()
        )

        for row in rows:
            stats.scanned += 1
            object_key = getattr(row, "face_image_object_key", None)
            bucket = getattr(row, "face_image_bucket", None)
            if not object_key or not bucket:
                stats.skipped += 1
                continue

            response = None
            try:
                response = storage.client.get_object(bucket, object_key)
                payload = response.read()
                if not payload:
                    raise ValueError("empty face image payload")

                embedding, thumbnail = registry.recognizer.extract_embedding_from_image_bytes(payload)
                row.face_embedding = registry.recognizer.serialize_embedding(embedding)
                if hasattr(row, "thumbnail"):
                    row.thumbnail = thumbnail
                stats.rebuilt += 1
                print(f"[rebuilt] {label}: {row.id}")
            except Exception as exc:
                stats.failed += 1
                print(f"[failed] {label}: {getattr(row, 'id', '<unknown>')} -> {exc}")
            finally:
                if response is not None:
                    response.close()
                    response.release_conn()

        await session.commit()


async def main() -> None:
    stats = RebuildStats()
    await _rebuild_model_embeddings(Person, "employee", stats)
    await _rebuild_model_embeddings(ExternalPerson, "external_person", stats)
    await _rebuild_model_embeddings(
        ExternalPersonnelRegistration, "external_registration", stats
    )
    print(
        "done",
        {
            "scanned": stats.scanned,
            "rebuilt": stats.rebuilt,
            "skipped": stats.skipped,
            "failed": stats.failed,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
