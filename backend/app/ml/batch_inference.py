"""Batched inference entrypoint for GPU-oriented detection workloads."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import numpy as np

from ..core.config import settings
from .detector_factory import get_detector
from .person_filters import filter_persons_by_min_box_area_ratio
from .person_detector import PersonDetector, get_person_detector
from .pose_detector import YOLOPoseDetector, get_pose_detector
from .yolov11_detector import YOLOv11Detector, get_yolov11_detector

logger = logging.getLogger(__name__)


class BatchInferenceEngine:
    """Runs the heavy detection stage in multi-frame batches."""

    def __init__(self) -> None:
        self.detector_type = settings.DETECTOR_TYPE.lower()
        self.person_detector: Optional[PersonDetector] = None
        self.ppe_detector: Optional[YOLOv11Detector] = None
        self.pose_detector: Optional[YOLOPoseDetector] = None
        self.fallback_detector = None
        self._initialized = False
        self._model_executor = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(settings, "INFERENCE_MODEL_PARALLELISM", 2))),
            thread_name_prefix="batch-model",
        )

    def initialize(self) -> None:
        if self._initialized:
            return

        if self.detector_type == "hybrid":
            self.person_detector = get_person_detector()
            self.person_detector.initialize()
            self.ppe_detector = get_yolov11_detector()
            self.ppe_detector.initialize()
            if getattr(settings, "USE_POSE_ESTIMATION", False):
                self.pose_detector = get_pose_detector()
                self.pose_detector.initialize()
        elif self.detector_type == "yolov11":
            self.ppe_detector = get_yolov11_detector()
            self.ppe_detector.initialize()
            if getattr(settings, "USE_POSE_ESTIMATION", False):
                self.pose_detector = get_pose_detector()
                self.pose_detector.initialize()
        else:
            self.fallback_detector = get_detector()
            self.fallback_detector.initialize()

        self._initialized = True
        logger.info("BatchInferenceEngine initialized for detector_type=%s", self.detector_type)

    def infer_batch(
        self,
        frames: List[np.ndarray],
        *,
        frame_options: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        if not frames:
            return []

        if not self._initialized:
            self.initialize()

        normalized_options = list(frame_options or [])
        if len(normalized_options) < len(frames):
            normalized_options.extend({} for _ in range(len(frames) - len(normalized_options)))

        if self.detector_type == "hybrid":
            return self._infer_hybrid(frames, normalized_options)
        if self.detector_type == "yolov11":
            return self._infer_yolov11(frames, normalized_options)
        return self._infer_fallback(frames, normalized_options)

    def _infer_hybrid(
        self,
        frames: List[np.ndarray],
        frame_options: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if self.person_detector is None or self.ppe_detector is None:
            raise RuntimeError("Hybrid batch engine is not initialized")

        total_started = time.perf_counter()
        person_started = time.perf_counter()
        person_batches = self.person_detector.detect_batch(frames)
        person_detect_ms = round((time.perf_counter() - person_started) * 1000.0, 1)
        results: List[Dict[str, Any]] = [
            {
                "persons": persons,
                "ppe_detections": {},
                "violation_detections": {},
                "action_violations": [],
                "pose_detections": [],
                "frame_shape": frame.shape[:2],
            }
            for frame, persons in zip(frames, person_batches)
        ]
        frame_profiles: list[dict[str, Any]] = []
        pose_enabled_indices: list[int] = []
        for index, (persons, options) in enumerate(zip(person_batches, frame_options)):
            filtered_persons = filter_persons_by_min_box_area_ratio(persons, frames[index].shape[:2])
            selected_persons = self._limit_persons_for_frame(filtered_persons, options)
            results[index]["persons"] = selected_persons
            results[index]["person_total_count"] = len(filtered_persons)
            results[index]["person_selected_count"] = len(selected_persons)
            results[index]["person_dropped_count"] = max(0, len(filtered_persons) - len(selected_persons))
            frame_profiles.append(
                {
                    "person_detect_ms": person_detect_ms,
                    "ppe_detect_ms": 0.0,
                    "pose_detect_ms": 0.0,
                    "postprocess_ms": 0.0,
                    "total_engine_ms": 0.0,
                }
            )
            if self._should_run_pose_for_frame(filtered_persons, options):
                pose_enabled_indices.append(index)

        non_empty_indices = [
            index
            for index, persons in enumerate(person_batches)
            if filter_persons_by_min_box_area_ratio(persons, frames[index].shape[:2])
        ]
        if not non_empty_indices:
            total_engine_ms = round((time.perf_counter() - total_started) * 1000.0, 1)
            for result, profile in zip(results, frame_profiles):
                profile["total_engine_ms"] = total_engine_ms
                result["_engine_profile"] = dict(profile)
            return results

        active_frames = [frames[index] for index in non_empty_indices]
        ppe_started = time.perf_counter()
        ppe_future = self._model_executor.submit(
            self.ppe_detector.detect_batch,
            active_frames,
        )
        pose_future = (
            self._model_executor.submit(
                self.pose_detector.detect_batch,
                [frames[index] for index in pose_enabled_indices],
            )
            if self.pose_detector is not None and pose_enabled_indices
            else None
        )

        try:
            ppe_batches = ppe_future.result()
        except Exception:
            logger.exception("PPE batch inference failed for %s frame(s)", len(active_frames))
            ppe_batches = [
                {
                    "ppe_detections": {},
                    "violation_detections": {},
                    "action_violations": [],
                }
                for _ in active_frames
            ]
        ppe_detect_ms = round((time.perf_counter() - ppe_started) * 1000.0, 1)

        if pose_future is not None:
            pose_started = time.perf_counter()
            try:
                pose_batches = pose_future.result()
            except Exception:
                logger.exception("Pose batch inference failed for %s frame(s)", len(pose_enabled_indices))
                pose_batches = [[] for _ in pose_enabled_indices]
            pose_detect_ms = round((time.perf_counter() - pose_started) * 1000.0, 1)
        else:
            pose_batches = []
            pose_detect_ms = 0.0

        pose_by_index = {index: pose for index, pose in zip(pose_enabled_indices, pose_batches)}

        postprocess_started = time.perf_counter()
        for result_index, ppe_result, pose_detections in zip(
            non_empty_indices,
            ppe_batches,
            [pose_by_index.get(index, []) for index in non_empty_indices],
        ):
            results[result_index].update(
                {
                    "ppe_detections": ppe_result.get("ppe_detections", {}),
                    "violation_detections": ppe_result.get("violation_detections", {}),
                    "action_violations": ppe_result.get("action_violations", []),
                    "pose_detections": pose_detections,
                }
            )
        postprocess_ms = round((time.perf_counter() - postprocess_started) * 1000.0, 1)
        total_engine_ms = round((time.perf_counter() - total_started) * 1000.0, 1)
        for result, profile in zip(results, frame_profiles):
            profile.update(
                {
                    "ppe_detect_ms": ppe_detect_ms,
                    "pose_detect_ms": pose_detect_ms,
                    "postprocess_ms": postprocess_ms,
                    "total_engine_ms": total_engine_ms,
                }
            )
            result["_engine_profile"] = dict(profile)
        return results

    def _infer_yolov11(
        self,
        frames: List[np.ndarray],
        frame_options: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if self.ppe_detector is None:
            raise RuntimeError("YOLOv11 batch engine is not initialized")

        total_started = time.perf_counter()
        ppe_started = time.perf_counter()
        ppe_batches = self.ppe_detector.detect_batch(frames)
        ppe_detect_ms = round((time.perf_counter() - ppe_started) * 1000.0, 1)
        pose_indices = [
            index
            for index, (ppe_result, options) in enumerate(zip(ppe_batches, frame_options))
            if self._should_run_pose_for_frame(list(ppe_result.get("persons") or []), options)
        ]
        pose_started = time.perf_counter()
        pose_batches = (
            self.pose_detector.detect_batch([frames[index] for index in pose_indices])
            if self.pose_detector is not None and pose_indices
            else []
        )
        pose_detect_ms = round((time.perf_counter() - pose_started) * 1000.0, 1) if pose_indices else 0.0
        pose_by_index = {index: pose for index, pose in zip(pose_indices, pose_batches)}
        results: List[Dict[str, Any]] = []
        total_engine_ms = round((time.perf_counter() - total_started) * 1000.0, 1)
        for index, (frame, ppe_result) in enumerate(zip(frames, ppe_batches)):
            payload = dict(ppe_result)
            payload["pose_detections"] = pose_by_index.get(index, [])
            payload["frame_shape"] = frame.shape[:2]
            persons = filter_persons_by_min_box_area_ratio(
                list(payload.get("persons") or []),
                frame.shape[:2],
            )
            selected_persons = self._limit_persons_for_frame(persons, frame_options[index])
            payload["persons"] = selected_persons
            payload["person_total_count"] = len(persons)
            payload["person_selected_count"] = len(selected_persons)
            payload["person_dropped_count"] = max(0, len(persons) - len(selected_persons))
            payload["_engine_profile"] = {
                "person_detect_ms": 0.0,
                "ppe_detect_ms": ppe_detect_ms,
                "pose_detect_ms": pose_detect_ms,
                "postprocess_ms": 0.0,
                "total_engine_ms": total_engine_ms,
            }
            results.append(payload)
        return results

    def _infer_fallback(
        self,
        frames: List[np.ndarray],
        frame_options: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if self.fallback_detector is None:
            raise RuntimeError("Fallback detector is not initialized")
        total_started = time.perf_counter()
        results = [self.fallback_detector.detect(frame) for frame in frames]
        total_engine_ms = round((time.perf_counter() - total_started) * 1000.0, 1)
        for result, options in zip(results, frame_options):
            persons = filter_persons_by_min_box_area_ratio(
                list(result.get("persons") or []),
                result.get("frame_shape"),
            )
            selected_persons = self._limit_persons_for_frame(persons, options)
            result["persons"] = selected_persons
            result["person_total_count"] = len(persons)
            result["person_selected_count"] = len(selected_persons)
            result["person_dropped_count"] = max(0, len(persons) - len(selected_persons))
            result["_engine_profile"] = {
                "person_detect_ms": total_engine_ms,
                "ppe_detect_ms": 0.0,
                "pose_detect_ms": 0.0,
                "postprocess_ms": 0.0,
                "total_engine_ms": total_engine_ms,
            }
        return results

    @staticmethod
    def _limit_persons_for_frame(
        persons: List[Dict[str, Any]],
        options: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        max_persons = int(options.get("max_persons_for_frame") or 0)
        if max_persons <= 0 or len(persons) <= max_persons:
            return list(persons)
        ranked = sorted(
            persons,
            key=lambda person: float(person.get("score", 0.0)),
            reverse=True,
        )
        return ranked[:max_persons]

    @staticmethod
    def _should_run_pose_for_frame(
        persons: List[Dict[str, Any]],
        options: Dict[str, Any],
    ) -> bool:
        if not bool(options.get("pose_enabled", True)):
            return False
        skip_threshold = int(options.get("skip_pose_when_person_count_ge") or 0)
        if skip_threshold > 0 and len(persons) >= skip_threshold:
            return False
        return True


_batch_inference_engine: Optional[BatchInferenceEngine] = None


def get_batch_inference_engine() -> BatchInferenceEngine:
    global _batch_inference_engine
    if _batch_inference_engine is None:
        _batch_inference_engine = BatchInferenceEngine()
    return _batch_inference_engine
