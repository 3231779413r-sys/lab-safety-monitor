from __future__ import annotations

import asyncio
import logging
import time
import json
from concurrent.futures import Future, ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from queue import Empty, Full, Queue
from threading import Lock, Thread
from statistics import mean
from typing import Any, Deque, Dict, Optional
from uuid import uuid4

import cv2
import numpy as np
from sqlalchemy import select

from ..core.config import settings
from ..core.realtime_bus import publish_realtime_message
from ..core.database import async_session
from ..ml.pipeline import DetectionPipeline
from ..models.event import ComplianceEvent
from ..models.supervision import VisitorRegistration
from ..models.supervision_settings import SupervisionSettings
from ..services.event_service import EventService
from ..services.hikvision_sdk_source import HikvisionSdkFrameSource
from ..services.inspection_service import InspectionService
from ..services.live_preview_store import get_live_preview_store
from ..services.persistence import PersistenceManager
from ..services.shared_frame_store import get_shared_frame_store
from ..telemetry import (
    TelemetryMap,
    clone_telemetry,
    mark_telemetry,
    telemetry_duration_ms,
    telemetry_now,
)

logger = logging.getLogger(__name__)


@dataclass
class BufferedFrame:
    timestamp: datetime
    frame: np.ndarray


@dataclass
class PendingEventVideoClip:
    event_id: str
    timestamp: datetime
    camera_id: str
    post_deadline: datetime
    frames: list[np.ndarray]
    finalized: bool = False


@dataclass
class PendingInferenceFrame:
    request_id: str
    submitted_at: datetime
    video_source: str
    frame_path: Optional[str]
    frame_jpeg: Optional[bytes]
    frame: Optional[np.ndarray]
    telemetry: TelemetryMap


@dataclass
class PendingInferenceDispatchTask:
    request_id: str
    frame_path: Optional[str]
    frame_jpeg: Optional[bytes]
    inference_options: dict[str, Any]
    frame: Optional[np.ndarray]
    video_source: str
    submitted_at: datetime
    telemetry: TelemetryMap


@dataclass
class MailboxEnqueueResult:
    accepted: bool
    replaced_task: Any = None
    replace_reason: str | None = None
    error_reason: str | None = None


@dataclass
class PendingIdentityFrame:
    request_id: str
    submitted_at: datetime
    video_source: str
    frame_path: Optional[str]
    frame_jpeg: Optional[bytes]
    frame: Optional[np.ndarray]
    detections_override: dict[str, Any]
    telemetry: TelemetryMap


@dataclass
class PendingIdentityUpdate:
    request_id: str
    submitted_at: datetime
    video_source: str
    frame_path: Optional[str]
    frame_jpeg: Optional[bytes]
    telemetry: TelemetryMap


@dataclass
class PendingIdentityDispatchTask:
    request_id: str
    frame_path: Optional[str]
    frame_jpeg: Optional[bytes]
    frame: Optional[np.ndarray]
    video_source: str
    detections_override: dict[str, Any]
    identity_persons: list[dict[str, Any]]
    submitted_at: datetime
    face_detection_requested: bool
    telemetry: TelemetryMap


@dataclass
class PendingProcessingTask:
    request_id: str | None
    frame: Optional[np.ndarray]
    frame_path: Optional[str]
    video_source: str
    detections_override: Optional[dict[str, Any]]
    identity_observations_override: Optional[dict[str, Any]]
    processed_at: datetime
    telemetry: TelemetryMap


@dataclass
class RuntimeProfileState:
    profile: str
    degrade_level: int
    process_fps: int
    pose_enabled: bool
    identity_enabled: bool
    identity_unknown_only: bool
    max_identity_persons: int
    max_full_inference_persons: int
    max_persons_for_frame: int
    skip_segmentation: bool
    force_fast_core_ppe_only: bool


def _annotate_frame_with_result(
    pipeline: DetectionPipeline,
    frame: np.ndarray,
    detection_result: Optional[dict[str, Any]],
) -> np.ndarray:
    if not detection_result:
        return frame
    return pipeline._annotate_frame(
        frame,
        detection_result.get("persons", []),
        violation_detections=detection_result.get("violation_detections", {}),
        action_violations=detection_result.get("action_violations", []),
    )


_runtime_background_executor = ThreadPoolExecutor(
    max_workers=max(1, int(getattr(settings, "CAMERA_RUNTIME_BACKGROUND_WORKERS", 4))),
    thread_name_prefix="camera-runtime-bg",
)


