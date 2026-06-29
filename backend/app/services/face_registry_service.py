from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..ml.face_recognition import FaceRecognizer, get_face_recognizer
from ..services.object_storage import StoredObject, get_object_storage


@dataclass
class FaceRegistrationResult:
    embedding_bytes: bytes
    thumbnail_bytes: bytes
    stored_object: StoredObject


class FaceRegistryService:
    def __init__(self):
        self.recognizer: FaceRecognizer = get_face_recognizer()
        self.storage = get_object_storage()

    def register_face_image(
        self,
        content: bytes,
        *,
        subject_type: str,
        subject_id: str,
        filename: str,
        content_type: Optional[str] = None,
    ) -> FaceRegistrationResult:
        embedding, thumbnail_bytes = self.recognizer.extract_embedding_from_image_bytes(content)
        stored_object = self.storage.upload_image_bytes(
            content,
            object_key=self.storage.build_face_key(
                subject_type=subject_type,
                subject_id=subject_id,
                timestamp=datetime.now(),
                filename=filename,
            ),
            content_type=content_type or "image/jpeg",
        )
        return FaceRegistrationResult(
            embedding_bytes=FaceRecognizer.serialize_embedding(embedding),
            thumbnail_bytes=thumbnail_bytes,
            stored_object=stored_object,
        )


_service: Optional[FaceRegistryService] = None


def get_face_registry_service() -> FaceRegistryService:
    global _service
    if _service is None:
        _service = FaceRegistryService()
    return _service
