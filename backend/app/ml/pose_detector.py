"""YOLO pose detector for person keypoints."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..core.config import settings

logger = logging.getLogger(__name__)


class YOLOPoseDetector:
    """Runs Ultralytics YOLO pose models and returns COCO keypoints."""

    def __init__(self):
        self.model: Optional[Any] = None
        self.device = "cuda" if self._cuda_available() else "cpu"
        self.confidence_threshold = getattr(settings, "POSE_CONFIDENCE_THRESHOLD", 0.4)
        self._initialized = False

    def _cuda_available(self) -> bool:
        try:
            import torch

            return torch.cuda.is_available()
        except ImportError:
            return False

    def initialize(self) -> None:
        """Initialize the configured YOLO pose model."""
        if self._initialized:
            return

        try:
            from ultralytics import YOLO
        except ImportError:
            logger.warning("ultralytics is not installed - pose detection disabled")
            self._initialized = True
            return

        model_path = getattr(settings, "POSE_MODEL_PATH", None)
        model_ref = "yolo11n-pose.pt"
        if model_path:
            path = Path(model_path)
            model_ref = str(path) if path.exists() else path.name

        try:
            self.model = YOLO(model_ref)
            self.model.to(self.device)
            logger.info("YOLO pose model loaded: %s on %s", model_ref, self.device)
        except Exception as exc:
            logger.warning("YOLO pose model failed to load (%s): %s", model_ref, exc)
            self.model = None

        self._initialized = True

    def detect(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Detect person poses in a frame."""
        if not self._initialized:
            self.initialize()

        if self.model is None or frame is None or frame.size == 0:
            return []

        try:
            results = self.model(
                frame,
                conf=self.confidence_threshold,
                verbose=False,
                save=False,
            )
        except Exception as exc:
            logger.warning("YOLO pose detection failed: %s", exc)
            return []

        parsed = self._parse_results(results)
        return parsed[0] if parsed else []

    def detect_batch(self, frames: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        """Detect poses across multiple frames in one batch."""
        if not frames:
            return []

        if not self._initialized:
            self.initialize()

        if self.model is None:
            return [[] for _ in frames]

        try:
            results = self.model(
                frames,
                conf=self.confidence_threshold,
                verbose=False,
                save=False,
            )
        except Exception as exc:
            logger.warning("YOLO pose batch detection failed: %s", exc)
            return [[] for _ in frames]

        return self._parse_results(results)

    def _parse_results(self, results: Any) -> List[List[Dict[str, Any]]]:
        batch_poses: List[List[Dict[str, Any]]] = []
        for result in results:
            frame_poses: List[Dict[str, Any]] = []
            if result.boxes is None or result.keypoints is None:
                batch_poses.append(frame_poses)
                continue

            boxes = result.boxes.xyxy.cpu().numpy()
            scores = result.boxes.conf.cpu().numpy()
            keypoints = result.keypoints.xy.cpu().numpy()
            keypoint_conf = (
                result.keypoints.conf.cpu().numpy()
                if result.keypoints.conf is not None
                else np.ones(keypoints.shape[:2], dtype=float)
            )

            for idx in range(len(boxes)):
                frame_poses.append(
                    {
                        "box": [float(v) for v in boxes[idx].tolist()],
                        "score": float(scores[idx]),
                        "keypoints": keypoints[idx].astype(float).tolist(),
                        "keypoint_confidence": keypoint_conf[idx]
                        .astype(float)
                        .tolist(),
                    }
                )

            batch_poses.append(frame_poses)
        return batch_poses


_pose_detector: Optional[YOLOPoseDetector] = None


def get_pose_detector() -> YOLOPoseDetector:
    """Get the singleton YOLO pose detector."""
    global _pose_detector
    if _pose_detector is None:
        _pose_detector = YOLOPoseDetector()
    return _pose_detector
