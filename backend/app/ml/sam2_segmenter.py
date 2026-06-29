"""
SAM 2 Segmenter (Improved)

Wrapper for SAM 2 (Segment Anything Model 2) with:
- Batch processing for efficiency
- bfloat16 optimization for speed
- Improved video pre-loading
- Better error handling
"""

import numpy as np
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import os

try:
    import torch
except ImportError:
    torch = None

from ..core.config import settings

logger = logging.getLogger(__name__)


# SAM2 model configuration mapping
SAM2_CONFIG_MAP = {
    "sam2.1_hiera_tiny": "configs/sam2.1/sam2.1_hiera_t.yaml",
    "sam2.1_hiera_small": "configs/sam2.1/sam2.1_hiera_s.yaml",
    "sam2.1_hiera_base_plus": "configs/sam2.1/sam2.1_hiera_b+.yaml",
    "sam2.1_hiera_large": "configs/sam2.1/sam2.1_hiera_l.yaml",
    # Fallback configs (older format)
    "sam2_hiera_tiny": "sam2_hiera_t.yaml",
    "sam2_hiera_small": "sam2_hiera_s.yaml",
    "sam2_hiera_base_plus": "sam2_hiera_b+.yaml",
    "sam2_hiera_large": "sam2_hiera_l.yaml",
}


def get_sam2_config_path(model_type: str) -> str:
    """Get the correct config path for SAM2, handling different installation methods."""
    try:
        import sam2
        sam2_dir = Path(sam2.__file__).parent

        # Map model type to filename
        model_config_name = {
            "sam2.1_hiera_tiny": "sam2.1_hiera_t.yaml",
            "sam2.1_hiera_small": "sam2.1_hiera_s.yaml",
            "sam2.1_hiera_base_plus": "sam2.1_hiera_b+.yaml",
            "sam2.1_hiera_large": "sam2.1_hiera_l.yaml",
            "sam2_hiera_tiny": "sam2_hiera_t.yaml",
            "sam2_hiera_small": "sam2_hiera_s.yaml",
            "sam2_hiera_base_plus": "sam2_hiera_b+.yaml",
            "sam2_hiera_large": "sam2_hiera_l.yaml",
        }.get(model_type, "sam2.1_hiera_b+.yaml")

        search_paths = [
            sam2_dir / "configs" / "sam2.1" / model_config_name,
            sam2_dir / "configs" / "sam2" / model_config_name.replace("sam2.1_", "sam2_"),
            sam2_dir / model_config_name.replace("sam2.1_", "sam2_"),
            sam2_dir / model_config_name,
        ]

        for config_path in search_paths:
            if config_path.exists():
                print(f"Found SAM2 config at: {config_path}")
                return str(config_path)

        return SAM2_CONFIG_MAP.get(model_type, "sam2.1_hiera_b+.yaml")

    except ImportError:
        return SAM2_CONFIG_MAP.get(model_type, "sam2.1_hiera_b+.yaml")


