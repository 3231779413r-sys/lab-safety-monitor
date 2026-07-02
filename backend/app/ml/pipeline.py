"""
主检测管道

协调检测、跟踪和时间过滤。
"""

import cv2
import numpy as np
import logging
import asyncio
import time
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Generator, Tuple
from datetime import datetime
from threading import Condition, Lock
from uuid import uuid4

from .detector_factory import get_detector
from .temporal_filter import get_temporal_filter
from .action_analyzer import get_pose_action_analyzer
from .pose_action_filter import get_pose_action_filter
from .mask_utils import draw_person_with_ppe, get_color, draw_label_badge
from ..core.config import settings
from ..core.danger_events import (
    canonicalize_danger_event_key,
    match_danger_event_types,
    normalize_violation_key,
)
from ..models.person import Person
from ..models.external_person import ExternalPerson
from ..models.supervision import ExternalPersonnelRegistration
from .face_recognition import FaceRecognizer, get_face_recognizer
from .osnet_reid import get_reid_service
from .reid_identity_resolver import ReIDIdentityResolver
from .tracker import DeepSORTTracker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

_postprocess_executor = ThreadPoolExecutor(
    max_workers=max(1, int(getattr(settings, "POSTPROCESS_THREAD_POOL_WORKERS", 4))),
    thread_name_prefix="postprocess",
)


