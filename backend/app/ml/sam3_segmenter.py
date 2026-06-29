"""
SAM 3 Segmenter (with SAM2 Video fallback)

Wrapper for SAM 3 / SAM 2 Video for streaming video segmentation.
Since SAM3 requires triton (Linux-only), we use SAM2 Video on Windows.

Uses Sam2VideoModel for streaming video segmentation with box prompts.
This provides high-quality person masks that are better than box-based fallbacks.
"""

import numpy as np
import logging
from typing import List, Dict, Any, Optional

try:
    import torch
except ImportError:
    torch = None

from ..core.config import settings

logger = logging.getLogger(__name__)


class SAM3Segmenter:
    """
    SAM 3 / SAM 2 Video wrapper for streaming video segmentation.

    Uses Sam2VideoModel from HuggingFace Transformers for box-prompted
    segmentation. Falls back to per-frame SAM2 if video mode unavailable.

    Features:
    - Streaming video inference (frame-by-frame)
    - Box-prompted segmentation
    - Multi-object tracking with consistent IDs
    """

    def __init__(self):
        self.model = None
        self.processor = None
        self.image_model = None
        self.image_processor = None
        self._initialized = False
        self.device = "cuda" if self._cuda_available() else "cpu"
        self.dtype = None

        # Video state tracking
        self._video_initialized = False
        self._tracked_object_ids: Dict[int, int] = {}  # track_id -> sam_obj_id
        self._next_obj_id = 1
        self._frame_count = 0
        self._inference_session = None
        self._model_available = False
        self._image_model_available = False
        self._model_type = "none"  # "sam2_video", "sam2_image", or "none"

    def _cuda_available(self) -> bool:
        if torch is None:
            return False
        return torch.cuda.is_available()

    def initialize(self) -> None:
        """Initialize SAM model from local path."""
        if self._initialized:
            return

        if torch is None:
            raise ImportError("PyTorch is required for SAM")

        self.dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

        # 优先使用本地模型路径
        sam2_local_path = settings.SAM2_MODEL_PATH

        # 尝试使用 sam-2 包加载本地模型
        if sam2_local_path and sam2_local_path.exists():
            try:
                from sam2.build_sam import build_sam2
                from sam2.automatic_mask_generation import SAM2AutomaticMaskGenerator

                # 获取配置文件路径
                sam2_cfg = {
                    "sam2.1_hiera_base_plus": "sam2.1_hiera_b+.yaml",
                    "sam2.1_hiera_large": "sam2.1_hiera_l.yaml",
                }
                model_name = sam2_local_path.stem  # e.g., "sam2.1_hiera_base_plus"
                cfg_name = sam2_cfg.get(model_name, "sam2.1_hiera_b+.yaml")

                # 尝试从 sam-2 包目录加载配置
                try:
                    import sam2
                    sam2_dir = Path(sam2.__file__).parent
                    config_paths = [
                        sam2_dir / "configs" / "sam2.1" / cfg_name,
                        sam2_dir / "configs" / cfg_name,
                    ]
                    for cp in config_paths:
                        if cp.exists():
                            cfg_path = str(cp)
                            break
                    else:
                        logger.warning(f"[SAM3] SAM2 config not found in sam-2 package")
                        raise FileNotFoundError("SAM2 config not found")
                except ImportError:
                    logger.warning("[SAM3] sam-2 package not installed, skipping local model")
                    raise ImportError("sam-2 not installed")

                # 构建 SAM2 模型
                sam2_model = build_sam2(config_path=cfg_path, ckpt_path=str(sam2_local_path), device=self.device)

                # 创建自动掩码生成器
                self.model = SAM2AutomaticMaskGenerator(sam2_model)
                self._model_available = True
                self._model_type = "sam2_local"

                print(f"SAM3Segmenter initialized with local SAM2 model on {self.device}")
                logger.info(f"[SAM3] Initialized with local SAM2: {sam2_local_path}")

                self._initialized = True
                return

            except ImportError as e:
                logger.warning(f"[SAM3] sam-2 package not available: {e}")
            except Exception as e:
                logger.warning(f"[SAM3] Failed to load local SAM2 model: {e}")

        # 如果本地模型加载失败，使用简单的 box fallback
        logger.warning("[SAM3] SAM models not available - using box-based fallback")
        self._model_type = "none"
        print("SAM models not available - using box-based fallback masks")

        self._initialized = True

    def segment_boxes(
        self,
        frame: np.ndarray,
        boxes: List[List[float]],
        labels: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Segment objects using box prompts (per-frame).

        Args:
            frame: BGR numpy array from OpenCV
            boxes: List of bounding boxes [[x1, y1, x2, y2], ...]
            labels: Optional list of labels

        Returns:
            List of segmentation results with masks
        """
        if not self._initialized:
            self.initialize()

        if not boxes:
            return []

        if labels is None:
            labels = [f"object_{i}" for i in range(len(boxes))]

        h, w = frame.shape[:2]
        results = []

        logger.info(
            f"[SAM3] segment_boxes: {len(boxes)} boxes, model_type={self._model_type}"
        )

        if self._model_type == "none":
            # Fallback: return box-based masks
            logger.info("[SAM3] Using box-based fallback masks (no SAM model)")
            for box, label in zip(boxes, labels):
                mask = self._box_to_mask(box, h, w)
                mask_pixels = int(np.sum(mask > 0))
                logger.debug(
                    f"[SAM3] Fallback box mask for {label}: {mask_pixels} pixels"
                )
                results.append(
                    {
                        "mask": mask,
                        "box": box,
                        "label": label,
                        "score": 0.5,
                        "density": 1.0,
                        "valid": True,
                        "source": "box_fallback",
                    }
                )
            return results

        try:
            from PIL import Image

            # Convert BGR to RGB
            frame_rgb = frame[:, :, ::-1].copy()
            pil_image = Image.fromarray(frame_rgb)

            # Use image model for per-frame segmentation
            if self._image_model_available and self.image_model is not None:
                for box, label in zip(boxes, labels):
                    try:
                        # Format box for Sam2Processor: [[x1, y1, x2, y2]]
                        # Sam2Processor expects 3 levels: [image_level, boxes_for_image, box_coords]
                        input_boxes = [[box]]

                        inputs = self.image_processor(
                            images=pil_image,
                            input_boxes=input_boxes,
                            return_tensors="pt",
                        ).to(self.device)

                        with torch.no_grad():
                            outputs = self.image_model(**inputs)


                        # Get masks - outputs.pred_masks shape: [batch, num_masks, H, W]
                        pred_masks = outputs.pred_masks

                        # Post-process to original size
                        masks = self.image_processor.post_process_masks(
                            pred_masks.cpu(), inputs["original_sizes"], binarize=True
                        )[0]

                        # masks shape: [num_masks, H, W] after post_process_masks[0]
                        if masks.shape[0] > 0:
                            # Get best mask (first one, or use iou_scores if available)
                            if (
                                hasattr(outputs, "iou_scores")
                                and outputs.iou_scores is not None
                                and outputs.iou_scores.numel() > 0
                            ):
                                # iou_scores shape: [batch, num_masks] or [num_masks]
                                iou_scores = outputs.iou_scores
                                if iou_scores.ndim > 1:
                                    iou_scores = iou_scores[0]  # Get first batch
                                
                                # Get best scoring mask
                                if iou_scores.numel() > 1:
                                    best_idx = iou_scores.argmax().item()
                                else:
                                    best_idx = 0
                                
                                # Ensure index is within bounds
                                best_idx = min(best_idx, masks.shape[0] - 1)
                                mask = masks[best_idx].numpy().astype(np.uint8)
                                score = float(iou_scores[best_idx].cpu())
                            else:
                                # No iou_scores, use first mask
                                mask = masks[0].numpy().astype(np.uint8)
                                score = 0.9
                        else:
                            mask = self._box_to_mask(box, h, w)
                            score = 0.5


                        density = self._calculate_mask_density(mask, box)
                        density_threshold = getattr(
                            settings, "MASK_DENSITY_THRESHOLD", 0.1
                        )
                        mask_pixels = int(np.sum(mask > 0))

                        logger.info(
                            f"[SAM3] SAM2 mask for {label}: {mask_pixels} pixels, density={density:.2f}, score={score:.2f}"
                        )

                        results.append(
                            {
                                "mask": mask,
                                "box": box,
                                "label": label,
                                "score": score,
                                "density": density,
                                "valid": density >= density_threshold,
                                "source": "sam2_image",
                            }
                        )
                    except Exception as e:
                        logger.warning(
                            f"[SAM3] SAM2 segmentation failed for {label}: {e}"
                        )
                        mask = self._box_to_mask(box, h, w)
                        results.append(
                            {
                                "mask": mask,
                                "box": box,
                                "label": label,
                                "score": 0.0,
                                "density": 1.0,
                                "valid": False,
                                "source": "box_fallback_error",
                            }
                        )
            else:
                # Fallback to box masks
                for box, label in zip(boxes, labels):
                    mask = self._box_to_mask(box, h, w)
                    results.append(
                        {
                            "mask": mask,
                            "box": box,
                            "label": label,
                            "score": 0.5,
                            "density": 1.0,
                            "valid": True,
                            "source": "box_fallback",
                        }
                    )

        except Exception as e:
            logger.warning(f"[SAM3] Per-frame segmentation failed: {e}")
            for box, label in zip(boxes, labels):
                mask = self._box_to_mask(box, h, w)
                results.append(
                    {
                        "mask": mask,
                        "box": box,
                        "label": label,
                        "score": 0.0,
                        "density": 1.0,
                        "valid": False,
                        "source": "box_fallback_error",
                    }
                )

        return results

    def _box_to_mask(self, box: List[float], h: int, w: int) -> np.ndarray:
        """Create a simple box mask as fallback."""
        mask = np.zeros((h, w), dtype=np.uint8)
        x1, y1, x2, y2 = [int(c) for c in box]
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        mask[y1:y2, x1:x2] = 1
        return mask

    def _calculate_mask_density(self, mask: np.ndarray, box: List[float]) -> float:
        """Calculate mask density within bounding box."""
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

    def init_video_session(self, frame: Optional[np.ndarray] = None) -> None:
        """Initialize a streaming video session."""
        if not self._initialized:
            self.initialize()

        # Reset state
        self._tracked_object_ids = {}
        self._next_obj_id = 1
        self._frame_count = 0
        self._inference_session = None

        logger.info(f"[SAM3] init_video_session: model_type={self._model_type}")

        if self._model_type == "sam2_video" and self._model_available:
            try:
                # Initialize streaming session for Sam2VideoModel
                self._inference_session = self.processor.init_video_session(
                    inference_device=self.device,
                    dtype=self.dtype,
                )
                logger.info("[SAM3] Sam2VideoModel streaming session initialized")
            except Exception as e:
                logger.warning(f"[SAM3] Failed to init video session: {e}")
                self._inference_session = None

        self._video_initialized = True

    def process_frame(
        self,
        frame: np.ndarray,
        detections: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[int, np.ndarray]:
        """
        Process a single frame with optional new detections.

        Returns:
            Dict mapping track_id -> binary mask
        """
        if not self._initialized:
            self.initialize()

        if not self._video_initialized:
            self.init_video_session(frame)

        self._frame_count += 1

        num_dets = len(detections) if detections else 0
        logger.info(
            f"[SAM3] process_frame #{self._frame_count}: {num_dets} detections, model_type={self._model_type}"
        )

        # Try streaming video mode first
        if self._model_type == "sam2_video" and self._inference_session is not None:
            try:
                return self._process_frame_streaming(frame, detections)
            except Exception as e:
                logger.warning(f"[SAM3] Streaming failed: {e}, using per-frame")
                return self._process_frame_single(frame, detections)
        else:
            # Fall back to per-frame segmentation
            return self._process_frame_single(frame, detections)

    def _process_frame_streaming(
        self,
        frame: np.ndarray,
        detections: Optional[List[Dict[str, Any]]],
    ) -> Dict[int, np.ndarray]:
        """Process frame using Sam2VideoModel streaming inference."""
        from PIL import Image

        frame_rgb = frame[:, :, ::-1].copy()
        pil_image = Image.fromarray(frame_rgb)
        h, w = frame.shape[:2]

        # Process frame through processor
        inputs = self.processor(
            images=pil_image, device=self.device, return_tensors="pt"
        )

        # Add new objects if provided
        if detections:
            for det in detections:
                track_id = det.get("track_id")
                box = det.get("box")

                if track_id is not None and box is not None:
                    if track_id not in self._tracked_object_ids:
                        self._add_object_streaming(
                            box, track_id, inputs.original_sizes[0]
                        )

        # Run model in streaming mode
        # Only run streaming if we have tracked objects, otherwise conditioning fails
        if not self._tracked_object_ids:
            logger.debug("[SAM3] No tracked objects, using per-frame segmentation")
            return self._process_frame_single(frame, detections)
        
        try:
            model_outputs = self.model(
                inference_session=self._inference_session,
                frame=inputs.pixel_values[0],
                reverse=False,
            )

            # Post-process outputs
            masks_tensor = self.processor.post_process_masks(
                [model_outputs.pred_masks],
                original_sizes=inputs.original_sizes,
                binarize=True,
            )[0]

            # Map back to track IDs
            result = {}
            obj_id_to_track = {v: k for k, v in self._tracked_object_ids.items()}

            # Get object IDs from session
            if hasattr(self._inference_session, "obj_ids"):
                obj_ids = self._inference_session.obj_ids
            else:
                obj_ids = list(range(masks_tensor.shape[0]))

            for i, obj_id in enumerate(obj_ids):
                track_id = obj_id_to_track.get(int(obj_id))
                if track_id is not None and i < masks_tensor.shape[0]:
                    mask = masks_tensor[i].cpu().numpy().astype(np.uint8)
                    if mask.ndim == 3:
                        mask = mask[0]
                    result[track_id] = mask
                    mask_pixels = int(np.sum(mask > 0))
                    logger.info(
                        f"[SAM3] Streaming mask for track {track_id}: {mask_pixels} pixels"
                    )

            logger.info(f"[SAM3] Streaming produced {len(result)} masks")
            return result

        except Exception as e:
            # Common error: "maskmem_features in conditioning outputs cannot be empty"
            # This happens when streaming with no valid conditionings - fall back to per-frame
            error_str = str(e)
            if "maskmem_features" in error_str or "conditioning" in error_str.lower():
                logger.info(f"[SAM3] Streaming conditioning issue, using per-frame: {e}")
            else:
                logger.warning(f"[SAM3] Streaming inference error: {e}")
            return self._process_frame_single(frame, detections)

    def _add_object_streaming(
        self, box: List[float], track_id: int, original_size
    ) -> None:
        """Add object to streaming video tracking."""
        obj_id = self._next_obj_id
        self._next_obj_id += 1
        self._tracked_object_ids[track_id] = obj_id

        try:
            # Format box for add_inputs_to_inference_session
            # Sam2VideoProcessor expects input_boxes as [[[x1, y1, x2, y2]]]
            input_boxes = [[[box[0], box[1], box[2], box[3]]]]

            self.processor.add_inputs_to_inference_session(
                inference_session=self._inference_session,
                frame_idx=self._frame_count - 1,
                obj_ids=obj_id,
                input_boxes=input_boxes,
                original_size=original_size,
            )
            logger.info(
                f"[SAM3] Added track {track_id} as SAM object {obj_id}, box={box}"
            )
        except Exception as e:
            logger.warning(f"[SAM3] Failed to add object {track_id}: {e}")
            del self._tracked_object_ids[track_id]

    def _process_frame_single(
        self,
        frame: np.ndarray,
        detections: Optional[List[Dict[str, Any]]],
    ) -> Dict[int, np.ndarray]:
        """Process frame using per-frame segmentation."""
        if not detections:
            logger.debug("[SAM3] No detections for per-frame processing")
            return {}

        result = {}
        boxes = []
        track_ids = []

        for det in detections:
            track_id = det.get("track_id")
            box = det.get("box")
            if track_id is not None and box is not None:
                boxes.append(box)
                track_ids.append(track_id)

        if boxes:
            seg_results = self.segment_boxes(frame, boxes)
            for track_id, seg in zip(track_ids, seg_results):
                if seg.get("mask") is not None:
                    result[track_id] = seg["mask"]
                    mask_pixels = int(np.sum(seg["mask"] > 0))
                    source = seg.get("source", "unknown")
                    logger.info(
                        f"[SAM3] Per-frame mask for track {track_id}: {mask_pixels} pixels, source={source}"
                    )

        logger.info(f"[SAM3] Per-frame produced {len(result)} masks")
        return result

    def remove_object(self, track_id: int) -> None:
        """Remove an object from tracking."""
        if track_id in self._tracked_object_ids:
            del self._tracked_object_ids[track_id]
            logger.debug(f"[SAM3] Removed track {track_id} from tracking")

    def reset_video_state(self) -> None:
        """Reset video tracking state for a new video."""
        self._video_initialized = False
        self._tracked_object_ids = {}
        self._next_obj_id = 1
        self._frame_count = 0
        self._inference_session = None
        logger.info("[SAM3] Video state reset")

    def is_tracking(self, track_id: int) -> bool:
        """Check if a track is being tracked."""
        return track_id in self._tracked_object_ids

    def get_tracked_ids(self) -> List[int]:
        """Get list of currently tracked IDs."""
        return list(self._tracked_object_ids.keys())

    def __repr__(self) -> str:
        return (
            f"SAM3Segmenter(initialized={self._initialized}, "
            f"device={self.device}, model_type={self._model_type}, "
            f"video_initialized={self._video_initialized}, "
            f"tracked_objects={len(self._tracked_object_ids)})"
        )


# Singleton instance
_sam3_segmenter: Optional[SAM3Segmenter] = None


def get_sam3_segmenter() -> SAM3Segmenter:
    """Get singleton SAM3Segmenter instance."""
    global _sam3_segmenter
    if _sam3_segmenter is None:
        _sam3_segmenter = SAM3Segmenter()
    return _sam3_segmenter