def _parse_scope(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_polygon(value: Optional[str]) -> list[list[float]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            return []
        result: list[list[float]] = []
        for point in parsed:
            if isinstance(point, list) and len(point) == 2:
                result.append([float(point[0]), float(point[1])])
        return result
    except json.JSONDecodeError:
        return []


def _build_pipeline_camera_config(camera) -> dict[str, Any]:
    return {
        "camera_id": camera.id,
        "processing_profile": getattr(camera, "processing_profile", None),
        "camera_detection_scope": _parse_scope(getattr(camera, "camera_detection_scope", None)),
        "backend_detection_scope": _parse_scope(getattr(camera, "backend_detection_scope", None)),
        "area_overcapacity_polygon": _parse_polygon(getattr(camera, "area_overcapacity_polygon", None)),
        "area_overcapacity_limit": getattr(camera, "area_overcapacity_limit", None),
        "visitor_exemption_active": False,
        "other_person_scope": [],
        "workshop_overcapacity_enabled": False,
        "workshop_overcapacity_limit": None,
        "workshop_overcapacity_total_person_count": 0,
        "alert_cooldown_seconds": settings.VIOLATION_ALERT_COOLDOWN_SECONDS,
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class HikvisionSdkRuntimeSource:
    def __init__(self, camera):
        self.camera = camera
        self._source: Optional[HikvisionSdkFrameSource] = None
        self._last_frame_counter = 0

    def open(self) -> None:
        stream_type = (self.camera.stream_type or "sub").lower()
        self._source = HikvisionSdkFrameSource(
            sdk_dir=settings.HIKVISION_SDK_DIR,
            host=self.camera.host or "127.0.0.1",
            username=self.camera.username or "",
            password=self.camera.password or "",
            port=self.camera.port or settings.HIKVISION_SDK_PORT,
            channel=self.camera.channel or 1,
            stream_type="sub" if stream_type == "sub" else "main",
            link_mode=0,
        )
        self._source.open()
        self._last_frame_counter = 0

    def is_opened(self) -> bool:
        return self._source is not None

    def read(self) -> Optional[np.ndarray]:
        if self._source is None:
            return None
        return self._source.read_latest_frame(timeout=1.0)

    def read_new_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        if self._source is None:
            return None
        frame, frame_counter = self._source.wait_for_new_frame(
            last_frame_counter=self._last_frame_counter,
            timeout=timeout,
        )
        if frame is not None:
            self._last_frame_counter = frame_counter
        return frame

    def close(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None
        self._last_frame_counter = 0


@dataclass
class CameraRuntimeStatus:
    camera_id: str
    running: bool = False
    online: bool = False
    status: str = "stopped"
    last_frame_at: Optional[datetime] = None
    last_error: Optional[str] = None
    processed_frames: int = 0
    dropped_frames: int = 0
    degrade_level: int = 0
    camera_profile: str = "balanced"


class CameraRuntime:
    """Owns one always-on camera capture and AI processing loop."""

    def __init__(self, camera, registry: "CameraRuntimeRegistry"):
        self.camera_id = camera.id
        self.camera = camera
        self._registry = registry
        self._queue_inference_enabled = (
            settings.INFERENCE_BACKEND.lower() == "queue"
            and settings.BACKEND_MODE == "worker"
        )
        self._queue_identity_enabled = (
            self._queue_inference_enabled
            and settings.IDENTITY_BACKEND.lower() == "queue"
            and settings.BACKEND_MODE == "worker"
        )
        self.pipeline = DetectionPipeline()
        self.pipeline.initialize()
        self.pipeline.set_camera_config(_build_pipeline_camera_config(camera))
        self.status = CameraRuntimeStatus(camera_id=camera.id)
        self._lock = Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_frame_version = 0
        self._latest_raw_jpeg: Optional[bytes] = None
        self._latest_annotated_jpeg: Optional[bytes] = None
        self._latest_detection_result: Optional[Dict[str, Any]] = None
        self._latest_result_request_id: Optional[str] = None
        self._latest_result_telemetry: TelemetryMap = {}
        self._latest_preview_logged_request_id: Optional[str] = None
        self._thread: Optional[Thread] = None
        self._stop_requested = False
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._preview_store = get_live_preview_store()
        self._preview_cache_enabled = settings.BACKEND_MODE != "worker"
        self._shared_frame_store = get_shared_frame_store()
        self._last_shared_frame_cleanup_at = 0.0
        self._pending_persist = 0
        self._max_pending_persist = 1
        self._visitor_exemption_active = False
        self._visitor_exemption_checked_at = 0.0
        self._cached_runtime_settings: dict[str, Any] = {
            "other_person_scope": [],
            "workshop_overcapacity_enabled": False,
            "workshop_overcapacity_limit": None,
            "alert_cooldown_seconds": settings.VIOLATION_ALERT_COOLDOWN_SECONDS,
        }
        self._runtime_refresh_inflight = False
        pre_seconds = max(1, int(settings.EVENT_VIDEO_PRE_SECONDS))
        fps = max(1, int(settings.EVENT_VIDEO_CAPTURE_FPS))
        self._frame_buffer: Deque[BufferedFrame] = deque(maxlen=pre_seconds * fps + fps)
        self._pending_event_video_clips: list[PendingEventVideoClip] = []
        self._inference_result_queue: Queue[tuple[str, dict[str, Any]]] = Queue()
        self._pending_inference_frames: Dict[str, PendingInferenceFrame] = {}
        self._pending_inference_order: Deque[str] = deque()
        self._completed_inference_results: Dict[str, dict[str, Any]] = {}
        self._pending_inference_timeout_seconds = max(
            1.0, float(getattr(settings, "INFERENCE_PENDING_TIMEOUT_SECONDS", 10.0))
        )
        self._last_inference_backpressure_log_at = 0.0
        self._identity_result_queue: Queue[tuple[str, dict[str, Any]]] = Queue()
        self._pending_identity_frames: Dict[str, PendingIdentityFrame] = {}
        self._pending_identity_order: Deque[str] = deque()
        self._completed_identity_results: Dict[str, dict[str, Any]] = {}
        self._pending_identity_updates: Dict[str, PendingIdentityUpdate] = {}
        self._pending_identity_update_order: Deque[str] = deque()
        self._pending_identity_timeout_seconds = max(
            1.0, float(getattr(settings, "IDENTITY_PENDING_TIMEOUT_SECONDS", 10.0))
        )
        self._pending_identity_update_timeout_seconds = max(
            self._pending_identity_timeout_seconds,
            float(getattr(settings, "IDENTITY_ASYNC_PENDING_TIMEOUT_SECONDS", self._pending_identity_timeout_seconds)),
        )
        self._processing_queue: Queue[PendingProcessingTask] = Queue(
            maxsize=max(1, int(settings.CAMERA_RUNTIME_PROCESS_QUEUE_SIZE))
        )
        self._processing_scheduled = False
        self._result_dispatch_scheduled = False
        self._metrics_window_size = max(
            5, int(getattr(settings, "CAMERA_RUNTIME_METRICS_WINDOW_SIZE", 30))
        )
        self._recent_inference_total_ms: Deque[float] = deque(maxlen=self._metrics_window_size)
        self._recent_inference_compute_ms: Deque[float] = deque(maxlen=self._metrics_window_size)
        self._recent_inference_batch_wait_ms: Deque[float] = deque(maxlen=self._metrics_window_size)
        self._recent_person_count: Deque[int] = deque(maxlen=self._metrics_window_size)
        self._recent_complexity_score: Deque[float] = deque(maxlen=self._metrics_window_size)
        self._recent_inference_backlog: Deque[int] = deque(maxlen=self._metrics_window_size)
        self._recent_processing_ms: Deque[float] = deque(maxlen=self._metrics_window_size)
        self._recent_identity_selected_persons: Deque[int] = deque(maxlen=self._metrics_window_size)
        self._summary_interval_seconds = max(
            10, int(getattr(settings, "CAMERA_RUNTIME_SUMMARY_INTERVAL_SECONDS", 60))
        )
        self._last_summary_logged_at = 0.0
        self._identity_suspended_until = 0.0
        self._identity_suspended_until_at: datetime | None = None
        self._last_degrade_reason = ""
        self._last_degrade_log_at = 0.0
        self._degrade_candidate_level = 0
        self._degrade_candidate_reason = ""
        self._degrade_candidate_since = 0.0
        self._degrade_level_changed_at = 0.0
        self._expired_inference_count = 0
        self._expired_identity_count = 0
        self._selected_identity_persons_total = 0
        self._inference_backpressure_skip_count = 0
        self._replaced_pending_oldest_count = 0
        self._replaced_pending_latest_count = 0
        self._submit_queue_reject_count = 0
        self._identity_requests_submitted = 0
        self._identity_requests_skipped = 0
        self._identity_async_merged_count = 0
        self._identity_fallback_without_enrichment_count = 0
        self._recent_async_identity_merge_latency_ms: Deque[float] = deque(maxlen=self._metrics_window_size)
        self._processed_completed_at: Deque[float] = deque(maxlen=max(10, self._metrics_window_size * 4))
        self._latest_processed_capture_sampled_at: datetime | None = None
        self._recent_latest_frame_age_ms: Deque[float] = deque(maxlen=self._metrics_window_size)
        self._base_process_fps = max(1, int(settings.LIVE_STREAM_PROCESS_FPS))
        self._current_profile_state = self._compute_runtime_profile_state()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._main_loop = loop
        self._stop_requested = False
        self.status.running = True
        self.status.status = "starting"
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested = True
        self.status.running = False
        self.status.status = "stopping"
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.pipeline.reset()
        self._frame_buffer.clear()
        self._pending_event_video_clips.clear()
        self._clear_pending_inference()
        self._clear_pending_identity()
        self._clear_inference_submit_queue()
        self._clear_identity_submit_queue()
        self._clear_processing_queue()
        with self._lock:
            self._processing_scheduled = False
            self._result_dispatch_scheduled = False
        with self._lock:
            self._latest_raw_jpeg = None
        self.status.status = "stopped"
        self.status.online = False

    def update_camera(self, camera) -> None:
        self.camera = camera
        self.pipeline.set_camera_config(_build_pipeline_camera_config(camera))
        self._current_profile_state = self._compute_runtime_profile_state()

    def _configured_camera_profile(self) -> str:
        overrides = getattr(settings, "CAMERA_RUNTIME_FORCE_PROFILE_OVERRIDES", {}) or {}
        profile = str(
            overrides.get(self.camera_id)
            or getattr(self.camera, "processing_profile", None)
            or "balanced"
        ).strip().lower()
        if profile not in {"fast", "balanced", "accurate"}:
            return "balanced"
        return profile

    def _compute_runtime_profile_state(self) -> RuntimeProfileState:
        profile = self._configured_camera_profile()
        degrade_level = int(self.status.degrade_level)
        base_fps = self._base_process_fps
        process_fps = base_fps
        pose_enabled = True
        identity_enabled = True
        identity_unknown_only = False
        max_identity_persons = max(1, int(getattr(settings, "MAX_PERSONS_PER_FRAME_FOR_IDENTITY", 2)))
        max_full_inference_persons = max(1, int(getattr(settings, "MAX_PERSONS_PER_FRAME_FOR_FULL_INFERENCE", 4)))
        max_persons_for_frame = max_full_inference_persons
        skip_segmentation = False
        force_fast_core_ppe_only = False

        if profile == "fast":
            process_fps = min(process_fps, 2)
            pose_enabled = False
            identity_unknown_only = True
            max_identity_persons = 1
            max_full_inference_persons = max(
                1, int(getattr(settings, "MAX_PERSONS_PER_FRAME_FAST_PROFILE", 2))
            )
            max_persons_for_frame = max_full_inference_persons
            skip_segmentation = True
            force_fast_core_ppe_only = True
        elif profile == "accurate":
            process_fps = max(process_fps, int(settings.LIVE_STREAM_PROCESS_FPS))

        if degrade_level >= 1:
            process_fps = min(process_fps, 2)
        if degrade_level >= 2:
            process_fps = 1
            pose_enabled = False
            identity_unknown_only = True
            max_identity_persons = 1
        if degrade_level >= 3:
            pose_enabled = False
            skip_segmentation = True
            force_fast_core_ppe_only = True
            max_full_inference_persons = min(max_full_inference_persons, 2)
            max_persons_for_frame = min(max_persons_for_frame, 2)

        if time.monotonic() < self._identity_suspended_until:
            identity_enabled = False
            identity_unknown_only = True
            max_identity_persons = 1

        self.status.camera_profile = profile
        self.status.degrade_level = degrade_level
        return RuntimeProfileState(
            profile=profile,
            degrade_level=degrade_level,
            process_fps=max(1, process_fps),
            pose_enabled=pose_enabled,
            identity_enabled=identity_enabled,
            identity_unknown_only=identity_unknown_only,
            max_identity_persons=max(1, max_identity_persons),
            max_full_inference_persons=max(1, max_full_inference_persons),
            max_persons_for_frame=max(1, max_persons_for_frame),
            skip_segmentation=skip_segmentation,
            force_fast_core_ppe_only=force_fast_core_ppe_only,
        )

    @staticmethod
    def _percentile(values: Deque[float] | list[float], ratio: float) -> float | None:
        data = [float(item) for item in values if item is not None]
        if not data:
            return None
        ordered = sorted(data)
        if len(ordered) == 1:
            return ordered[0]
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio))))
        return ordered[index]

    def _record_runtime_metrics(
        self,
        *,
        telemetry: TelemetryMap | None,
        person_count: int,
        complexity_score: float,
        selected_for_identity: int,
    ) -> None:
        inference_total_ms = telemetry_duration_ms(telemetry, "capture_sampled_at", "inference_result_received_at")
        inference_compute_ms = telemetry_duration_ms(telemetry, "inference_batch_started_at", "inference_batch_finished_at")
        inference_batch_wait_ms = telemetry_duration_ms(telemetry, "inference_worker_received_at", "inference_batch_started_at")
        processing_ms = telemetry_duration_ms(telemetry, "processing_started_at", "processing_finished_at")
        if inference_total_ms is not None:
            self._recent_inference_total_ms.append(float(inference_total_ms))
        if inference_compute_ms is not None:
            self._recent_inference_compute_ms.append(float(inference_compute_ms))
        if inference_batch_wait_ms is not None:
            self._recent_inference_batch_wait_ms.append(float(inference_batch_wait_ms))
        if processing_ms is not None:
            self._recent_processing_ms.append(float(processing_ms))
        self._recent_person_count.append(int(person_count))
        self._recent_complexity_score.append(float(complexity_score))
        self._recent_inference_backlog.append(len(self._pending_inference_order))
        self._recent_identity_selected_persons.append(max(0, int(selected_for_identity)))
        self._selected_identity_persons_total += max(0, int(selected_for_identity))
        self._evaluate_runtime_degrade()
        self._maybe_log_runtime_summary()

    def _mark_processed_frame_state(self, telemetry: TelemetryMap | None) -> None:
        sampled_at = _parse_iso_datetime((telemetry or {}).get("capture_sampled_at"))
        if sampled_at is not None:
            self._latest_processed_capture_sampled_at = sampled_at
            self._recent_latest_frame_age_ms.append(
                round((datetime.now() - sampled_at).total_seconds() * 1000.0, 1)
            )
        self._processed_completed_at.append(time.monotonic())

    def _record_identity_fallback(self) -> None:
        self._identity_fallback_without_enrichment_count += 1

    def _evaluate_runtime_degrade(self) -> None:
        p95_latency = self._percentile(self._recent_inference_total_ms, 0.95)
        if p95_latency is None:
            return
        complexity_avg = mean(self._recent_complexity_score) if self._recent_complexity_score else 0.0
        desired_level = 0
        reason = ""
        if p95_latency >= float(getattr(settings, "CAMERA_LATENCY_DEGRADE_P95_MS_L3", 1700.0)):
            desired_level = 3
            reason = f"p95_latency={p95_latency:.1f}"
        elif p95_latency >= float(getattr(settings, "CAMERA_LATENCY_DEGRADE_P95_MS_L2", 1200.0)):
            desired_level = 2
            reason = f"p95_latency={p95_latency:.1f}"
        elif (
            p95_latency >= float(getattr(settings, "CAMERA_LATENCY_DEGRADE_P95_MS_L1", 850.0))
            or complexity_avg >= float(getattr(settings, "CAMERA_COMPLEXITY_DEGRADE_THRESHOLD", 5.0))
        ):
            desired_level = 1
            reason = f"p95_latency={p95_latency:.1f},complexity_avg={complexity_avg:.2f}"
        recover_threshold = float(getattr(settings, "CAMERA_LATENCY_RECOVER_P95_MS", 500.0))
        if self.status.degrade_level > 0 and p95_latency <= recover_threshold and complexity_avg < 2.0:
            desired_level = 0
            reason = f"recover p95_latency={p95_latency:.1f}"
        now_ts = time.monotonic()
        if bool(getattr(settings, "IDENTITY_SUSPEND_ON_HIGH_INFERENCE_LATENCY", True)) and p95_latency >= float(
            getattr(settings, "IDENTITY_HIGH_LATENCY_THRESHOLD_MS", 900.0)
        ):
            suspend_seconds = float(getattr(settings, "IDENTITY_SUSPEND_SECONDS", 8.0))
            self._identity_suspended_until = max(
                self._identity_suspended_until,
                now_ts + suspend_seconds,
            )
            self._identity_suspended_until_at = datetime.now() + timedelta(seconds=suspend_seconds)

        current_level = int(self.status.degrade_level)
        if desired_level == current_level:
            self._degrade_candidate_level = desired_level
            self._degrade_candidate_reason = reason
            self._degrade_candidate_since = now_ts
            self._current_profile_state = self._compute_runtime_profile_state()
            return

        if (
            self._degrade_candidate_level != desired_level
            or self._degrade_candidate_reason != reason
        ):
            self._degrade_candidate_level = desired_level
            self._degrade_candidate_reason = reason
            self._degrade_candidate_since = now_ts
            self._current_profile_state = self._compute_runtime_profile_state()
            return

        min_dwell_seconds = float(getattr(settings, "CAMERA_DEGRADE_MIN_DWELL_SECONDS", 15.0))
        candidate_hold_seconds = float(
            getattr(
                settings,
                "CAMERA_DEGRADE_ENTER_HOLD_SECONDS" if desired_level > current_level else "CAMERA_DEGRADE_RECOVER_HOLD_SECONDS",
                10.0 if desired_level > current_level else 25.0,
            )
        )
        if now_ts - self._degrade_level_changed_at < min_dwell_seconds:
            self._current_profile_state = self._compute_runtime_profile_state()
            return
        if now_ts - self._degrade_candidate_since < candidate_hold_seconds:
            self._current_profile_state = self._compute_runtime_profile_state()
            return

        next_level = (
            min(current_level + 1, desired_level)
            if desired_level > current_level
            else max(current_level - 1, desired_level)
        )
        if next_level != current_level:
            self.status.degrade_level = next_level
            self._degrade_level_changed_at = now_ts
            self._last_degrade_reason = reason
            self._current_profile_state = self._compute_runtime_profile_state()
            if now_ts - self._last_degrade_log_at >= 1.0:
                logger.info(
                    "Camera degrade level changed camera=%s level=%s profile=%s reason=%s process_fps=%s identity_enabled=%s pose_enabled=%s desired_level=%s",
                    self.camera_id,
                    next_level,
                    self._current_profile_state.profile,
                    reason,
                    self._current_profile_state.process_fps,
                    self._current_profile_state.identity_enabled,
                    self._current_profile_state.pose_enabled,
                    desired_level,
                )
                self._last_degrade_log_at = now_ts
        else:
            self._current_profile_state = self._compute_runtime_profile_state()

    def _hot_camera_recommendation(self) -> dict[str, Any]:
        avg_latency = round(mean(self._recent_inference_total_ms), 1) if self._recent_inference_total_ms else None
        p95_latency = self._percentile(self._recent_inference_total_ms, 0.95)
        avg_batch_wait = round(mean(self._recent_inference_batch_wait_ms), 1) if self._recent_inference_batch_wait_ms else None
        p95_batch_wait = self._percentile(self._recent_inference_batch_wait_ms, 0.95)
        avg_latest_frame_age = round(mean(self._recent_latest_frame_age_ms), 1) if self._recent_latest_frame_age_ms else None
        avg_person_count = round(mean(self._recent_person_count), 2) if self._recent_person_count else None
        avg_backlog = round(mean(self._recent_inference_backlog), 2) if self._recent_inference_backlog else None
        hot_reasons: list[str] = []
        if avg_latency is not None and avg_latency > float(getattr(settings, "CAMERA_HOT_AVG_INFERENCE_TOTAL_MS", 600.0)):
            hot_reasons.append(f"avg_latency={avg_latency:.1f}")
        if p95_latency is not None and p95_latency > float(getattr(settings, "CAMERA_HOT_P95_INFERENCE_TOTAL_MS", 1200.0)):
            hot_reasons.append(f"p95_latency={p95_latency:.1f}")
        if avg_batch_wait is not None and avg_batch_wait > float(getattr(settings, "CAMERA_HOT_AVG_INFERENCE_BATCH_WAIT_MS", 250.0)):
            hot_reasons.append(f"avg_batch_wait={avg_batch_wait:.1f}")
        if p95_batch_wait is not None and p95_batch_wait > float(getattr(settings, "CAMERA_HOT_P95_INFERENCE_BATCH_WAIT_MS", 600.0)):
            hot_reasons.append(f"p95_batch_wait={p95_batch_wait:.1f}")
        if avg_latest_frame_age is not None and avg_latest_frame_age > float(getattr(settings, "CAMERA_HOT_LATEST_FRAME_AGE_MS", 900.0)):
            hot_reasons.append(f"latest_frame_age={avg_latest_frame_age:.1f}")
        if avg_person_count is not None and avg_person_count > float(getattr(settings, "CAMERA_HOT_AVG_PERSON_COUNT", 1.5)):
            hot_reasons.append(f"avg_person_count={avg_person_count:.2f}")
        if avg_backlog is not None and avg_backlog > float(getattr(settings, "CAMERA_HOT_AVG_BACKLOG", 0.5)):
            hot_reasons.append(f"avg_backlog={avg_backlog:.2f}")
        if self._inference_backpressure_skip_count >= int(getattr(settings, "CAMERA_HOT_BACKPRESSURE_SKIP_COUNT", 3)):
            hot_reasons.append(f"backpressure_skips={self._inference_backpressure_skip_count}")
        hotness_score = self._camera_hotness_score()
        hot_threshold = float(getattr(settings, "CAMERA_RUNTIME_HOT_CAMERA_SCORE_THRESHOLD", 3.0))
        if hotness_score >= hot_threshold and not hot_reasons:
            hot_reasons.append(f"hotness_score={hotness_score:.2f}")

        hot_camera = bool(hot_reasons)
        recommended_profile = "balanced"
        recommended_reason = "current load is within balanced profile range"
        if hot_camera:
            recommended_profile = "fast"
            recommended_reason = ",".join(hot_reasons)
        elif (
            avg_latency is not None
            and avg_latency < 220.0
            and (p95_latency or 0.0) < 350.0
            and (avg_person_count or 0.0) <= 0.5
            and int(self.status.degrade_level) == 0
        ):
            recommended_profile = "accurate"
            recommended_reason = "latency headroom is high"
        return {
            "hot_camera": hot_camera,
            "recommended_profile": recommended_profile,
            "recommended_reason": recommended_reason,
            "recommended_target_shard": None,
        }

    def _maybe_log_runtime_summary(self) -> None:
        now_ts = time.monotonic()
        if now_ts - self._last_summary_logged_at < self._summary_interval_seconds:
            return
        self._last_summary_logged_at = now_ts
        payload = self.runtime_summary_snapshot()
        payload["summary_type"] = "camera_runtime"
        logger.info("CAMERA_RUNTIME_SUMMARY %s", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def enqueue_inference_result(self, request_id: str, detections: dict[str, Any]) -> None:
        self._inference_result_queue.put((request_id, detections))

    def enqueue_identity_result(self, request_id: str, identity_observations: dict[str, Any]) -> None:
        self._identity_result_queue.put((request_id, identity_observations))

    async def _has_active_visitor_exemption(self) -> bool:
        async with async_session() as session:
            current = datetime.now()
            query = select(VisitorRegistration.id).where(
                VisitorRegistration.start_time <= current,
                VisitorRegistration.end_time >= current,
            ).limit(1)
            result = await session.execute(query)
            return result.scalar_one_or_none() is not None

    async def _load_supervision_runtime_settings(self) -> dict[str, Any]:
        async with async_session() as session:
            settings_row = (
                await session.execute(
                    select(SupervisionSettings).order_by(SupervisionSettings.updated_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if settings_row is None:
                return {
                    "other_person_scope": [],
                    "workshop_overcapacity_enabled": False,
                    "workshop_overcapacity_limit": None,
                    "alert_cooldown_seconds": settings.VIOLATION_ALERT_COOLDOWN_SECONDS,
                }
            return {
                "other_person_scope": _parse_scope(getattr(settings_row, "other_person_scope", None)),
                "workshop_overcapacity_enabled": bool(
                    getattr(settings_row, "workshop_overcapacity_enabled", 0)
                ),
                "workshop_overcapacity_limit": getattr(
                    settings_row,
                    "workshop_overcapacity_limit",
                    None,
                ),
                "alert_cooldown_seconds": getattr(
                    settings_row,
                    "alert_cooldown_seconds",
                    settings.VIOLATION_ALERT_COOLDOWN_SECONDS,
                ),
            }

    def _refresh_runtime_camera_config(self) -> None:
        config = _build_pipeline_camera_config(self.camera)
        refresh_interval = max(
            1,
            settings.CAMERA_RUNTIME_VISITOR_EXEMPTION_REFRESH_SECONDS,
        )
        now_ts = time.monotonic()
        if now_ts - self._visitor_exemption_checked_at < refresh_interval:
            config["visitor_exemption_active"] = self._visitor_exemption_active
            config["other_person_scope"] = self._cached_runtime_settings.get("other_person_scope", [])
            config["workshop_overcapacity_enabled"] = bool(
                self._cached_runtime_settings.get("workshop_overcapacity_enabled", False)
            )
            config["workshop_overcapacity_limit"] = self._cached_runtime_settings.get(
                "workshop_overcapacity_limit"
            )
            config["workshop_overcapacity_total_person_count"] = self._registry.get_total_person_count(
                exclude_camera_id=self.camera_id,
            )
            config["alert_cooldown_seconds"] = self._cached_runtime_settings.get(
                "alert_cooldown_seconds",
                settings.VIOLATION_ALERT_COOLDOWN_SECONDS,
            )
            self.pipeline.set_camera_config(config)
            return
        if self._main_loop is None:
            config["visitor_exemption_active"] = self._visitor_exemption_active
            self.pipeline.set_camera_config(config)
            return
        if not self._runtime_refresh_inflight:
            self._runtime_refresh_inflight = True

            async def _refresh_runtime_cache() -> None:
                try:
                    visitor_exemption = await self._has_active_visitor_exemption()
                    runtime_settings = await self._load_supervision_runtime_settings()
                    self._visitor_exemption_active = bool(visitor_exemption)
                    self._cached_runtime_settings = runtime_settings
                    self._visitor_exemption_checked_at = time.monotonic()
                except Exception:
                    logger.debug(
                        "Failed to refresh visitor exemption status for camera %s",
                        self.camera_id,
                        exc_info=True,
                    )
                finally:
                    self._runtime_refresh_inflight = False

            asyncio.run_coroutine_threadsafe(_refresh_runtime_cache(), self._main_loop)
        config["visitor_exemption_active"] = self._visitor_exemption_active
        config["other_person_scope"] = self._cached_runtime_settings.get("other_person_scope", [])
        config["workshop_overcapacity_enabled"] = bool(
            self._cached_runtime_settings.get("workshop_overcapacity_enabled", False)
        )
        config["workshop_overcapacity_limit"] = self._cached_runtime_settings.get(
            "workshop_overcapacity_limit"
        )
        config["workshop_overcapacity_total_person_count"] = self._registry.get_total_person_count(
            exclude_camera_id=self.camera_id,
        )
        config["alert_cooldown_seconds"] = self._cached_runtime_settings.get(
            "alert_cooldown_seconds",
            settings.VIOLATION_ALERT_COOLDOWN_SECONDS,
        )
        self.pipeline.set_camera_config(config)

    def _sync_global_workshop_overcapacity(self, frame: np.ndarray, result: dict[str, Any]) -> None:
        if not bool(self.pipeline._camera_config.get("workshop_overcapacity_enabled")):
            return

        limit = self.pipeline._camera_config.get("workshop_overcapacity_limit")
        if not isinstance(limit, int) or limit < 0:
            return

        persons = result.get("persons", [])
        frame_action_violations = result.setdefault("action_violations", [])
        total_person_count = self._registry.get_total_person_count(
            exclude_camera_id=self.camera_id,
            replacement_count=len(persons),
        )
        over_limit = total_person_count > limit

        result["action_violations"] = [
            item
            for item in frame_action_violations
            if item.get("action") != "workshop_overcapacity"
        ]

        for person in persons:
            person_action_violations = [
                item
                for item in person.get("action_violations", [])
                if item.get("action") != "workshop_overcapacity"
            ]

            if over_limit:
                person_action_violations.append(
                    {
                        "action": "workshop_overcapacity",
                        "score": 1.0,
                    }
                )
                result["action_violations"].append(
                    {
                        "action": "workshop_overcapacity",
                        "class": "workshop_overcapacity",
                        "score": 1.0,
                        "box": person.get("box", [0, 0, 0, 0]),
                    }
                )

            person["action_violations"] = person_action_violations
            person["is_violation"] = bool(person.get("missing_ppe") or person_action_violations)

    def latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def latest_frame_jpeg(self, *, raw: bool = False) -> Optional[bytes]:
        with self._lock:
            if raw:
                if self._latest_raw_jpeg is not None:
                    return bytes(self._latest_raw_jpeg)
                frame = self._latest_frame.copy() if self._latest_frame is not None else None
                version = self._latest_frame_version
            else:
                if self._latest_annotated_jpeg is not None:
                    return bytes(self._latest_annotated_jpeg)
                if self._latest_raw_jpeg is not None:
                    return bytes(self._latest_raw_jpeg)
                frame = self._latest_frame.copy() if self._latest_frame is not None else None
                version = self._latest_frame_version
        if frame is None:
            return None
        encoded = self._encode_frame_jpeg(frame)
        if raw and encoded is not None:
            with self._lock:
                if version == self._latest_frame_version:
                    self._latest_raw_jpeg = bytes(encoded)
        return encoded

    def snapshot_status(self) -> dict:
        return {
            "camera_id": self.status.camera_id,
            "running": self.status.running,
            "online": self.status.online,
            "status": self.status.status,
            "last_frame_at": self.status.last_frame_at.isoformat() if self.status.last_frame_at else None,
            "last_error": self.status.last_error,
            "processed_frames": self.status.processed_frames,
            "dropped_frames": self.status.dropped_frames,
            "degrade_level": self.status.degrade_level,
            "camera_profile": self.status.camera_profile,
            "effective_processed_fps": self._effective_processed_fps(),
            "latest_frame_age_ms": self._latest_processed_frame_age_ms(),
            "replaced_pending_count": self._replaced_pending_oldest_count + self._replaced_pending_latest_count,
            "submit_queue_reject_count": self._submit_queue_reject_count,
            "identity_skip_ratio": self._identity_skip_ratio(),
            "camera_hotness_score": self._camera_hotness_score(),
        }

    def _build_annotated_preview_jpeg(
        self,
        frame: np.ndarray,
        result: dict[str, Any],
    ) -> Optional[bytes]:
        try:
            annotated = self.pipeline._annotate_frame(
                frame.copy(),
                list(result.get("persons") or []),
                dict(result.get("violation_detections") or {}),
                list(result.get("action_violations") or []),
            )
        except Exception:
            logger.exception("Failed to build annotated preview camera=%s", self.camera_id)
            annotated = frame
        return self._encode_frame_jpeg(annotated if annotated is not None else frame)

    def latest_person_overlays(self) -> dict[str, Any]:
        with self._lock:
            latest_result = self._latest_detection_result
            latest_frame = self._latest_frame
            last_frame_at = self.status.last_frame_at

        frame_height = int(latest_frame.shape[0]) if latest_frame is not None else 0
        frame_width = int(latest_frame.shape[1]) if latest_frame is not None else 0
        persons_payload: list[dict[str, Any]] = []
        if latest_result:
            for person in latest_result.get("persons", []):
                box = person.get("box")
                if not isinstance(box, list) or len(box) != 4:
                    continue
                try:
                    persons_payload.append(
                        {
                            "track_id": int(person.get("track_id")) if person.get("track_id") is not None else None,
                            "stable_track_id": int(person.get("stable_track_id")) if person.get("stable_track_id") is not None else None,
                            "raw_track_id": int(person.get("raw_track_id")) if person.get("raw_track_id") is not None else None,
                            "person_id": person.get("person_id"),
                            "person_name": person.get("person_name") or "未知人员",
                            "box": [float(value) for value in box],
                        }
                    )
                except (TypeError, ValueError):
                    continue
        return {
            "camera_id": self.camera_id,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "persons": persons_payload,
            "last_frame_at": last_frame_at.isoformat() if last_frame_at else None,
        }

    def runtime_summary_snapshot(self) -> dict[str, Any]:
        summary = {
            "camera_id": self.camera_id,
            "camera_name": getattr(self.camera, "name", None) or self.camera_id,
            "shard_index": int(getattr(settings, "CAMERA_MONITOR_SHARD_INDEX", 0)),
            "processed_frames": self.status.processed_frames,
            "dropped_frames": self.status.dropped_frames,
            "expired_inference_count": self._expired_inference_count,
            "expired_identity_count": self._expired_identity_count,
            "avg_inference_total_ms": round(mean(self._recent_inference_total_ms), 1) if self._recent_inference_total_ms else None,
            "p95_inference_total_ms": self._percentile(self._recent_inference_total_ms, 0.95),
            "avg_inference_compute_ms": round(mean(self._recent_inference_compute_ms), 1) if self._recent_inference_compute_ms else None,
            "p95_inference_compute_ms": self._percentile(self._recent_inference_compute_ms, 0.95),
            "avg_inference_batch_wait_ms": round(mean(self._recent_inference_batch_wait_ms), 1) if self._recent_inference_batch_wait_ms else None,
            "p95_inference_batch_wait_ms": self._percentile(self._recent_inference_batch_wait_ms, 0.95),
            "avg_processing_ms": round(mean(self._recent_processing_ms), 1) if self._recent_processing_ms else None,
            "avg_person_count": round(mean(self._recent_person_count), 2) if self._recent_person_count else None,
            "avg_complexity_score": round(mean(self._recent_complexity_score), 2) if self._recent_complexity_score else None,
            "avg_backlog": round(mean(self._recent_inference_backlog), 2) if self._recent_inference_backlog else None,
            "recent_backlog_avg": round(mean(self._recent_inference_backlog), 2) if self._recent_inference_backlog else None,
            "avg_identity_selected_persons": round(mean(self._recent_identity_selected_persons), 2) if self._recent_identity_selected_persons else None,
            "selected_identity_persons": self._selected_identity_persons_total,
            "effective_processed_fps": self._effective_processed_fps(),
            "latest_frame_age_ms": self._latest_processed_frame_age_ms(),
            "avg_latest_frame_age_ms": round(mean(self._recent_latest_frame_age_ms), 1) if self._recent_latest_frame_age_ms else None,
            "replaced_pending_count": self._replaced_pending_oldest_count + self._replaced_pending_latest_count,
            "replaced_pending_oldest_count": self._replaced_pending_oldest_count,
            "replaced_pending_latest_count": self._replaced_pending_latest_count,
            "submit_queue_reject_count": self._submit_queue_reject_count,
            "identity_requests_submitted": self._identity_requests_submitted,
            "identity_requests_skipped": self._identity_requests_skipped,
            "identity_async_merged_count": self._identity_async_merged_count,
            "identity_fallback_without_enrichment_count": self._identity_fallback_without_enrichment_count,
            "identity_skip_ratio": self._identity_skip_ratio(),
            "async_identity_merge_latency_ms": (
                round(mean(self._recent_async_identity_merge_latency_ms), 1)
                if self._recent_async_identity_merge_latency_ms
                else None
            ),
            "camera_hotness_score": self._camera_hotness_score(),
            "degrade_level": self.status.degrade_level,
            "camera_profile": self.status.camera_profile,
            "current_profile": self.status.camera_profile,
            "identity_suspended": time.monotonic() < self._identity_suspended_until,
            "identity_suspended_until": self._identity_suspended_until_at.isoformat() if self._identity_suspended_until_at and time.monotonic() < self._identity_suspended_until else None,
            "last_degrade_reason": self._last_degrade_reason,
            "inference_backpressure_skip_count": self._inference_backpressure_skip_count,
        }
        summary.update(self._hot_camera_recommendation())
        return summary

    def _effective_processed_fps(self) -> float | None:
        if len(self._processed_completed_at) < 2:
            return None
        elapsed = self._processed_completed_at[-1] - self._processed_completed_at[0]
        if elapsed <= 0.0:
            return None
        return round((len(self._processed_completed_at) - 1) / elapsed, 2)

    def _latest_processed_frame_age_ms(self) -> float | None:
        if self._latest_processed_capture_sampled_at is None:
            return None
        return round(
            (datetime.now() - self._latest_processed_capture_sampled_at).total_seconds() * 1000.0,
            1,
        )

    def _identity_skip_ratio(self) -> float | None:
        total = self._identity_requests_submitted + self._identity_requests_skipped
        if total <= 0:
            return None
        return round(self._identity_requests_skipped / total, 3)

    def _camera_hotness_score(self) -> float:
        score = 0.0
        avg_latency = mean(self._recent_inference_total_ms) if self._recent_inference_total_ms else 0.0
        p95_latency = self._percentile(self._recent_inference_total_ms, 0.95) or 0.0
        avg_batch_wait = mean(self._recent_inference_batch_wait_ms) if self._recent_inference_batch_wait_ms else 0.0
        p95_batch_wait = self._percentile(self._recent_inference_batch_wait_ms, 0.95) or 0.0
        avg_latest_frame_age = mean(self._recent_latest_frame_age_ms) if self._recent_latest_frame_age_ms else 0.0
        avg_person_count = mean(self._recent_person_count) if self._recent_person_count else 0.0
        avg_backlog = mean(self._recent_inference_backlog) if self._recent_inference_backlog else 0.0
        if avg_latency >= float(getattr(settings, "CAMERA_HOT_AVG_INFERENCE_TOTAL_MS", 600.0)):
            score += 1.0
        if p95_latency >= float(getattr(settings, "CAMERA_HOT_P95_INFERENCE_TOTAL_MS", 1200.0)):
            score += 1.0
        if avg_batch_wait >= float(getattr(settings, "CAMERA_HOT_AVG_INFERENCE_BATCH_WAIT_MS", 250.0)):
            score += 1.0
        if p95_batch_wait >= float(getattr(settings, "CAMERA_HOT_P95_INFERENCE_BATCH_WAIT_MS", 600.0)):
            score += 1.0
        if avg_latest_frame_age >= float(getattr(settings, "CAMERA_HOT_LATEST_FRAME_AGE_MS", 900.0)):
            score += 1.0
        if avg_person_count >= float(getattr(settings, "CAMERA_HOT_AVG_PERSON_COUNT", 1.5)):
            score += 1.0
        if avg_backlog >= float(getattr(settings, "CAMERA_HOT_AVG_BACKLOG", 0.5)):
            score += 1.0
        if self._inference_backpressure_skip_count > 0:
            score += min(1.0, self._inference_backpressure_skip_count / max(1, int(getattr(settings, "CAMERA_HOT_BACKPRESSURE_SKIP_COUNT", 3))))
        return round(score, 2)

    @staticmethod
    def _count_ppe_detections(ppe_detections: Any) -> int:
        if isinstance(ppe_detections, dict):
            return sum(len(value) for value in ppe_detections.values() if isinstance(value, list))
        if isinstance(ppe_detections, list):
            return len(ppe_detections)
        return 0

    def _compute_frame_complexity_score(
        self,
        *,
        person_count: int,
        ppe_detection_count: int,
    ) -> float:
        return float(person_count + ppe_detection_count)

    def _resolve_frame_request_id(
        self,
        request_id: str | None,
        telemetry: TelemetryMap | None,
    ) -> str:
        if request_id:
            return request_id
        telemetry_request_id = str((telemetry or {}).get("frame_request_id") or "")
        return telemetry_request_id

    def _log_frame_timing(
        self,
        *,
        stage: str,
        request_id: str | None,
        telemetry: TelemetryMap | None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "camera_id": self.camera_id,
            "stage": stage,
            "request_id": self._resolve_frame_request_id(request_id, telemetry),
            "capture_to_inference_enqueue_ms": telemetry_duration_ms(
                telemetry, "capture_sampled_at", "inference_publish_enqueued_at"
            ),
            "capture_read_ms": telemetry.get("capture_read_ms") if telemetry else None,
            "capture_resize_ms": telemetry.get("capture_resize_ms") if telemetry else None,
            "capture_raw_encode_ms": telemetry.get("capture_raw_encode_ms") if telemetry else None,
            "capture_cache_update_ms": telemetry.get("capture_cache_update_ms") if telemetry else None,
            "capture_frame_gap_ms": telemetry.get("capture_frame_gap_ms") if telemetry else None,
            "transport_inline_encode_ms": telemetry.get("transport_inline_encode_ms") if telemetry else None,
            "shared_frame_write_ms": telemetry.get("shared_frame_write_ms") if telemetry else None,
            "transport_prepare_wall_ms": telemetry.get("transport_prepare_wall_ms") if telemetry else None,
            "inference_submit_queue_wait_ms": telemetry_duration_ms(
                telemetry, "inference_submit_queued_at", "inference_dispatch_started_at"
            ),
            "inference_broker_wait_ms": telemetry_duration_ms(
                telemetry, "inference_publish_enqueued_at", "inference_publish_started_at"
            ),
            "inference_worker_wait_ms": telemetry_duration_ms(
                telemetry, "inference_publish_started_at", "inference_worker_received_at"
            ),
            "inference_batch_wait_ms": telemetry_duration_ms(
                telemetry, "inference_worker_received_at", "inference_batch_started_at"
            ),
            "inference_batch_id": telemetry.get("inference_batch_id") if telemetry else None,
            "inference_batch_collect_ms": telemetry.get("inference_batch_collect_ms") if telemetry else None,
            "inference_compute_ms": telemetry_duration_ms(
                telemetry, "inference_batch_started_at", "inference_batch_finished_at"
            ),
            "inference_result_publish_ms": telemetry_duration_ms(
                telemetry, "inference_batch_finished_at", "inference_result_published_at"
            ),
            "inference_result_broker_to_runtime_ms": telemetry_duration_ms(
                telemetry, "inference_result_broker_received_at", "inference_result_received_at"
            ),
            "inference_total_ms": telemetry_duration_ms(
                telemetry, "capture_sampled_at", "inference_result_received_at"
            ),
            "inference_batch_received_count": telemetry.get("inference_batch_received_count") if telemetry else None,
            "inference_batch_valid_count": telemetry.get("inference_batch_valid_count") if telemetry else None,
            "inference_batch_invalid_count": telemetry.get("inference_batch_invalid_count") if telemetry else None,
            "inference_batch_person_total": telemetry.get("inference_batch_person_total") if telemetry else None,
            "inference_batch_camera_count": telemetry.get("inference_batch_camera_count") if telemetry else None,
            "inference_batch_bucket": telemetry.get("inference_batch_bucket") if telemetry else None,
            "inference_batch_priority_reason": telemetry.get("inference_batch_priority_reason") if telemetry else None,
            "inference_batch_hot_camera_count": telemetry.get("inference_batch_hot_camera_count") if telemetry else None,
            "inference_batch_stale_frame_count": telemetry.get("inference_batch_stale_frame_count") if telemetry else None,
            "inference_batch_max_persons_per_frame": telemetry.get("inference_batch_max_persons_per_frame") if telemetry else None,
            "inference_batch_avg_persons_per_frame": telemetry.get("inference_batch_avg_persons_per_frame") if telemetry else None,
            "person_detect_ms": telemetry.get("person_detect_ms") if telemetry else None,
            "ppe_detect_ms": telemetry.get("ppe_detect_ms") if telemetry else None,
            "pose_detect_ms": telemetry.get("pose_detect_ms") if telemetry else None,
            "postprocess_ms": telemetry.get("postprocess_ms") if telemetry else None,
            "total_engine_ms": telemetry.get("total_engine_ms") if telemetry else None,
            "identity_enqueue_wait_ms": telemetry_duration_ms(
                telemetry, "inference_result_received_at", "identity_publish_enqueued_at"
            ),
            "identity_submit_queue_wait_ms": telemetry_duration_ms(
                telemetry, "identity_submit_queued_at", "identity_dispatch_started_at"
            ),
            "identity_payload_prepare_ms": telemetry.get("identity_payload_prepare_ms") if telemetry else None,
            "identity_frame_encode_ms": telemetry.get("identity_frame_encode_ms") if telemetry else None,
            "identity_crop_encode_ms": telemetry.get("identity_crop_encode_ms") if telemetry else None,
            "identity_broker_wait_ms": telemetry_duration_ms(
                telemetry, "identity_publish_enqueued_at", "identity_publish_started_at"
            ),
            "identity_worker_wait_ms": telemetry_duration_ms(
                telemetry, "identity_publish_started_at", "identity_worker_received_at"
            ),
            "identity_batch_wait_ms": telemetry_duration_ms(
                telemetry, "identity_worker_received_at", "identity_batch_started_at"
            ),
            "identity_batch_collect_ms": telemetry.get("identity_batch_collect_ms") if telemetry else None,
            "identity_compute_ms": telemetry_duration_ms(
                telemetry, "identity_batch_started_at", "identity_batch_finished_at"
            ),
            "identity_result_publish_ms": telemetry_duration_ms(
                telemetry, "identity_batch_finished_at", "identity_result_published_at"
            ),
            "identity_result_broker_to_runtime_ms": telemetry_duration_ms(
                telemetry, "identity_result_broker_received_at", "identity_result_received_at"
            ),
            "identity_face_detect_ms": telemetry.get("identity_face_detect_ms") if telemetry else None,
            "identity_reid_extract_ms": telemetry.get("identity_reid_extract_ms") if telemetry else None,
            "identity_face_match_ms": telemetry.get("identity_face_match_ms") if telemetry else None,
            "identity_payload_build_ms": telemetry.get("identity_payload_build_ms") if telemetry else None,
            "identity_total_ms": telemetry_duration_ms(
                telemetry, "inference_result_received_at", "identity_result_received_at"
            ),
            "identity_batch_received_count": telemetry.get("identity_batch_received_count") if telemetry else None,
            "identity_batch_valid_count": telemetry.get("identity_batch_valid_count") if telemetry else None,
            "identity_batch_invalid_count": telemetry.get("identity_batch_invalid_count") if telemetry else None,
            "processing_queue_wait_ms": telemetry_duration_ms(
                telemetry, "processing_enqueued_at", "processing_started_at"
            ),
            "pipeline_process_ms": telemetry.get("pipeline_process_ms") if telemetry else None,
            "workshop_overcapacity_sync_ms": telemetry.get("workshop_overcapacity_sync_ms") if telemetry else None,
            "annotated_preview_jpeg_ms": telemetry.get("annotated_preview_jpeg_ms") if telemetry else None,
            "state_update_ms": telemetry.get("state_update_ms") if telemetry else None,
            "processing_ms": telemetry_duration_ms(
                telemetry, "processing_started_at", "processing_finished_at"
            ),
            "preview_publish_queue_wait_ms": telemetry_duration_ms(
                telemetry, "processing_finished_at", "preview_publish_started_at"
            ),
            "preview_raw_encode_ms": telemetry.get("preview_raw_encode_ms") if telemetry else None,
            "preview_raw_write_ms": telemetry.get("preview_raw_write_ms") if telemetry else None,
            "preview_annotated_write_ms": telemetry.get("preview_annotated_write_ms") if telemetry else None,
            "preview_people_write_ms": telemetry.get("preview_people_write_ms") if telemetry else None,
            "preview_status_write_ms": telemetry.get("preview_status_write_ms") if telemetry else None,
            "preview_publish_store_ms": telemetry.get("preview_publish_store_ms") if telemetry else None,
            "end_to_preview_ms": telemetry_duration_ms(
                telemetry, "capture_sampled_at", "preview_snapshot_published_at"
            ),
            "preview_total_ms": telemetry_duration_ms(
                telemetry, "capture_sampled_at", "preview_snapshot_published_at"
            ),
            "persist_total_ms": telemetry_duration_ms(
                telemetry, "capture_sampled_at", "persist_finished_at"
            ),
            "persist_ms": telemetry_duration_ms(
                telemetry, "persist_started_at", "persist_finished_at"
            ),
            "frame_path": telemetry.get("shared_frame_path") if telemetry else None,
            "frame_transport_mode": telemetry.get("frame_transport_mode") if telemetry else None,
            "inference_batch_size": telemetry.get("inference_batch_size") if telemetry else None,
            "identity_batch_size": telemetry.get("identity_batch_size") if telemetry else None,
            "identity_selected_persons": telemetry.get("identity_selected_persons") if telemetry else None,
            "identity_total_persons": telemetry.get("identity_total_persons") if telemetry else None,
            "effective_processed_fps": self._effective_processed_fps(),
            "latest_frame_age_ms": self._latest_processed_frame_age_ms(),
            "replaced_pending_count": self._replaced_pending_oldest_count + self._replaced_pending_latest_count,
            "submit_queue_reject_count": self._submit_queue_reject_count,
            "identity_skip_ratio": self._identity_skip_ratio(),
            "async_identity_merge_latency_ms": (
                telemetry_duration_ms(telemetry, "identity_publish_enqueued_at", "identity_result_received_at")
                if telemetry
                else None
            ),
            "camera_hotness_score": self._camera_hotness_score(),
            "degrade_level": self.status.degrade_level,
            "camera_profile": self.status.camera_profile,
            "frame_deleted_at": telemetry.get("frame_deleted_at") if telemetry else None,
            "frame_delete_reason": telemetry.get("frame_delete_reason") if telemetry else None,
        }
        if extra:
            payload.update(extra)
        logger.info(
            "FRAME_TIMING %s",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        ordered_step_keys = [
            "capture_read_ms",
            "capture_resize_ms",
            "capture_raw_encode_ms",
            "capture_cache_update_ms",
            "transport_inline_encode_ms",
            "shared_frame_write_ms",
            "transport_prepare_wall_ms",
            "inference_submit_queue_wait_ms",
            "inference_broker_wait_ms",
            "inference_worker_wait_ms",
            "inference_batch_wait_ms",
            "person_detect_ms",
            "ppe_detect_ms",
            "pose_detect_ms",
            "postprocess_ms",
            "inference_compute_ms",
            "inference_result_publish_ms",
            "inference_result_broker_to_runtime_ms",
            "identity_enqueue_wait_ms",
            "identity_payload_prepare_ms",
            "identity_crop_encode_ms",
            "identity_frame_encode_ms",
            "identity_submit_queue_wait_ms",
            "identity_broker_wait_ms",
            "identity_worker_wait_ms",
            "identity_batch_wait_ms",
            "identity_face_detect_ms",
            "identity_reid_extract_ms",
            "identity_face_match_ms",
            "identity_payload_build_ms",
            "identity_compute_ms",
            "identity_result_publish_ms",
            "identity_result_broker_to_runtime_ms",
            "processing_queue_wait_ms",
            "pipeline_process_ms",
            "workshop_overcapacity_sync_ms",
            "annotated_preview_jpeg_ms",
            "state_update_ms",
            "processing_ms",
            "preview_publish_queue_wait_ms",
            "preview_raw_encode_ms",
            "preview_raw_write_ms",
            "preview_annotated_write_ms",
            "preview_people_write_ms",
            "preview_status_write_ms",
            "preview_publish_store_ms",
            "persist_ms",
        ]
        step_parts = [
            f"{key}={payload[key]}ms"
            for key in ordered_step_keys
            if payload.get(key) is not None
        ]
        logger.info(
            "FRAME_TIMING_STEPS camera=%s request=%s stage=%s end_to_preview_ms=%s inference_total_ms=%s identity_total_ms=%s persist_total_ms=%s steps=%s",
            payload.get("camera_id"),
            payload.get("request_id"),
            payload.get("stage"),
            payload.get("end_to_preview_ms"),
            payload.get("inference_total_ms"),
            payload.get("identity_total_ms"),
            payload.get("persist_total_ms"),
            " -> ".join(step_parts) if step_parts else "n/a",
        )

    def _mark_frame_deleted(
        self,
        telemetry: TelemetryMap | None,
        *,
        reason: str,
    ) -> TelemetryMap:
        updated = mark_telemetry(telemetry, "frame_deleted_at")
        updated["frame_delete_reason"] = reason
        return updated

    def _delete_shared_frame(
        self,
        frame_path: str | None,
        *,
        reason: str,
        telemetry: TelemetryMap | None,
    ) -> TelemetryMap:
        if not frame_path:
            return clone_telemetry(telemetry)
        try:
            self._shared_frame_store.delete_frame(frame_path, reason=reason)
        except Exception:
            logger.exception(
                "Failed to delete shared frame camera=%s frame_path=%s reason=%s",
                self.camera_id,
                frame_path,
                reason,
            )
            return clone_telemetry(telemetry)
        return self._mark_frame_deleted(telemetry, reason=reason)

    def _release_inference_frame(
        self,
        frame_path: str | None,
        *,
        reason: str,
        telemetry: TelemetryMap | None,
    ) -> TelemetryMap:
        if not frame_path:
            return clone_telemetry(telemetry)
        try:
            result = self._shared_frame_store.release_frame(
                frame_path,
                consumer="inference",
                reason=reason,
            )
        except Exception:
            logger.exception(
                "Failed to release shared inference frame camera=%s frame_path=%s reason=%s",
                self.camera_id,
                frame_path,
                reason,
            )
            return clone_telemetry(telemetry)
        if result.deleted:
            return self._mark_frame_deleted(telemetry, reason=reason)
        return clone_telemetry(telemetry)

    def _publish_live_preview_snapshot(self) -> None:
        if not self._preview_cache_enabled:
            return
        publish_started_at = datetime.now()
        with self._lock:
            raw_frame = self._latest_frame.copy() if self._latest_frame is not None else None
            raw_jpeg = bytes(self._latest_raw_jpeg) if self._latest_raw_jpeg is not None else None
            annotated_jpeg = (
                bytes(self._latest_annotated_jpeg)
                if self._latest_annotated_jpeg is not None
                else None
            )
            latest_result_request_id = self._latest_result_request_id
            latest_result_telemetry = clone_telemetry(self._latest_result_telemetry)
            preview_logged_request_id = self._latest_preview_logged_request_id

        people_payload = self.latest_person_overlays()
        status_payload = self.snapshot_status()
        preview_metrics = self._preview_store.publish_snapshot(
            camera_id=self.camera_id,
            raw_frame=raw_frame,
            raw_jpeg=raw_jpeg,
            annotated_jpeg=annotated_jpeg,
            people_payload=people_payload,
            status_payload=status_payload,
        )
        if latest_result_request_id and latest_result_request_id != preview_logged_request_id:
            updated_telemetry = mark_telemetry(
                latest_result_telemetry,
                "preview_publish_started_at",
                at=publish_started_at,
            )
            updated_telemetry = mark_telemetry(
                updated_telemetry,
                "preview_snapshot_published_at",
            )
            updated_telemetry.update(
                {
                    "preview_raw_encode_ms": preview_metrics.get("raw_encode_ms"),
                    "preview_raw_write_ms": preview_metrics.get("raw_write_ms"),
                    "preview_annotated_write_ms": preview_metrics.get("annotated_write_ms"),
                    "preview_people_write_ms": preview_metrics.get("people_write_ms"),
                    "preview_status_write_ms": preview_metrics.get("status_write_ms"),
                    "preview_publish_store_ms": preview_metrics.get("publish_snapshot_ms"),
                }
            )
            with self._lock:
                if self._latest_result_request_id == latest_result_request_id:
                    self._latest_result_telemetry = clone_telemetry(updated_telemetry)
                    self._latest_preview_logged_request_id = latest_result_request_id
            self._log_frame_timing(
                stage="preview_published",
                request_id=latest_result_request_id,
                telemetry=updated_telemetry,
            )

    def _run(self) -> None:
        while not self._stop_requested:
            source = None
            try:
                self.status.status = "connecting"
                source = self._open_frame_source()
                self.status.online = True
                self.status.status = "running"
                self.status.last_error = None
                self._capture_loop(source)
            except Exception as exc:
                self.status.online = False
                self.status.status = "reconnecting"
                self.status.last_error = str(exc)
                logger.warning("Camera runtime %s error: %s", self.camera_id, exc)
                if not self._stop_requested:
                    self._sleep_with_stop(settings.CAMERA_MONITOR_RECONNECT_SECONDS)
            finally:
                if source is not None:
                    source.close()

    def _capture_loop(self, source) -> None:
        process_fps = max(1, self._current_profile_state.process_fps)
        display_fps = max(1, settings.CAMERA_MONITOR_DISPLAY_FPS)
        capture_poll_fps = max(
            process_fps,
            int(getattr(settings, "CAMERA_CAPTURE_POLL_FPS", settings.CAMERA_MONITOR_DISPLAY_FPS)),
        )
        process_interval_seconds = 1.0 / process_fps
        display_interval_seconds = 1.0 / display_fps
        poll_timeout_seconds = max(0.05, 1.0 / capture_poll_fps)
        no_frame_timeout_seconds = max(
            1.0,
            float(settings.CAMERA_READ_FAILURE_THRESHOLD) * poll_timeout_seconds * 2.0,
        )
        event_buffer_interval_seconds = 1.0 / max(1, int(settings.EVENT_VIDEO_CAPTURE_FPS))
        video_source = f"camera:{self.camera_id}"
        last_successful_frame_at: datetime | None = None
        next_process_at = time.monotonic()
        next_display_encode_at = time.monotonic()
        next_event_buffer_at = time.monotonic()
        no_frame_started_at: float | None = None

        while not self._stop_requested and source.is_opened():
            read_started_at = time.perf_counter()
            if hasattr(source, "read_new_frame"):
                frame = source.read_new_frame(timeout=poll_timeout_seconds)
            else:
                frame = source.read()
            read_ms = round((time.perf_counter() - read_started_at) * 1000.0, 1)
            if frame is None:
                if no_frame_started_at is None:
                    no_frame_started_at = time.monotonic()
                no_frame_elapsed = time.monotonic() - no_frame_started_at
                if no_frame_elapsed >= no_frame_timeout_seconds:
                    raise RuntimeError(
                        f"No camera frame received for {no_frame_elapsed:.2f} seconds"
                    )
                continue
            no_frame_started_at = None

            resize_started_at = time.perf_counter()
            frame = self._resize_frame(frame)
            resize_ms = round((time.perf_counter() - resize_started_at) * 1000.0, 1)
            now = datetime.now()
            frame_gap_ms = (
                round((now - last_successful_frame_at).total_seconds() * 1000.0, 1)
                if last_successful_frame_at is not None
                else None
            )
            last_successful_frame_at = now
            monotonic_now = time.monotonic()
            raw_jpeg = None
            raw_encode_ms = 0.0
            if monotonic_now >= next_display_encode_at:
                raw_encode_started_at = time.perf_counter()
                raw_jpeg = self._encode_frame_jpeg(frame)
                raw_encode_ms = round((time.perf_counter() - raw_encode_started_at) * 1000.0, 1)
                next_display_encode_at = max(
                    next_display_encode_at + display_interval_seconds,
                    monotonic_now + display_interval_seconds * 0.5,
                )
            cache_update_started_at = time.perf_counter()
            with self._lock:
                self._latest_frame = frame
                self._latest_frame_version += 1
                if raw_jpeg is not None:
                    self._latest_raw_jpeg = raw_jpeg
                self.status.last_frame_at = now
                if monotonic_now >= next_event_buffer_at:
                    self._frame_buffer.append(
                        BufferedFrame(
                            timestamp=now,
                            frame=frame.copy(),
                        )
                    )
                    next_event_buffer_at = max(
                        next_event_buffer_at + event_buffer_interval_seconds,
                        monotonic_now + event_buffer_interval_seconds * 0.5,
                    )
            cache_update_ms = round((time.perf_counter() - cache_update_started_at) * 1000.0, 1)

            if monotonic_now >= next_process_at:
                self._refresh_runtime_camera_config()
                self._current_profile_state = self._compute_runtime_profile_state()
                process_fps = max(1, self._current_profile_state.process_fps)
                process_interval_seconds = 1.0 / process_fps
                capture_telemetry = {
                    "capture_sampled_at": telemetry_now(at=now),
                    "frame_request_id": "",
                    "capture_read_ms": read_ms,
                    "capture_resize_ms": resize_ms,
                    "capture_raw_encode_ms": raw_encode_ms,
                    "capture_cache_update_ms": cache_update_ms,
                    "capture_frame_gap_ms": frame_gap_ms,
                    "capture_poll_fps": capture_poll_fps,
                    "capture_process_fps": process_fps,
                    "camera_profile": self.status.camera_profile,
                    "degrade_level": self.status.degrade_level,
                }
                if self._queue_inference_enabled:
                    self._enqueue_inference_submit_task(
                        frame=frame,
                        video_source=video_source,
                        submitted_at=now,
                        telemetry=capture_telemetry,
                    )
                else:
                    self._enqueue_processing_task(
                        request_id=None,
                        frame=frame,
                        frame_path=None,
                        video_source=video_source,
                        detections_override=None,
                        identity_observations_override=None,
                        processed_at=now,
                        telemetry=capture_telemetry,
                    )
                next_process_at = max(next_process_at + process_interval_seconds, monotonic_now)

            self._cleanup_shared_frames_if_needed()
            self._flush_pending_event_video_clips(now)

    def _process_detection_result(
        self,
        *,
        request_id: str | None,
        frame: np.ndarray,
        frame_path: str | None,
        video_source: str,
        detections_override: Optional[dict[str, Any]],
        identity_observations_override: Optional[dict[str, Any]],
        processed_at: datetime,
        telemetry: TelemetryMap | None,
    ) -> None:
        processing_telemetry = mark_telemetry(telemetry, "processing_started_at")
        pipeline_started_at = time.perf_counter()
        result = self.pipeline.process_frame(
            frame,
            video_source=video_source,
            detections_override=detections_override,
            identity_observations_override=identity_observations_override,
            annotate=False,
        )
        processing_telemetry["pipeline_process_ms"] = round(
            (time.perf_counter() - pipeline_started_at) * 1000.0,
            1,
        )
        result["video_source"] = video_source
        result["alert_cooldown_seconds"] = int(
            self.pipeline._camera_config.get(
                "alert_cooldown_seconds",
                settings.VIOLATION_ALERT_COOLDOWN_SECONDS,
            )
        )
        workshop_sync_started_at = time.perf_counter()
        self._sync_global_workshop_overcapacity(frame, result)
        processing_telemetry["workshop_overcapacity_sync_ms"] = round(
            (time.perf_counter() - workshop_sync_started_at) * 1000.0,
            1,
        )
        person_count = len(result.get("persons") or [])
        ppe_detection_count = self._count_ppe_detections(result.get("ppe_detections"))
        selected_for_identity = int((processing_telemetry or {}).get("identity_selected_persons") or 0)
        frame_complexity_score = self._compute_frame_complexity_score(
            person_count=person_count,
            ppe_detection_count=ppe_detection_count,
        )
        result_telemetry = clone_telemetry(processing_telemetry)
        result_telemetry.update(
            {
                "person_count": person_count,
                "selected_for_identity": selected_for_identity,
                "frame_complexity_score": frame_complexity_score,
            }
        )
        annotated_started_at = time.perf_counter()
        annotated_jpeg = self._build_annotated_preview_jpeg(frame, result)
        result_telemetry["annotated_preview_jpeg_ms"] = round(
            (time.perf_counter() - annotated_started_at) * 1000.0,
            1,
        )
        if frame_path:
            result_telemetry = self._delete_shared_frame(
                frame_path,
                reason="processing_complete",
                telemetry=result_telemetry,
            )
        state_update_started_at = time.perf_counter()
        with self._lock:
            self._latest_detection_result = result
            self._latest_annotated_jpeg = annotated_jpeg
            self._latest_result_request_id = request_id
            self._latest_preview_logged_request_id = None
            self.status.processed_frames += 1
            self.status.last_frame_at = processed_at
        result_telemetry["state_update_ms"] = round(
            (time.perf_counter() - state_update_started_at) * 1000.0,
            1,
        )
        result_telemetry = mark_telemetry(result_telemetry, "processing_finished_at")
        with self._lock:
            if self._latest_result_request_id == request_id:
                self._latest_result_telemetry = clone_telemetry(result_telemetry)
        self._mark_processed_frame_state(result_telemetry)
        self._record_runtime_metrics(
            telemetry=result_telemetry,
            person_count=person_count,
            complexity_score=frame_complexity_score,
            selected_for_identity=selected_for_identity,
        )
        if self._preview_cache_enabled and self._main_loop is not None:
            self._main_loop.run_in_executor(
                _runtime_background_executor,
                self._publish_live_preview_snapshot,
            )
        self._log_frame_timing(
            stage="process_complete",
            request_id=request_id,
            telemetry=result_telemetry,
            extra={
                "persons": person_count,
                "person_count": person_count,
                "selected_for_identity": selected_for_identity,
                "frame_complexity_score": frame_complexity_score,
                "violations": len(result.get("action_violations") or []),
            },
        )
        if self._should_persist_result(result):
            self._schedule_persist(result, frame, request_id=request_id, telemetry=result_telemetry)

    def _merge_identity_observations_into_latest_result(
        self,
        *,
        identity_observations: dict[str, Any],
    ) -> bool:
        with self._lock:
            latest_result = self._latest_detection_result
            if not latest_result:
                return False
            persons = latest_result.get("persons") or []
            if not persons:
                return False

            appearance_features = list(identity_observations.get("appearance_features") or [])
            face_identities = list(identity_observations.get("person_face_identities") or [])
            detected_faces = list(identity_observations.get("detected_faces") or [])
            now = time.monotonic()
            active_tracking_keys: set[str] = set()
            persons_by_raw_track_id: dict[int, dict[str, Any]] = {}
            for person in persons:
                raw_track_id = person.get("raw_track_id")
                if raw_track_id is None:
                    continue
                try:
                    persons_by_raw_track_id[int(raw_track_id)] = person
                except (TypeError, ValueError):
                    continue

            for person in persons:
                track_id = person.get("track_id")
                fallback_id = person.get("raw_track_id", person.get("id", 0))
                raw_tracking_key = person.get("raw_tracking_key") or f"track:{fallback_id if fallback_id is not None else track_id}"
                active_tracking_keys.add(raw_tracking_key)

            for person_index, override in enumerate(face_identities):
                if not override:
                    continue
                raw_track_id = override.get("raw_track_id")
                try:
                    target_person = persons_by_raw_track_id.get(int(raw_track_id)) if raw_track_id is not None else None
                except (TypeError, ValueError):
                    target_person = None
                if target_person is None:
                    continue

                track_id = target_person.get("track_id")
                fallback_id = target_person.get("raw_track_id", target_person.get("id", 0))
                raw_tracking_key = target_person.get("raw_tracking_key") or f"track:{fallback_id if fallback_id is not None else track_id}"

                if person_index < len(appearance_features):
                    target_person["appearance_feature"] = appearance_features[person_index]

                identity = {
                    "person_id": override.get("person_id") or target_person.get("person_id"),
                    "person_name": override.get("person_name") or target_person.get("person_name") or "未知人员",
                    "face_matched": bool(override.get("face_matched")),
                    "identity_source": override.get("identity_source") or target_person.get("identity_source", "unknown"),
                    "face_observed_this_frame": bool(override.get("face_observed_this_frame")),
                    "face_confirmed_this_frame": bool(override.get("face_confirmed_this_frame")),
                    "subject_type": override.get("subject_type", target_person.get("subject_type", "unknown")),
                    "subject_supervision_scope": list(override.get("subject_supervision_scope") or target_person.get("subject_supervision_scope") or []),
                    "allowed_camera_ids": list(override.get("allowed_camera_ids") or target_person.get("allowed_camera_ids") or []),
                    "appointment_start": override.get("appointment_start", target_person.get("appointment_start")),
                    "appointment_end": override.get("appointment_end", target_person.get("appointment_end")),
                    "external_person_id": override.get("external_person_id", target_person.get("external_person_id")),
                    "face_embedding": override.get("face_embedding"),
                    "thumbnail": override.get("thumbnail"),
                    "tracking_key": raw_tracking_key,
                    "last_attempt_frame": self.pipeline.frame_count,
                    "last_seen_at": now,
                }
                self.pipeline._face_identity_cache[raw_tracking_key] = identity

                target_person["person_id"] = identity.get("person_id")
                target_person["person_name"] = identity.get("person_name")
                target_person["face_matched"] = bool(identity.get("face_matched"))
                target_person["identity_source"] = identity.get("identity_source", "unknown")
                target_person["face_observed_this_frame"] = bool(identity.get("face_observed_this_frame"))
                target_person["face_confirmed_this_frame"] = bool(identity.get("face_confirmed_this_frame"))
                target_person["subject_type"] = identity.get("subject_type", "unknown")
                target_person["subject_supervision_scope"] = list(identity.get("subject_supervision_scope") or [])
                target_person["allowed_camera_ids"] = list(identity.get("allowed_camera_ids") or [])
                target_person["appointment_start"] = identity.get("appointment_start")
                target_person["appointment_end"] = identity.get("appointment_end")
                target_person["external_person_id"] = identity.get("external_person_id")
                target_person["face_embedding"] = identity.get("face_embedding")
                target_person["thumbnail"] = identity.get("thumbnail")

            stale_keys = [
                key
                for key, value in self.pipeline._face_identity_cache.items()
                if key not in active_tracking_keys and now - float(value.get("last_seen_at", now)) > self.pipeline._face_identity_ttl_seconds
            ]
            for key in stale_keys:
                self.pipeline._face_identity_cache.pop(key, None)

            latest_result["detected_faces"] = detected_faces
            return True

    def _cleanup_shared_frames_if_needed(self) -> None:
        retention_seconds = max(1, int(getattr(settings, "SHARED_FRAME_RETENTION_SECONDS", 120)))
        now_ts = time.monotonic()
        if now_ts - self._last_shared_frame_cleanup_at < retention_seconds:
            return
        self._last_shared_frame_cleanup_at = now_ts
        try:
            removed = self._shared_frame_store.cleanup_stale_files(max_age_seconds=retention_seconds)
        except Exception:
            logger.exception("Failed to cleanup shared frame store for camera %s", self.camera_id)
            return
        if removed:
            logger.info(
                "Cleaned up %s stale shared frame file(s) for camera %s",
                removed,
                self.camera_id,
            )

    def _enqueue_inference_submit_task(
        self,
        *,
        frame: np.ndarray,
        video_source: str,
        submitted_at: datetime,
        telemetry: TelemetryMap | None,
    ) -> None:
        request_id = str(uuid4())
        request_telemetry = clone_telemetry(telemetry)
        request_telemetry["frame_request_id"] = request_id
        transport_prepare_started_at = time.perf_counter()
        transport_quality = max(35, min(90, int(getattr(settings, "INFERENCE_FRAME_JPEG_QUALITY", 80))))

        def _encode_inline_jpeg() -> tuple[Optional[bytes], float]:
            encode_started_at = time.perf_counter()
            encoded = self._encode_transport_jpeg(frame, quality=transport_quality)
            return encoded, round((time.perf_counter() - encode_started_at) * 1000.0, 1)

        def _write_shared_frame() -> tuple[str | None, float]:
            shared_write_started_at = time.perf_counter()
            written_path = self._shared_frame_store.write_frame(
                camera_id=self.camera_id,
                request_id=request_id,
                frame=frame,
                consumers_pending=["inference"],
            )
            return written_path, round((time.perf_counter() - shared_write_started_at) * 1000.0, 1)

        encode_future = _runtime_background_executor.submit(_encode_inline_jpeg)
        write_future = _runtime_background_executor.submit(_write_shared_frame)
        try:
            frame_jpeg, transport_inline_encode_ms = encode_future.result()
        except Exception:
            logger.exception(
                "Failed to encode inline inference JPEG for camera %s request_id=%s",
                self.camera_id,
                request_id,
            )
            frame_jpeg = None
            transport_inline_encode_ms = round(
                (time.perf_counter() - transport_prepare_started_at) * 1000.0,
                1,
            )
        request_telemetry["transport_inline_encode_ms"] = transport_inline_encode_ms
        frame_path: str | None = None
        try:
            frame_path, shared_frame_write_ms = write_future.result()
        except Exception:
            logger.exception(
                "Failed to write shared frame for camera %s request_id=%s; continue with inline frame payload",
                self.camera_id,
                request_id,
            )
            shared_frame_write_ms = round((time.perf_counter() - transport_prepare_started_at) * 1000.0, 1)
        request_telemetry["shared_frame_write_ms"] = shared_frame_write_ms
        request_telemetry["transport_prepare_wall_ms"] = round(
            (time.perf_counter() - transport_prepare_started_at) * 1000.0,
            1,
        )
        request_telemetry["shared_frame_path"] = frame_path
        request_telemetry["frame_transport_mode"] = (
            "shared_frame+inline_jpeg"
            if frame_path and frame_jpeg
            else "shared_frame"
            if frame_path
            else "inline_jpeg"
        )
        inference_options = {
            "camera_id": self.camera_id,
            "pose_enabled": self._current_profile_state.pose_enabled,
            "skip_pose_when_person_count_ge": int(getattr(settings, "SKIP_POSE_WHEN_PERSON_COUNT_GE", 3)),
            "skip_segmentation_when_person_count_ge": int(getattr(settings, "SKIP_SEGMENTATION_WHEN_PERSON_COUNT_GE", 5)),
            "skip_segmentation": self._current_profile_state.skip_segmentation,
            "max_persons_for_frame": self._current_profile_state.max_persons_for_frame,
            "max_full_inference_persons": self._current_profile_state.max_full_inference_persons,
            "force_fast_core_ppe_only": self._current_profile_state.force_fast_core_ppe_only,
        }
        if frame_path:
            request_telemetry = mark_telemetry(request_telemetry, "frame_persisted_to_shared_store_at")
        if frame_path is None and frame_jpeg is None:
            self.status.dropped_frames += 1
            logger.warning(
                "Dropping frame for camera %s request_id=%s because both shared frame write and inline JPEG encode failed",
                self.camera_id,
                request_id,
            )
            return
        task = PendingInferenceDispatchTask(
            request_id=request_id,
            frame_path=frame_path,
            frame_jpeg=frame_jpeg,
            inference_options=inference_options,
            frame=frame,
            video_source=video_source,
            submitted_at=submitted_at,
            telemetry=request_telemetry,
        )
        task.telemetry = mark_telemetry(task.telemetry, "inference_submit_queued_at")
        enqueue_result = self._registry.enqueue_inference_submit(self.camera_id, task)
        if enqueue_result.replaced_task is not None:
            self._replaced_pending_latest_count += 1
            self._inference_backpressure_skip_count += 1
            replaced_task = enqueue_result.replaced_task
            replaced_telemetry = self._release_inference_frame(
                getattr(replaced_task, "frame_path", None),
                reason="replaced_pending_latest",
                telemetry=getattr(replaced_task, "telemetry", None),
            )
            self._log_frame_timing(
                stage="replaced_pending_latest",
                request_id=getattr(replaced_task, "request_id", None),
                telemetry=replaced_telemetry,
            )
        if not enqueue_result.accepted:
            self._submit_queue_reject_count += 1
            released_telemetry = request_telemetry
            if frame_path:
                released_telemetry = self._release_inference_frame(
                    frame_path,
                    reason=enqueue_result.error_reason or "submit_queue_full",
                    telemetry=request_telemetry,
                )
            self._log_frame_timing(
                stage=enqueue_result.error_reason or "submit_queue_full",
                request_id=request_id,
                telemetry=released_telemetry,
            )
            logger.debug(
                "Skipping inference submit task for camera %s because shared submit queue is full",
                self.camera_id,
            )
            return

    def _submit_frame_for_inference(
        self,
        *,
        task: PendingInferenceDispatchTask,
    ) -> None:
        max_pending = max(1, int(settings.INFERENCE_MAX_PENDING_FRAMES_PER_CAMERA))
        should_skip_submit = False
        with self._lock:
            pending_count = len(self._pending_inference_order)
            if pending_count >= max_pending:
                should_skip_submit = True
        if should_skip_submit:
            self._inference_backpressure_skip_count += 1
            skipped_telemetry = self._release_inference_frame(
                task.frame_path,
                reason="inference_pending_full",
                telemetry=task.telemetry,
            )
            self.status.dropped_frames += 1
            self._log_frame_timing(
                stage="inference_pending_full",
                request_id=task.request_id,
                telemetry=skipped_telemetry,
            )
            return

        request_telemetry = mark_telemetry(task.telemetry, "inference_dispatch_started_at")
        request_telemetry["frame_request_id"] = task.request_id
        published = self._registry.publish_inference_frame(
            request_id=task.request_id,
            camera_id=self.camera_id,
            video_source=task.video_source,
            frame_path=task.frame_path,
            frame_jpeg=task.frame_jpeg,
            inference_options=task.inference_options,
            submitted_at=task.submitted_at,
            telemetry=request_telemetry,
        )
        if not published:
            failed_telemetry = self._release_inference_frame(
                task.frame_path,
                reason="inference_publish_failed",
                telemetry=request_telemetry,
            )
            self.status.dropped_frames += 1
            self.status.last_error = "Failed to publish frame to inference queue"
            self._log_frame_timing(
                stage="inference_publish_failed",
                request_id=task.request_id,
                telemetry=failed_telemetry,
            )
            return

        with self._lock:
            self._pending_inference_frames[task.request_id] = PendingInferenceFrame(
                request_id=task.request_id,
                submitted_at=task.submitted_at,
                video_source=task.video_source,
                frame_path=task.frame_path,
                frame_jpeg=task.frame_jpeg,
                frame=task.frame,
                telemetry=request_telemetry,
            )
            self._pending_inference_order.append(task.request_id)

    def _drain_completed_inference(self) -> None:
        while True:
            try:
                request_id, detections = self._inference_result_queue.get_nowait()
            except Empty:
                break
            with self._lock:
                if request_id in self._pending_inference_frames:
                    self._completed_inference_results[request_id] = detections

        self._expire_stale_pending_inference()

        while True:
            ready = self._pop_next_ready_inference()
            if ready is None:
                break
            pending, detections = ready
            self._route_inference_result(
                pending=pending,
                detections=detections,
            )

    def _route_inference_result(
        self,
        *,
        pending: PendingInferenceFrame,
        detections: dict[str, Any],
    ) -> None:
        inference_telemetry = clone_telemetry(detections.pop("_telemetry", None))
        inference_telemetry = mark_telemetry(inference_telemetry, "inference_result_received_at")
        inference_error = str(detections.pop("_error", "") or "").strip()
        inference_error_type = str(detections.pop("_error_type", "") or "").strip()
        if inference_error_type in {
            "missing_shared_frame",
            "stale_shared_frame",
            "decode_failed",
        } or inference_error in {"missing_frame", "invalid_frame"}:
            self.status.dropped_frames += 1
            released_telemetry = self._release_inference_frame(
                pending.frame_path,
                reason=f"inference_{inference_error_type or inference_error or 'invalid'}",
                telemetry=inference_telemetry,
            )
            self._log_frame_timing(
                stage=f"inference_{inference_error_type or inference_error}",
                request_id=pending.request_id,
                telemetry=released_telemetry,
            )
            return
        if not self._queue_identity_enabled:
            released_telemetry = self._release_inference_frame(
                pending.frame_path,
                reason="inference_processed_locally",
                telemetry=inference_telemetry,
            )
            self._enqueue_processing_task(
                request_id=pending.request_id,
                frame=pending.frame,
                frame_path=None,
                video_source=pending.video_source,
                detections_override=detections,
                identity_observations_override=None,
                processed_at=datetime.now(),
                telemetry=released_telemetry,
            )
            return

        persons = list(detections.get("persons") or [])
        person_count = len(persons)
        inference_telemetry["person_count"] = person_count
        inference_telemetry["frame_complexity_score"] = self._compute_frame_complexity_score(
            person_count=person_count,
            ppe_detection_count=self._count_ppe_detections(detections.get("ppe_detections")),
        )
        inference_telemetry["identity_total_persons"] = person_count
        if not persons:
            released_telemetry = self._release_inference_frame(
                pending.frame_path,
                reason="inference_no_persons",
                telemetry=inference_telemetry,
            )
            self._identity_requests_skipped += 1
            self._enqueue_processing_task(
                request_id=pending.request_id,
                frame=pending.frame,
                frame_path=None,
                video_source=pending.video_source,
                detections_override=detections,
                identity_observations_override=None,
                processed_at=datetime.now(),
                telemetry=released_telemetry,
            )
            return
        identity_persons = self._select_identity_persons(persons)
        if not identity_persons:
            skip_stage = (
                "identity_suspended"
                if not self._current_profile_state.identity_enabled
                else "identity_skipped_cached_tracks"
            )
            self._identity_requests_skipped += 1
            released_telemetry = self._release_inference_frame(
                pending.frame_path,
                reason=skip_stage,
                telemetry=inference_telemetry,
            )
            released_telemetry["identity_selected_persons"] = 0
            self._enqueue_processing_without_identity(
                request_id=pending.request_id,
                frame=pending.frame,
                frame_path=None,
                video_source=pending.video_source,
                detections_override=detections,
                telemetry=released_telemetry,
            )
            self._log_frame_timing(
                stage=skip_stage,
                request_id=pending.request_id,
                telemetry=released_telemetry,
                extra={"persons": person_count, "identity_selected_persons": 0},
            )
            return

        self._enqueue_processing_without_identity(
            request_id=pending.request_id,
            frame=pending.frame,
            frame_path=None,
            video_source=pending.video_source,
            detections_override=detections,
            telemetry=inference_telemetry,
        )

        identity_payload_prepare_started_at = time.perf_counter()
        prepared_identity_persons, all_have_crops, identity_crop_encode_ms = self._prepare_identity_person_payloads(
            pending.frame,
            identity_persons,
        )
        inference_telemetry["identity_payload_prepare_ms"] = round(
            (time.perf_counter() - identity_payload_prepare_started_at) * 1000.0,
            1,
        )
        inference_telemetry["identity_crop_encode_ms"] = identity_crop_encode_ms

        try:
            face_detection_requested = self.pipeline._should_run_face_detection(identity_persons)
        except Exception:
            logger.exception(
                "Failed to evaluate face detection scheduling for camera %s; skip async identity enrichment",
                self.camera_id,
            )
            self._identity_requests_skipped += 1
            self._record_identity_fallback()
            fallback_telemetry = self._release_inference_frame(
                pending.frame_path,
                reason="identity_schedule_failed",
                telemetry=inference_telemetry,
            )
            self._log_frame_timing(
                stage="identity_schedule_failed",
                request_id=pending.request_id,
                telemetry=fallback_telemetry,
            )
            return

        identity_frame_jpeg = None
        if not all_have_crops:
            identity_frame_encode_started_at = time.perf_counter()
            identity_frame_jpeg = (
                self._encode_transport_jpeg(frame=pending.frame, quality=60)
                if pending.frame is not None
                else None
            )
            inference_telemetry["identity_frame_encode_ms"] = round(
                (time.perf_counter() - identity_frame_encode_started_at) * 1000.0,
                1,
            )
        else:
            inference_telemetry["identity_frame_encode_ms"] = 0.0
        if not all_have_crops and identity_frame_jpeg is None:
            self._identity_requests_skipped += 1
            self._record_identity_fallback()
            released_telemetry = self._release_inference_frame(
                pending.frame_path,
                reason="identity_inline_encode_failed",
                telemetry=inference_telemetry,
            )
            self._log_frame_timing(
                stage="identity_inline_encode_failed",
                request_id=pending.request_id,
                telemetry=released_telemetry,
            )
            return
        released_telemetry = self._release_inference_frame(
            pending.frame_path,
            reason="inference_released_after_identity_dispatch",
            telemetry=inference_telemetry,
        )
        released_telemetry["identity_selected_persons"] = len(identity_persons)
        released_telemetry["identity_total_persons"] = len(persons)
        released_telemetry["identity_transport_mode"] = (
            "person_crops"
            if all_have_crops
            else "person_crops+frame_jpeg"
            if identity_frame_jpeg
            else "frame_jpeg"
        )
        published = self._enqueue_identity_submit_task(
            request_id=pending.request_id,
            frame_path=None,
            frame_jpeg=identity_frame_jpeg,
            frame=None,
            video_source=pending.video_source,
            detections_override=detections,
            submitted_at=pending.submitted_at,
            face_detection_requested=face_detection_requested,
            identity_persons=prepared_identity_persons,
            telemetry=released_telemetry,
        )
        if not published:
            with self._lock:
                self._pending_identity_updates.pop(pending.request_id, None)
            self._identity_requests_skipped += 1
            self._record_identity_fallback()
            self._log_frame_timing(
                stage="identity_inline_dispatch_failed",
                request_id=pending.request_id,
                telemetry=released_telemetry,
            )
            return
        self._identity_requests_submitted += 1

    def _pop_next_ready_inference(
        self,
    ) -> Optional[tuple[PendingInferenceFrame, dict[str, Any]]]:
        with self._lock:
            if not self._pending_inference_order:
                return None
            request_id = next(
                (
                    pending_request_id
                    for pending_request_id in self._pending_inference_order
                    if pending_request_id in self._completed_inference_results
                    and pending_request_id in self._pending_inference_frames
                ),
                None,
            )
            if request_id is None:
                return None
            detections = self._completed_inference_results.get(request_id)
            pending = self._pending_inference_frames.get(request_id)
            if detections is None or pending is None:
                return None
            self._pending_inference_order.remove(request_id)
            self._pending_inference_frames.pop(request_id, None)
            self._completed_inference_results.pop(request_id, None)
        return pending, detections

    def _expire_stale_pending_inference(self) -> None:
        deadline = datetime.now().timestamp() - self._pending_inference_timeout_seconds
        expired_items: list[PendingInferenceFrame] = []
        with self._lock:
            for request_id in list(self._pending_inference_order):
                pending = self._pending_inference_frames.get(request_id)
                if pending is None:
                    self._pending_inference_order.remove(request_id)
                    self._completed_inference_results.pop(request_id, None)
                    continue
                if pending.submitted_at.timestamp() < deadline:
                    expired_items.append(pending)
                    self._pending_inference_frames.pop(request_id, None)
                    self._completed_inference_results.pop(request_id, None)
                    self._pending_inference_order.remove(request_id)
        if expired_items:
            self._expired_inference_count += len(expired_items)
            self.status.dropped_frames += len(expired_items)
            logger.warning(
                "Expired %s pending inference frame(s) for camera %s",
                len(expired_items),
                self.camera_id,
            )
            for pending in expired_items:
                expired_telemetry = self._release_inference_frame(
                    pending.frame_path,
                    reason="inference_expired",
                    telemetry=pending.telemetry,
                )
                self._log_frame_timing(
                    stage="inference_expired",
                    request_id=pending.request_id,
                    telemetry=expired_telemetry,
                )

    def _clear_pending_inference(self) -> None:
        with self._lock:
            pending_frames = list(self._pending_inference_frames.values())
            self._pending_inference_frames.clear()
            self._pending_inference_order.clear()
            self._completed_inference_results.clear()
        for pending in pending_frames:
            self._release_inference_frame(
                pending.frame_path,
                reason="inference_cleared",
                telemetry=pending.telemetry,
            )
        while True:
            try:
                self._inference_result_queue.get_nowait()
            except Empty:
                break

    def _enqueue_identity_submit_task(
        self,
        *,
        request_id: str,
        frame_path: str | None,
        frame_jpeg: bytes | None,
        frame: np.ndarray | None,
        video_source: str,
        detections_override: dict[str, Any],
        identity_persons: list[dict[str, Any]],
        submitted_at: datetime,
        face_detection_requested: bool,
        telemetry: TelemetryMap | None,
    ) -> bool:
        task = PendingIdentityDispatchTask(
            request_id=request_id,
            frame_path=frame_path,
            frame_jpeg=frame_jpeg,
            frame=frame,
            video_source=video_source,
            detections_override=detections_override,
            identity_persons=identity_persons,
            submitted_at=submitted_at,
            face_detection_requested=face_detection_requested,
            telemetry=clone_telemetry(telemetry),
        )
        task.telemetry = mark_telemetry(task.telemetry, "identity_submit_queued_at")
        enqueue_result = self._registry.enqueue_identity_submit(self.camera_id, task)
        if enqueue_result.replaced_task is not None:
            self._replaced_pending_latest_count += 1
            replaced_task = enqueue_result.replaced_task
            self._log_frame_timing(
                stage="replaced_pending_latest",
                request_id=getattr(replaced_task, "request_id", None),
                telemetry=getattr(replaced_task, "telemetry", None),
            )
        if enqueue_result.accepted:
            return True
        self._submit_queue_reject_count += 1
        logger.debug(
            "Identity submit queue is full for camera %s; fallback to local identity path",
            self.camera_id,
        )
        return False

    def _submit_frame_for_identity(
        self,
        *,
        task: PendingIdentityDispatchTask,
    ) -> bool:
        max_pending = max(1, int(settings.IDENTITY_MAX_PENDING_FRAMES_PER_CAMERA))
        dropped_pending: PendingIdentityFrame | None = None
        dropped_update: PendingIdentityUpdate | None = None
        with self._lock:
            pending_count = len(self._pending_identity_order) + len(self._pending_identity_update_order)
            if pending_count >= max_pending:
                if self._pending_identity_order:
                    oldest_request_id = self._pending_identity_order.popleft()
                    dropped_pending = self._pending_identity_frames.pop(oldest_request_id, None)
                    self._completed_identity_results.pop(oldest_request_id, None)
                elif self._pending_identity_update_order:
                    oldest_request_id = self._pending_identity_update_order.popleft()
                    dropped_update = self._pending_identity_updates.pop(oldest_request_id, None)
        if dropped_pending is not None:
            self._replaced_pending_oldest_count += 1
            self._log_frame_timing(
                stage="replaced_pending_oldest",
                request_id=dropped_pending.request_id,
                telemetry=dropped_pending.telemetry,
            )
            self._record_identity_fallback()
            self._enqueue_processing_without_identity(
                request_id=dropped_pending.request_id,
                frame=dropped_pending.frame,
                frame_path=None,
                video_source=dropped_pending.video_source,
                detections_override=dropped_pending.detections_override,
                telemetry=dropped_pending.telemetry,
            )
        if dropped_update is not None:
            self._replaced_pending_oldest_count += 1
            self._log_frame_timing(
                stage="replaced_pending_oldest",
                request_id=dropped_update.request_id,
                telemetry=dropped_update.telemetry,
            )

        persons = list(task.identity_persons or task.detections_override.get("persons") or [])
        request_telemetry = mark_telemetry(task.telemetry, "identity_dispatch_started_at")
        request_telemetry["frame_request_id"] = request_telemetry.get("frame_request_id") or task.request_id
        published = self._registry.publish_identity_frame(
            request_id=task.request_id,
            camera_id=self.camera_id,
            video_source=task.video_source,
            frame_path=task.frame_path,
            frame_jpeg=task.frame_jpeg,
            persons=persons,
            submitted_at=task.submitted_at,
            face_detection_requested=task.face_detection_requested,
            telemetry=request_telemetry,
        )
        if not published:
            self.status.last_error = "Failed to publish frame to identity queue"
            return False

        with self._lock:
            if task.frame is None:
                self._pending_identity_updates[task.request_id] = PendingIdentityUpdate(
                    request_id=task.request_id,
                    submitted_at=task.submitted_at,
                    video_source=task.video_source,
                    frame_path=task.frame_path,
                    frame_jpeg=task.frame_jpeg,
                    telemetry=request_telemetry,
                )
                self._pending_identity_update_order.append(task.request_id)
            else:
                self._pending_identity_frames[task.request_id] = PendingIdentityFrame(
                    request_id=task.request_id,
                    submitted_at=task.submitted_at,
                    video_source=task.video_source,
                    frame_path=task.frame_path,
                    frame_jpeg=task.frame_jpeg,
                    frame=task.frame,
                    detections_override=task.detections_override,
                    telemetry=request_telemetry,
                )
                self._pending_identity_order.append(task.request_id)
        return True

    def _drain_completed_identity(self) -> None:
        while True:
            try:
                request_id, identity_observations = self._identity_result_queue.get_nowait()
            except Empty:
                break
            with self._lock:
                if request_id in self._pending_identity_frames:
                    self._completed_identity_results[request_id] = identity_observations
                    continue
                pending_update = self._pending_identity_updates.pop(request_id, None)
            if pending_update is None:
                continue
            identity_telemetry = clone_telemetry(identity_observations.get("_telemetry"))
            identity_telemetry = mark_telemetry(identity_telemetry, "identity_result_received_at")
            merge_latency_ms = telemetry_duration_ms(
                identity_telemetry,
                "identity_publish_enqueued_at",
                "identity_result_received_at",
            )
            if merge_latency_ms is not None:
                self._recent_async_identity_merge_latency_ms.append(float(merge_latency_ms))
            with self._lock:
                try:
                    self._pending_identity_update_order.remove(request_id)
                except ValueError:
                    pass
            if identity_observations.get("error"):
                logger.warning(
                    "Identity worker returned async update error for camera=%s request_id=%s: %s type=%s detail=%s frame_path=%s",
                    self.camera_id,
                    request_id,
                    identity_observations.get("error"),
                    identity_observations.get("error_type"),
                    identity_observations.get("error_detail"),
                    identity_observations.get("frame_path"),
                )
                continue
            if self._merge_identity_observations_into_latest_result(identity_observations=identity_observations):
                self._identity_async_merged_count += 1
                if self._preview_cache_enabled and self._main_loop is not None:
                    self._main_loop.run_in_executor(
                        _runtime_background_executor,
                        self._publish_live_preview_snapshot,
                    )
                self._log_frame_timing(
                    stage="identity_async_merged",
                    request_id=request_id,
                    telemetry=identity_telemetry,
                )

        self._expire_stale_pending_identity()

        while True:
            ready = self._pop_next_ready_identity()
            if ready is None:
                break
            pending, identity_observations = ready
            identity_telemetry = clone_telemetry(identity_observations.get("_telemetry"))
            identity_telemetry = mark_telemetry(identity_telemetry, "identity_result_received_at")
            if identity_observations.get("error"):
                logger.warning(
                    "Identity worker returned error for camera=%s request_id=%s: %s type=%s detail=%s frame_path=%s; continue without identity enrichment",
                    self.camera_id,
                    pending.request_id,
                    identity_observations.get("error"),
                    identity_observations.get("error_type"),
                    identity_observations.get("error_detail"),
                    identity_observations.get("frame_path"),
                )
                identity_observations = self._build_default_identity_observations(
                    pending.detections_override
                )
                self._record_identity_fallback()
            self._enqueue_processing_task(
                request_id=pending.request_id,
                frame=pending.frame,
                frame_path=None,
                video_source=pending.video_source,
                detections_override=pending.detections_override,
                identity_observations_override=identity_observations,
                processed_at=datetime.now(),
                telemetry=identity_telemetry,
            )

    def _pop_next_ready_identity(
        self,
    ) -> Optional[tuple[PendingIdentityFrame, dict[str, Any]]]:
        with self._lock:
            if not self._pending_identity_order:
                return None
            request_id = next(
                (
                    pending_request_id
                    for pending_request_id in self._pending_identity_order
                    if pending_request_id in self._completed_identity_results
                    and pending_request_id in self._pending_identity_frames
                ),
                None,
            )
            if request_id is None:
                return None
            identity_observations = self._completed_identity_results.get(request_id)
            pending = self._pending_identity_frames.get(request_id)
            if identity_observations is None or pending is None:
                return None
            self._pending_identity_order.remove(request_id)
            self._pending_identity_frames.pop(request_id, None)
            self._completed_identity_results.pop(request_id, None)
        return pending, identity_observations

    def _expire_stale_pending_identity(self) -> None:
        deadline = datetime.now().timestamp() - self._pending_identity_timeout_seconds
        update_deadline = datetime.now().timestamp() - self._pending_identity_update_timeout_seconds
        expired_items: list[PendingIdentityFrame] = []
        expired_updates: list[PendingIdentityUpdate] = []
        with self._lock:
            for request_id in list(self._pending_identity_order):
                pending = self._pending_identity_frames.get(request_id)
                if pending is None:
                    self._pending_identity_order.remove(request_id)
                    self._completed_identity_results.pop(request_id, None)
                    continue
                if pending.submitted_at.timestamp() < deadline:
                    expired_items.append(pending)
                    self._pending_identity_frames.pop(request_id, None)
                    self._completed_identity_results.pop(request_id, None)
                    self._pending_identity_order.remove(request_id)
            for request_id in list(self._pending_identity_update_order):
                pending_update = self._pending_identity_updates.get(request_id)
                if pending_update is None:
                    self._pending_identity_update_order.remove(request_id)
                    continue
                if pending_update.submitted_at.timestamp() < update_deadline:
                    expired_updates.append(pending_update)
                    self._pending_identity_updates.pop(request_id, None)
                    self._pending_identity_update_order.remove(request_id)
        if expired_items:
            self._expired_identity_count += len(expired_items)
            logger.warning(
                "Expired %s pending identity frame(s) for camera %s",
                len(expired_items),
                self.camera_id,
            )
            for pending in expired_items:
                self._log_frame_timing(
                    stage="identity_expired",
                    request_id=pending.request_id,
                    telemetry=pending.telemetry,
                )
                self._record_identity_fallback()
                self._enqueue_processing_without_identity(
                    request_id=pending.request_id,
                    frame=pending.frame,
                    frame_path=None,
                    video_source=pending.video_source,
                    detections_override=pending.detections_override,
                    telemetry=pending.telemetry,
                )
        if expired_updates:
            self._expired_identity_count += len(expired_updates)
            logger.warning(
                "Expired %s pending async identity update(s) for camera %s",
                len(expired_updates),
                self.camera_id,
            )
            for pending_update in expired_updates:
                self._log_frame_timing(
                    stage="identity_async_expired",
                    request_id=pending_update.request_id,
                    telemetry=pending_update.telemetry,
                )

    def _identity_cache_key_for_person(self, person: dict[str, Any]) -> tuple[str, Any]:
        raw_track_id = person.get("raw_track_id")
        if raw_track_id is None:
            raw_track_id = person.get("detector_track_id")
        if raw_track_id is None:
            raw_track_id = person.get("track_id")
        if raw_track_id is None:
            raw_track_id = person.get("id", 0)
        return f"track:{raw_track_id}", raw_track_id

    def _build_default_identity_observations(
        self,
        detections_override: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        persons = list((detections_override or {}).get("persons") or [])
        person_face_identities: list[dict[str, Any]] = []
        now = time.monotonic()
        cache_ttl = max(
            self.pipeline._face_identity_ttl_seconds,
            float(getattr(settings, "IDENTITY_PENDING_TIMEOUT_SECONDS", 10.0)),
        )
        for person in persons:
            tracking_key, raw_track_id = self._identity_cache_key_for_person(person)
            cached = self.pipeline._face_identity_cache.get(tracking_key)
            if cached is not None and now - float(cached.get("last_seen_at", now)) <= cache_ttl:
                person_face_identities.append(
                    {
                        "raw_track_id": raw_track_id,
                        "person_id": cached.get("person_id") or f"unknown:{self.camera_id}:{raw_track_id}",
                        "person_name": cached.get("person_name") or "未知人员",
                        "face_matched": bool(cached.get("face_matched")),
                        "identity_source": cached.get("identity_source", "cache"),
                        "face_observed_this_frame": False,
                        "face_confirmed_this_frame": False,
                        "subject_type": cached.get("subject_type", "unknown"),
                        "subject_supervision_scope": list(cached.get("subject_supervision_scope") or []),
                        "allowed_camera_ids": list(cached.get("allowed_camera_ids") or []),
                        "appointment_start": cached.get("appointment_start"),
                        "appointment_end": cached.get("appointment_end"),
                        "external_person_id": cached.get("external_person_id"),
                        "face_embedding": cached.get("face_embedding"),
                        "thumbnail": cached.get("thumbnail"),
                    }
                )
                continue
            unknown_id = f"unknown:{self.camera_id}:{raw_track_id}"
            person_face_identities.append(
                {
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
            )
        return {
            "appearance_features": [None for _ in persons],
            "detected_faces": [],
            "person_face_identities": person_face_identities,
        }

    def _select_identity_persons(
        self,
        persons: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not persons:
            return []
        if not self._current_profile_state.identity_enabled:
            return []
        max_persons = max(1, int(self._current_profile_state.max_identity_persons))
        candidates: list[tuple[int, float, int, dict[str, Any]]] = []
        current_frame = max(1, int(self.pipeline.frame_count))
        for index, person in enumerate(persons):
            tracking_key, _ = self._identity_cache_key_for_person(person)
            cached = self.pipeline._face_identity_cache.get(tracking_key)
            last_attempt_frame = int((cached or {}).get("last_attempt_frame", 0))
            frames_since_attempt = current_frame - last_attempt_frame
            priority: int | None
            if cached is None:
                priority = 0
            elif not bool(cached.get("face_matched")):
                if frames_since_attempt < self.pipeline._face_retry_interval_frames:
                    continue
                priority = 1
            else:
                if self._current_profile_state.identity_unknown_only:
                    continue
                if frames_since_attempt < self.pipeline._face_identity_refresh_interval_frames:
                    continue
                priority = 2
            box = person.get("box") or [0, 0, 0, 0]
            width = max(0.0, float(box[2]) - float(box[0])) if len(box) == 4 else 0.0
            height = max(0.0, float(box[3]) - float(box[1])) if len(box) == 4 else 0.0
            area_score = -(width * height)
            candidates.append((priority, area_score, index, dict(person)))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[3] for item in candidates[:max_persons]]

    def _enqueue_processing_without_identity(
        self,
        *,
        request_id: str | None,
        frame: np.ndarray | None,
        frame_path: str | None,
        video_source: str,
        detections_override: dict[str, Any],
        telemetry: TelemetryMap | None,
    ) -> None:
        self._enqueue_processing_task(
            request_id=request_id,
            frame=frame,
            frame_path=frame_path,
            video_source=video_source,
            detections_override=detections_override,
            identity_observations_override=self._build_default_identity_observations(
                detections_override
            ),
            processed_at=datetime.now(),
            telemetry=telemetry,
        )

    def _clear_pending_identity(self) -> None:
        with self._lock:
            pending_frames = list(self._pending_identity_frames.values())
            pending_updates = list(self._pending_identity_updates.values())
            self._pending_identity_frames.clear()
            self._pending_identity_order.clear()
            self._completed_identity_results.clear()
            self._pending_identity_updates.clear()
            self._pending_identity_update_order.clear()
        for pending in pending_frames:
            if pending.frame_path:
                self._delete_shared_frame(
                    pending.frame_path,
                    reason="identity_cleared",
                    telemetry=pending.telemetry,
                )
        for pending_update in pending_updates:
            if pending_update.frame_path:
                self._delete_shared_frame(
                    pending_update.frame_path,
                    reason="identity_update_cleared",
                    telemetry=pending_update.telemetry,
                )
        while True:
            try:
                self._identity_result_queue.get_nowait()
            except Empty:
                break

    def _enqueue_processing_task(
        self,
        *,
        request_id: str | None,
        frame: np.ndarray | None,
        frame_path: str | None,
        video_source: str,
        detections_override: Optional[dict[str, Any]],
        identity_observations_override: Optional[dict[str, Any]],
        processed_at: datetime,
        telemetry: TelemetryMap | None,
    ) -> None:
        task = PendingProcessingTask(
            request_id=request_id,
            frame=frame,
            frame_path=frame_path,
            video_source=video_source,
            detections_override=detections_override,
            identity_observations_override=identity_observations_override,
            processed_at=processed_at,
            telemetry=mark_telemetry(telemetry, "processing_enqueued_at"),
        )
        try:
            self._processing_queue.put_nowait(task)
        except Full:
            if frame_path:
                task.telemetry = self._delete_shared_frame(
                    frame_path,
                    reason="processing_queue_full",
                    telemetry=task.telemetry,
                )
            self.status.dropped_frames += 1
            self._log_frame_timing(
                stage="processing_dropped",
                request_id=request_id,
                telemetry=task.telemetry,
            )
            logger.debug(
                "Dropping processing task for camera %s because processing queue is full",
                self.camera_id,
            )
            return
        self._registry.schedule_processing(self.camera_id)

    def _clear_processing_queue(self) -> None:
        while True:
            try:
                task = self._processing_queue.get_nowait()
            except Empty:
                break
            if task.frame_path:
                self._delete_shared_frame(
                    task.frame_path,
                    reason="processing_queue_cleared",
                    telemetry=task.telemetry,
                )

    def _clear_inference_submit_queue(self) -> None:
        self._registry.clear_inference_submit_queue(self.camera_id)

    def _clear_identity_submit_queue(self) -> None:
        self._registry.clear_identity_submit_queue(self.camera_id)

    def _process_next_queued_task(self) -> bool:
        try:
            task = self._processing_queue.get_nowait()
        except Empty:
            return False
        try:
            frame = task.frame
            if frame is None and task.frame_path:
                frame = self._shared_frame_store.read_frame(task.frame_path)
            if frame is None:
                self.status.dropped_frames += 1
                self._log_frame_timing(
                    stage="processing_invalid_frame",
                    request_id=task.request_id,
                    telemetry=task.telemetry,
                )
                if task.frame_path:
                    self._delete_shared_frame(
                        task.frame_path,
                        reason="processing_invalid_frame",
                        telemetry=task.telemetry,
                    )
                return True
            self._process_detection_result(
                request_id=task.request_id,
                frame=frame,
                frame_path=task.frame_path,
                video_source=task.video_source,
                detections_override=task.detections_override,
                identity_observations_override=task.identity_observations_override,
                processed_at=task.processed_at,
                telemetry=task.telemetry,
            )
            return True
        except Exception:
            if task.frame_path:
                self._delete_shared_frame(
                    task.frame_path,
                    reason="processing_exception",
                    telemetry=task.telemetry,
                )
            logger.exception("Failed to process detection task for camera %s", self.camera_id)
            return True

    def _encode_frame_jpeg(self, frame: np.ndarray) -> Optional[bytes]:
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, max(10, min(100, int(settings.LIVE_FRAME_JPEG_QUALITY)))],
        )
        if not ok:
            return None
        return encoded.tobytes()

    def _encode_transport_jpeg(
        self,
        frame: np.ndarray,
        *,
        quality: int,
    ) -> Optional[bytes]:
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, max(10, min(100, int(quality)))],
        )
        if not ok:
            return None
        return encoded.tobytes()

    def _encode_person_crop_jpeg(
        self,
        frame: np.ndarray | None,
        person: dict[str, Any],
        *,
        quality: int = 70,
    ) -> bytes | None:
        if frame is None:
            return None
        box = person.get("box") or []
        if len(box) != 4:
            return None
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = [int(round(float(v))) for v in box]
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width, x2))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        ok, encoded = cv2.imencode(
            ".jpg",
            crop,
            [cv2.IMWRITE_JPEG_QUALITY, max(10, min(100, int(quality)))],
        )
        if not ok:
            return None
        return encoded.tobytes()

    def _prepare_identity_person_payloads(
        self,
        frame: np.ndarray | None,
        persons: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool, float]:
        use_crops_first = bool(getattr(settings, "IDENTITY_USE_PERSON_CROPS_FIRST", True))
        max_crops = max(1, int(getattr(settings, "IDENTITY_MAX_CROPS_PER_FRAME", 2)))
        prepared: list[dict[str, Any]] = []
        all_have_crops = True
        crop_encode_ms = 0.0
        crop_futures: list[tuple[int, Future[bytes | None]]] = []
        for index, person in enumerate(persons):
            item = dict(person)
            item["crop_jpeg"] = None
            item["raw_track_id"] = item.get("raw_track_id", item.get("detector_track_id", item.get("track_id")))
            item["crop_score"] = float(item.get("score", item.get("confidence", 0.0)) or 0.0)
            prepared.append(item)
            if use_crops_first and index < max_crops:
                crop_futures.append(
                    (
                        index,
                        _runtime_background_executor.submit(
                            self._encode_person_crop_jpeg,
                            frame,
                            item,
                        ),
                    )
                )
        if crop_futures:
            crop_encode_started_at = time.perf_counter()
            for index, future in crop_futures:
                try:
                    crop_jpeg = future.result()
                except Exception:
                    logger.exception(
                        "Failed to encode identity crop camera=%s index=%s",
                        self.camera_id,
                        index,
                    )
                    crop_jpeg = None
                prepared[index]["crop_jpeg"] = crop_jpeg
                if crop_jpeg is None:
                    all_have_crops = False
            crop_encode_ms = (time.perf_counter() - crop_encode_started_at) * 1000.0
        if not use_crops_first:
            all_have_crops = False
        return prepared, all_have_crops, round(crop_encode_ms, 1)

    def _schedule_persist(
        self,
        result: dict,
        frame: np.ndarray,
        *,
        request_id: str | None,
        telemetry: TelemetryMap | None,
    ) -> None:
        if self._main_loop is None:
            return
        if self._pending_persist >= self._max_pending_persist:
            self.status.dropped_frames += 1
            self._log_frame_timing(
                stage="persist_skipped_pending_full",
                request_id=request_id,
                telemetry=telemetry,
            )
            return
        self._pending_persist += 1

        async def _persist_and_dec():
            persist_telemetry = mark_telemetry(telemetry, "persist_started_at")
            try:
                async with async_session() as session:
                    persistence = PersistenceManager(session)
                    persist_result = await persistence.persist_frame_results(result, frame)
                    for event_id in persist_result.get("created_event_ids", []):
                        self._register_pending_event_video_clip(
                            event_id=event_id,
                            timestamp=datetime.fromisoformat(result.get("timestamp")) if result.get("timestamp") else datetime.now(),
                        )
                    persist_telemetry = mark_telemetry(persist_telemetry, "persist_finished_at")
                    self._log_frame_timing(
                        stage="persist_complete",
                        request_id=request_id,
                        telemetry=persist_telemetry,
                        extra={
                            "created_event_ids": len(persist_result.get("created_event_ids") or []),
                        },
                    )
            except Exception:
                logger.exception(
                    "Failed to persist runtime frame results for camera %s",
                    self.camera_id,
                )
            finally:
                self._pending_persist -= 1

        asyncio.run_coroutine_threadsafe(_persist_and_dec(), self._main_loop)

    def _register_pending_event_video_clip(self, *, event_id: str, timestamp: datetime) -> None:
        pre_window = timedelta(seconds=max(1, int(settings.EVENT_VIDEO_PRE_SECONDS)))
        post_window = timedelta(seconds=max(1, int(settings.EVENT_VIDEO_POST_SECONDS)))
        with self._lock:
            frames = [
                item.frame.copy()
                for item in self._frame_buffer
                if item.timestamp >= timestamp - pre_window
            ]
            self._pending_event_video_clips.append(
                PendingEventVideoClip(
                    event_id=event_id,
                    timestamp=timestamp,
                    camera_id=self.camera_id,
                    post_deadline=timestamp + post_window,
                    frames=frames,
                )
            )

    def _flush_pending_event_video_clips(self, now: datetime) -> None:
        ready_clips: list[PendingEventVideoClip] = []
        with self._lock:
            for clip in self._pending_event_video_clips:
                if clip.finalized:
                    continue
                clip.frames.append(self._latest_frame.copy() if self._latest_frame is not None else None)
                clip.frames = [frame for frame in clip.frames if frame is not None]
                if now >= clip.post_deadline:
                    clip.finalized = True
                    ready_clips.append(
                        PendingEventVideoClip(
                            event_id=clip.event_id,
                            timestamp=clip.timestamp,
                            camera_id=clip.camera_id,
                            post_deadline=clip.post_deadline,
                            frames=[frame.copy() for frame in clip.frames],
                            finalized=True,
                        )
                    )
            self._pending_event_video_clips = [
                clip for clip in self._pending_event_video_clips if not clip.finalized
            ]

        for clip in ready_clips:
            self._schedule_event_video_upload(clip)

    def _schedule_event_video_upload(self, clip: PendingEventVideoClip) -> None:
        if self._main_loop is None or not clip.frames:
            return

        async def _upload_video():
            try:
                async with async_session() as session:
                    event_service = EventService(session)
                    saved = await event_service.save_event_video(
                        frames=clip.frames,
                        event_id=clip.event_id,
                        timestamp=clip.timestamp,
                        camera_id=clip.camera_id,
                        fps=max(1, int(settings.EVENT_VIDEO_CAPTURE_FPS)),
                    )
                    if saved is None:
                        return
                    await event_service.update_event_video(
                        clip.event_id,
                        video_path=saved.video_path,
                        video_storage=saved.storage,
                        video_bucket=saved.bucket,
                        video_object_key=saved.object_key,
                        video_content_type=saved.content_type,
                        video_size_bytes=saved.size_bytes,
                    )
                    await session.commit()
            except Exception:
                logger.exception(
                    "Failed to save event video clip for camera %s event %s",
                    self.camera_id,
                    clip.event_id,
                )

        asyncio.run_coroutine_threadsafe(_upload_video(), self._main_loop)

    def _should_persist_result(self, result: dict) -> bool:
        persons = result.get("persons", [])
        if not persons:
            return False
        has_non_workshop_violation = False
        for person in persons:
            if person.get("face_matched"):
                return True
            if person.get("stable_violation"):
                return True
            if person.get("stable_missing_ppe"):
                return True
            action_violations = [
                item
                for item in (person.get("action_violations") or [])
                if item.get("action") != "workshop_overcapacity"
            ]
            if action_violations:
                has_non_workshop_violation = True
        return has_non_workshop_violation

    def _open_frame_source(self):
        source = HikvisionSdkRuntimeSource(self.camera)
        source.open()
        return source

    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        max_width = 960
        if width <= max_width:
            return frame
        scale = max_width / width
        return cv2.resize(frame, (max_width, int(height * scale)), interpolation=cv2.INTER_LINEAR)

    def _sleep_with_stop(self, seconds: float) -> None:
        end = datetime.now().timestamp() + seconds
        while not self._stop_requested and datetime.now().timestamp() < end:
            time.sleep(min(0.1, max(0.0, end - datetime.now().timestamp())))


class CameraRuntimeRegistry:
    """Keeps always-on runtime workers and one pipeline per live camera."""

    def __init__(self):
        self._runtimes: Dict[str, CameraRuntime] = {}
        self._pipelines: Dict[str, DetectionPipeline] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._inference_broker = None
        self._identity_broker = None
        self._inspection_task: Optional[asyncio.Task] = None
        self._workshop_overcapacity_task: Optional[asyncio.Task] = None
        self._workshop_overcapacity_last_alert_key: Optional[str] = None
        self._summary_task: Optional[asyncio.Task] = None
        self._shard_summary_interval_seconds = max(
            10, int(getattr(settings, "CAMERA_RUNTIME_SUMMARY_INTERVAL_SECONDS", 60))
        )
        self._last_shard_summary_logged_at = 0.0
        self._submit_lock = Lock()
        self._inference_submit_queue: Queue[str] = Queue(
            maxsize=max(
                8,
                int(settings.CAMERA_MONITOR_MAX_CAMERAS)
                * max(1, int(getattr(settings, "INFERENCE_MAX_PENDING_FRAMES_PER_CAMERA", 2))),
            )
        )
        self._identity_submit_queue: Queue[str] = Queue(
            maxsize=max(
                8,
                int(settings.CAMERA_MONITOR_MAX_CAMERAS)
                * max(1, int(getattr(settings, "IDENTITY_MAX_PENDING_FRAMES_PER_CAMERA", 4))),
            )
        )
        self._inference_submit_mailboxes: Dict[str, PendingInferenceDispatchTask] = {}
        self._identity_submit_mailboxes: Dict[str, PendingIdentityDispatchTask] = {}
        self._inference_submit_ready: set[str] = set()
        self._identity_submit_ready: set[str] = set()
        self._processing_ready_queue: Queue[str] = Queue(
            maxsize=max(
                8,
                int(settings.CAMERA_MONITOR_MAX_CAMERAS)
                * max(1, int(getattr(settings, "CAMERA_RUNTIME_PROCESS_QUEUE_SIZE", 2))),
            )
        )
        self._processing_executor = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(settings, "CAMERA_RUNTIME_SHARED_PROCESSING_WORKERS", 4))),
            thread_name_prefix="camera-runtime-process",
        )
        self._processing_dispatch_thread: Optional[Thread] = None
        self._result_dispatch_thread: Optional[Thread] = None
        self._shared_submit_threads: list[Thread] = []
        self._result_ready_queue: Queue[str] = Queue(
            maxsize=max(
                8,
                int(settings.CAMERA_MONITOR_MAX_CAMERAS)
                * max(1, int(getattr(settings, "INFERENCE_MAX_PENDING_FRAMES_PER_CAMERA", 2))),
            )
        )
        self._stop_requested = False

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._stop_requested = False
        if self._inspection_task is None or self._inspection_task.done():
            self._inspection_task = loop.create_task(self._inspection_check_loop())
        if self._workshop_overcapacity_task is None or self._workshop_overcapacity_task.done():
            self._workshop_overcapacity_task = loop.create_task(self._workshop_overcapacity_check_loop())
        if self._summary_task is None or self._summary_task.done():
            self._summary_task = loop.create_task(self._runtime_summary_loop())
        self._ensure_background_threads()

    def bind_inference_broker(self, broker) -> None:
        self._inference_broker = broker

    def bind_identity_broker(self, broker) -> None:
        self._identity_broker = broker

    def enqueue_inference_submit(self, camera_id: str, task: PendingInferenceDispatchTask) -> MailboxEnqueueResult:
        return self._enqueue_mailbox_task(
            queue_obj=self._inference_submit_queue,
            mailbox=self._inference_submit_mailboxes,
            ready_set=self._inference_submit_ready,
            camera_id=camera_id,
            task=task,
        )

    def enqueue_identity_submit(self, camera_id: str, task: PendingIdentityDispatchTask) -> MailboxEnqueueResult:
        return self._enqueue_mailbox_task(
            queue_obj=self._identity_submit_queue,
            mailbox=self._identity_submit_mailboxes,
            ready_set=self._identity_submit_ready,
            camera_id=camera_id,
            task=task,
        )

    def clear_inference_submit_queue(self, camera_id: str) -> None:
        self._drain_submit_queue(
            queue_obj=self._inference_submit_queue,
            mailbox=self._inference_submit_mailboxes,
            ready_set=self._inference_submit_ready,
            camera_id=camera_id,
        )

    def clear_identity_submit_queue(self, camera_id: str) -> None:
        self._drain_submit_queue(
            queue_obj=self._identity_submit_queue,
            mailbox=self._identity_submit_mailboxes,
            ready_set=self._identity_submit_ready,
            camera_id=camera_id,
        )

    def schedule_processing(self, camera_id: str) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None or runtime._stop_requested:
            return
        with runtime._lock:
            if runtime._processing_scheduled:
                return
            runtime._processing_scheduled = True
        try:
            self._processing_ready_queue.put_nowait(camera_id)
        except Full:
            with runtime._lock:
                runtime._processing_scheduled = False
            logger.debug(
                "Dropping processing schedule signal for camera %s because shared processing queue is full",
                camera_id,
            )

    def schedule_result_dispatch(self, camera_id: str) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None or runtime._stop_requested:
            return
        with runtime._lock:
            if runtime._result_dispatch_scheduled:
                return
            runtime._result_dispatch_scheduled = True
        try:
            self._result_ready_queue.put_nowait(camera_id)
        except Full:
            with runtime._lock:
                runtime._result_dispatch_scheduled = False
            logger.debug(
                "Dropping result dispatch signal for camera %s because result dispatch queue is full",
                camera_id,
            )

    def get_pipeline(self, camera_id: str) -> DetectionPipeline:
        runtime = self._runtimes.get(camera_id)
        if runtime:
            return runtime.pipeline
        if camera_id not in self._pipelines:
            pipeline = DetectionPipeline()
            pipeline.initialize()
            self._pipelines[camera_id] = pipeline
        return self._pipelines[camera_id]

    def start_camera(self, camera) -> None:
        if not self._loop:
            raise RuntimeError("Camera runtime loop is not bound")
        runtime = self._runtimes.get(camera.id)
        if runtime:
            runtime.update_camera(camera)
        else:
            runtime = CameraRuntime(camera, self)
            self._runtimes[camera.id] = runtime
        runtime.start(self._loop)

    def restart_camera(self, camera) -> None:
        self.stop_camera(camera.id)
        self.start_camera(camera)

    def stop_camera(self, camera_id: str) -> None:
        runtime = self._runtimes.pop(camera_id, None)
        if runtime:
            runtime.stop()
        self.reset_pipeline(camera_id)

    def reset_pipeline(self, camera_id: str) -> None:
        pipeline = self._pipelines.pop(camera_id, None)
        if pipeline:
            pipeline.reset()

    def get_latest_frame(self, camera_id: str) -> Optional[np.ndarray]:
        runtime = self._runtimes.get(camera_id)
        return runtime.latest_frame() if runtime else None

    def get_latest_raw_frame(self, camera_id: str) -> Optional[np.ndarray]:
        runtime = self._runtimes.get(camera_id)
        if not runtime:
            return None
        with runtime._lock:
            return runtime._latest_frame.copy() if runtime._latest_frame is not None else None

    def get_latest_frame_jpeg(self, camera_id: str, *, raw: bool = False) -> Optional[bytes]:
        runtime = self._runtimes.get(camera_id)
        return runtime.latest_frame_jpeg(raw=raw) if runtime else None

    def get_status(self, camera_id: str) -> dict:
        runtime = self._runtimes.get(camera_id)
        if not runtime:
            return {
                "camera_id": camera_id,
                "running": False,
                "online": False,
                "status": "stopped",
                "last_frame_at": None,
                "last_error": None,
                "processed_frames": 0,
                "dropped_frames": 0,
            }
        return runtime.snapshot_status()

    def publish_inference_frame(
        self,
        *,
        request_id: str,
        camera_id: str,
        video_source: str,
        frame_path: str | None,
        frame_jpeg: bytes | None,
        inference_options: dict[str, Any] | None,
        submitted_at: datetime,
        telemetry: TelemetryMap | None,
    ) -> bool:
        if self._inference_broker is None:
            logger.warning("Inference broker is not bound, camera=%s", camera_id)
            return False
        return bool(
            self._inference_broker.publish_frame(
                request_id=request_id,
                camera_id=camera_id,
                video_source=video_source,
                frame_path=frame_path,
                frame_jpeg=frame_jpeg,
                inference_options=inference_options,
                submitted_at=submitted_at,
                telemetry=telemetry,
            )
        )

    def handle_inference_result(
        self,
        camera_id: str,
        request_id: str,
        detections: dict[str, Any],
    ) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None:
            logger.debug(
                "Ignoring inference result for inactive camera=%s request_id=%s",
                camera_id,
                request_id,
            )
            return
        runtime.enqueue_inference_result(request_id, detections)
        self.schedule_result_dispatch(camera_id)

    def publish_identity_frame(
        self,
        *,
        request_id: str,
        camera_id: str,
        video_source: str,
        frame_path: str | None,
        frame_jpeg: bytes | None,
        persons: list[dict[str, Any]],
        submitted_at: datetime,
        face_detection_requested: bool,
        telemetry: TelemetryMap | None,
    ) -> bool:
        if self._identity_broker is None:
            logger.warning("Identity broker is not bound, camera=%s", camera_id)
            return False
        return bool(
            self._identity_broker.publish_identity_task(
                request_id=request_id,
                camera_id=camera_id,
                video_source=video_source,
                frame_path=frame_path,
                frame_jpeg=frame_jpeg,
                persons=persons,
                submitted_at=submitted_at,
                face_detection_requested=face_detection_requested,
                telemetry=telemetry,
            )
        )

    def handle_identity_result(
        self,
        camera_id: str,
        request_id: str,
        identity_observations: dict[str, Any],
    ) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None:
            logger.debug(
                "Ignoring identity result for inactive camera=%s request_id=%s",
                camera_id,
                request_id,
            )
            return
        runtime.enqueue_identity_result(request_id, identity_observations)
        self.schedule_result_dispatch(camera_id)

    def get_latest_person_overlays(self, camera_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(camera_id)
        if not runtime:
            return {
                "camera_id": camera_id,
                "frame_width": 0,
                "frame_height": 0,
                "persons": [],
                "last_frame_at": None,
            }
        return runtime.latest_person_overlays()

    def list_latest_person_overlays(self) -> dict[str, dict[str, Any]]:
        return {
            camera_id: runtime.latest_person_overlays()
            for camera_id, runtime in self._runtimes.items()
        }

    def get_total_person_count(
        self,
        *,
        exclude_camera_id: Optional[str] = None,
        replacement_count: Optional[int] = None,
    ) -> int:
        total = 0
        for camera_id, runtime in self._runtimes.items():
            if exclude_camera_id is not None and camera_id == exclude_camera_id:
                if replacement_count is not None:
                    total += max(0, replacement_count)
                continue
            with runtime._lock:
                latest_result = runtime._latest_detection_result
            if latest_result:
                total += len(latest_result.get("persons", []))
        if exclude_camera_id is not None and exclude_camera_id not in self._runtimes and replacement_count is not None:
            total += max(0, replacement_count)
        return total

    def list_statuses(self) -> list[dict]:
        return [runtime.snapshot_status() for runtime in self._runtimes.values()]

    def list_runtime_summaries(self) -> list[dict[str, Any]]:
        return [runtime.runtime_summary_snapshot() for runtime in self._runtimes.values()]

    def shard_summary_snapshot(self) -> dict[str, Any]:
        camera_summaries = self.list_runtime_summaries()
        latencies = [
            float(item["avg_inference_total_ms"])
            for item in camera_summaries
            if item.get("avg_inference_total_ms") is not None
        ]
        hot_cameras = [item for item in camera_summaries if bool(item.get("hot_camera"))]
        hottest = sorted(
            camera_summaries,
            key=lambda item: float(item.get("p95_inference_total_ms") or 0.0),
            reverse=True,
        )[:5]
        queue_snapshot = {
            "inference_submit_queue": self._inference_submit_queue.qsize(),
            "identity_submit_queue": self._identity_submit_queue.qsize(),
            "inference_submit_mailboxes": len(self._inference_submit_mailboxes),
            "identity_submit_mailboxes": len(self._identity_submit_mailboxes),
            "processing_ready_queue": self._processing_ready_queue.qsize(),
            "result_ready_queue": self._result_ready_queue.qsize(),
        }
        capacity_warning = (
            len(hot_cameras) >= 2
            and (
                queue_snapshot["inference_submit_queue"] > 0
                or queue_snapshot["result_ready_queue"] > 0
            )
        )
        return {
            "summary_type": "camera_runtime_shard",
            "shard_index": int(getattr(settings, "CAMERA_MONITOR_SHARD_INDEX", 0)),
            "active_camera_count": len(camera_summaries),
            "avg_latency_ms": round(mean(latencies), 1) if latencies else None,
            "p95_latency_ms": CameraRuntime._percentile(latencies, 0.95),
            "hottest_cameras": hottest,
            "hot_camera_count": len(hot_cameras),
            "total_selected_identity_persons": sum(
                int(item.get("selected_identity_persons") or 0) for item in camera_summaries
            ),
            "queue_pressure_snapshot": queue_snapshot,
            "capacity_warning": capacity_warning,
            "capacity_warning_reason": (
                "multiple hot cameras with queued realtime work; low-latency mode is nearing shard capacity"
                if capacity_warning
                else None
            ),
        }

    def stop_all(self) -> None:
        self._stop_requested = True
        if self._inspection_task is not None:
            self._inspection_task.cancel()
            self._inspection_task = None
        if self._workshop_overcapacity_task is not None:
            self._workshop_overcapacity_task.cancel()
            self._workshop_overcapacity_task = None
        if self._summary_task is not None:
            self._summary_task.cancel()
            self._summary_task = None
        for camera_id in list(self._runtimes.keys()):
            self.stop_camera(camera_id)
        self._join_background_threads()
        self._processing_executor.shutdown(wait=False, cancel_futures=True)

    def _ensure_background_threads(self) -> None:
        alive_submit_threads = [thread for thread in self._shared_submit_threads if thread.is_alive()]
        self._shared_submit_threads = alive_submit_threads
        if self._processing_dispatch_thread is None or not self._processing_dispatch_thread.is_alive():
            self._processing_dispatch_thread = Thread(
                target=self._shared_processing_dispatch_loop,
                name="camera-runtime-processing-dispatch",
                daemon=True,
            )
            self._processing_dispatch_thread.start()
        if self._result_dispatch_thread is None or not self._result_dispatch_thread.is_alive():
            self._result_dispatch_thread = Thread(
                target=self._result_dispatch_loop,
                name="camera-runtime-result-dispatch",
                daemon=True,
            )
            self._result_dispatch_thread.start()

        submit_target_count = max(1, int(getattr(settings, "CAMERA_RUNTIME_SHARED_SUBMIT_THREADS", 2)))

        while len(self._shared_submit_threads) < submit_target_count:
            thread = Thread(
                target=self._shared_submit_loop,
                name=f"camera-runtime-submit-{len(self._shared_submit_threads)}",
                daemon=True,
            )
            thread.start()
            self._shared_submit_threads.append(thread)

    def _join_background_threads(self) -> None:
        for thread in [self._processing_dispatch_thread, self._result_dispatch_thread, *self._shared_submit_threads]:
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        self._processing_dispatch_thread = None
        self._result_dispatch_thread = None
        self._shared_submit_threads = []

    def _drain_submit_queue(
        self,
        *,
        queue_obj: Queue[str],
        mailbox: Dict[str, Any],
        ready_set: set[str],
        camera_id: str,
    ) -> None:
        with self._submit_lock:
            task = mailbox.pop(camera_id, None)
            ready_set.discard(camera_id)
        if task is not None:
            frame_path = getattr(task, "frame_path", None)
            if frame_path:
                if isinstance(task, PendingInferenceDispatchTask):
                    get_shared_frame_store().release_frame(
                        frame_path,
                        consumer="inference",
                        reason="submit_queue_drained",
                    )
                else:
                    get_shared_frame_store().delete_frame(
                        frame_path,
                        reason="submit_queue_drained",
                    )

    def _enqueue_mailbox_task(
        self,
        *,
        queue_obj: Queue[str],
        mailbox: Dict[str, Any],
        ready_set: set[str],
        camera_id: str,
        task: Any,
    ) -> MailboxEnqueueResult:
        latest_only_mode = bool(getattr(settings, "CAMERA_RUNTIME_LATEST_ONLY_MODE", True))
        max_mailbox_frames = max(1, int(getattr(settings, "CAMERA_RUNTIME_MAX_MAILBOX_FRAMES", 1)))
        with self._submit_lock:
            previous_task = mailbox.get(camera_id)
            mailbox[camera_id] = task
            if camera_id in ready_set:
                if not latest_only_mode or max_mailbox_frames > 1:
                    mailbox[camera_id] = previous_task if previous_task is not None else task
                    return MailboxEnqueueResult(
                        accepted=False,
                        replaced_task=None,
                        error_reason="submit_queue_full",
                    )
                return MailboxEnqueueResult(
                    accepted=True,
                    replaced_task=previous_task,
                    replace_reason="replaced_pending_latest" if previous_task is not None else None,
                )
            try:
                queue_obj.put_nowait(camera_id)
            except Full:
                if previous_task is None:
                    mailbox.pop(camera_id, None)
                else:
                    mailbox[camera_id] = previous_task
                return MailboxEnqueueResult(
                    accepted=False,
                    replaced_task=None,
                    error_reason="submit_queue_full",
                )
            ready_set.add(camera_id)
            return MailboxEnqueueResult(
                accepted=True,
                replaced_task=previous_task,
                replace_reason="replaced_pending_latest" if previous_task is not None else None,
            )

    def _take_mailbox_task(
        self,
        *,
        mailbox: Dict[str, Any],
        ready_set: set[str],
        camera_id: str,
    ) -> Any | None:
        with self._submit_lock:
            ready_set.discard(camera_id)
            return mailbox.pop(camera_id, None)

    def _shared_submit_loop(self) -> None:
        while not self._stop_requested:
            handled_task = False
            try:
                camera_id = self._inference_submit_queue.get_nowait()
                task = self._take_mailbox_task(
                    mailbox=self._inference_submit_mailboxes,
                    ready_set=self._inference_submit_ready,
                    camera_id=camera_id,
                )
                if task is not None:
                    self._handle_inference_submit_task(camera_id, task)
                    handled_task = True
            except Empty:
                pass

            try:
                camera_id = self._identity_submit_queue.get_nowait()
                task = self._take_mailbox_task(
                    mailbox=self._identity_submit_mailboxes,
                    ready_set=self._identity_submit_ready,
                    camera_id=camera_id,
                )
                if task is not None:
                    self._handle_identity_submit_task(camera_id, task)
                    handled_task = True
            except Empty:
                pass

            if handled_task:
                continue

            try:
                camera_id = self._inference_submit_queue.get(timeout=0.05)
                task = self._take_mailbox_task(
                    mailbox=self._inference_submit_mailboxes,
                    ready_set=self._inference_submit_ready,
                    camera_id=camera_id,
                )
                if task is not None:
                    self._handle_inference_submit_task(camera_id, task)
                    continue
            except Empty:
                pass

            try:
                camera_id = self._identity_submit_queue.get(timeout=0.05)
                task = self._take_mailbox_task(
                    mailbox=self._identity_submit_mailboxes,
                    ready_set=self._identity_submit_ready,
                    camera_id=camera_id,
                )
                if task is not None:
                    self._handle_identity_submit_task(camera_id, task)
            except Empty:
                continue

    def _handle_inference_submit_task(
        self,
        camera_id: str,
        task: PendingInferenceDispatchTask,
    ) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None or runtime._stop_requested:
            if task.frame_path:
                get_shared_frame_store().release_frame(
                    task.frame_path,
                    consumer="inference",
                    reason="inference_runtime_missing",
                )
            return
        try:
            runtime._submit_frame_for_inference(task=task)
        except Exception:
            if task.frame_path:
                runtime._shared_frame_store.release_frame(
                    task.frame_path,
                    consumer="inference",
                    reason="inference_submit_exception",
                )
            logger.exception(
                "Failed to publish inference task for camera %s",
                camera_id,
            )

    def _handle_identity_submit_task(
        self,
        camera_id: str,
        task: PendingIdentityDispatchTask,
    ) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None or runtime._stop_requested:
            if task.frame_path:
                get_shared_frame_store().delete_frame(task.frame_path, reason="identity_runtime_missing")
            return
        try:
            published = runtime._submit_frame_for_identity(task=task)
        except Exception:
            if task.frame_path:
                runtime._shared_frame_store.delete_frame(task.frame_path, reason="identity_submit_exception")
            logger.exception(
                "Failed to publish identity task for camera %s",
                camera_id,
            )
            published = False
        if not published and task.frame is not None:
            runtime._enqueue_processing_without_identity(
                request_id=task.request_id,
                frame=task.frame,
                frame_path=None,
                video_source=task.video_source,
                detections_override=task.detections_override,
                telemetry=task.telemetry,
            )

    def _result_dispatch_loop(self) -> None:
        while not self._stop_requested:
            try:
                camera_id = self._result_ready_queue.get(timeout=0.5)
            except Empty:
                self._expire_pending_results()
                continue
            self._drain_runtime_results(camera_id)

    def _shared_processing_dispatch_loop(self) -> None:
        while not self._stop_requested:
            try:
                camera_id = self._processing_ready_queue.get(timeout=0.05)
            except Empty:
                continue
            runtime = self._runtimes.get(camera_id)
            if runtime is None or runtime._stop_requested:
                continue
            future = self._processing_executor.submit(self._process_runtime_task, camera_id)
            future.add_done_callback(lambda done_future, cid=camera_id: self._on_processing_task_done(cid, done_future))

    def _process_runtime_task(self, camera_id: str) -> bool:
        runtime = self._runtimes.get(camera_id)
        if runtime is None or runtime._stop_requested:
            return False
        return runtime._process_next_queued_task()

    def _on_processing_task_done(self, camera_id: str, future: Future) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None:
            return
        try:
            future.result()
        except Exception:
            logger.exception("Shared processing worker failed for camera %s", camera_id)
        if runtime._stop_requested:
            with runtime._lock:
                runtime._processing_scheduled = False
            return
        if not runtime._processing_queue.empty():
            try:
                self._processing_ready_queue.put_nowait(camera_id)
                return
            except Full:
                logger.debug(
                    "Shared processing queue is full while rescheduling camera %s",
                    camera_id,
                )
        with runtime._lock:
            runtime._processing_scheduled = False
            needs_reschedule = not runtime._processing_queue.empty()
        if needs_reschedule:
            self.schedule_processing(camera_id)

    def _drain_runtime_results(self, camera_id: str) -> None:
        runtime = self._runtimes.get(camera_id)
        if runtime is None:
            return
        try:
            runtime._drain_completed_inference()
        except Exception:
            logger.exception(
                "Failed to drain inference results for camera %s",
                camera_id,
            )
        try:
            runtime._drain_completed_identity()
        except Exception:
            logger.exception(
                "Failed to drain identity results for camera %s",
                camera_id,
            )
        if runtime._stop_requested:
            with runtime._lock:
                runtime._result_dispatch_scheduled = False
            return
        with runtime._lock:
            runtime._result_dispatch_scheduled = False
            needs_reschedule = (
                not runtime._inference_result_queue.empty()
                or not runtime._identity_result_queue.empty()
            )
        if needs_reschedule:
            self.schedule_result_dispatch(camera_id)

    def _expire_pending_results(self) -> None:
        for camera_id, runtime in list(self._runtimes.items()):
            if runtime._stop_requested:
                continue
            try:
                runtime._expire_stale_pending_inference()
            except Exception:
                logger.exception(
                    "Failed to expire inference results for camera %s",
                    camera_id,
                )
            try:
                runtime._expire_stale_pending_identity()
            except Exception:
                logger.exception(
                    "Failed to expire identity results for camera %s",
                    camera_id,
                )

    def _maybe_log_shard_summary(self) -> None:
        now_ts = time.monotonic()
        if now_ts - self._last_shard_summary_logged_at < self._shard_summary_interval_seconds:
            return
        self._last_shard_summary_logged_at = now_ts
        logger.info(
            "CAMERA_RUNTIME_SHARD_SUMMARY %s",
            json.dumps(
                self.shard_summary_snapshot(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    async def _runtime_summary_loop(self) -> None:
        interval = self._shard_summary_interval_seconds
        while True:
            try:
                self._maybe_log_shard_summary()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to emit shard runtime summary")
            await asyncio.sleep(interval)

    async def _inspection_check_loop(self) -> None:
        interval = max(5, int(settings.INSPECTION_CHECK_INTERVAL_SECONDS))
        while True:
            try:
                async with async_session() as session:
                    inspection_service = InspectionService(session)
                    await inspection_service.evaluate_area_missed_inspection()
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to evaluate periodic area missed inspection")
            await asyncio.sleep(interval)

    async def _workshop_overcapacity_check_loop(self) -> None:
        interval = max(5, int(settings.WORKSHOP_OVERCAPACITY_CHECK_INTERVAL_SECONDS))
        while True:
            try:
                async with async_session() as session:
                    await self._evaluate_workshop_overcapacity(session)
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to evaluate workshop overcapacity")
            await asyncio.sleep(interval)

    async def _evaluate_workshop_overcapacity(self, session) -> None:
        settings_row = (
            await session.execute(
                select(SupervisionSettings).order_by(SupervisionSettings.updated_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if settings_row is None or not bool(getattr(settings_row, "workshop_overcapacity_enabled", 0)):
            self._workshop_overcapacity_last_alert_key = None
            return

        limit = getattr(settings_row, "workshop_overcapacity_limit", None)
        if not isinstance(limit, int) or limit < 0:
            self._workshop_overcapacity_last_alert_key = None
            return

        total_person_count = self.get_total_person_count()
        if total_person_count <= limit:
            self._workshop_overcapacity_last_alert_key = None
            return

        cooldown_seconds = int(
            getattr(settings_row, "alert_cooldown_seconds", None)
            or settings.VIOLATION_ALERT_COOLDOWN_SECONDS
        )
        if cooldown_seconds <= 0:
            cooldown_seconds = settings.VIOLATION_ALERT_COOLDOWN_SECONDS

        timestamp = datetime.now()
        alert_key = f"{int(timestamp.timestamp() // cooldown_seconds)}"
        if self._workshop_overcapacity_last_alert_key == alert_key:
            return

        runtime_items = list(self._runtimes.items())
        camera_ids = [camera_id for camera_id, _ in runtime_items]
        camera_names = [runtime.camera.name or camera_id for camera_id, runtime in runtime_items]
        if not camera_ids:
            return

        video_source = f"workshop_overcapacity:{alert_key}"
        existing = (
            await session.execute(
                select(ComplianceEvent.id).where(ComplianceEvent.video_source == video_source).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            self._workshop_overcapacity_last_alert_key = alert_key
            return

        event_service = EventService(session)
        event = await event_service.create_event(
            person_id=None,
            track_id=None,
            timestamp=timestamp,
            video_source=video_source,
            camera_id=None,
            frame_number=0,
            detected_ppe=[],
            missing_ppe=[],
            action_violations=["workshop_overcapacity"],
            danger_event_types=["workshop_overcapacity"],
            is_violation=True,
            person_name="车间",
            camera_ids=camera_ids,
            camera_name="、".join([name for name in camera_names if name]),
            is_ongoing=False,
            end_timestamp=timestamp,
            duration_frames=0,
            start_frame=0,
        )
        await publish_realtime_message(
            {
                "type": "violation",
                "title": "车间超员",
                "message": f"车间总人数 {total_person_count} 超过阈值 {limit}",
                "timestamp": timestamp.isoformat(),
                "severity": "error",
                "event_id": event.id,
                "person_id": None,
                "person_name": "车间",
                "missing_ppe": ["workshop_overcapacity"],
                "violation_labels": ["车间超员"],
                "snapshot_filename": None,
                "snapshot_path": None,
                "snapshot_url": None,
                "camera_id": None,
                "camera_ids": camera_ids,
                "camera_name": "、".join([name for name in camera_names if name]),
            }
        )
        self._workshop_overcapacity_last_alert_key = alert_key


camera_runtime_registry = CameraRuntimeRegistry()
