"""
Hybrid Detector

Combined YOLOv8 (person tracking) + YOLOv11 (PPE detection) + SAM3/SAM2 (segmentation) pipeline.

Flow:
- PersonDetector (YOLOv8) detects persons with tracking
- SAM3/SAM2 generates precise masks for persons
- YOLOv11 detects PPE and violations
- Associate PPE with persons using spatial overlap
"""

import numpy as np
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Any, Optional, Set

from .person_detector import get_person_detector, PersonDetector
from .yolov11_detector import get_yolov11_detector, YOLOv11Detector
from .mask_utils import calculate_box_containment, calculate_mask_containment
from .person_filters import filter_persons_by_min_box_area_ratio
from ..core.config import settings

logger = logging.getLogger(__name__)


class HybridDetector:
    """
    Combined YOLOv8 + YOLOv11 + SAM3/SAM2 pipeline.

    Flow:
    - PersonDetector (YOLOv8) detects person boxes with track_ids
    - SAM3/SAM2 generates person masks
    - YOLOv11 detects PPE and violation boxes
    - Associate PPE with persons using spatial overlap
    """

    def __init__(self):
        self.person_detector: Optional[PersonDetector] = None
        self.ppe_detector: Optional[YOLOv11Detector] = None
        self.pose_detector = None
        self.sam3_segmenter = None
        self.sam2_segmenter = None

        self._initialized = False
        self._use_pose = getattr(settings, "USE_POSE_ESTIMATION", False)
        self._pose_run_interval = max(1, getattr(settings, "POSE_RUN_INTERVAL", 1))
        self._pose_iou_threshold = getattr(settings, "POSE_IOU_THRESHOLD", 0.3)
        self._last_pose_detections: List[Dict[str, Any]] = []
        self._use_sam3 = getattr(settings, "USE_SAM3", True)
        self._use_sam2 = getattr(settings, "USE_SAM2", True) and not self._use_sam3
        self._use_sam2_video = getattr(settings, "USE_SAM2_VIDEO_PROPAGATION", True)
        self._sam2_propagate_interval = getattr(settings, "SAM2_PROPAGATE_INTERVAL", 2)
        self._segment_ppe = getattr(settings, "SAM2_SEGMENT_PPE", True)

        self._frame_count = 0
        self._video_initialized = False
        self._last_track_ids: Set[int] = set()
        # 每 N 帧强制重置 SAM2 状态，防止 inference_state KV-cache 无限积累
        self._sam2_reset_interval = getattr(settings, "SAM2_RESET_INTERVAL", 150)
        self._containment_threshold = getattr(
            settings, "MASK_CONTAINMENT_THRESHOLD", 0.3
        )
        self._violation_containment_threshold = 0.1
        self._model_executor = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(settings, "INFERENCE_MODEL_PARALLELISM", 2))),
            thread_name_prefix="hybrid-model",
        )

    def initialize(self) -> None:
        """Initialize all sub-detectors."""
        if self._initialized:
            return

        print("Initializing HybridDetector...")

        # Person detector (YOLOv8)
        self.person_detector = get_person_detector()
        self.person_detector.initialize()

        # PPE detector (YOLOv11)
        self.ppe_detector = get_yolov11_detector()
        self.ppe_detector.initialize()

        if self.ppe_detector.model is None:
            logger.error("YOLOv11 model failed to load - PPE detection disabled")

        if self._use_pose:
            try:
                from .pose_detector import get_pose_detector

                self.pose_detector = get_pose_detector()
                self.pose_detector.initialize()
                logger.info("YOLO pose detector initialized")
            except Exception as e:
                logger.warning(f"YOLO pose init failed: {e}")
                self.pose_detector = None
                self._use_pose = False

        if self._use_sam3:
            try:
                from .sam3_segmenter import get_sam3_segmenter

                self.sam3_segmenter = get_sam3_segmenter()
                self.sam3_segmenter.initialize()
                logger.info("SAM3 segmenter initialized")
            except Exception as e:
                logger.warning(f"SAM3 init failed, trying SAM2: {e}")
                self.sam3_segmenter = None
                self._use_sam3 = False
                self._use_sam2 = True

        if not self._use_sam3 and self._use_sam2:
            try:
                from .sam2_segmenter import get_sam2_segmenter

                self.sam2_segmenter = get_sam2_segmenter()
                self.sam2_segmenter.initialize()
                logger.info("SAM2 segmenter initialized")
            except Exception as e:
                logger.warning(f"SAM2 init failed: {e}")
                self.sam2_segmenter = None
                self._use_sam2 = False

        self._initialized = True
        segmenter = (
            "SAM3" if self.sam3_segmenter else "SAM2" if self.sam2_segmenter else "None"
        )
        print(f"HybridDetector initialized (Segmenter: {segmenter})")

    def detect(self, frame: np.ndarray) -> Dict[str, Any]:
        """Run full detection pipeline on a frame."""
        if not self._initialized:
            self.initialize()

        self._frame_count += 1

        persons = self.person_detector.detect_with_tracking(frame)
        persons = filter_persons_by_min_box_area_ratio(persons, frame.shape[:2])

        if self.sam3_segmenter and self._use_sam3:
            persons = self._add_masks_sam3(frame, persons)
        elif self.sam2_segmenter and self._use_sam2:
            persons = self._add_masks_sam2(frame, persons)

        ppe_detections = {}
        violation_detections = {}
        action_violations = []
        pose_detections = []

        if persons:
            ppe_future = self._model_executor.submit(self.ppe_detector.detect, frame)
            pose_future = None
            if self.pose_detector and self._use_pose:
                if self._frame_count % self._pose_run_interval == 0:
                    pose_future = self._model_executor.submit(self.pose_detector.detect, frame)
                else:
                    pose_detections = self._last_pose_detections

            ppe_result = ppe_future.result()
            ppe_detections = ppe_result.get("ppe_detections", {})
            violation_detections = ppe_result.get("violation_detections", {})
            action_violations = ppe_result.get("action_violations", [])
            if pose_future is not None:
                pose_detections = pose_future.result()
                self._last_pose_detections = pose_detections

        segmenter = self.sam3_segmenter or self.sam2_segmenter
        if persons and segmenter and self._segment_ppe:
            ppe_detections = self._add_masks_to_ppe(frame, ppe_detections)
            violation_detections = self._add_masks_to_ppe(frame, violation_detections)

        return {
            "persons": persons,
            "ppe_detections": ppe_detections,
            "violation_detections": violation_detections,
            "action_violations": action_violations,
            "pose_detections": pose_detections,
            "frame_shape": frame.shape[:2],
        }

    def _add_masks_sam3(self, frame: np.ndarray, persons: List[Dict]) -> List[Dict]:
        """Add SAM3 masks to persons."""
        if not persons:
            return persons

        logger.info(
            f"[HybridDetector] _add_masks_sam3 called with {len(persons)} persons"
        )

        try:
            masks = self.sam3_segmenter.process_frame(frame, persons)
            logger.info(f"[HybridDetector] SAM3 returned {len(masks)} masks")

            masks_assigned = 0
            for person in persons:
                track_id = person.get("track_id")
                if track_id is not None and track_id in masks:
                    mask = masks[track_id]
                    if mask.dtype != np.uint8:
                        mask = (mask > 0).astype(np.uint8)
                    if mask.ndim == 3:
                        mask = mask[0] if mask.shape[0] == 1 else mask[:, :, 0]
                    person["mask"] = mask
                    masks_assigned += 1
                    mask_pixels = int(np.sum(mask > 0))
                    logger.info(
                        f"[HybridDetector] Assigned mask to track {track_id}: {mask_pixels} pixels, shape={mask.shape}"
                    )
                else:
                    logger.debug(f"[HybridDetector] No mask for track {track_id}")

            logger.info(
                f"[HybridDetector] Assigned {masks_assigned}/{len(persons)} masks from SAM3"
            )

        except Exception as e:
            logger.warning(f"[HybridDetector] SAM3 mask generation failed: {e}")

        return persons

    def _add_masks_sam2(self, frame: np.ndarray, persons: List[Dict]) -> List[Dict]:
        """Add SAM2 masks to persons using video propagation."""
        if not persons:
            return persons

        current_track_ids = {
            p.get("track_id") for p in persons if p.get("track_id") is not None
        }

        # 定期强制重置，防止 SAM2 inference_state KV-cache 随时间无限增长导致卡顿
        if self._video_initialized and self._frame_count % self._sam2_reset_interval == 0:
            logger.debug(f"SAM2 periodic state reset at frame {self._frame_count}")
            self.sam2_segmenter.reset_video_state()
            self._video_initialized = False
            self._last_track_ids = set()

        # Initialize video tracking on first frame
        if not self._video_initialized:
            try:
                self.sam2_segmenter.init_video_tracking(frame, persons)
                self._video_initialized = True
                self._last_track_ids = current_track_ids
                masks = self.sam2_segmenter.propagate_masks(frame)
                self._assign_masks(persons, masks)
            except Exception as e:
                logger.debug(f"SAM2 video init failed: {e}")
                return self._segment_single_frame(frame, persons)
            return persons

        # Add new tracks
        new_tracks = current_track_ids - self._last_track_ids
        for person in persons:
            track_id = person.get("track_id")
            if track_id in new_tracks:
                box = person.get("box")
                if box:
                    try:
                        result = self.sam2_segmenter.add_new_object(
                            frame, box, track_id
                        )
                        if result and result.get("mask") is not None:
                            person["mask"] = self._normalize_mask(result["mask"])
                    except Exception:
                        pass

        # Remove lost tracks
        for track_id in self._last_track_ids - current_track_ids:
            try:
                self.sam2_segmenter.remove_object(track_id)
            except Exception:
                pass

        self._last_track_ids = current_track_ids

        # Propagate masks
        if self._frame_count % self._sam2_propagate_interval == 0:
            try:
                masks = self.sam2_segmenter.propagate_masks(frame)
                self._assign_masks(persons, masks)
            except Exception as e:
                logger.debug(f"SAM2 propagation failed: {e}")

        return persons

    def _assign_masks(self, persons: List[Dict], masks: Dict[int, np.ndarray]):
        """Assign masks to persons by track_id."""
        for person in persons:
            track_id = person.get("track_id")
            if track_id in masks:
                person["mask"] = self._normalize_mask(masks[track_id])

    def _normalize_mask(self, mask: np.ndarray) -> np.ndarray:
        """Ensure mask is uint8 and 2D."""
        if mask.dtype != np.uint8:
            mask = (mask > 0).astype(np.uint8)
        if mask.ndim == 3:
            mask = mask[0] if mask.shape[0] == 1 else mask[:, :, 0]
        return mask

    def _segment_single_frame(
        self, frame: np.ndarray, persons: List[Dict]
    ) -> List[Dict]:
        """Fallback single-frame segmentation."""
        if not self.sam2_segmenter:
            return persons

        boxes = [p.get("box") for p in persons if p.get("box")]
        labels = [f"person_{p.get('track_id', i)}" for i, p in enumerate(persons)]

        if not boxes:
            return persons

        try:
            results = self.sam2_segmenter.segment_boxes_batch(
                frame, boxes, labels, use_multimask=True
            )
            for person, result in zip(persons, results):
                if result.get("valid") and result.get("mask") is not None:
                    person["mask"] = self._normalize_mask(result["mask"])
        except Exception:
            pass

        return persons

    def _add_masks_to_ppe(self, frame: np.ndarray, ppe_detections: Dict) -> Dict:
        """Add masks to PPE detections."""
        segmenter = self.sam3_segmenter or self.sam2_segmenter
        if not segmenter:
            return ppe_detections

        all_boxes, all_labels, box_to_key = [], [], []
        for ppe_type, detections in ppe_detections.items():
            for i, det in enumerate(detections):
                if det.get("box"):
                    all_boxes.append(det["box"])
                    all_labels.append(ppe_type)
                    box_to_key.append((ppe_type, i))

        if not all_boxes:
            return ppe_detections

        try:
            results = segmenter.segment_boxes_batch(
                frame, all_boxes, all_labels, use_multimask=True
            )
            for (ppe_type, idx), result in zip(box_to_key, results):
                if result.get("valid") and result.get("mask") is not None:
                    ppe_detections[ppe_type][idx]["mask"] = result["mask"]
        except Exception:
            pass

        return ppe_detections

    def associate_pose_to_persons(
        self, persons: List[Dict], pose_detections: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """Associate YOLO pose keypoints with tracked persons using IoU."""
        pose_detections = pose_detections or []

        for person in persons:
            person["pose"] = None
            person_box = person.get("box")
            if not person_box:
                continue

            best_pose = None
            best_iou = 0.0
            for pose in pose_detections:
                pose_box = pose.get("box")
                if not pose_box:
                    continue
                iou = self._calculate_iou(person_box, pose_box)
                if iou > best_iou:
                    best_iou = iou
                    best_pose = pose

            if best_pose and best_iou >= self._pose_iou_threshold:
                person["pose"] = {
                    "box": best_pose.get("box"),
                    "score": best_pose.get("score", 0.0),
                    "keypoints": best_pose.get("keypoints", []),
                    "keypoint_confidence": best_pose.get("keypoint_confidence", []),
                    "match_iou": best_iou,
                }

        return persons

    def associate_ppe_to_persons(
        self,
        persons: List[Dict],
        ppe_detections: Dict[str, List[Dict]],
        violation_detections: Optional[Dict[str, List[Dict]]] = None,
        action_violations: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        """Associate PPE items and violations with persons using box/mask overlap."""
        class_map = getattr(settings, "PPE_CLASS_MAP", {})
        required_ppe = set(settings.REQUIRED_PPE)
        unknown_as_missing_types = set(getattr(settings, "PPE_UNKNOWN_AS_MISSING_TYPES", []))
        unknown_missing_confidence = float(
            getattr(settings, "PPE_UNKNOWN_AS_MISSING_CONFIDENCE", 0.45) or 0.45
        )
        violation_detections = violation_detections or {}
        action_violations = action_violations or {}

        for person in persons:
            person_box = person.get("box", [0, 0, 0, 0])
            person_mask = person.get("mask")
            detected_ppe, missing_ppe, person_actions = [], [], []
            detection_confidence, ppe_dets = {}, []
            associated_ppe: set[str] = set()
            associated_violations: set[str] = set()

            for ppe_class, ppe_list in ppe_detections.items():
                if ppe_class.startswith("No "):
                    continue
                for ppe in ppe_list:
                    ppe_box = ppe.get("box", [0, 0, 0, 0])
                    ppe_mask = ppe.get("mask")

                    if person_mask is not None and ppe_mask is not None:
                        containment = calculate_mask_containment(ppe_mask, person_mask)
                    else:
                        containment = calculate_box_containment(ppe_box, person_box)

                    if containment >= self._containment_threshold:
                        ppe_name = class_map.get(ppe_class, ppe_class)
                        associated_ppe.add(ppe_name)
                        if ppe_name not in detected_ppe:
                            detected_ppe.append(ppe_name)
                            detection_confidence[ppe_name] = ppe.get("score", 0.0)
                        ppe_dets.append(
                            {
                                "label": ppe_class,
                                "display_name": ppe_name,
                                "box": ppe_box,
                                "score": ppe.get("score", 0.0),
                                "is_violation": False,
                            }
                        )

            for viol_class, viol_list in violation_detections.items():
                for viol in viol_list:
                    viol_box = viol.get("box", [0, 0, 0, 0])
                    viol_mask = viol.get("mask")

                    viol_cx = (viol_box[0] + viol_box[2]) / 2
                    viol_cy = (viol_box[1] + viol_box[3]) / 2
                    center_inside = (
                        person_box[0] <= viol_cx <= person_box[2]
                        and person_box[1] <= viol_cy <= person_box[3]
                    )

                    if person_mask is not None and viol_mask is not None:
                        containment = calculate_mask_containment(viol_mask, person_mask)
                    else:
                        containment = max(
                            calculate_box_containment(viol_box, person_box),
                            calculate_box_containment(person_box, viol_box),
                        )

                    iou = self._calculate_iou(viol_box, person_box)

                    if (
                        center_inside
                        or containment >= self._violation_containment_threshold
                        or iou >= 0.1
                    ):
                        associated_violations.add(viol_class)
                        if viol_class in associated_ppe:
                            ppe_dets.append(
                                {
                                    "label": viol_class,
                                    "display_name": viol_class,
                                    "box": viol_box,
                                    "score": viol.get("score", 0.0),
                                    "is_violation": True,
                                    "suppressed_by_positive": True,
                                }
                            )
                            continue
                        if viol_class in required_ppe and viol_class not in missing_ppe:
                            missing_ppe.append(viol_class)
                            detection_confidence[f"no_{viol_class}"] = viol.get(
                                "score", 0.0
                            )
                        ppe_dets.append(
                            {
                                "label": viol_class,
                                "display_name": viol_class,
                                "box": viol_box,
                                "score": viol.get("score", 0.0),
                                "is_violation": True,
                            }
                        )

            for ppe_name in sorted(required_ppe & unknown_as_missing_types):
                if ppe_name in associated_ppe or ppe_name in associated_violations:
                    continue
                if ppe_name not in missing_ppe:
                    missing_ppe.append(ppe_name)
                    detection_confidence[f"no_{ppe_name}"] = unknown_missing_confidence

            for action in action_violations:
                action_box = action.get("box", [0, 0, 0, 0])
                containment = calculate_box_containment(action_box, person_box)
                if containment >= self._containment_threshold:
                    person_actions.append(
                        {
                            "action": action.get("class", action.get("action", "")),
                            "score": action.get("score", 0.0),
                            "box": action_box,
                        }
                    )

            person["detected_ppe"] = detected_ppe
            person["missing_ppe"] = missing_ppe
            person["action_violations"] = person_actions
            person["detection_confidence"] = detection_confidence
            person["ppe_detections"] = ppe_dets
            person["is_violation"] = len(missing_ppe) > 0 or len(person_actions) > 0

        return persons

    def _calculate_iou(self, box1: List[float], box2: List[float]) -> float:
        """Calculate IoU between two boxes."""
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0

    def reset_video_state(self) -> None:
        """Reset video tracking state."""
        self._frame_count = 0
        self._video_initialized = False
        self._last_track_ids = set()

        if self.sam3_segmenter and hasattr(self.sam3_segmenter, "reset_video_state"):
            self.sam3_segmenter.reset_video_state()
        if self.sam2_segmenter and hasattr(self.sam2_segmenter, "reset_video_state"):
            self.sam2_segmenter.reset_video_state()

    def reset_sam_state(self) -> None:
        """Alias for reset_video_state."""
        self.reset_video_state()

    def reset_sam2_state(self) -> None:
        """Alias for reset_video_state."""
        self.reset_video_state()

    def __repr__(self) -> str:
        return f"HybridDetector(initialized={self._initialized})"


_hybrid_detector: Optional[HybridDetector] = None


def get_hybrid_detector() -> HybridDetector:
    """Get singleton HybridDetector instance."""
    global _hybrid_detector
    if _hybrid_detector is None:
        _hybrid_detector = HybridDetector()
    return _hybrid_detector
