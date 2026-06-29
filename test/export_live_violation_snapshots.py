#!/usr/bin/env python3
"""Export live violation snapshots from PostgreSQL + MinIO."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from minio import Minio


OUTPUT_DIR = Path("/data/exported_violation_snapshots")


def sanitize_timestamp(timestamp: str) -> str:
    return timestamp.replace(" ", "_").replace(":", "-").replace("/", "-")


async def fetch_rows(database_url: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(database_url.replace("+asyncpg", ""))
    try:
        return await conn.fetch(
            """
            select id, camera_id, "timestamp", snapshot_bucket, snapshot_object_key
            from compliance_events
            where is_violation = true
              and snapshot_bucket is not null
              and snapshot_object_key is not null
            order by "timestamp" asc
            """
        )
    finally:
        await conn.close()


async def main() -> int:
    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = await fetch_rows(os.environ["DATABASE_URL"])
    client = Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
    )

    downloaded = 0
    failed = 0
    for row in rows:
        timestamp = sanitize_timestamp(str(row["timestamp"]))
        camera_id = row["camera_id"] or "unknown_camera"
        event_id = row["id"]
        bucket = row["snapshot_bucket"]
        object_key = row["snapshot_object_key"]
        target = output_dir / f"{timestamp}__{camera_id}__{event_id}.jpg"
        if target.exists():
            continue

        try:
            client.fget_object(bucket, object_key, str(target))
            downloaded += 1
        except Exception:
            failed += 1

    print(
        f"records={len(rows)} downloaded={downloaded} failed={failed} output={output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
