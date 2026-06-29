"""
OSNet + FAISS based person ReID service.

This module provides:
1. OSNet feature extraction from person crops.
2. A worker-wide FAISS identity index shared across camera pipelines.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import faiss
import numpy as np
import torch

from ..core.config import settings


logger = logging.getLogger(__name__)


def _load_osnet_module():
    version_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        Path(sys.prefix)
        / "lib"
        / version_dir
        / "site-packages"
        / "torchreid"
        / "reid"
        / "models"
        / "osnet.py"
    ]
    for path in candidates:
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location("torchreid_osnet", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("OSNet module not found. Ensure torchreid is installed in backend/.venv.")


@dataclass
class ReIDIdentityRecord:
    person_id: str
    person_name: Optional[str] = None
    identity_data: dict[str, Any] = field(default_factory=dict)
    features: deque["ReIDFeatureSample"] = field(default_factory=deque)
    last_seen_at: float = 0.0
    last_camera_id: Optional[str] = None
    face_verified: bool = False


@dataclass
class ReIDFeatureSample:
    feature: np.ndarray
    added_at: float


class OSNetFeatureExtractor:
    """Extract ReID embeddings using OSNet."""

    def __init__(
        self,
        model_name: str,
        model_path: Path,
        input_width: int,
        input_height: int,
    ):
        self.model_name = model_name
        self.model_path = model_path
        self.input_width = input_width
        self.input_height = input_height
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._lock = threading.RLock()
        self._module = _load_osnet_module()
        self._model = self._build_model()
        self._mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        self._std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def _build_model(self):
        model_fn = getattr(self._module, self.model_name, None)
        if model_fn is None:
            raise RuntimeError(f"Unsupported OSNet model: {self.model_name}")

        model = model_fn(pretrained=False)
        self._ensure_weights()
        state_dict = torch.load(self.model_path, map_location="cpu")
        if isinstance(state_dict, dict) and "state_dict" in state_dict:
            state_dict = state_dict["state_dict"]
        cleaned_state_dict = {}
        model_state = model.state_dict()
        for key, value in state_dict.items():
            clean_key = key[7:] if key.startswith("module.") else key
            if clean_key in model_state and model_state[clean_key].shape == value.shape:
                cleaned_state_dict[clean_key] = value
        model.load_state_dict({**model_state, **cleaned_state_dict})
        model.eval()
        model.to(self.device)
        return model

    def _ensure_weights(self) -> None:
        if self.model_path.exists():
            return

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        import gdown

        pretrained_urls = getattr(self._module, "pretrained_urls", {})
        url = pretrained_urls.get(self.model_name)
        if not url:
            raise RuntimeError(f"No pretrained URL registered for {self.model_name}")
        logger.info("Downloading OSNet weights: %s -> %s", self.model_name, self.model_path)
        gdown.download(url, str(self.model_path), quiet=False)
        if not self.model_path.exists():
            raise RuntimeError(f"OSNet weights download failed: {self.model_path}")

    def extract(
        self,
        frame: np.ndarray,
        box: list[float],
        mask: Optional[np.ndarray] = None,
    ) -> Optional[np.ndarray]:
        crop = self._crop_person(frame, box, mask)
        if crop is None:
            return None

        input_tensor = self._preprocess(crop)
        with self._lock:
            with torch.no_grad():
                features = self._model(input_tensor.to(self.device))
        if isinstance(features, (list, tuple)):
            features = features[0]
        feature = features.detach().float().cpu().numpy().reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(feature))
        if norm <= 1e-6:
            return None
        return feature / norm

    def extract_crops(self, crops: list[np.ndarray]) -> list[Optional[np.ndarray]]:
        valid_indices: list[int] = []
        tensors: list[torch.Tensor] = []
        for index, crop in enumerate(crops):
            if crop is None or crop.size == 0:
                continue
            try:
                tensors.append(self._preprocess(crop))
            except Exception:
                continue
            valid_indices.append(index)
        if not tensors:
            return [None for _ in crops]

        batch = torch.cat(tensors, dim=0)
        with self._lock:
            with torch.no_grad():
                features = self._model(batch.to(self.device))
        if isinstance(features, (list, tuple)):
            features = features[0]
        features_np = features.detach().float().cpu().numpy().astype(np.float32)

        results: list[Optional[np.ndarray]] = [None for _ in crops]
        for index, feature in zip(valid_indices, features_np):
            norm = float(np.linalg.norm(feature))
            if norm <= 1e-6:
                continue
            results[index] = feature.reshape(-1) / norm
        return results

    def _crop_person(
        self,
        frame: np.ndarray,
        box: list[float],
        mask: Optional[np.ndarray],
    ) -> Optional[np.ndarray]:
        if frame is None or frame.size == 0 or not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = [int(round(v)) for v in box]
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width, x2))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return None

        crop = frame[y1:y2, x1:x2].copy()
        if crop.size == 0:
            return None

        if mask is not None:
            mask_array = np.asarray(mask)
            if mask_array.ndim == 3:
                mask_array = mask_array[:, :, 0]
            if mask_array.ndim == 2 and mask_array.shape[:2] == (height, width):
                cropped_mask = (mask_array[y1:y2, x1:x2] > 0).astype(np.uint8)
                if cropped_mask.size > 0 and np.any(cropped_mask):
                    crop = crop * cropped_mask[:, :, None]
        return crop

    def _preprocess(self, crop: np.ndarray) -> torch.Tensor:
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(
            rgb,
            (self.input_width, self.input_height),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32)
        resized /= 255.0
        resized = (resized - self._mean) / self._std
        chw = np.transpose(resized, (2, 0, 1))
        tensor = torch.from_numpy(chw).unsqueeze(0)
        return tensor


class GlobalReIDService:
    """Worker-wide identity gallery powered by FAISS."""

    def __init__(
        self,
        extractor: Optional[OSNetFeatureExtractor] = None,
        match_threshold: float = 0.72,
        max_features_per_person: int = 80,
        feature_dim: Optional[int] = None,
    ):
        self.extractor = extractor
        self.match_threshold = match_threshold
        self.max_features_per_person = max_features_per_person
        self.feature_dim = feature_dim
        self.records: dict[str, ReIDIdentityRecord] = {}
        self._faiss_index: Optional[faiss.IndexFlatIP] = None
        self._faiss_person_ids: list[str] = []
        self._dirty = True
        self._lock = threading.RLock()
        self.feature_ttl_seconds = float(
            max(1, int(getattr(settings, "REID_FEATURE_TTL_SECONDS", 7200)))
        )

    def extract_feature(
        self,
        frame: np.ndarray,
        box: list[float],
        mask: Optional[np.ndarray] = None,
    ) -> Optional[np.ndarray]:
        if self.extractor is None:
            return None
        return self.extractor.extract(frame, box, mask)

    def clear(self) -> None:
        with self._lock:
            self.records.clear()
            self._faiss_index = None
            self._faiss_person_ids = []
            self.feature_dim = None
            self._dirty = True

    def get_identity(self, person_id: str) -> Optional[ReIDIdentityRecord]:
        with self._lock:
            self._prune_expired_locked()
            return self.records.get(person_id)

    def _prune_expired_locked(self) -> None:
        cutoff = time.monotonic() - self.feature_ttl_seconds
        removed_any = False
        empty_record_ids: list[str] = []
        for person_id, record in self.records.items():
            while record.features and float(record.features[0].added_at) < cutoff:
                record.features.popleft()
                removed_any = True
            if not record.features and record.last_seen_at < cutoff:
                empty_record_ids.append(person_id)
        for person_id in empty_record_ids:
            self.records.pop(person_id, None)
            removed_any = True
        if removed_any:
            self._dirty = True

    def rename_identity(
        self,
        source_person_id: str,
        target_person_id: str,
        target_name: Optional[str] = None,
        target_identity_data: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._prune_expired_locked()
            if source_person_id == target_person_id:
                record = self.records.get(target_person_id)
                if record is not None:
                    if target_name:
                        record.person_name = target_name
                    if target_identity_data:
                        record.identity_data.update(target_identity_data)
                return

            source = self.records.pop(source_person_id, None)
            target = self.records.get(target_person_id)
            if target is None:
                target = ReIDIdentityRecord(person_id=target_person_id)
                self.records[target_person_id] = target

            if source is not None:
                for sample in source.features:
                    target.features.append(sample)
                while len(target.features) > self.max_features_per_person:
                    target.features.popleft()
                if source.identity_data:
                    target.identity_data.update(source.identity_data)
                target.face_verified = target.face_verified or source.face_verified
                target.last_seen_at = max(target.last_seen_at, source.last_seen_at)
                target.last_camera_id = source.last_camera_id or target.last_camera_id
                if source.person_name and not target.person_name:
                    target.person_name = source.person_name

            if target_name:
                target.person_name = target_name
            if target_identity_data:
                target.identity_data.update(target_identity_data)
            self._dirty = True

    def upsert_identity(
        self,
        person_id: str,
        feature: Optional[np.ndarray],
        person_name: Optional[str],
        identity_data: Optional[dict[str, Any]],
        camera_id: Optional[str],
        *,
        face_verified: bool,
        index_identity: bool,
    ) -> None:
        if not person_id:
            return
        with self._lock:
            self._prune_expired_locked()
            record = self.records.get(person_id)
            if record is None:
                record = ReIDIdentityRecord(person_id=person_id)
                self.records[person_id] = record

            if person_name:
                record.person_name = person_name
            if identity_data:
                record.identity_data.update(identity_data)
            record.face_verified = record.face_verified or face_verified
            record.last_seen_at = time.monotonic()
            record.last_camera_id = camera_id

            if feature is None or not index_identity:
                return

            feature = np.asarray(feature, dtype=np.float32).reshape(-1)
            if self.feature_dim is None:
                self.feature_dim = int(feature.shape[0])
            if int(feature.shape[0]) != self.feature_dim:
                logger.warning(
                    "Skipping ReID feature with unexpected dimension: got=%s expected=%s",
                    feature.shape[0],
                    self.feature_dim,
                )
                return

            record.features.append(
                ReIDFeatureSample(
                    feature=feature,
                    added_at=time.monotonic(),
                )
            )
            while len(record.features) > self.max_features_per_person:
                record.features.popleft()
            self._dirty = True

    def search(
        self,
        feature: Optional[np.ndarray],
        *,
        threshold: Optional[float] = None,
        exclude_person_ids: Optional[set[str]] = None,
    ) -> tuple[Optional[str], float]:
        if feature is None:
            return None, 0.0

        query = np.asarray(feature, dtype=np.float32).reshape(1, -1)
        with self._lock:
            self._prune_expired_locked()
            self._ensure_index_locked()
            if self._faiss_index is None or self._faiss_index.ntotal == 0:
                return None, 0.0

            scores, indices = self._faiss_index.search(query, min(5, self._faiss_index.ntotal))
            threshold_value = self.match_threshold if threshold is None else threshold
            excluded = exclude_person_ids or set()
            for score, index in zip(scores[0], indices[0]):
                if index < 0:
                    continue
                person_id = self._faiss_person_ids[index]
                if person_id in excluded:
                    continue
                if float(score) >= float(threshold_value):
                    return person_id, float(score)
            return None, 0.0

    def _ensure_index_locked(self) -> None:
        self._prune_expired_locked()
        if not self._dirty and self._faiss_index is not None:
            return

        vectors: list[np.ndarray] = []
        person_ids: list[str] = []
        for person_id, record in self.records.items():
            if not record.features:
                continue
            subject_type = str(record.identity_data.get("subject_type") or "unknown")
            if subject_type == "unknown" and not record.face_verified:
                continue
            for sample in record.features:
                vectors.append(np.asarray(sample.feature, dtype=np.float32).reshape(-1))
                person_ids.append(person_id)

        if not vectors:
            self._faiss_index = None
            self._faiss_person_ids = []
            self._dirty = False
            return

        if self.feature_dim is None:
            self.feature_dim = int(vectors[0].shape[0])
        matrix = np.vstack(vectors).astype(np.float32)
        faiss.normalize_L2(matrix)
        self._faiss_index = faiss.IndexFlatIP(self.feature_dim)
        self._faiss_index.add(matrix)
        self._faiss_person_ids = person_ids
        self._dirty = False


_reid_service: Optional[GlobalReIDService] = None
_reid_service_lock = threading.Lock()


def get_reid_service() -> GlobalReIDService:
    global _reid_service
    if _reid_service is None:
        with _reid_service_lock:
            if _reid_service is None:
                model_path = getattr(settings, "REID_MODEL_PATH", None)
                if model_path is None:
                    raise RuntimeError("REID_MODEL_PATH is not configured")
                extractor = OSNetFeatureExtractor(
                    model_name=getattr(settings, "REID_MODEL_NAME", "osnet_x1_0"),
                    model_path=Path(model_path),
                    input_width=int(getattr(settings, "REID_INPUT_WIDTH", 128)),
                    input_height=int(getattr(settings, "REID_INPUT_HEIGHT", 256)),
                )
                _reid_service = GlobalReIDService(
                    extractor=extractor,
                    match_threshold=float(getattr(settings, "REID_MATCH_THRESHOLD", 0.72)),
                    max_features_per_person=int(getattr(settings, "REID_MAX_FEATURES_PER_PERSON", 80)),
                    feature_dim=extractor._model.feature_dim,
                )
    return _reid_service
