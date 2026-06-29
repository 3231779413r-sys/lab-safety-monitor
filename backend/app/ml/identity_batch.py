"""GPU-oriented identity enrichment for face embeddings and ReID features."""

from __future__ import annotations

import logging
from typing import Any, Optional

import cv2
import numpy as np

from ..core.config import settings
from ..identity_payloads import build_identity_result_payload
from .pipeline import DetectionPipeline
from .face_recognition import get_face_recognizer
from .osnet_reid import OSNetFeatureExtractor

logger = logging.getLogger(__name__)


class IdentityBatchEngine:
    def __init__(self) -> None:
        self.face_recognizer = get_face_recognizer()
        self.extractor: Optional[OSNetFeatureExtractor] = None
        self._face_identity_pipeline = DetectionPipeline()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self.face_recognizer.initialize()
        model_path = getattr(settings, "REID_MODEL_PATH", None)
        if model_path is not None:
            self.extractor = OSNetFeatureExtractor(
                model_name=getattr(settings, "REID_MODEL_NAME", "osnet_x1_0"),
                model_path=model_path,
                input_width=int(getattr(settings, "REID_INPUT_WIDTH", 128)),
                input_height=int(getattr(settings, "REID_INPUT_HEIGHT", 256)),
            )
        self._initialized = True
        logger.info("IdentityBatchEngine initialized")

    def enrich_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._initialized:
            self.initialize()

        crops: list[np.ndarray] = []
        crop_bindings: list[tuple[int, int]] = []
        face_results: list[list[dict[str, Any]]] = [[] for _ in tasks]
        appearance_results: list[list[Optional[np.ndarray]]] = [
            [None for _ in list(task.get("persons") or [])] for task in tasks
        ]
        person_face_identities: list[list[dict[str, Any]]] = [[] for _ in tasks]

        for task_index, task in enumerate(tasks):
            frame = task.get("frame")
            persons = list(task.get("persons") or [])
            if frame is not None and bool(task.get("face_detection_requested", True)):
                face_results[task_index] = self._detect_faces(frame, persons)
            for person_index, person in enumerate(persons):
                crop = self._resolve_person_crop(frame, person)
                crops.append(crop)
                crop_bindings.append((task_index, person_index))

        if self.extractor is not None and crops:
            extracted = self.extractor.extract_crops(crops)
            for (task_index, person_index), feature in zip(crop_bindings, extracted):
                appearance_results[task_index][person_index] = feature

        for task_index, task in enumerate(tasks):
            frame = task.get("frame")
            persons = list(task.get("persons") or [])
            if not persons:
                continue
            if frame is not None:
                person_face_identities[task_index] = self._match_person_faces(
                    frame=frame,
                    camera_id=str(task.get("camera_id") or ""),
                    video_source=str(task.get("video_source") or ""),
                    persons=persons,
                    detected_faces=face_results[task_index],
                )
            else:
                person_face_identities[task_index] = self._match_person_faces_from_crops(
                    camera_id=str(task.get("camera_id") or ""),
                    persons=persons,
                )

        payloads: list[dict[str, Any]] = []
        for task_index, task in enumerate(tasks):
            payloads.append(
                build_identity_result_payload(
                    request_id=str(task.get("request_id") or ""),
                    camera_id=str(task.get("camera_id") or ""),
                    video_source=str(task.get("video_source") or ""),
                    appearance_features=appearance_results[task_index],
                    detected_faces=face_results[task_index],
                    person_face_identities=person_face_identities[task_index],
                    result_queue=str(task.get("result_queue") or ""),
                    submitted_at=task.get("submitted_at"),
                    telemetry=task.get("telemetry"),
                )
            )
        return payloads

    def _match_person_faces(
        self,
        *,
        frame: np.ndarray,
        camera_id: str,
        video_source: str,
        persons: list[dict[str, Any]],
        detected_faces: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        pipeline = self._face_identity_pipeline
        pipeline.current_video_source = video_source
        pipeline.frame_count = 1
        pipeline._camera_config = {"camera_id": camera_id}
        pipeline._face_identity_cache.clear()
        pipeline._last_face_tracking_key_by_person.clear()
        working_persons = [dict(person) for person in persons]
        pipeline._attach_face_identities(
            frame,
            working_persons,
            detected_faces_override=detected_faces,
        )
        results: list[dict[str, Any]] = []
        for person in working_persons:
            results.append(
                {
                    "track_id": person.get("track_id"),
                    "detector_track_id": person.get("detector_track_id"),
                    "raw_track_id": person.get("detector_track_id", person.get("track_id")),
                    "person_id": person.get("person_id"),
                    "person_name": person.get("person_name"),
                    "face_matched": bool(person.get("face_matched")),
                    "identity_source": person.get("identity_source", "unknown"),
                    "face_observed_this_frame": bool(person.get("face_observed_this_frame")),
                    "face_confirmed_this_frame": bool(person.get("face_confirmed_this_frame")),
                    "subject_type": person.get("subject_type", "unknown"),
                    "subject_supervision_scope": list(person.get("subject_supervision_scope") or []),
                    "allowed_camera_ids": list(person.get("allowed_camera_ids") or []),
                    "appointment_start": person.get("appointment_start"),
                    "appointment_end": person.get("appointment_end"),
                    "external_person_id": person.get("external_person_id"),
                    "face_embedding": person.get("face_embedding"),
                    "thumbnail": person.get("thumbnail"),
                }
            )
        return results

    def _crop_person(self, frame: np.ndarray, box: list[float]) -> np.ndarray:
        if frame is None or frame.size == 0 or len(box) != 4:
            return np.empty((0, 0, 3), dtype=np.uint8)
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = [int(round(v)) for v in box]
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width, x2))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return np.empty((0, 0, 3), dtype=np.uint8)
        return frame[y1:y2, x1:x2].copy()

    def _resolve_person_crop(
        self,
        frame: np.ndarray | None,
        person: dict[str, Any],
    ) -> np.ndarray:
        crop_jpeg = person.get("crop_jpeg")
        if crop_jpeg:
            crop = cv2.imdecode(np.frombuffer(crop_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if crop is not None:
                return crop
        if frame is None:
            return np.empty((0, 0, 3), dtype=np.uint8)
        return self._crop_person(frame, person.get("box") or [])

    def _match_person_faces_from_crops(
        self,
        *,
        camera_id: str,
        persons: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        known_faces = self._face_identity_pipeline._known_faces
        if not known_faces:
            self._face_identity_pipeline._refresh_known_faces_cache()
            known_faces = self._face_identity_pipeline._known_faces
        for person in persons:
            crop = self._resolve_person_crop(None, person)
            raw_track_id = person.get("raw_track_id", person.get("detector_track_id", person.get("track_id")))
            unknown_id = f"unknown:{camera_id}:{raw_track_id}"
            identity_payload = {
                "track_id": person.get("track_id"),
                "detector_track_id": person.get("detector_track_id"),
                "raw_track_id": raw_track_id,
                "person_id": unknown_id,
                "person_name": "未知人员",
                "face_matched": False,
                "identity_source": "unknown",
                "face_observed_this_frame": False,
                "face_confirmed_this_frame": False,
                "subject_type": "unknown",
                "subject_supervision_scope": [],
                "allowed_camera_ids": [],
                "appointment_start": None,
                "appointment_end": None,
                "external_person_id": None,
                "face_embedding": None,
                "thumbnail": None,
            }
            if crop.size == 0:
                results.append(identity_payload)
                continue
            try:
                detections = self.face_recognizer.detect_faces(crop)
            except RuntimeError:
                detections = []
            if not detections:
                results.append(identity_payload)
                continue
            best_face = max(detections, key=lambda item: float(item.get("score", 0.0)))
            if float(best_face.get("score", 0.0)) < self.face_recognizer.min_detection_score:
                results.append(identity_payload)
                continue
            embedding = best_face.get("embedding")
            if embedding is None:
                results.append(identity_payload)
                continue
            matched_subject, best_score, _top_candidates, second_best_score = self._face_identity_pipeline._match_known_face(
                np.asarray(embedding, dtype=np.float32)
            )
            thumbnail_bytes = None
            ok, encoded = cv2.imencode(".jpg", crop)
            if ok:
                thumbnail_bytes = encoded.tobytes()
            identity_payload.update(
                {
                    "face_observed_this_frame": True,
                    "thumbnail": thumbnail_bytes,
                    "face_embedding": np.asarray(embedding, dtype=np.float32),
                }
            )
            if matched_subject and self.face_recognizer.is_strong_match(best_score, second_best_score):
                identity_payload.update(
                    {
                        "person_id": matched_subject.subject_id,
                        "person_name": matched_subject.subject_name,
                        "face_matched": True,
                        "identity_source": "face",
                        "face_confirmed_this_frame": True,
                        "subject_type": matched_subject.subject_type,
                        "subject_supervision_scope": list(matched_subject.supervision_scope),
                        "allowed_camera_ids": list(matched_subject.allowed_camera_ids),
                        "appointment_start": (
                            matched_subject.start_time.isoformat()
                            if matched_subject.start_time is not None
                            else None
                        ),
                        "appointment_end": (
                            matched_subject.end_time.isoformat()
                            if matched_subject.end_time is not None
                            else None
                        ),
                        "external_person_id": matched_subject.external_person_id,
                    }
                )
            results.append(identity_payload)
        return results

    def _detect_faces(self, frame: np.ndarray, persons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        detected_faces: list[dict[str, Any]] = []
        used_face_boxes: list[list[float]] = []
        try:
            detections = self.face_recognizer.detect_faces(frame)
        except RuntimeError:
            detections = []

        for face in sorted(detections, key=lambda item: float(item.get("score", 0.0)), reverse=True):
            embedding = face.get("embedding")
            face_box = face.get("box")
            if embedding is None or not face_box:
                continue
            if float(face.get("score", 0.0)) < self.face_recognizer.min_detection_score:
                continue
            frame_box = [float(v) for v in face_box]
            if any(self._box_iou(frame_box, used_box) > 0.7 for used_box in used_face_boxes):
                continue
            x1, y1, x2, y2 = [max(0, int(v)) for v in frame_box]
            if x2 <= x1 or y2 <= y1:
                continue
            face_crop = frame[y1:y2, x1:x2]
            thumbnail_bytes = None
            ok, encoded = cv2.imencode(".jpg", face_crop)
            if ok:
                thumbnail_bytes = encoded.tobytes()
            detected_faces.append(
                {
                    "frame_box": frame_box,
                    "embedding": np.asarray(embedding, dtype=np.float32),
                    "thumbnail": thumbnail_bytes,
                    "score": float(face.get("score", 0.0)),
                }
            )
            used_face_boxes.append(frame_box)

        if detected_faces:
            return detected_faces

        for person in persons:
            crop = self._crop_person(frame, person.get("box") or [])
            if crop.size == 0:
                continue
            crop_h, crop_w = crop.shape[:2]
            if min(crop_h, crop_w) < 48:
                continue
            try:
                fallback_detections = self.face_recognizer.detect_faces(crop)
            except RuntimeError:
                continue
            for face in fallback_detections:
                embedding = face.get("embedding")
                face_box = face.get("box")
                if embedding is None or not face_box:
                    continue
                if float(face.get("score", 0.0)) < self.face_recognizer.min_detection_score:
                    continue
                frame_box = self._face_box_to_frame(face_box, person.get("box") or [])
                if any(self._box_iou(frame_box, used_box) > 0.7 for used_box in used_face_boxes):
                    continue
                x1, y1, x2, y2 = [max(0, int(v)) for v in frame_box]
                face_crop = frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else crop
                thumbnail_bytes = None
                ok, encoded = cv2.imencode(".jpg", face_crop)
                if ok:
                    thumbnail_bytes = encoded.tobytes()
                detected_faces.append(
                    {
                        "frame_box": frame_box,
                        "embedding": np.asarray(embedding, dtype=np.float32),
                        "thumbnail": thumbnail_bytes,
                        "score": float(face.get("score", 0.0)),
                    }
                )
                used_face_boxes.append(frame_box)
        return detected_faces

    @staticmethod
    def _box_iou(box1: list[float], box2: list[float]) -> float:
        x1 = max(float(box1[0]), float(box2[0]))
        y1 = max(float(box1[1]), float(box2[1]))
        x2 = min(float(box1[2]), float(box2[2]))
        y2 = min(float(box1[3]), float(box2[3]))
        inter_w = max(0.0, x2 - x1)
        inter_h = max(0.0, y2 - y1)
        inter = inter_w * inter_h
        if inter <= 0.0:
            return 0.0
        area1 = max(0.0, float(box1[2]) - float(box1[0])) * max(0.0, float(box1[3]) - float(box1[1]))
        area2 = max(0.0, float(box2[2]) - float(box2[0])) * max(0.0, float(box2[3]) - float(box2[1]))
        union = area1 + area2 - inter
        if union <= 0.0:
            return 0.0
        return inter / union

    @staticmethod
    def _face_box_to_frame(face_box: list[float], person_box: list[float]) -> list[float]:
        px1, py1, _, _ = [float(v) for v in person_box]
        fx1, fy1, fx2, fy2 = [float(v) for v in face_box]
        return [px1 + fx1, py1 + fy1, px1 + fx2, py1 + fy2]


_identity_batch_engine: Optional[IdentityBatchEngine] = None


def get_identity_batch_engine() -> IdentityBatchEngine:
    global _identity_batch_engine
    if _identity_batch_engine is None:
        _identity_batch_engine = IdentityBatchEngine()
    return _identity_batch_engine