@dataclass
class KnownFaceSubject:
    subject_id: str
    subject_name: str
    embedding: np.ndarray
    subject_type: str
    supervision_scope: list[str]
    allowed_camera_ids: list[str]
    external_person_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class DetectionPipeline:
    """
    协调检测和跟踪的主管道。

    流程：
    1. 按目标FPS采样帧
    2. 检测人员和PPE
    3. 将PPE与人员关联
    4. 应用时间过滤
    5. 生成合规事件
    """

    _shared_known_faces: List[KnownFaceSubject] = []
    _shared_known_faces_loaded_at: float = 0.0
    _shared_known_faces_lock: Lock = Lock()
    _shared_known_faces_condition: Condition = Condition(_shared_known_faces_lock)
    _shared_known_faces_refreshing: bool = False
    _shared_known_faces_refresh_executor: ThreadPoolExecutor | None = None

    def __init__(self):
        self.detector = get_detector()
        self.temporal_filter = get_temporal_filter()
        self.pose_action_analyzer = get_pose_action_analyzer()
        self.pose_action_filter = get_pose_action_filter()

        self.target_fps = settings.FRAME_SAMPLE_RATE
        self.frame_count = 0
        self.current_video_source: Optional[str] = None

        self.show_masks = getattr(settings, "SHOW_MASKS", True)
        self.mask_alpha = getattr(settings, "MASK_ALPHA", 0.4)
        self.use_confidence_fusion = (
            getattr(settings, "TEMPORAL_FUSION_STRATEGY", "ema") != "none"
        )
        self._camera_config: dict[str, Any] = {}
        self.face_recognizer = get_face_recognizer()
        self._known_faces: List[KnownFaceSubject] = []
        self._known_faces_loaded_at = 0.0
        self._known_faces_refresh_seconds = 30.0
        self._face_identity_cache: dict[str, dict[str, Any]] = {}
        self._face_identity_ttl_seconds = float(
            getattr(settings, "FACE_IDENTITY_CACHE_TTL_SECONDS", 8.0)
        )
        self._face_identity_refresh_interval_frames = max(
            1, int(getattr(settings, "FACE_IDENTITY_REFRESH_INTERVAL_FRAMES", 8))
        )
        self._face_detection_interval_frames = max(
            1, int(getattr(settings, "FACE_DETECTION_INTERVAL_FRAMES", 3))
        )
        self._face_retry_interval_frames = max(
            1, int(getattr(settings, "FACE_RETRY_INTERVAL_FRAMES", 10))
        )
        self._face_min_crop_size = 48
        self._last_face_detection_frame = 0
        self._next_stable_track_id = 1
        self._stable_track_ids_by_person: dict[str, int] = {}
        self._stable_track_ids_by_raw_tracking_key: dict[str, int] = {}
        self._last_face_tracking_key_by_person: dict[str, str] = {}
        self.reid_enabled = bool(getattr(settings, "REID_ENABLED", True))
        self.reid_service = get_reid_service() if self.reid_enabled else None
        self.reid_tracker = DeepSORTTracker()
        self.reid_identity_resolver = ReIDIdentityResolver(
            reid_enabled=self.reid_enabled,
            reid_service=self.reid_service,
            reid_tracker=self.reid_tracker,
            face_identity_cache=self._face_identity_cache,
            last_face_tracking_key_by_person=self._last_face_tracking_key_by_person,
            stable_track_id_resolver=lambda person_id, raw_tracking_key: self._resolve_stable_track_id(
                person_id=person_id,
                raw_tracking_key=raw_tracking_key,
            ),
        )

    def initialize(self):
        """初始化所有ML模型。"""
        self._ensure_known_faces_refresh_started()
        if (
            settings.INFERENCE_BACKEND.lower() == "queue"
            and settings.BACKEND_MODE == "worker"
        ):
            logger.info("Skipping heavy detector initialization in queue collector mode")
            return
        self.detector.initialize()

    @classmethod
    def _ensure_known_faces_refresh_started(cls) -> None:
        with cls._shared_known_faces_lock:
            if cls._shared_known_faces_refresh_executor is None:
                cls._shared_known_faces_refresh_executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="face-cache-refresh",
                )

    def set_camera_config(self, camera_config: Optional[dict[str, Any]]) -> None:
        """Attach runtime camera config for camera-specific rules."""
        self._camera_config = camera_config or {}

    def process_frame(
        self,
        frame: np.ndarray,
        video_source: str = "video",
        detections_override: Optional[Dict[str, Any]] = None,
        identity_observations_override: Optional[Dict[str, Any]] = None,
        annotate: bool = True,
    ) -> Dict[str, Any]:
        """通过管道处理单帧。"""
        # 视频更改时重置状态
        if video_source != self.current_video_source:
            self.current_video_source = video_source
            if hasattr(self.detector, "reset_video_state"):
                self.detector.reset_video_state()
            self.temporal_filter.clear_all()
            self._reset_reid_state()
            self.frame_count = 0

        self.frame_count += 1
        timestamp = datetime.now()

        result = {
            "frame_number": self.frame_count,
            "timestamp": timestamp.isoformat(),
            "persons": [],
            "violations": [],
            "events": [],
            "tracks": [],
            "action_violations": [],
            "pose_detections": [],
            "violation_detections": {},
            "annotated_frame": None,
        }

        # 检测人员和PPE
        detections = detections_override if detections_override is not None else self.detector.detect(frame)
        persons = detections.get("persons", [])
        ppe_detections = detections.get("ppe_detections", {})
        violation_detections = detections.get("violation_detections", {})
        action_violations = detections.get("action_violations", [])
        pose_detections = detections.get("pose_detections", [])
        result["action_violations"] = action_violations
        result["violation_detections"] = violation_detections
        result["pose_detections"] = pose_detections

        # 将PPE与人员关联
        persons = self._associate_detector_outputs(
            persons,
            ppe_detections,
            violation_detections,
            action_violations,
            pose_detections,
        )
        appearance_features_override = None
        detected_faces_override = None
        face_identities_override = None
        if identity_observations_override is not None:
            appearance_features_override = identity_observations_override.get("appearance_features")
            detected_faces_override = identity_observations_override.get("detected_faces")
            face_identities_override = identity_observations_override.get("person_face_identities")
        should_extract_reid_features = (
            appearance_features_override is not None
            or not bool(getattr(settings, "REID_EXTRACT_FEATURES_IN_MAIN_PATH", False))
        )
        self._attach_reid_tracks(
            frame,
            persons,
            appearance_features_override=appearance_features_override,
            extract_features=should_extract_reid_features,
        )
        self._attach_face_identities(
            frame,
            persons,
            detected_faces_override=detected_faces_override,
            face_identities_override=face_identities_override,
        )
        self.reid_identity_resolver.resolve_identities(
            persons,
            frame_count=self.frame_count,
            camera_id=str(self._camera_config.get("camera_id") or ""),
        )
        self._apply_area_overcapacity(persons, frame.shape[:2], action_violations)
        self._apply_workshop_overcapacity(persons, action_violations)

        pose_analyses = self._analyze_pose_actions_parallel(persons)

        # 处理每个人员
        for person_index, person in enumerate(persons):
            raw_track_id = person.get("track_id")
            if raw_track_id is None:
                raw_track_id = person.get("id", 0)
            stable_track_id = person.get("stable_track_id")
            track_id = stable_track_id if stable_track_id is not None else raw_track_id
            tracking_key = person.get("tracking_key") or f"track:{raw_track_id}"
            person_id = person.get("person_id") or (
                f"person_{track_id}"
                if track_id is not None
                else f"track_{person.get('id', 0)}"
            )
            person_name = person.get("person_name")

            pose_analysis = pose_analyses[person_index] if person_index < len(pose_analyses) else {
                "pose_status": "unknown",
                "pose_actions": [],
            }
            if getattr(settings, "POSE_ACTION_LABELS_ENABLED", True):
                pose_analysis["pose_actions"] = self.pose_action_filter.update(
                    tracking_key, pose_analysis.get("pose_actions", [])
                )

            person_result = {
                "person_id": person_id,
                "person_name": person_name,
                "tracking_key": tracking_key,
                "face_matched": bool(person.get("face_matched")),
                "subject_type": person.get("subject_type", "unknown"),
                "subject_supervision_scope": list(person.get("subject_supervision_scope") or []),
                "allowed_camera_ids": list(person.get("allowed_camera_ids") or []),
                "appointment_start": person.get("appointment_start"),
                "appointment_end": person.get("appointment_end"),
                "face_embedding": person.get("face_embedding"),
                "thumbnail": person.get("thumbnail"),
                "identity_source": person.get("identity_source", "unknown"),
                "reid_track_state": person.get("reid_track_state"),
                "reid_track_hits": person.get("reid_track_hits"),
                "reid_track_age": person.get("reid_track_age"),
                "detector_track_id": person.get("detector_track_id"),
                "track_id": track_id,
                "stable_track_id": stable_track_id,
                "raw_track_id": raw_track_id,
                "box": person.get("box", [0, 0, 0, 0]),
                "mask": person.get("mask"),
                "detected_ppe": person.get("detected_ppe", []),
                "missing_ppe": person.get("missing_ppe", []),
                "action_violations": person.get("action_violations", []),
                "pose": person.get("pose"),
                "pose_status": pose_analysis.get("pose_status", "unknown"),
                "pose_actions": pose_analysis.get("pose_actions", []),
                "detection_confidence": person.get("detection_confidence", {}),
                "ppe_detections": person.get("ppe_detections", []),
                "is_violation": person.get("is_violation", False),
            }
            self._apply_supervision_rules(person_result, action_violations, timestamp)

            # 应用时间过滤
            detection_confidence = person_result.get("detection_confidence", {})
            if self.use_confidence_fusion and detection_confidence:
                filter_result = self.temporal_filter.update_with_confidence(
                    tracking_key, detection_confidence
                )
                person_result["fused_confidence"] = filter_result.get(
                    "fused_confidence", {}
                )
            else:
                filter_result = self.temporal_filter.update(
                    tracking_key, person_result.get("missing_ppe", [])
                )

            person_result["stable_violation"] = filter_result["is_violation"]
            person_result["stable_missing_ppe"] = filter_result["stable_missing_ppe"]

            action_viols = person_result.get("action_violations", [])
            has_action_violation = len(action_viols) > 0

            # 如果检测到违规则生成事件
            if filter_result["is_violation"] or has_action_violation:
                all_violations = list(filter_result["stable_missing_ppe"])
                for av in action_viols:
                    all_violations.append(f"{av['action']} in lab")

                event = {
                    "id": str(uuid4()),
                    "person_id": person_id,
                    "track_id": track_id,
                    "raw_track_id": raw_track_id,
                    "timestamp": timestamp.isoformat(),
                    "video_source": video_source,
                    "frame_number": self.frame_count,
                    "detected_ppe": person_result.get("detected_ppe", []),
                    "missing_ppe": filter_result["stable_missing_ppe"],
                    "action_violations": [av["action"] for av in action_viols],
                    "is_violation": True,
                    "detection_confidence": person_result.get(
                        "detection_confidence", {}
                    ),
                    "fused_confidence": person_result.get("fused_confidence", {}),
                }
                result["events"].append(event)
                result["violations"].append(
                    {
                        "person_id": person_id,
                        "track_id": track_id,
                        "raw_track_id": raw_track_id,
                        "missing_ppe": filter_result["stable_missing_ppe"],
                        "action_violations": [av["action"] for av in action_viols],
                        "box": person_result.get("box"),
                    }
                )

            result["persons"].append(person_result)
            result["tracks"].append(
                {
                    "track_id": track_id,
                    "raw_track_id": raw_track_id,
                    "person_id": person_id,
                    "box": person_result.get("box"),
                }
            )

        if annotate:
            result["annotated_frame"] = self._annotate_frame(
                frame, result["persons"], violation_detections, action_violations
            )

        return result

    def _associate_detector_outputs(
        self,
        persons: List[Dict[str, Any]],
        ppe_detections: Dict[str, Any],
        violation_detections: Dict[str, Any],
        action_violations: List[Dict[str, Any]],
        pose_detections: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not persons:
            return persons

        ppe_persons_input = [dict(person) for person in persons]
        pose_persons_input = [dict(person) for person in persons]
        ppe_future = _postprocess_executor.submit(
            self.detector.associate_ppe_to_persons,
            ppe_persons_input,
            ppe_detections,
            violation_detections,
            action_violations,
        )
        pose_future = (
            _postprocess_executor.submit(
                self.detector.associate_pose_to_persons,
                pose_persons_input,
                pose_detections,
            )
            if hasattr(self.detector, "associate_pose_to_persons")
            else None
        )

        associated = ppe_future.result()
        if pose_future is None:
            return associated

        pose_persons = pose_future.result()
        for person, pose_person in zip(associated, pose_persons):
            if "pose" in pose_person:
                person["pose"] = pose_person.get("pose")
        return associated

    def _analyze_pose_actions_parallel(self, persons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not getattr(settings, "POSE_ACTION_LABELS_ENABLED", True) or not persons:
            return [{"pose_status": "unknown", "pose_actions": []} for _ in persons]

        futures = [
            _postprocess_executor.submit(self.pose_action_analyzer.analyze, person)
            for person in persons
        ]
        analyses: List[Dict[str, Any]] = []
        for future in futures:
            try:
                analyses.append(future.result())
            except Exception:
                logger.exception("Failed to analyze pose action")
                analyses.append({"pose_status": "unknown", "pose_actions": []})
        return analyses

    @classmethod
    async def _load_known_faces(cls) -> List[KnownFaceSubject]:
        known_faces: List[KnownFaceSubject] = []
        employee_count = 0
        external_count = 0
        registration_count = 0
        # DetectionPipeline runs inside camera worker threads. Using the shared
        # async engine/sessionmaker from a different event loop can corrupt the
        # asyncpg connection state and surface as "another operation is in
        # progress" on unrelated HTTP requests. Refresh the face cache through
        # a short-lived isolated engine instead.
        isolated_engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            poolclass=NullPool,
        )
        isolated_session = async_sessionmaker(
            isolated_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        try:
            async with isolated_session() as session:
                employee_rows = list(
                    (
                        await session.execute(
                            select(Person).where(
                                Person.face_embedding.isnot(None),
                                Person.is_employee == True,
                            )
                        )
                    ).scalars().all()
                )
                for person in employee_rows:
                    if person.face_embedding:
                        employee_count += 1
                        known_faces.append(
                            KnownFaceSubject(
                                subject_id=person.id,
                                subject_name=person.name or person.id,
                                embedding=FaceRecognizer.deserialize_embedding(person.face_embedding),
                                subject_type="employee",
                                supervision_scope=cls._parse_supervision_scope(getattr(person, "supervision_scope", None)),
                                allowed_camera_ids=[],
                            )
                        )

                external_rows = list(
                    (
                        await session.execute(
                            select(ExternalPerson).where(ExternalPerson.face_embedding.isnot(None))
                        )
                    ).scalars().all()
                )
                for person in external_rows:
                    if person.face_embedding:
                        external_count += 1
                        known_faces.append(
                            KnownFaceSubject(
                                subject_id=person.id,
                                subject_name=person.name or person.id,
                                embedding=FaceRecognizer.deserialize_embedding(person.face_embedding),
                                subject_type="external_person",
                                supervision_scope=cls._parse_supervision_scope(getattr(person, "supervision_scope", None)),
                                allowed_camera_ids=cls._parse_camera_ids(getattr(person, "allowed_camera_ids", None)),
                                external_person_id=person.id,
                            )
                        )

                registration_rows = list(
                    (
                        await session.execute(
                            select(ExternalPersonnelRegistration).where(
                                ExternalPersonnelRegistration.face_embedding.isnot(None)
                            )
                        )
                    ).scalars().all()
                )
                for person in registration_rows:
                    if person.face_embedding:
                        registration_count += 1
                        known_faces.append(
                            KnownFaceSubject(
                                subject_id=person.id,
                                subject_name=person.name or person.id,
                                embedding=FaceRecognizer.deserialize_embedding(person.face_embedding),
                                subject_type="external_registration",
                                supervision_scope=cls._parse_supervision_scope(getattr(person, "supervision_events", None)),
                                allowed_camera_ids=cls._parse_camera_ids(getattr(person, "allowed_camera_ids", None)),
                                external_person_id=getattr(person, "external_person_id", None),
                                start_time=getattr(person, "start_time", None),
                                end_time=getattr(person, "end_time", None),
                            )
                        )
        finally:
            await isolated_engine.dispose()
        logger.info(
            "[FaceRecognition] Loaded face library: employees=%s external=%s registrations=%s total=%s",
            employee_count,
            external_count,
            registration_count,
            len(known_faces),
        )
        return known_faces

    def _refresh_known_faces_cache(self) -> None:
        now = time.monotonic()
        cls = type(self)
        cache_age = now - cls._shared_known_faces_loaded_at
        if cls._shared_known_faces and cache_age < self._known_faces_refresh_seconds:
            self._known_faces = cls._shared_known_faces
            self._known_faces_loaded_at = cls._shared_known_faces_loaded_at
            return

        should_refresh = False
        with cls._shared_known_faces_lock:
            cache_age = now - cls._shared_known_faces_loaded_at
            if cls._shared_known_faces and cache_age < self._known_faces_refresh_seconds:
                self._known_faces = cls._shared_known_faces
                self._known_faces_loaded_at = cls._shared_known_faces_loaded_at
                return
            if cls._shared_known_faces_refreshing:
                if cls._shared_known_faces:
                    self._known_faces = cls._shared_known_faces
                    self._known_faces_loaded_at = cls._shared_known_faces_loaded_at
                    return
                if cls._shared_known_faces:
                    self._known_faces = cls._shared_known_faces
                    self._known_faces_loaded_at = cls._shared_known_faces_loaded_at
                return
            cls._shared_known_faces_refreshing = True
            should_refresh = True

        if not should_refresh:
            return

        def _refresh_task() -> None:
            refreshed_faces: Optional[List[KnownFaceSubject]] = None
            refresh_error: Optional[Exception] = None
            try:
                refreshed_faces = asyncio.run(cls._load_known_faces())
            except Exception as exc:
                refresh_error = exc

            with cls._shared_known_faces_lock:
                if refreshed_faces is not None:
                    cls._shared_known_faces = refreshed_faces
                    cls._shared_known_faces_loaded_at = time.monotonic()
                    preview = ", ".join(
                        f"{subject.subject_name}({subject.subject_id})"
                        for subject in cls._shared_known_faces[:5]
                    )
                    logger.info(
                        "[FaceRecognition] Refreshed shared face cache: total=%s threshold=%.3f preview=[%s]",
                        len(cls._shared_known_faces),
                        self.face_recognizer.threshold,
                        preview,
                    )
                elif refresh_error is not None:
                    logger.debug("Failed to refresh shared known face cache", exc_info=refresh_error)
                cls._shared_known_faces_refreshing = False
                cls._shared_known_faces_condition.notify_all()

        cls._ensure_known_faces_refresh_started()
        assert cls._shared_known_faces_refresh_executor is not None
        cls._shared_known_faces_refresh_executor.submit(_refresh_task)
        self._known_faces = cls._shared_known_faces
        self._known_faces_loaded_at = cls._shared_known_faces_loaded_at

    def _apply_supervision_rules(
        self,
        person_result: Dict[str, Any],
        frame_action_violations: List[Dict[str, Any]],
        timestamp: datetime,
    ) -> None:
        camera_scope = {
            canonicalize_danger_event_key(str(item))
            for item in (self._camera_config.get("camera_detection_scope") or [])
            if canonicalize_danger_event_key(str(item))
        }
        backend_scope = {
            canonicalize_danger_event_key(str(item))
            for item in (self._camera_config.get("backend_detection_scope") or [])
            if canonicalize_danger_event_key(str(item))
        }
        configured_scope = camera_scope.union(backend_scope)
        other_scope = {
            canonicalize_danger_event_key(str(item))
            for item in (self._camera_config.get("other_person_scope") or [])
            if canonicalize_danger_event_key(str(item))
        }
        global_event_types: set[str] = set()
        if "area_overcapacity" in configured_scope:
            global_event_types.add("area_overcapacity")
        if bool(self._camera_config.get("workshop_overcapacity_enabled")):
            global_event_types.add("workshop_overcapacity")

        if not configured_scope and not global_event_types:
            person_result["missing_ppe"] = []
            person_result["action_violations"] = []
            person_result["detection_confidence"] = {}
            person_result["is_violation"] = False
            return

        subject_type = str(person_result.get("subject_type") or "unknown")
        identity_scope = set(person_result.get("subject_supervision_scope") or [])
        allowed_camera_ids = set(person_result.get("allowed_camera_ids") or [])
        camera_id = self._camera_config.get("camera_id")
        forced_event_types: set[str] = set()

        if subject_type == "unknown":
            identity_scope = set(other_scope)
            if "unauthorized_intrusion" in identity_scope and "unauthorized_intrusion" in configured_scope:
                forced_event_types.add("unauthorized_intrusion")
        elif subject_type in {"external_person", "external_registration"}:
            if subject_type == "external_registration":
                appointment_start = self._parse_iso_datetime(person_result.get("appointment_start"))
                appointment_end = self._parse_iso_datetime(person_result.get("appointment_end"))
                appointment_active = (
                    appointment_start is not None
                    and appointment_end is not None
                    and appointment_start <= timestamp <= appointment_end
                )
                if not appointment_active:
                    identity_scope = {"unauthorized_intrusion"}
                    if "unauthorized_intrusion" in configured_scope:
                        forced_event_types.add("unauthorized_intrusion")
                elif (
                    "unauthorized_intrusion" in identity_scope
                    and allowed_camera_ids
                    and camera_id not in allowed_camera_ids
                    and "unauthorized_intrusion" in configured_scope
                ):
                    forced_event_types.add("unauthorized_intrusion")

        allowed_event_types = identity_scope.intersection(configured_scope)
        allowed_event_types.update(forced_event_types)
        allowed_event_types.update(global_event_types)

        filtered_missing_ppe: list[str] = []
        for item in person_result.get("missing_ppe", []):
            matched_types = match_danger_event_types([item])
            if matched_types and matched_types[0] in allowed_event_types:
                canonical_item = canonicalize_danger_event_key(item)
                if canonical_item:
                    filtered_missing_ppe.append(canonical_item)

        filtered_action_violations: list[Dict[str, Any]] = []
        seen_actions: set[str] = set()
        for action in person_result.get("action_violations", []):
            action_name = canonicalize_danger_event_key(str(action.get("action", "")))
            if action_name and action_name in allowed_event_types and action_name not in seen_actions:
                filtered_action_violations.append(action)
                seen_actions.add(action_name)

        for forced_event in sorted(forced_event_types):
            if forced_event in seen_actions:
                continue
            forced_action = {
                "action": forced_event,
                "score": 1.0,
                "box": person_result.get("box", [0, 0, 0, 0]),
            }
            filtered_action_violations.append(forced_action)
            seen_actions.add(forced_event)
            if not any(
                canonicalize_danger_event_key(str(item.get("action", ""))) == forced_event
                and item.get("box") == forced_action["box"]
                for item in frame_action_violations
            ):
                frame_action_violations.append(
                    {
                        "action": forced_event,
                        "class": forced_event,
                        "score": 1.0,
                        "box": forced_action["box"],
                    }
                )

        filtered_confidence: dict[str, Any] = {}
        for key, value in (person_result.get("detection_confidence", {}) or {}).items():
            matched_types = match_danger_event_types([key])
            if matched_types and matched_types[0] in allowed_event_types:
                filtered_confidence[key] = value

        person_result["missing_ppe"] = filtered_missing_ppe
        person_result["action_violations"] = filtered_action_violations
        person_result["detection_confidence"] = filtered_confidence
        person_result["is_violation"] = bool(filtered_missing_ppe or filtered_action_violations)

    def _apply_workshop_overcapacity(
        self,
        persons: List[Dict[str, Any]],
        frame_action_violations: List[Dict[str, Any]],
    ) -> None:
        if not bool(self._camera_config.get("workshop_overcapacity_enabled")):
            return

        limit = self._camera_config.get("workshop_overcapacity_limit")
        if not isinstance(limit, int) or limit < 0 or not persons:
            return

        total_person_count = self._camera_config.get("workshop_overcapacity_total_person_count")
        if isinstance(total_person_count, int) and total_person_count >= 0:
            compared_count = total_person_count
        else:
            compared_count = len(persons)

        if compared_count <= limit:
            return

        for person in persons:
            person_action_violations = person.setdefault("action_violations", [])
            if not any(
                item.get("action") == "workshop_overcapacity"
                for item in person_action_violations
            ):
                person_action_violations.append(
                    {
                        "action": "workshop_overcapacity",
                        "score": 1.0,
                    }
                )

            box = person.get("box", [0, 0, 0, 0])
            if not any(
                item.get("action") == "workshop_overcapacity" and item.get("box") == box
                for item in frame_action_violations
            ):
                frame_action_violations.append(
                    {
                        "action": "workshop_overcapacity",
                        "class": "workshop_overcapacity",
                        "score": 1.0,
                        "box": box,
                    }
                )
            person["is_violation"] = True

    @staticmethod
    def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_supervision_scope(value: Optional[str]) -> list[str]:
        if not value:
            return []
        try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    result: list[str] = []
                    for item in parsed:
                        normalized = canonicalize_danger_event_key(str(item))
                        if normalized and normalized not in result:
                            result.append(normalized)
                return result
        except json.JSONDecodeError:
            pass
        result: list[str] = []
        for item in value.split(","):
            normalized = canonicalize_danger_event_key(item)
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    @staticmethod
    def _parse_camera_ids(value: Optional[str]) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _crop_person_frame(frame: np.ndarray, person_box: List[float]) -> Optional[np.ndarray]:
        if frame is None or not isinstance(person_box, (list, tuple)) or len(person_box) != 4:
            return None
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in person_box]
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width, x2))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2].copy()

    @staticmethod
    def _box_iou(box_a: List[float], box_b: List[float]) -> float:
        ax1, ay1, ax2, ay2 = [float(v) for v in box_a]
        bx1, by1, bx2, by2 = [float(v) for v in box_b]
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        if inter_area <= 0:
            return 0.0
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        denom = area_a + area_b - inter_area
        if denom <= 0:
            return 0.0
        return inter_area / denom

    @staticmethod
    def _box_center(box: List[float]) -> Tuple[float, float]:
        x1, y1, x2, y2 = [float(v) for v in box]
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @staticmethod
    def _point_in_box(point: Tuple[float, float], box: List[float], padding: float = 0.0) -> bool:
        x, y = point
        x1, y1, x2, y2 = [float(v) for v in box]
        width = max(0.0, x2 - x1)
        height = max(0.0, y2 - y1)
        pad_x = width * padding
        pad_y = height * padding
        return (x1 - pad_x) <= x <= (x2 + pad_x) and (y1 - pad_y) <= y <= (y2 + pad_y)

    @staticmethod
    def _box_containment(inner_box: List[float], outer_box: List[float]) -> float:
        ix1, iy1, ix2, iy2 = [float(v) for v in inner_box]
        ox1, oy1, ox2, oy2 = [float(v) for v in outer_box]
        inter_x1 = max(ix1, ox1)
        inter_y1 = max(iy1, oy1)
        inter_x2 = min(ix2, ox2)
        inter_y2 = min(iy2, oy2)
        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        if inter_area <= 0:
            return 0.0
        inner_area = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        if inner_area <= 0:
            return 0.0
        return inter_area / inner_area

    @staticmethod
    def _face_box_to_frame(face_box: List[float], person_box: List[float]) -> List[float]:
        px1, py1, _px2, _py2 = [float(v) for v in person_box]
        fx1, fy1, fx2, fy2 = [float(v) for v in face_box]
        return [px1 + fx1, py1 + fy1, px1 + fx2, py1 + fy2]

    def _detect_frame_faces(self, frame: np.ndarray, persons: List[Dict[str, Any]]) -> List[dict[str, Any]]:
        detected_faces: List[dict[str, Any]] = []
        used_face_boxes: List[List[float]] = []

        try:
            detections = self.face_recognizer.detect_faces(frame)
        except RuntimeError:
            detections = []
        logger.debug(
            "[FaceRecognition] Frame %s camera=%s detected %s face(s) on full frame for %s person track(s)",
            self.frame_count,
            self._camera_config.get("camera_id") or self.current_video_source or "unknown",
            len(detections),
            len(persons),
        )

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
            logger.debug(
                "[FaceRecognition] Frame %s using %s deduplicated full-frame face(s)",
                self.frame_count,
                len(detected_faces),
            )
            return detected_faces

        fallback_faces = 0
        for person in persons:
            crop = self._crop_person_frame(frame, person.get("box", []))
            if crop is None or crop.size == 0:
                continue
            crop_h, crop_w = crop.shape[:2]
            if min(crop_h, crop_w) < self._face_min_crop_size:
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
                frame_box = self._face_box_to_frame(face_box, person.get("box", []))
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
                fallback_faces += 1

        logger.debug(
            "[FaceRecognition] Frame %s fell back to per-person face detection, found %s face(s)",
            self.frame_count,
            fallback_faces,
        )

        return detected_faces

    def _select_face_index_for_person(
        self,
        person: Dict[str, Any],
        detected_faces: List[dict[str, Any]],
    ) -> Optional[int]:
        best_index, _ = self._select_face_match_for_person(person, detected_faces)
        return best_index

    def _select_face_match_for_person(
        self,
        person: Dict[str, Any],
        detected_faces: List[dict[str, Any]],
    ) -> Tuple[Optional[int], float]:
        person_box = person.get("box", [])
        if not person_box or len(person_box) != 4:
            return None, 0.0

        best_index: Optional[int] = None
        best_score = 0.0
        for index, face in enumerate(detected_faces):
            frame_box = face.get("frame_box", [])
            if not frame_box:
                continue
            face_center = self._box_center(frame_box)
            center_inside = self._point_in_box(face_center, person_box, padding=0.08)
            containment = self._box_containment(frame_box, person_box)
            overlap = self._box_iou(person_box, frame_box)
            if not center_inside and containment < 0.6 and overlap <= 0.0:
                continue

            score = (
                (1.5 if center_inside else 0.0)
                + containment * 2.0
                + overlap
                + float(face.get("score", 0.0)) * 0.2
            )
            if score > best_score:
                best_score = score
                best_index = index
        return best_index, best_score

    def _assign_faces_to_persons(
        self,
        persons: List[Dict[str, Any]],
        detected_faces: List[dict[str, Any]],
    ) -> dict[int, int]:
        assignments: dict[int, int] = {}
        if not persons or not detected_faces:
            return assignments

        candidate_pairs: List[Tuple[float, int, int]] = []
        for person_index, person in enumerate(persons):
            for face_index, _face in enumerate(detected_faces):
                matched_face_index, score = self._select_face_match_for_person(person, [detected_faces[face_index]])
                if matched_face_index is None or score <= 0.0:
                    continue
                candidate_pairs.append((score, person_index, face_index))

        used_person_indices: set[int] = set()
        used_face_indices: set[int] = set()
        for _score, person_index, face_index in sorted(candidate_pairs, key=lambda item: item[0], reverse=True):
            if person_index in used_person_indices or face_index in used_face_indices:
                continue
            assignments[person_index] = face_index
            used_person_indices.add(person_index)
            used_face_indices.add(face_index)

        return assignments

    def _match_known_face(
        self,
        embedding: np.ndarray,
    ) -> Tuple[Optional[KnownFaceSubject], float, List[Tuple[str, str, float]], Optional[float]]:
        best_match_subject: Optional[KnownFaceSubject] = None
        best_score = 0.0
        scored_candidates: List[Tuple[str, str, float]] = []
        for subject in self._known_faces:
            similarity = self.face_recognizer.compare_embeddings(embedding, subject.embedding)
            scored_candidates.append((subject.subject_id, subject.subject_name, similarity))
            if similarity > best_score:
                best_score = similarity
                best_match_subject = subject
        scored_candidates.sort(key=lambda item: item[2], reverse=True)
        second_best_score = scored_candidates[1][2] if len(scored_candidates) > 1 else None
        if not self.face_recognizer.is_strong_match(best_score, second_best_score):
            return None, best_score, scored_candidates[:5], second_best_score
        return best_match_subject, best_score, scored_candidates[:5], second_best_score

    def _reset_reid_state(self) -> None:
        self._face_identity_cache.clear()
        self._next_stable_track_id = 1
        self._stable_track_ids_by_person.clear()
        self._stable_track_ids_by_raw_tracking_key.clear()
        self._last_face_tracking_key_by_person.clear()
        self.reid_tracker.reset()

    def _allocate_stable_track_id(self) -> int:
        stable_track_id = int(self._next_stable_track_id)
        self._next_stable_track_id += 1
        return stable_track_id

    def _resolve_stable_track_id(
        self,
        *,
        person_id: Optional[str],
        raw_tracking_key: str,
    ) -> int:
        stable_track_id = self._stable_track_ids_by_raw_tracking_key.get(raw_tracking_key)
        if person_id and not self.reid_identity_resolver.is_unknown_identity(person_id):
            stable_track_id = self._stable_track_ids_by_person.get(str(person_id), stable_track_id)
            if stable_track_id is None:
                stable_track_id = self._allocate_stable_track_id()
            self._stable_track_ids_by_person[str(person_id)] = int(stable_track_id)
        elif stable_track_id is None:
            stable_track_id = self._allocate_stable_track_id()

        stable_track_id = int(stable_track_id)
        self._stable_track_ids_by_raw_tracking_key[raw_tracking_key] = stable_track_id
        return stable_track_id

    def _attach_reid_tracks(
        self,
        frame: np.ndarray,
        persons: List[Dict[str, Any]],
        appearance_features_override: Optional[List[Optional[np.ndarray]]] = None,
        extract_features: bool = True,
    ) -> None:
        if not persons:
            return

        for index, person in enumerate(persons):
            detector_track_id = person.get("track_id")
            if detector_track_id is not None:
                person["detector_track_id"] = detector_track_id
            if appearance_features_override is not None and index < len(appearance_features_override):
                person["appearance_feature"] = appearance_features_override[index]
            elif extract_features:
                person["appearance_feature"] = (
                    self.reid_service.extract_feature(frame, person.get("box", []), person.get("mask"))
                    if self.reid_service is not None
                    else None
                )
            else:
                person["appearance_feature"] = None

        if not self.reid_enabled:
            for person in persons:
                person.setdefault("identity_source", "unknown")
            return

        try:
            self.reid_tracker.update(persons)
        except Exception:
            logger.exception("ReID tracker update failed")
            return

        for person in persons:
            reid_track_id = person.get("reid_track_id")
            if reid_track_id is not None:
                person["track_id"] = reid_track_id
            person.setdefault("identity_source", "unknown")

    def _match_face_identity(
        self,
        tracking_key: str,
        matched_face: Optional[dict[str, Any]],
        person: Dict[str, Any],
    ) -> dict[str, Any]:
        camera_id = self._camera_config.get("camera_id") or "camera"
        track_id = person.get("track_id", person.get("id", 0))
        unknown_id = f"unknown:{camera_id}:{track_id}"
        unknown_result = {
            "person_id": unknown_id,
            "person_name": "未知人员",
            "face_matched": False,
            "identity_source": "unknown",
            "face_observed_this_frame": matched_face is not None,
            "face_confirmed_this_frame": False,
            "subject_type": "unknown",
            "subject_supervision_scope": [],
            "allowed_camera_ids": [],
            "appointment_start": None,
            "appointment_end": None,
            "external_person_id": None,
            "face_embedding": None,
            "thumbnail": None,
            "tracking_key": tracking_key,
            "last_attempt_frame": self.frame_count,
            "last_seen_at": time.monotonic(),
        }
        if not matched_face:
            logger.debug(
                "[FaceRecognition] Frame %s %s no face assigned, mark unknown=%s",
                self.frame_count,
                tracking_key,
                unknown_id,
            )
            return unknown_result

        embedding_array = matched_face["embedding"]
        thumbnail_bytes = matched_face.get("thumbnail")
        best_match_subject, best_score, top_candidates, second_best_score = self._match_known_face(embedding_array)
        candidate_summary = ", ".join(
            f"{name}({subject_id})={score:.3f}"
            for subject_id, name, score in top_candidates
        )
        logger.debug(
            "[FaceRecognition] Frame %s %s candidates: threshold=%.3f top=[%s]",
            self.frame_count,
            tracking_key,
            self.face_recognizer.threshold,
            candidate_summary,
        )

        if best_match_subject:
            logger.debug(
                "[FaceRecognition] Frame %s %s matched person=%s(%s) similarity=%.3f face_score=%.3f",
                self.frame_count,
                tracking_key,
                best_match_subject.subject_name,
                best_match_subject.subject_id,
                best_score,
                float(matched_face.get("score", 0.0)),
            )
            return {
                "person_id": best_match_subject.subject_id,
                "person_name": best_match_subject.subject_name,
                "face_matched": True,
                "identity_source": "face",
                "face_observed_this_frame": True,
                "face_confirmed_this_frame": True,
                "subject_type": best_match_subject.subject_type,
                "subject_supervision_scope": list(best_match_subject.supervision_scope),
                "allowed_camera_ids": list(best_match_subject.allowed_camera_ids),
                "external_person_id": best_match_subject.external_person_id,
                "appointment_start": (
                    best_match_subject.start_time.isoformat()
                    if best_match_subject.start_time is not None
                    else None
                ),
                "appointment_end": (
                    best_match_subject.end_time.isoformat()
                    if best_match_subject.end_time is not None
                    else None
                ),
                "face_embedding": embedding_array,
                "thumbnail": thumbnail_bytes,
                "tracking_key": tracking_key,
                "last_attempt_frame": self.frame_count,
                "last_seen_at": time.monotonic(),
            }

        unknown_result["face_embedding"] = embedding_array
        unknown_result["thumbnail"] = thumbnail_bytes
        logger.debug(
            "[FaceRecognition] Frame %s %s rejected face match, keep unknown=%s best_similarity=%.3f second_best=%.3f threshold=%.3f margin=%.3f face_score=%.3f",
            self.frame_count,
            tracking_key,
            unknown_id,
            best_score,
            second_best_score or 0.0,
            self.face_recognizer.threshold,
            self.face_recognizer.min_margin,
            float(matched_face.get("score", 0.0)),
        )
        return unknown_result

    def _attach_face_identities(
        self,
        frame: np.ndarray,
        persons: List[Dict[str, Any]],
        detected_faces_override: Optional[List[dict[str, Any]]] = None,
        face_identities_override: Optional[List[dict[str, Any]]] = None,
    ) -> None:
        if not persons:
            return

        if face_identities_override is not None:
            now = time.monotonic()
            active_tracking_keys: set[str] = set()
            for person_index, person in enumerate(persons):
                track_id = person.get("track_id")
                fallback_id = person.get("id", 0)
                tracking_key = f"track:{track_id if track_id is not None else fallback_id}"
                active_tracking_keys.add(tracking_key)
                override = (
                    face_identities_override[person_index]
                    if person_index < len(face_identities_override)
                    else None
                ) or {}
                identity = {
                    "person_id": override.get("person_id") or f"unknown:{self._camera_config.get('camera_id') or 'camera'}:{track_id if track_id is not None else fallback_id}",
                    "person_name": override.get("person_name") or "未知人员",
                    "face_matched": bool(override.get("face_matched")),
                    "identity_source": override.get("identity_source", "unknown"),
                    "face_observed_this_frame": bool(override.get("face_observed_this_frame")),
                    "face_confirmed_this_frame": bool(override.get("face_confirmed_this_frame")),
                    "subject_type": override.get("subject_type", "unknown"),
                    "subject_supervision_scope": list(override.get("subject_supervision_scope") or []),
                    "allowed_camera_ids": list(override.get("allowed_camera_ids") or []),
                    "appointment_start": override.get("appointment_start"),
                    "appointment_end": override.get("appointment_end"),
                    "external_person_id": override.get("external_person_id"),
                    "face_embedding": override.get("face_embedding"),
                    "thumbnail": override.get("thumbnail"),
                    "tracking_key": tracking_key,
                    "last_attempt_frame": self.frame_count,
                    "last_seen_at": now,
                }
                self._face_identity_cache[tracking_key] = identity
                person["raw_tracking_key"] = tracking_key
                person["tracking_key"] = tracking_key
                person["person_id"] = identity.get("person_id")
                person["person_name"] = identity.get("person_name")
                person["face_matched"] = bool(identity.get("face_matched"))
                person["identity_source"] = identity.get("identity_source", "unknown")
                person["face_observed_this_frame"] = bool(identity.get("face_observed_this_frame"))
                person["face_confirmed_this_frame"] = bool(identity.get("face_confirmed_this_frame"))
                person["subject_type"] = identity.get("subject_type", "unknown")
                person["subject_supervision_scope"] = list(identity.get("subject_supervision_scope") or [])
                person["allowed_camera_ids"] = list(identity.get("allowed_camera_ids") or [])
                person["appointment_start"] = identity.get("appointment_start")
                person["appointment_end"] = identity.get("appointment_end")
                person["external_person_id"] = identity.get("external_person_id")
                person["face_embedding"] = identity.get("face_embedding")
                person["thumbnail"] = identity.get("thumbnail")

            stale_keys = [
                key
                for key, value in self._face_identity_cache.items()
                if key not in active_tracking_keys and now - float(value.get("last_seen_at", now)) > self._face_identity_ttl_seconds
            ]
            for key in stale_keys:
                self._face_identity_cache.pop(key, None)
            return

        should_run_face_detection = detected_faces_override is not None or self._should_run_face_detection(persons)
        if should_run_face_detection:
            self._refresh_known_faces_cache()
            detected_faces = (
                self._normalize_detected_faces(detected_faces_override)
                if detected_faces_override is not None
                else self._detect_frame_faces(frame, persons)
            )
            self._last_face_detection_frame = self.frame_count
        else:
            detected_faces = []
            logger.debug(
                "[FaceRecognition] Frame %s skip face detector for camera=%s interval=%s",
                self.frame_count,
                self._camera_config.get("camera_id") or self.current_video_source or "unknown",
                self._face_detection_interval_frames,
            )
        now = time.monotonic()
        active_tracking_keys: set[str] = set()
        face_assignments = self._assign_faces_to_persons(persons, detected_faces)

        for person_index, person in enumerate(persons):
            track_id = person.get("track_id")
            fallback_id = person.get("id", 0)
            tracking_key = f"track:{track_id if track_id is not None else fallback_id}"
            active_tracking_keys.add(tracking_key)
            cached = self._face_identity_cache.get(tracking_key)
            face_index = face_assignments.get(person_index)
            has_assignable_face = face_index is not None

            reuse_cached = False
            if cached:
                age_seconds = now - float(cached.get("last_seen_at", now))
                if (
                    not has_assignable_face
                    and bool(cached.get("face_matched"))
                    and age_seconds <= self._face_identity_ttl_seconds
                ):
                    if (
                        self.frame_count - int(cached.get("last_attempt_frame", 0))
                        < self._face_identity_refresh_interval_frames
                    ):
                        reuse_cached = True
                elif (
                    not bool(cached.get("face_matched"))
                    and self.reid_identity_resolver.is_unknown_identity(cached.get("person_id"))
                    and not has_assignable_face
                    and self.frame_count - int(cached.get("last_attempt_frame", 0)) < self._face_retry_interval_frames
                ):
                    reuse_cached = True

            if reuse_cached:
                identity = dict(cached)
                identity["face_observed_this_frame"] = False
                identity["face_confirmed_this_frame"] = False
                logger.debug(
                    "[FaceRecognition] Frame %s %s reuse cache matched=%s person_id=%s person_name=%s",
                    self.frame_count,
                    tracking_key,
                    bool(identity.get("face_matched")),
                    identity.get("person_id"),
                    identity.get("person_name"),
                )
            else:
                matched_face = detected_faces[face_index] if face_index is not None else None
                logger.debug(
                    "[FaceRecognition] Frame %s %s selected face_index=%s assigned_faces=%s",
                    self.frame_count,
                    tracking_key,
                    face_index,
                    len(face_assignments),
                )
                identity = self._match_face_identity(tracking_key, matched_face, person)
            identity["last_seen_at"] = now
            self._face_identity_cache[tracking_key] = identity

            person["raw_tracking_key"] = tracking_key
            person["tracking_key"] = tracking_key
            person["person_id"] = identity.get("person_id")
            person["person_name"] = identity.get("person_name")
            person["face_matched"] = bool(identity.get("face_matched"))
            person["identity_source"] = identity.get("identity_source", "unknown")
            person["face_observed_this_frame"] = bool(identity.get("face_observed_this_frame"))
            person["face_confirmed_this_frame"] = bool(identity.get("face_confirmed_this_frame"))
            person["subject_type"] = identity.get("subject_type", "unknown")
            person["subject_supervision_scope"] = list(identity.get("subject_supervision_scope") or [])
            person["allowed_camera_ids"] = list(identity.get("allowed_camera_ids") or [])
            person["appointment_start"] = identity.get("appointment_start")
            person["appointment_end"] = identity.get("appointment_end")
            person["external_person_id"] = identity.get("external_person_id")
            person["face_embedding"] = identity.get("face_embedding")
            person["thumbnail"] = identity.get("thumbnail")

        stale_keys = [
            key
            for key, value in self._face_identity_cache.items()
            if key not in active_tracking_keys and now - float(value.get("last_seen_at", now)) > self._face_identity_ttl_seconds
        ]
        for key in stale_keys:
            self._face_identity_cache.pop(key, None)

    def _should_run_face_detection(self, persons: List[Dict[str, Any]]) -> bool:
        if not persons:
            return False
        if self._last_face_detection_frame <= 0:
            return True

        frames_since_detection = self.frame_count - self._last_face_detection_frame
        if frames_since_detection >= self._face_detection_interval_frames:
            return True

        new_track_seen = False
        for person in persons:
            track_id = person.get("track_id")
            fallback_id = person.get("id", 0)
            tracking_key = f"track:{track_id if track_id is not None else fallback_id}"
            cached = self._face_identity_cache.get(tracking_key)
            if cached is None:
                new_track_seen = True
                continue
            last_attempt_frame = int(cached.get("last_attempt_frame", 0))
            if (
                bool(cached.get("face_matched"))
                and self.frame_count - last_attempt_frame >= self._face_identity_refresh_interval_frames
            ):
                return True
            if (
                not bool(cached.get("face_matched"))
                and self.reid_identity_resolver.is_unknown_identity(cached.get("person_id"))
                and self.frame_count - last_attempt_frame >= self._face_retry_interval_frames
            ):
                return True

        if new_track_seen and frames_since_detection >= min(2, self._face_detection_interval_frames):
            return True
        return False

    @staticmethod
    def _normalize_detected_faces(
        detected_faces_override: Optional[List[dict[str, Any]]],
    ) -> List[dict[str, Any]]:
        normalized: List[dict[str, Any]] = []
        for face in detected_faces_override or []:
            embedding = face.get("embedding")
            if embedding is None:
                continue
            frame_box = [float(value) for value in face.get("frame_box") or []]
            if len(frame_box) != 4:
                continue
            normalized.append(
                {
                    "frame_box": frame_box,
                    "embedding": np.asarray(embedding, dtype=np.float32),
                    "thumbnail": face.get("thumbnail"),
                    "score": float(face.get("score", 0.0)),
                }
            )
        return normalized

    def _apply_area_overcapacity(
        self,
        persons: List[Dict[str, Any]],
        frame_shape: Tuple[int, int],
        frame_action_violations: List[Dict[str, Any]],
    ) -> None:
        """Mark persons inside configured polygon when count exceeds limit."""
        camera_scope = self._camera_config.get("camera_detection_scope") or []
        polygon = self._camera_config.get("area_overcapacity_polygon") or []
        limit = self._camera_config.get("area_overcapacity_limit")

        if (
            "area_overcapacity" not in camera_scope
            or len(polygon) != 4
            or not isinstance(limit, int)
            or limit < 0
            or not persons
        ):
            return

        frame_height, frame_width = frame_shape
        polygon_pixels = self._polygon_to_pixels(polygon, frame_width, frame_height)
        if len(polygon_pixels) != 4:
            return

        persons_inside: list[Dict[str, Any]] = []
        for person in persons:
            inside, anchor = self._person_inside_overcapacity_region(person, polygon_pixels)
            person["area_overcapacity_inside"] = inside
            person["area_overcapacity_anchor"] = anchor
            if inside:
                persons_inside.append(person)

        if len(persons_inside) <= limit:
            return

        for person in persons_inside:
            person_action_violations = person.setdefault("action_violations", [])
            if not any(
                item.get("action") == "area_overcapacity" for item in person_action_violations
            ):
                person_action_violations.append(
                    {
                        "action": "area_overcapacity",
                        "score": 1.0,
                    }
                )
            box = person.get("box", [0, 0, 0, 0])
            if not any(
                item.get("action") == "area_overcapacity" and item.get("box") == box
                for item in frame_action_violations
            ):
                frame_action_violations.append(
                    {
                        "action": "area_overcapacity",
                        "class": "area_overcapacity",
                        "score": 1.0,
                        "box": box,
                    }
                )
            person["is_violation"] = True

    def _person_inside_overcapacity_region(
        self, person: Dict[str, Any], polygon: List[Tuple[float, float]]
    ) -> Tuple[bool, Optional[Tuple[float, float]]]:
        pose = person.get("pose") or {}
        keypoints = pose.get("keypoints") or []
        confidences = pose.get("keypoint_confidence") or []

        foot_center = self._get_feet_center(keypoints, confidences)
        if foot_center is not None:
            return self._point_in_polygon(foot_center, polygon), foot_center

        # Fall back to the bottom-center of the tracked person box when pose
        # keypoints are unavailable or too weak. Area-overcapacity should still
        # work with plain person detection instead of silently depending on pose.
        box_anchor = self._get_box_bottom_center(person)
        if box_anchor is not None:
            return self._point_in_polygon(box_anchor, polygon), box_anchor

        valid_points: list[Tuple[float, float]] = []
        for index, point in enumerate(keypoints):
            if index >= len(confidences):
                confidence = 1.0
            else:
                confidence = confidences[index]
            if confidence < getattr(settings, "POSE_KEYPOINT_CONFIDENCE_THRESHOLD", 0.35):
                continue
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            x, y = float(point[0]), float(point[1])
            if x <= 0 or y <= 0:
                continue
            valid_points.append((x, y))

        if not valid_points:
            return False, None

        inside_count = sum(1 for point in valid_points if self._point_in_polygon(point, polygon))
        inside = inside_count / len(valid_points) >= 0.5
        anchor = (
            sum(point[0] for point in valid_points) / len(valid_points),
            sum(point[1] for point in valid_points) / len(valid_points),
        )
        return inside, anchor

    @staticmethod
    def _get_box_bottom_center(person: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        box = person.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        x1, y1, x2, y2 = box
        try:
            x1 = float(x1)
            y1 = float(y1)
            x2 = float(x2)
            y2 = float(y2)
        except (TypeError, ValueError):
            return None
        if x2 <= x1 or y2 <= y1:
            return None
        return ((x1 + x2) / 2.0, y2)

    def _get_feet_center(
        self, keypoints: List[Any], confidences: List[float]
    ) -> Optional[Tuple[float, float]]:
        min_conf = getattr(settings, "POSE_KEYPOINT_CONFIDENCE_THRESHOLD", 0.35)
        foot_points: list[Tuple[float, float]] = []
        for index in (15, 16):
            if index >= len(keypoints):
                continue
            if confidences and index < len(confidences) and confidences[index] < min_conf:
                continue
            point = keypoints[index]
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            x, y = float(point[0]), float(point[1])
            if x <= 0 or y <= 0:
                continue
            foot_points.append((x, y))

        if len(foot_points) == 2:
            return (
                (foot_points[0][0] + foot_points[1][0]) / 2,
                (foot_points[0][1] + foot_points[1][1]) / 2,
            )
        return None

    @staticmethod
    def _polygon_to_pixels(
        polygon: List[List[float]], frame_width: int, frame_height: int
    ) -> List[Tuple[float, float]]:
        points: list[Tuple[float, float]] = []
        for point in polygon:
            if not isinstance(point, list) or len(point) != 2:
                continue
            x = float(point[0]) * frame_width
            y = float(point[1]) * frame_height
            points.append((x, y))
        return points

    @staticmethod
    def _point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
        x, y = point
        inside = False
        total = len(polygon)
        for index in range(total):
            x1, y1 = polygon[index]
            x2, y2 = polygon[(index + 1) % total]
            denominator = y2 - y1
            if abs(denominator) < 1e-6:
                denominator = -1e-6 if denominator < 0 else 1e-6
            intersects = ((y1 > y) != (y2 > y)) and (
                x < (x2 - x1) * (y - y1) / denominator + x1
            )
            if intersects:
                inside = not inside
        return inside

    def _annotate_frame(
        self,
        frame: np.ndarray,
        persons: List[Dict],
        violation_detections: Optional[Dict] = None,
        action_violations: Optional[List] = None,
    ) -> np.ndarray:
        """在帧上绘制标注。"""
        annotated = frame.copy()

        # 记录每个人员的掩码状态
        persons_with_masks = sum(1 for p in persons if p.get("mask") is not None)
        logger.debug(
            f"[Pipeline] _annotate_frame: {len(persons)} persons, {persons_with_masks} with masks, show_masks={self.show_masks}"
        )

        for person in persons:
            track_id = person.get("track_id", "?")
            has_mask = person.get("mask") is not None
            if has_mask:
                mask_pixels = int(np.sum(person["mask"] > 0))
                logger.debug(
                    f"[Pipeline] Person track {track_id}: mask={mask_pixels} pixels"
                )
            else:
                logger.debug(f"[Pipeline] Person track {track_id}: no mask")

            ppe_detections = person.get("ppe_detections", [])
            annotated = draw_person_with_ppe(
                annotated,
                person,
                ppe_detections,
                show_masks=self.show_masks,
                mask_alpha=self.mask_alpha,
            )
            annotated = self._draw_pose_skeleton(annotated, person)
            annotated = self._draw_pose_action_labels(annotated, person)

        # 绘制行为违规（PPE 违规已在 draw_person_with_ppe 中绘制）
        if action_violations:
            for action in action_violations:
                box = action.get("box", [0, 0, 0, 0])
                if box != [0, 0, 0, 0]:
                    x1, y1, x2, y2 = [int(c) for c in box]
                    color = get_color(action.get("action", "fall_detected"))
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                    label = f"行为: {action.get('action', 'violation')}"
                    annotated = draw_label_badge(
                        annotated,
                        label,
                        (x1, y1),
                        color,
                        font_scale=0.4,
                    )

        annotated = self._draw_area_overcapacity_region(annotated)

        return annotated

    def _draw_area_overcapacity_region(self, frame: np.ndarray) -> np.ndarray:
        polygon = self._camera_config.get("area_overcapacity_polygon") or []
        if len(polygon) != 4:
            return frame

        frame_height, frame_width = frame.shape[:2]
        points = self._polygon_to_pixels(polygon, frame_width, frame_height)
        if len(points) != 4:
            return frame

        pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts], (0, 0, 255))
        cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 255), thickness=2)
        return frame

    def _draw_pose_action_labels(self, frame: np.ndarray, person: Dict) -> np.ndarray:
        """Draw pose status and pose-derived action labels near the person box."""
        pose_status = person.get("pose_status", "unknown")
        pose_actions = person.get("pose_actions", [])
        show_neutral = getattr(settings, "POSE_SHOW_NEUTRAL_STATUS", False)
        show_status = pose_status not in {"unknown", "standing"} or show_neutral
        if not show_status and not pose_actions:
            return frame

        box = person.get("box", [0, 0, 0, 0])
        x1, y1, _, _ = [int(c) for c in box]

        status_color = {
            "standing": (255, 0, 0),
            "bending": (0, 165, 255),
            "crouching": (0, 215, 255),
            "fallen": (0, 0, 255),
            "unknown": (128, 128, 128),
        }.get(pose_status, (128, 128, 128))

        result = frame
        next_offset = -22
        if show_status:
            result = draw_label_badge(
                result,
                f"Pose: {pose_status}",
                (x1, y1),
                status_color,
                offset_y=next_offset,
                font_scale=0.4,
            )
            next_offset -= 22

        for idx, action in enumerate(pose_actions):
            result = draw_label_badge(
                result,
                action.get("label", f"Action: {action.get('action', 'unknown')}"),
                (x1, y1),
                (0, 0, 255),
                offset_y=next_offset - idx * 22,
                font_scale=0.4,
            )

        return result

    def _draw_pose_skeleton(self, frame: np.ndarray, person: Dict) -> np.ndarray:
        """Draw COCO 17-point pose skeleton for a person."""
        pose = person.get("pose")
        if not pose:
            return frame

        keypoints = pose.get("keypoints") or []
        confidences = pose.get("keypoint_confidence") or []
        if len(keypoints) < 17:
            return frame

        min_conf = getattr(settings, "POSE_KEYPOINT_CONFIDENCE_THRESHOLD", 0.35)
        skeleton = [
            (5, 6),
            (5, 7),
            (7, 9),
            (6, 8),
            (8, 10),
            (5, 11),
            (6, 12),
            (11, 12),
            (11, 13),
            (13, 15),
            (12, 14),
            (14, 16),
            (0, 5),
            (0, 6),
        ]
        line_color = (80, 220, 255)
        point_color = (255, 255, 255)

        def valid(index: int) -> bool:
            if index >= len(keypoints):
                return False
            if confidences and index < len(confidences) and confidences[index] < min_conf:
                return False
            x, y = keypoints[index]
            return x > 0 and y > 0

        for start, end in skeleton:
            if valid(start) and valid(end):
                p1 = tuple(int(v) for v in keypoints[start])
                p2 = tuple(int(v) for v in keypoints[end])
                cv2.line(frame, p1, p2, line_color, 2)

        for idx, point in enumerate(keypoints):
            if valid(idx):
                cv2.circle(frame, tuple(int(v) for v in point), 3, point_color, -1)

        return frame

    def process_video(self, video_path: str) -> Generator[Dict[str, Any], None, None]:
        """逐帧处理视频文件。"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"无法打开视频: {video_path}")

        video_fps = cap.get(cv2.CAP_PROP_FPS)
        frame_skip = max(1, int(video_fps / self.target_fps))
        frame_idx = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % frame_skip == 0:
                    result = self.process_frame(frame, video_source=video_path)
                    result["video_frame_idx"] = frame_idx
                    yield result

                frame_idx += 1
        finally:
            cap.release()

    def load_known_persons(self, persons: List):
        """加载已知人员（遗留功能，不与YOLOv8跟踪一起使用）。"""
        pass

    def get_stats(self) -> Dict[str, Any]:
        """获取管道统计信息。"""
        return {
            "frame_count": self.frame_count,
            "current_video": self.current_video_source,
        }

    def reset(self):
        """重置管道状态。"""
        self.temporal_filter.clear_all()
        self.pose_action_filter.clear_all()
        self.frame_count = 0
        self._last_face_detection_frame = 0
        self.current_video_source = None
        self._reset_reid_state()
        if hasattr(self.detector, "reset_video_state"):
            self.detector.reset_video_state()


# 单例
_pipeline = None


def get_pipeline() -> DetectionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DetectionPipeline()
    return _pipeline
