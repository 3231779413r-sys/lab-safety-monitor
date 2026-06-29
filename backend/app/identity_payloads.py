"""Helpers for serializing identity enrichment tasks and results."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

import numpy as np

from .core.config import settings
from .telemetry import clone_telemetry


def encode_float32_array(values: np.ndarray | None) -> str | None:
    if values is None:
        return None
    array = np.asarray(values, dtype=np.float32)
    return base64.b64encode(array.tobytes()).decode("ascii")


def decode_float32_array(data: str | None) -> np.ndarray | None:
    if not data:
        return None
    raw = base64.b64decode(data.encode("ascii"))
    return np.frombuffer(raw, dtype=np.float32).copy()


def encode_bytes(data: bytes | None) -> str | None:
    if not data:
        return None
    return base64.b64encode(data).decode("ascii")


def decode_bytes(data: str | None) -> bytes | None:
    if not data:
        return None
    return base64.b64decode(data.encode("ascii"))


def build_identity_task_metadata(
    *,
    request_id: str,
    camera_id: str,
    video_source: str,
    frame_path: str | None,
    frame_jpeg: bytes | None,
    persons: list[dict[str, Any]],
    result_queue: str | None = None,
    submitted_at: datetime | None = None,
    face_detection_requested: bool = True,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    serialized_persons: list[dict[str, Any]] = []
    for index, person in enumerate(persons):
        box = person.get("box") or [0, 0, 0, 0]
        serialized_persons.append(
            {
                "person_index": index,
                "track_id": person.get("track_id"),
                "detector_track_id": person.get("detector_track_id"),
                "raw_track_id": person.get("raw_track_id"),
                "box": [float(value) for value in box],
                "crop_jpeg_b64": encode_bytes(person.get("crop_jpeg")),
                "crop_score": float(person.get("crop_score", 0.0) or 0.0),
                "identity_source_hint": person.get("identity_source"),
            }
        )
    return {
        "request_id": request_id,
        "camera_id": camera_id,
        "video_source": video_source,
        "frame_path": frame_path or "",
        "frame_jpeg_b64": encode_bytes(frame_jpeg),
        "result_queue": result_queue or settings.RABBITMQ_IDENTITY_RESULT_QUEUE,
        "submitted_at": (submitted_at or datetime.now()).isoformat(),
        "face_detection_requested": bool(face_detection_requested),
        "persons": serialized_persons,
        "telemetry": clone_telemetry(telemetry),
    }


def build_identity_task_message(
    *,
    request_id: str,
    camera_id: str,
    video_source: str,
    frame_path: str | None,
    frame_jpeg: bytes | None,
    persons: list[dict[str, Any]],
    result_queue: str | None = None,
    submitted_at: datetime | None = None,
    face_detection_requested: bool = True,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_identity_task_metadata(
        request_id=request_id,
        camera_id=camera_id,
        video_source=video_source,
        frame_path=frame_path,
        frame_jpeg=frame_jpeg,
        persons=persons,
        result_queue=result_queue,
        submitted_at=submitted_at,
        face_detection_requested=face_detection_requested,
        telemetry=telemetry,
    )


def parse_identity_task_message(payload: dict[str, Any]) -> dict[str, Any]:
    persons: list[dict[str, Any]] = []
    for person in list(payload.get("persons") or []):
        parsed_person = dict(person or {})
        parsed_person["crop_jpeg"] = decode_bytes(parsed_person.get("crop_jpeg_b64"))
        persons.append(parsed_person)
    return {
        "request_id": str(payload.get("request_id") or ""),
        "camera_id": str(payload.get("camera_id") or ""),
        "video_source": str(payload.get("video_source") or ""),
        "frame_path": str(payload.get("frame_path") or ""),
        "frame_jpeg": decode_bytes(payload.get("frame_jpeg_b64")),
        "result_queue": str(payload.get("result_queue") or settings.RABBITMQ_IDENTITY_RESULT_QUEUE),
        "submitted_at": payload.get("submitted_at"),
        "face_detection_requested": bool(payload.get("face_detection_requested", True)),
        "telemetry": clone_telemetry(payload.get("telemetry")),
        "persons": persons,
    }


def build_identity_result_payload(
    *,
    request_id: str,
    camera_id: str,
    video_source: str,
    appearance_features: list[np.ndarray | None],
    detected_faces: list[dict[str, Any]],
    person_face_identities: list[dict[str, Any]] | None = None,
    result_queue: str | None = None,
    submitted_at: str | None = None,
    completed_at: datetime | None = None,
    error: str | None = None,
    error_type: str | None = None,
    error_detail: str | None = None,
    frame_path: str | None = None,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    serialized_faces: list[dict[str, Any]] = []
    for face in detected_faces:
        serialized_faces.append(
            {
                "frame_box": [float(value) for value in face.get("frame_box") or []],
                "embedding_b64": encode_float32_array(face.get("embedding")),
                "thumbnail_b64": encode_bytes(face.get("thumbnail")),
                "score": float(face.get("score", 0.0)),
            }
        )
    serialized_person_face_identities: list[dict[str, Any]] = []
    for identity in person_face_identities or []:
        serialized_person_face_identities.append(
            {
                "track_id": identity.get("track_id"),
                "detector_track_id": identity.get("detector_track_id"),
                "raw_track_id": identity.get("raw_track_id"),
                "person_id": identity.get("person_id"),
                "person_name": identity.get("person_name"),
                "face_matched": bool(identity.get("face_matched")),
                "identity_source": identity.get("identity_source"),
                "face_observed_this_frame": bool(identity.get("face_observed_this_frame")),
                "face_confirmed_this_frame": bool(identity.get("face_confirmed_this_frame")),
                "subject_type": identity.get("subject_type"),
                "subject_supervision_scope": list(identity.get("subject_supervision_scope") or []),
                "allowed_camera_ids": list(identity.get("allowed_camera_ids") or []),
                "appointment_start": identity.get("appointment_start"),
                "appointment_end": identity.get("appointment_end"),
                "external_person_id": identity.get("external_person_id"),
                "face_embedding_b64": encode_float32_array(identity.get("face_embedding")),
                "thumbnail_b64": encode_bytes(identity.get("thumbnail")),
            }
        )
    payload: dict[str, Any] = {
        "request_id": request_id,
        "camera_id": camera_id,
        "video_source": video_source,
        "frame_path": frame_path or "",
        "result_queue": result_queue or settings.RABBITMQ_IDENTITY_RESULT_QUEUE,
        "appearance_features_b64": [encode_float32_array(item) for item in appearance_features],
        "detected_faces": serialized_faces,
        "person_face_identities": serialized_person_face_identities,
        "completed_at": (completed_at or datetime.now()).isoformat(),
        "telemetry": clone_telemetry(telemetry),
    }
    if submitted_at:
        payload["submitted_at"] = submitted_at
    if error:
        payload["error"] = error
    if error_type:
        payload["error_type"] = error_type
    if error_detail:
        payload["error_detail"] = error_detail
    return payload


def parse_identity_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": str(payload.get("request_id") or ""),
        "camera_id": str(payload.get("camera_id") or ""),
        "video_source": str(payload.get("video_source") or ""),
        "frame_path": str(payload.get("frame_path") or ""),
        "submitted_at": payload.get("submitted_at"),
        "completed_at": payload.get("completed_at"),
        "error": payload.get("error"),
        "error_type": payload.get("error_type"),
        "error_detail": payload.get("error_detail"),
        "telemetry": clone_telemetry(payload.get("telemetry")),
        "appearance_features": [
            decode_float32_array(item) for item in list(payload.get("appearance_features_b64") or [])
        ],
        "detected_faces": [
            {
                "frame_box": [float(value) for value in face.get("frame_box") or []],
                "embedding": decode_float32_array(face.get("embedding_b64")),
                "thumbnail": decode_bytes(face.get("thumbnail_b64")),
                "score": float(face.get("score", 0.0)),
            }
            for face in list(payload.get("detected_faces") or [])
        ],
        "person_face_identities": [
            {
                "track_id": identity.get("track_id"),
                "detector_track_id": identity.get("detector_track_id"),
                "raw_track_id": identity.get("raw_track_id"),
                "person_id": identity.get("person_id"),
                "person_name": identity.get("person_name"),
                "face_matched": bool(identity.get("face_matched")),
                "identity_source": identity.get("identity_source"),
                "face_observed_this_frame": bool(identity.get("face_observed_this_frame")),
                "face_confirmed_this_frame": bool(identity.get("face_confirmed_this_frame")),
                "subject_type": identity.get("subject_type"),
                "subject_supervision_scope": list(identity.get("subject_supervision_scope") or []),
                "allowed_camera_ids": list(identity.get("allowed_camera_ids") or []),
                "appointment_start": identity.get("appointment_start"),
                "appointment_end": identity.get("appointment_end"),
                "external_person_id": identity.get("external_person_id"),
                "face_embedding": decode_float32_array(identity.get("face_embedding_b64")),
                "thumbnail": decode_bytes(identity.get("thumbnail_b64")),
            }
            for identity in list(payload.get("person_face_identities") or [])
        ],
    }
