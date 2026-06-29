"""Rule-based action labels from YOLO pose keypoints."""

import math
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import settings


class PoseActionAnalyzer:
    """Derive simple worker action labels from COCO 17-point pose keypoints."""

    NOSE = 0
    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_WRIST = 9
    RIGHT_WRIST = 10
    LEFT_HIP = 11
    RIGHT_HIP = 12
    LEFT_ANKLE = 15
    RIGHT_ANKLE = 16

    def __init__(self):
        self.min_conf = getattr(settings, "POSE_KEYPOINT_CONFIDENCE_THRESHOLD", 0.35)
        self.hand_mouth_ratio = getattr(settings, "POSE_HAND_MOUTH_DISTANCE_RATIO", 0.18)
        self.fall_aspect_ratio = getattr(settings, "POSE_FALL_ASPECT_RATIO", 1.25)
        self.bending_angle_deg = getattr(settings, "POSE_BENDING_ANGLE_DEG", 35.0)

    def analyze(self, person: Dict[str, Any]) -> Dict[str, Any]:
        """Return pose status and action labels for a person."""
        pose = person.get("pose")
        box = person.get("box", [0, 0, 0, 0])
        if not pose:
            return {"pose_status": "unknown", "pose_actions": []}

        keypoints = pose.get("keypoints") or []
        confidences = pose.get("keypoint_confidence") or []
        if len(keypoints) < 17:
            return {"pose_status": "unknown", "pose_actions": []}

        status = self._classify_status(box, keypoints, confidences)
        actions: List[Dict[str, Any]] = []

        if self._is_hand_near_mouth(box, keypoints, confidences):
            actions.append(
                {
                    "action": "hand_near_mouth",
                    "label": "Action: hand near mouth",
                    "score": 0.75,
                }
            )

        return {
            "pose_status": status,
            "pose_actions": actions,
        }

    def _classify_status(
        self, box: List[float], keypoints: List[List[float]], confidences: List[float]
    ) -> str:
        width = max(1.0, float(box[2] - box[0]))
        height = max(1.0, float(box[3] - box[1]))
        aspect_ratio = width / height

        if aspect_ratio >= self.fall_aspect_ratio:
            return "fallen"

        shoulder = self._midpoint(
            keypoints, confidences, self.LEFT_SHOULDER, self.RIGHT_SHOULDER
        )
        hip = self._midpoint(keypoints, confidences, self.LEFT_HIP, self.RIGHT_HIP)
        if shoulder and hip:
            dx = shoulder[0] - hip[0]
            dy = abs(shoulder[1] - hip[1])
            angle_from_vertical = math.degrees(math.atan2(abs(dx), max(dy, 1.0)))
            if angle_from_vertical >= self.bending_angle_deg:
                return "bending"

        ankle = self._midpoint(
            keypoints, confidences, self.LEFT_ANKLE, self.RIGHT_ANKLE
        )
        if shoulder and ankle:
            body_span = abs(ankle[1] - shoulder[1])
            if body_span < height * 0.45:
                return "crouching"

        return "standing"

    def _is_hand_near_mouth(
        self, box: List[float], keypoints: List[List[float]], confidences: List[float]
    ) -> bool:
        nose = self._point(keypoints, confidences, self.NOSE)
        if not nose:
            return False

        height = max(1.0, float(box[3] - box[1]))
        threshold = height * self.hand_mouth_ratio

        for wrist_idx in (self.LEFT_WRIST, self.RIGHT_WRIST):
            wrist = self._point(keypoints, confidences, wrist_idx)
            if wrist and self._distance(nose, wrist) <= threshold:
                return True
        return False

    def _point(
        self, keypoints: List[List[float]], confidences: List[float], idx: int
    ) -> Optional[Tuple[float, float]]:
        if idx >= len(keypoints):
            return None
        if confidences and idx < len(confidences) and confidences[idx] < self.min_conf:
            return None
        x, y = keypoints[idx]
        if x <= 0 or y <= 0:
            return None
        return float(x), float(y)

    def _midpoint(
        self,
        keypoints: List[List[float]],
        confidences: List[float],
        left_idx: int,
        right_idx: int,
    ) -> Optional[Tuple[float, float]]:
        left = self._point(keypoints, confidences, left_idx)
        right = self._point(keypoints, confidences, right_idx)
        if left and right:
            return (left[0] + right[0]) / 2, (left[1] + right[1]) / 2
        return left or right

    @staticmethod
    def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])


_pose_action_analyzer: Optional[PoseActionAnalyzer] = None


def get_pose_action_analyzer() -> PoseActionAnalyzer:
    """Get the singleton pose action analyzer."""
    global _pose_action_analyzer
    if _pose_action_analyzer is None:
        _pose_action_analyzer = PoseActionAnalyzer()
    return _pose_action_analyzer
