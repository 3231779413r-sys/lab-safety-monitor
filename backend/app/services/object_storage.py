from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional

import cv2
from minio import Minio

from ..core.config import settings


@dataclass
class StoredObject:
    bucket: str
    object_key: str
    content_type: str
    size_bytes: int
    url: Optional[str] = None


class MinioStorage:
    """Small wrapper around MinIO for violation snapshot objects."""

    def __init__(self):
        self.bucket = settings.MINIO_BUCKET
        self.video_bucket = settings.MINIO_VIDEO_BUCKET
        self.face_bucket = settings.MINIO_FACE_BUCKET
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._checked_buckets: set[str] = set()

    def ensure_bucket(self, bucket: Optional[str] = None) -> None:
        target_bucket = bucket or self.bucket
        if target_bucket in self._checked_buckets:
            return
        if not self.client.bucket_exists(target_bucket):
            self.client.make_bucket(target_bucket)
        self._checked_buckets.add(target_bucket)

    def build_snapshot_key(
        self,
        *,
        event_id: str,
        timestamp: datetime,
        camera_id: Optional[str],
    ) -> str:
        env = settings.APP_ENV.strip("/") or "dev"
        camera = (camera_id or "unknown_camera").strip("/") or "unknown_camera"
        return (
            f"snapshots/{env}/{timestamp:%Y/%m/%d}/"
            f"{camera}/{event_id}.jpg"
        )

    def build_video_key(
        self,
        *,
        event_id: str,
        timestamp: datetime,
        camera_id: Optional[str],
    ) -> str:
        env = settings.APP_ENV.strip("/") or "dev"
        camera = (camera_id or "unknown_camera").strip("/") or "unknown_camera"
        return (
            f"videos/{env}/{timestamp:%Y/%m/%d}/"
            f"{camera}/{event_id}.mp4"
        )

    def upload_jpeg_frame(
        self,
        frame,
        *,
        object_key: str,
        quality: int = 85,
    ) -> StoredObject:
        self.ensure_bucket(self.bucket)
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("Failed to encode snapshot frame as JPEG")

        payload = buffer.tobytes()
        self.client.put_object(
            self.bucket,
            object_key,
            BytesIO(payload),
            length=len(payload),
            content_type="image/jpeg",
        )
        return StoredObject(
            bucket=self.bucket,
            object_key=object_key,
            content_type="image/jpeg",
            size_bytes=len(payload),
        )

    def upload_file_path(
        self,
        file_path: str,
        *,
        object_key: str,
        content_type: str,
        bucket: Optional[str] = None,
    ) -> StoredObject:
        target_bucket = bucket or self.bucket
        self.ensure_bucket(target_bucket)
        self.client.fput_object(
            target_bucket,
            object_key,
            file_path,
            content_type=content_type,
        )
        stat = Path(file_path).stat()
        return StoredObject(
            bucket=target_bucket,
            object_key=object_key,
            content_type=content_type,
            size_bytes=stat.st_size,
        )

    def build_face_key(
        self,
        *,
        subject_type: str,
        subject_id: str,
        timestamp: datetime,
        filename: str,
    ) -> str:
        env = settings.APP_ENV.strip("/") or "dev"
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
        return f"faces/{env}/{subject_type}/{timestamp:%Y/%m/%d}/{subject_id}.{extension}"

    def upload_image_bytes(
        self,
        payload: bytes,
        *,
        object_key: str,
        content_type: str,
        bucket: Optional[str] = None,
    ) -> StoredObject:
        target_bucket = bucket or self.face_bucket
        self.ensure_bucket(target_bucket)
        self.client.put_object(
            target_bucket,
            object_key,
            BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
        return StoredObject(
            bucket=target_bucket,
            object_key=object_key,
            content_type=content_type,
            size_bytes=len(payload),
        )

    def presigned_url(self, bucket: str, object_key: str) -> str:
        expires = timedelta(seconds=settings.MINIO_PRESIGNED_EXPIRE_SECONDS)
        return self.client.presigned_get_object(bucket, object_key, expires=expires)


_storage: Optional[MinioStorage] = None


def get_object_storage() -> MinioStorage:
    global _storage
    if _storage is None:
        _storage = MinioStorage()
    return _storage
