"""Upload legacy local violation snapshots to MinIO.

Run from the backend directory after applying database migrations:
    uv run python scripts/migrate_snapshots_to_minio.py
"""

import asyncio
from datetime import datetime
from pathlib import Path

import cv2
from sqlalchemy import select

from app.core.database import async_session
from app.models.event import ComplianceEvent
from app.services.object_storage import get_object_storage


async def migrate() -> None:
    storage = get_object_storage()
    migrated = 0
    skipped = 0
    missing = 0

    async with async_session() as session:
        result = await session.execute(
            select(ComplianceEvent).where(
                ComplianceEvent.snapshot_path.isnot(None),
                ComplianceEvent.snapshot_object_key.is_(None),
            )
        )
        events = result.scalars().all()

        for event in events:
            path = Path(event.snapshot_path)
            if not path.is_file():
                missing += 1
                continue

            frame = cv2.imread(str(path))
            if frame is None:
                skipped += 1
                continue

            timestamp = event.timestamp or datetime.now()
            object_key = storage.build_snapshot_key(
                event_id=event.id,
                timestamp=timestamp,
                camera_id=event.camera_id,
            )
            stored = storage.upload_jpeg_frame(frame, object_key=object_key)

            event.snapshot_storage = "minio"
            event.snapshot_bucket = stored.bucket
            event.snapshot_object_key = stored.object_key
            event.snapshot_content_type = stored.content_type
            event.snapshot_size_bytes = stored.size_bytes
            migrated += 1

        await session.commit()

    print(
        f"Snapshot migration finished: migrated={migrated}, "
        f"missing_files={missing}, skipped={skipped}"
    )


if __name__ == "__main__":
    asyncio.run(migrate())