class SAM2Segmenter:
    """
    SAM 2 wrapper with box-prompted segmentation and video propagation.

    Features:
    - Box-prompted segmentation: YOLO boxes → precise masks
    - Batch processing: Process multiple boxes efficiently
    - Video propagation: Tracks masks across frames automatically
    - bfloat16 optimization: Faster inference on CUDA
    - Mask density validation: Rejects low-density false positives
    """

    def __init__(self):
        self.model: Optional[Any] = None
        self.predictor: Optional[Any] = None
        self.video_predictor: Optional[Any] = None
        self._initialized = False
        self.device = "cuda" if self._cuda_available() else "cpu"
        self.dtype = torch.bfloat16 if self.device == "cuda" and torch is not None else None

        # Video state tracking
        self._video_initialized = False
        self._inference_state: Optional[Any] = None
        self._tracked_object_ids: Dict[int, int] = {}  # track_id -> sam2_obj_id
        self._next_obj_id = 1
        self._frame_idx = 0

    def _cuda_available(self) -> bool:
        """Check if CUDA is available."""
        if torch is None:
            return False
        return torch.cuda.is_available()

    def initialize(self) -> None:
        """Initialize SAM 2 model."""
        if self._initialized:
            return

        if torch is None:
            raise ImportError(
                "PyTorch is required for SAM2. Install with: pip install torch"
            )

        # Try to import SAM2
        try:
            from sam2.build_sam import build_sam2, build_sam2_video_predictor
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except ImportError:
            raise ImportError(
                "SAM2 is required. Install with: pip install git+https://github.com/facebookresearch/sam2.git"
            )

        model_type = getattr(settings, "SAM2_MODEL_TYPE", "sam2.1_hiera_base_plus")
        model_path = getattr(settings, "SAM2_MODEL_PATH", None)

        if model_path is None:
            model_path = settings.WEIGHTS_DIR / "sam2" / "sam2.1_hiera_base_plus.pt"

        config_name = get_sam2_config_path(model_type)

        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"SAM2 weights not found at {model_path}. "
                f"Download with:\n"
                f"  mkdir -p backend/weights/sam2\n"
                f'  wget https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt -O "{model_path}"'
            )

        print(f"Loading SAM2 model: {model_type} from {model_path}")
        print(f"Using config: {config_name}")

        self.model = build_sam2(config_name, str(model_path), device=self.device)
        self.predictor = SAM2ImagePredictor(self.model)

        use_video = getattr(settings, "USE_SAM2_VIDEO_PROPAGATION", True)
        if use_video:
            try:
                self.video_predictor = build_sam2_video_predictor(
                    config_name, str(model_path), device=self.device
                )
                print("SAM2 video propagation enabled")
            except Exception as e:
                print(f"Warning: Could not initialize video predictor: {e}")
                self.video_predictor = None

        self._initialized = True
        logger.info(f"SAM2Segmenter initialized on {self.device} with dtype={self.dtype}")
        print(f"SAM2Segmenter initialized on {self.device}")

    def segment_boxes_batch(
        self,
        frame: np.ndarray,
        boxes: List[List[float]],
        labels: Optional[List[str]] = None,
        use_multimask: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Generate masks for multiple bounding boxes in a single batch (more efficient).

        Args:
            frame: BGR numpy array from OpenCV
            boxes: List of bounding boxes [[x1, y1, x2, y2], ...]
            labels: Optional list of labels for each box
            use_multimask: Generate multiple mask options and select best

        Returns:
            List of segmentation results with masks, scores, and metadata
        """
        if not self._initialized:
            self.initialize()

        if not boxes:
            return []

        if labels is None:
            labels = [f"object_{i}" for i in range(len(boxes))]

        frame_rgb = frame[:, :, ::-1].copy()
        self.predictor.set_image(frame_rgb)

        results = []

        for i, (box, label) in enumerate(zip(boxes, labels)):
            try:
                box_np = np.array(box, dtype=np.float32)

                with torch.autocast(self.device, dtype=self.dtype) if self.dtype else torch.no_grad():
                    masks, scores, logits = self.predictor.predict(
                        box=box_np,
                        multimask_output=use_multimask,
                    )

                best_idx = np.argmax(scores)
                mask = masks[best_idx]
                score = float(scores[best_idx])

                density = self.calculate_mask_density(mask, box)
                density_threshold = getattr(settings, "MASK_DENSITY_THRESHOLD", 0.1)
                is_valid = density >= density_threshold

                results.append({
                    "mask": mask.astype(np.uint8),
                    "box": box,
                    "label": label,
                    "score": score,
                    "density": density,
                    "valid": is_valid,
                    "source": "sam2_batch",
                })

            except Exception as e:
                logger.warning(f"Failed to segment box {i} ({label}): {e}")
                results.append({
                    "mask": None,
                    "box": box,
                    "label": label,
                    "score": 0.0,
                    "density": 0.0,
                    "valid": False,
                    "source": "error",
                })

        return results

    def segment_boxes(
        self,
        frame: np.ndarray,
        boxes: List[List[float]],
        labels: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generate masks for given bounding boxes using box-prompted segmentation.

        Args:
            frame: BGR numpy array from OpenCV
            boxes: List of bounding boxes [[x1, y1, x2, y2], ...]
            labels: Optional list of labels for each box

        Returns:
            List of segmentation results, each with:
                - mask: Binary mask (H, W)
                - box: Original box
                - label: Original label
                - score: Mask confidence score
                - valid: Whether mask passed density validation
        """
        # Redirect to batch version for better performance
        return self.segment_boxes_batch(frame, boxes, labels, use_multimask=True)

    def calculate_mask_density(self, mask: np.ndarray, box: List[float]) -> float:
        """
        Calculate what fraction of the bounding box is covered by the mask.

        Used to reject false positive detections where the mask is too sparse.

        Args:
            mask: Binary mask (H, W)
            box: Bounding box [x1, y1, x2, y2]

        Returns:
            Density ratio (0 to 1)
        """
        x1, y1, x2, y2 = [int(c) for c in box]

        h, w = mask.shape[:2]
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            return 0.0

        box_area = (x2 - x1) * (y2 - y1)
        if box_area == 0:
            return 0.0

        mask_region = mask[y1:y2, x1:x2]
        mask_pixels = np.sum(mask_region > 0)

        return float(mask_pixels) / float(box_area)

    def init_video_tracking(
        self,
        frame: np.ndarray,
        detections: List[Dict[str, Any]],
    ) -> None:
        """
        Initialize video tracking with initial detections.

        Args:
            frame: First video frame (BGR)
            detections: Initial detections with boxes and track_ids
        """
        if self.video_predictor is None:
            return

        self.reset_video_state()
        frame_rgb = frame[:, :, ::-1]

        try:
            self._inference_state = self.video_predictor.init_state(frame_rgb)
            self._video_initialized = True
            self._frame_idx = 0

            for det in detections:
                track_id = det.get("track_id")
                box = det.get("box")
                if track_id is not None and box is not None:
                    self.add_new_object(frame, box, track_id)

        except Exception as e:
            print(f"Warning: Failed to initialize video tracking: {e}")
            self._video_initialized = False

    def add_new_object(
        self,
        frame: np.ndarray,
        box: List[float],
        track_id: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Add a new object to video tracking.

        Args:
            frame: Current frame (BGR)
            box: Object bounding box
            track_id: Track ID from DeepSORT

        Returns:
            Segmentation result for the new object
        """
        if not self._video_initialized or self.video_predictor is None:
            results = self.segment_boxes(frame, [box])
            return results[0] if results else None

        if track_id in self._tracked_object_ids:
            return None

        try:
            obj_id = self._next_obj_id
            self._next_obj_id += 1
            self._tracked_object_ids[track_id] = obj_id

            box_np = np.array(box, dtype=np.float32)

            _, out_obj_ids, out_mask_logits = (
                self.video_predictor.add_new_points_or_box(
                    inference_state=self._inference_state,
                    frame_idx=self._frame_idx,
                    obj_id=obj_id,
                    box=box_np,
                )
            )

            mask_logits = out_mask_logits[out_obj_ids == obj_id]
            if len(mask_logits) > 0:
                mask = (mask_logits[0] > 0).cpu().numpy().astype(np.uint8)
                return {
                    "mask": mask,
                    "box": box,
                    "track_id": track_id,
                    "obj_id": obj_id,
                }

        except Exception as e:
            print(f"Warning: Failed to add object {track_id} to video tracking: {e}")

        return None

    def propagate_masks(
        self,
        frame: np.ndarray,
    ) -> Dict[int, np.ndarray]:
        """
        Propagate masks to the next frame.

        Args:
            frame: Current frame (BGR)

        Returns:
            Dict mapping track_id -> mask
        """
        if not self._video_initialized or self.video_predictor is None:
            return {}

        self._frame_idx += 1

        try:
            frame_rgb = frame[:, :, ::-1].copy()

            out_obj_ids, out_mask_logits = self.video_predictor.propagate_in_video(
                inference_state=self._inference_state,
                frame_idx=self._frame_idx,
                image=frame_rgb,
            )

            masks = {}
            obj_id_to_track = {v: k for k, v in self._tracked_object_ids.items()}

            for obj_id, mask_logits in zip(out_obj_ids, out_mask_logits):
                track_id = obj_id_to_track.get(int(obj_id))
                if track_id is not None:
                    mask = (mask_logits > 0).cpu().numpy().astype(np.uint8)
                    if mask.ndim == 3:
                        mask = mask[0]
                    masks[track_id] = mask

            return masks

        except Exception as e:
            print(f"Warning: Failed to propagate masks: {e}")
            return {}

    def reset_video_state(self) -> None:
        """Reset video tracking state for a new video."""
        self._video_initialized = False
        self._inference_state = None
        self._tracked_object_ids = {}
        self._next_obj_id = 1
        self._frame_idx = 0

    def remove_object(self, track_id: int) -> None:
        """Remove an object from video tracking."""
        if track_id in self._tracked_object_ids:
            del self._tracked_object_ids[track_id]

    def is_tracking(self, track_id: int) -> bool:
        """Check if a track is being tracked by video propagation."""
        return track_id in self._tracked_object_ids

    def __repr__(self) -> str:
        return (
            f"SAM2Segmenter(initialized={self._initialized}, "
            f"device={self.device}, "
            f"video_enabled={self.video_predictor is not None}, "
            f"tracked_objects={len(self._tracked_object_ids)})"
        )


# Singleton instance
_sam2_segmenter: Optional[SAM2Segmenter] = None


def get_sam2_segmenter() -> SAM2Segmenter:
    """Get singleton SAM2Segmenter instance."""
    global _sam2_segmenter
    if _sam2_segmenter is None:
        _sam2_segmenter = SAM2Segmenter()
    return _sam2_segmenter
