#!/usr/bin/env python3
"""Extract violation snapshot JPGs from local SQL + MinIO backups."""

from __future__ import annotations

import csv
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_BACKUP = ROOT / "backup" / "sentinelvision.sql"
MINIO_BACKUP = ROOT / "backup" / "minio_data.tar.gz"
OUTPUT_DIR = ROOT / "test" / "images"

COPY_PREFIX = 'COPY public.compliance_events (id, person_id, track_id, "timestamp", video_source, camera_id, frame_number, detected_ppe, missing_ppe, action_violations, danger_event_types, is_violation, detection_confidence, snapshot_path, snapshot_storage, snapshot_bucket, snapshot_object_key, snapshot_content_type, snapshot_size_bytes, start_frame, end_frame, end_timestamp, duration_frames, is_ongoing, person_name, camera_ids, camera_name, video_path, video_storage, video_bucket, video_object_key, video_content_type, video_size_bytes, snapshot_overlay) FROM stdin;'


@dataclass(frozen=True)
class EventSnapshot:
    event_id: str
    timestamp: str
    camera_id: str
    object_key: str


def parse_event_snapshots() -> list[EventSnapshot]:
    snapshots: list[EventSnapshot] = []
    in_copy = False

    with SQL_BACKUP.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not in_copy:
                if line == COPY_PREFIX:
                    in_copy = True
                continue

            if line == r"\.":
                break

            row = next(csv.reader([line], delimiter="\t"))
            if len(row) != 34:
                continue

            event_id = row[0]
            timestamp = row[3]
            camera_id = row[5]
            is_violation = row[11] == "t"
            snapshot_storage = row[14]
            snapshot_bucket = row[15]
            snapshot_object_key = row[16]

            if not is_violation:
                continue
            if snapshot_storage != "minio":
                continue
            if snapshot_bucket != "lab-safety-monitor":
                continue
            if not snapshot_object_key or snapshot_object_key == r"\N":
                continue

            snapshots.append(
                EventSnapshot(
                    event_id=event_id,
                    timestamp=timestamp,
                    camera_id=camera_id,
                    object_key=snapshot_object_key,
                )
            )

    return snapshots


def build_part_index(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    part_index: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        if not member.isfile():
            continue
        if not member.name.endswith("/part.1"):
            continue

        prefix, _, _ = member.name.rpartition("/")
        object_dir, _, _ = prefix.rpartition("/")
        if object_dir.startswith("./"):
            object_dir = object_dir[2:]
        if not object_dir.startswith("lab-safety-monitor/"):
            continue

        object_key = object_dir.removeprefix("lab-safety-monitor/")
        part_index.setdefault(object_key, member)

    return part_index


def sanitize_timestamp(timestamp: str) -> str:
    return (
        timestamp.replace(" ", "_")
        .replace(":", "-")
        .replace("/", "-")
    )


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = parse_event_snapshots()

    extracted = 0
    missing = 0

    with tarfile.open(MINIO_BACKUP, "r:gz") as archive:
        part_index = build_part_index(archive)

        for snapshot in snapshots:
            member = part_index.get(snapshot.object_key)
            if member is None:
                missing += 1
                continue

            payload = archive.extractfile(member)
            if payload is None:
                missing += 1
                continue

            filename = (
                f"{sanitize_timestamp(snapshot.timestamp)}"
                f"__{snapshot.camera_id}__{snapshot.event_id}.jpg"
            )
            output_path = OUTPUT_DIR / filename
            output_path.write_bytes(payload.read())
            extracted += 1

    print(f"parsed={len(snapshots)} extracted={extracted} missing={missing} output={OUTPUT_DIR}")
    return 0 if extracted else 1


if __name__ == "__main__":
    raise SystemExit(main())
