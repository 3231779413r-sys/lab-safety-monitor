"""
Face recognition using InsightFace buffalo_l.

This model bundle provides:
- RetinaFace-based face detection
- ArcFace-based 512-dim face embeddings
"""

import os
import pickle
import shutil
import ctypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..core.config import settings


MPL_CACHE_DIR = Path(settings.BASE_DIR) / "runs" / "matplotlib"
MPL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE_DIR))


class FaceRecognizer:
    """
    Face detection and recognition using InsightFace.

    Uses InsightFace buffalo_l for detection and embeddings.
    """

    def __init__(self, threshold: float = settings.FACE_RECOGNITION_THRESHOLD):
        self.app = None
        self.threshold = threshold
        self.min_detection_score = getattr(
            settings, "FACE_RECOGNITION_MIN_DETECTION_SCORE", 0.6
        )
        self.min_margin = getattr(settings, "FACE_RECOGNITION_MIN_MARGIN", 0.05)
        self._initialized = False
        self.providers: List[str] = ["CPUExecutionProvider"]

    def _configure_runtime_libraries(self) -> None:
        """Expose CUDA/cuDNN shared libraries bundled in the venv to ONNX Runtime."""
        search_dirs: list[Path] = []
        for relative in (
            "nvidia/cudnn/lib",
            "nvidia/cu13/lib",
        ):
            candidate = Path(__file__).resolve().parents[2] / ".venv" / "lib" / "python3.11" / "site-packages" / relative
            if candidate.is_dir():
                search_dirs.append(candidate)

        if not search_dirs:
            return

        existing_ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        current_paths = [part for part in existing_ld_path.split(":") if part]
        for directory in search_dirs:
            directory_str = str(directory)
            if directory_str not in current_paths:
                current_paths.insert(0, directory_str)

        os.environ["LD_LIBRARY_PATH"] = ":".join(current_paths)

        for directory in search_dirs:
            for library_name in ("libcudnn.so.9", "libcudnn_ops.so.9", "libcudnn_cnn.so.9"):
                library_path = directory / library_name
                if library_path.exists():
                    try:
                        ctypes.CDLL(str(library_path), mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        pass

    def _resolve_providers(self) -> List[str]:
        try:
            import onnxruntime as ort
            import torch

            self._configure_runtime_libraries()
            available_providers = ort.get_available_providers()
            if torch.cuda.is_available() and "CUDAExecutionProvider" in available_providers:
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        except Exception:
            pass
        return ["CPUExecutionProvider"]

    def initialize(self):
        """Lazy initialization of InsightFace."""
        if self._initialized:
            return

        try:
            from insightface.app import FaceAnalysis

            model_root = Path(settings.WEIGHTS_DIR) / "insightface"
            model_root.mkdir(parents=True, exist_ok=True)
            model_dir = model_root / "models" / "buffalo_l"
            if not model_dir.exists():
                flat_model_files = [
                    model_root / "1k3d68.onnx",
                    model_root / "2d106det.onnx",
                    model_root / "det_10g.onnx",
                    model_root / "genderage.onnx",
                    model_root / "w600k_r50.onnx",
                ]
                if all(path.exists() for path in flat_model_files):
                    model_dir.mkdir(parents=True, exist_ok=True)
                    for source in flat_model_files:
                        target = model_dir / source.name
                        if not target.exists():
                            shutil.copy2(source, target)

            print("Loading InsightFace buffalo_l model...")
            self.providers = self._resolve_providers()
            self.app = FaceAnalysis(
                name="buffalo_l",
                root=str(model_root),
                providers=self.providers,
            )
            ctx_id = 0 if "CUDAExecutionProvider" in self.providers else -1
            self.app.prepare(ctx_id=ctx_id, det_size=(640, 640))
            self._initialized = True
            print(f"InsightFace buffalo_l loaded successfully with providers={self.providers}")
        except Exception as e:
            raise RuntimeError(
                "InsightFace buffalo_l 初始化失败。请先安装 insightface 及其依赖，"
                f"当前错误: {e}"
            ) from e

    def detect_faces(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Detect faces in frame and extract embeddings.

        Args:
            frame: BGR numpy array from OpenCV

        Returns:
            List of face detections with boxes and embeddings
        """
        if not self._initialized:
            self.initialize()

        if self.app is None:
            raise RuntimeError("人脸识别模型未初始化")

        try:
            faces = self.app.get(frame)

            results = []
            for face in faces:
                results.append(
                    {
                        "box": face.bbox.tolist(),
                        "embedding": face.embedding,
                        "score": float(face.det_score),
                        "landmarks": face.landmark_2d_106.tolist()
                        if face.landmark_2d_106 is not None
                        else None,
                    }
                )

            return results
        except Exception as e:
            print(f"Face detection error: {e}")
            return []

    def extract_embedding_from_image_bytes(self, content: bytes) -> Tuple[np.ndarray, bytes]:
        """Extract the primary face embedding from uploaded image bytes."""
        image_array = np.frombuffer(content, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("无法解析上传的人脸图片")

        faces = self.detect_faces(frame)
        if not faces:
            raise ValueError("未检测到可用人脸")

        strong_faces = [
            face for face in faces if float(face.get("score", 0.0)) >= self.min_detection_score
        ]
        if not strong_faces:
            raise ValueError(
                f"检测到的人脸质量过低，请上传更清晰的正脸照片（检测分数需 >= {self.min_detection_score:.2f}）"
            )

        best_face = max(strong_faces, key=lambda item: float(item.get("score", 0.0)))
        embedding = best_face.get("embedding")
        if embedding is None:
            raise ValueError("未提取到人脸特征")

        x1, y1, x2, y2 = [max(0, int(v)) for v in best_face.get("box", [0, 0, frame.shape[1], frame.shape[0]])]
        if x2 <= x1 or y2 <= y1:
            cropped = frame
        else:
            cropped = frame[y1:y2, x1:x2]
        ok, encoded = cv2.imencode(".jpg", cropped)
        if not ok:
            raise ValueError("无法编码人脸裁剪图")
        return np.asarray(embedding, dtype=np.float32), encoded.tobytes()

    def compare_embeddings(
        self, embedding1: np.ndarray, embedding2: np.ndarray
    ) -> float:
        """
        Compare two face embeddings using cosine similarity.

        Returns:
            Cosine similarity score between -1 and 1
        """
        if embedding1 is None or embedding2 is None:
            return 0.0

        # Normalize embeddings
        norm1 = np.linalg.norm(embedding1)
        norm2 = np.linalg.norm(embedding2)
        if norm1 <= 1e-8 or norm2 <= 1e-8:
            return 0.0

        e1 = embedding1 / norm1
        e2 = embedding2 / norm2

        # Cosine similarity
        similarity = float(np.dot(e1, e2))
        return max(-1.0, min(1.0, similarity))

    def find_matching_person(
        self, embedding: np.ndarray, known_embeddings: List[Tuple[str, np.ndarray]]
    ) -> Optional[Tuple[str, float]]:
        """
        Find the best matching person from known embeddings.

        Args:
            embedding: Face embedding to match
            known_embeddings: List of (person_id, embedding) tuples

        Returns:
            Tuple of (person_id, similarity) if match found, else None
        """
        if embedding is None or not known_embeddings:
            return None

        best_match = None
        best_score = 0.0

        for person_id, known_embedding in known_embeddings:
            similarity = self.compare_embeddings(embedding, known_embedding)

            if similarity > best_score and similarity >= self.threshold:
                best_score = similarity
                best_match = person_id

        if best_match:
            return (best_match, best_score)

        return None

    def is_strong_match(
        self,
        best_similarity: float,
        second_best_similarity: Optional[float] = None,
    ) -> bool:
        if best_similarity < self.threshold:
            return False
        if second_best_similarity is None:
            return True
        return (best_similarity - second_best_similarity) >= self.min_margin

    @staticmethod
    def similarity_to_score(similarity: float) -> float:
        normalized = max(0.0, min(1.0, (similarity + 1.0) / 2.0))
        return normalized * 100.0

    @staticmethod
    def serialize_embedding(embedding: np.ndarray) -> bytes:
        """Serialize embedding for database storage."""
        return pickle.dumps(embedding)

    @staticmethod
    def deserialize_embedding(data: bytes) -> np.ndarray:
        """Deserialize embedding from database."""
        return pickle.loads(data)


# Singleton instance
_recognizer = None


def get_face_recognizer() -> FaceRecognizer:
    global _recognizer
    if _recognizer is None:
        _recognizer = FaceRecognizer()
    return _recognizer
