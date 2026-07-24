"""Application settings with environment variable support."""

from pydantic_settings import BaseSettings
from pydantic import Field, model_validator
from pathlib import Path
from typing import List, Optional, Dict


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # App settings
    APP_NAME: str = "碳纤维碳化车间人员危险行为识别系统"
    DEBUG: bool = True
    APP_ENV: str = "dev"
    BACKEND_MODE: str = "all"
    SQL_ECHO: bool = False

    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"]
    )

        # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:123456@localhost:5432/sentinelvision"

    # JWT Settings
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 0
    WORKER_INTERNAL_BASE_URL: str = "http://monitor-worker:8001"
    WORKER_INTERNAL_BASE_URLS: List[str] = Field(default_factory=list)
    WORKER_INTERNAL_TOKEN: str = "change-this-in-production"
    WORKER_INTERNAL_TIMEOUT_SECONDS: int = 30
    CAMERA_MONITOR_SHARD_INDEX: int = 0
    CAMERA_MONITOR_SHARD_COUNT: int = 1
    INFERENCE_BACKEND: str = "local"
    IDENTITY_BACKEND: str = "local"
    RABBITMQ_URL: str = "amqp://lab:rabbitmq-password@rabbitmq:5672/"
    RABBITMQ_FRAME_QUEUE: str = "lab-safety.inference.frames"
    RABBITMQ_RESULT_QUEUE: str = "lab-safety.inference.results"
    RABBITMQ_IDENTITY_QUEUE: str = "lab-safety.identity.frames"
    RABBITMQ_IDENTITY_RESULT_QUEUE: str = "lab-safety.identity.results"
    RABBITMQ_PUBLISH_TIMEOUT_SECONDS: float = 2.0
    BROKER_PUBLISH_TIMEOUT_SECONDS: float = 2.0
    INFERENCE_BATCH_SIZE: int = 2
    INFERENCE_MIN_BATCH_SIZE: int = 1
    INFERENCE_MAX_BATCH_SIZE: int = 2
    INFERENCE_BATCH_TIMEOUT_MS: int = 6
    INFERENCE_BATCH_HOT_TIMEOUT_MS: int = 8
    INFERENCE_BATCH_STALE_TIMEOUT_MS: int = 5
    INFERENCE_BATCH_STALE_FRAME_MS: float = 700.0
    INFERENCE_BATCH_FORCE_DISPATCH_AGE_MS: float = 1200.0
    INFERENCE_BATCH_COMPLEX_FRAME_PERSON_THRESHOLD: int = 2
    INFERENCE_BATCH_MAX_COMPLEX_FRAMES: int = 1
    INFERENCE_BATCH_HOT_CAMERA_MAX_BATCH_SIZE: int = 2
    INFERENCE_BATCH_READY_QUEUE_SIZE: int = 1
    INFERENCE_QUEUE_PREFETCH: int = 16
    INFERENCE_DYNAMIC_BATCH_ENABLED: bool = True
    INFERENCE_DYNAMIC_BATCH_BACKLOG_THRESHOLD: int = 8
    INFERENCE_DYNAMIC_BATCH_MAX_LATENCY_MS: int = 220
    INFERENCE_MAX_FRAMES_PER_CAMERA_PER_BATCH: int = 2
    INFERENCE_WORKER_EXECUTOR_WORKERS: int = 1
    INFERENCE_MODEL_PARALLELISM: int = 2
    INFERENCE_FRAME_JPEG_QUALITY: int = 80
    INFERENCE_MAX_PENDING_FRAMES_PER_CAMERA: int = 1
    INFERENCE_PENDING_TIMEOUT_SECONDS: float = 10.0
    IDENTITY_BATCH_SIZE: int = 4
    IDENTITY_MAX_BATCH_SIZE: int = 6
    IDENTITY_BATCH_TIMEOUT_MS: int = 20
    IDENTITY_QUEUE_PREFETCH: int = 8
    IDENTITY_WORKER_EXECUTOR_WORKERS: int = 1
    IDENTITY_MAX_PENDING_FRAMES_PER_CAMERA: int = 4
    IDENTITY_MAX_PERSONS_PER_FRAME: int = 2
    IDENTITY_PENDING_TIMEOUT_SECONDS: float = 10.0
    IDENTITY_ASYNC_PENDING_TIMEOUT_SECONDS: float = 30.0
    IDENTITY_SUSPEND_ON_HIGH_INFERENCE_LATENCY: bool = True
    IDENTITY_HIGH_LATENCY_THRESHOLD_MS: float = 900.0
    IDENTITY_SUSPEND_SECONDS: float = 8.0
    SHARED_FRAME_DIR: str = "/shared-frames/lab-safety-monitor"
    SHARED_FRAME_RETENTION_SECONDS: int = 120
    LIVE_FRAME_JPEG_QUALITY: int = 75
    LIVE_PREVIEW_MIN_INTERVAL_SECONDS: float = 0.5
    PG_NOTIFY_CHANNEL: str = "lab_safety_realtime"
    INIT_ADMIN_ENABLED: bool = True
    INIT_ADMIN_USERNAME: str = "admin"
    INIT_ADMIN_EMAIL: str = "admin@example.com"
    INIT_ADMIN_PASSWORD: str = "Admin123456"
    INIT_ADMIN_FULL_NAME: str = "系统管理员"

    # Detector settings
    DETECTOR_TYPE: str = Field(default="yolov11")
    DETECTION_CONFIDENCE_THRESHOLD: float = 0.5
    POSITIVE_PPE_CONFIDENCE_THRESHOLDS: Dict[str, float] = Field(
        default={"protective_clothing": 0.35}
    )
    PERSON_MIN_BOX_AREA_RATIO: float = 0.14
    PERSON_MIN_BOX_AREA_FALLBACK_SCORE_THRESHOLD: float = 0.7
    PERSON_MIN_BOX_AREA_FALLBACK_MIN_ASPECT_RATIO: float = 1.3
    VIOLATION_CONFIDENCE_THRESHOLD: float = 0.3
    FACE_RECOGNITION_THRESHOLD: float = 0.45
    FACE_RECOGNITION_MIN_DETECTION_SCORE: float = 0.6
    FACE_RECOGNITION_MIN_MARGIN: float = 0.05
    REID_ENABLED: bool = Field(default=True)
    REID_MODEL_NAME: str = Field(default="osnet_x1_0")
    REID_MODEL_PATH: Optional[Path] = None
    REID_INPUT_WIDTH: int = Field(default=128)
    REID_INPUT_HEIGHT: int = Field(default=256)
    REID_MATCH_THRESHOLD: float = Field(default=0.72)
    REID_MAX_FEATURES_PER_PERSON: int = Field(default=80)
    REID_FEATURE_TTL_SECONDS: int = Field(default=7200)
    REID_UNKNOWN_ID_PREFIX: str = Field(default="reid_unknown")
    REID_EXTRACT_FEATURES_IN_MAIN_PATH: bool = Field(default=False)

    # Pose estimation
    USE_POSE_ESTIMATION: bool = Field(default=False)
    POSE_MODEL_PATH: Optional[Path] = None
    POSE_CONFIDENCE_THRESHOLD: float = Field(default=0.4)
    POSE_IOU_THRESHOLD: float = Field(default=0.3)
    POSE_RUN_INTERVAL: int = Field(default=1)
    POSE_KEYPOINT_CONFIDENCE_THRESHOLD: float = Field(default=0.35)
    POSE_ACTION_LABELS_ENABLED: bool = Field(default=True)
    POSE_SHOW_NEUTRAL_STATUS: bool = Field(default=False)
    POSE_ACTION_MIN_FRAMES: int = Field(default=2)
    POSE_ACTION_CLEAR_FRAMES: int = Field(default=2)
    POSE_HAND_MOUTH_DISTANCE_RATIO: float = Field(default=0.18)
    POSE_FALL_ASPECT_RATIO: float = Field(default=1.25)
    POSE_BENDING_ANGLE_DEG: float = Field(default=35.0)

    # SAM3 settings
    SAM3_MODEL: str = "facebook/sam3"
    SAM3_MODEL_ID: str = (
        "facebook/sam2.1-hiera-tiny"  # HuggingFace model for transformers
    )
    SAM3_MODEL_PATH: Optional[Path] = None
    USE_SAM3: bool = Field(default=True)

    # SAM2 settings
    SAM2_MODEL_TYPE: str = Field(default="sam2.1_hiera_base_plus")
    SAM2_MODEL_PATH: Optional[Path] = None
    USE_SAM2: bool = Field(default=True)
    USE_SAM2_VIDEO_PROPAGATION: bool = Field(default=True)
    SAM2_PROPAGATE_INTERVAL: int = Field(default=2)
    SAM2_SEGMENT_PPE: bool = Field(default=True)

    # Mask settings
    MASK_DENSITY_THRESHOLD: float = Field(default=0.1)
    MASK_CONTAINMENT_THRESHOLD: float = Field(default=0.5)
    SHOW_MASKS: bool = Field(default=True)
    MASK_ALPHA: float = Field(default=0.4)

    # Mock mode
    USE_MOCK_DETECTOR: bool = False
    USE_MOCK_FACE: bool = False

    # Temporal filtering
    FRAME_SAMPLE_RATE: int = 10
    TEMPORAL_BUFFER_SIZE: int = 5
    TEMPORAL_VIOLATION_MIN_FRAMES: int = 5
    TEMPORAL_VIOLATION_MIN_FRAMES_CLEAR: int = 3  # Frames without violation to clear (hysteresis)
    TEMPORAL_FUSION_STRATEGY: str = Field(default="ema")
    TEMPORAL_EMA_ALPHA: float = Field(default=0.7)
    TEMPORAL_CONFIDENCE_THRESHOLD: float = Field(default=0.4)
    PPE_UNKNOWN_AS_MISSING_CONFIDENCE: float = Field(default=0.45)
    PPE_UNKNOWN_AS_MISSING_TYPES: List[str] = Field(default=["protective_clothing"])
    PPE_STRICT_CONSECUTIVE_TYPES: List[str] = Field(
        default=["hardhat", "protective_clothing"]
    )
    PPE_VIOLATION_MIN_FRAMES: Dict[str, int] = Field(
        default={"hardhat": 5, "protective_clothing": 5}
    )

    # Live stream settings
    LIVE_STREAM_DISPLAY_FPS: int = Field(default=30)  # Display frame rate
    LIVE_STREAM_PROCESS_FPS: int = Field(default=2)   # ML processing rate (lower = faster)
    LIVE_STREAM_QUEUE_SIZE: int = Field(default=2)    # Max queued frames for processing
    LIVE_STREAM_INTERPOLATE: bool = Field(default=False)  # Smooth bbox movement (optional)
    CAMERA_MONITOR_AUTO_START: bool = Field(default=True)
    CAMERA_MONITOR_RECONNECT_SECONDS: int = Field(default=5)
    CAMERA_MONITOR_MAX_CAMERAS: int = Field(default=4)
    CAMERA_CAPTURE_POLL_FPS: int = Field(default=12)
    CAMERA_MONITOR_DISPLAY_FPS: int = Field(default=10)
    CAMERA_RUNTIME_SHARED_SUBMIT_THREADS: int = Field(default=2)
    CAMERA_RUNTIME_SHARED_DISPATCH_THREADS: int = Field(default=2)
    CAMERA_RUNTIME_BACKGROUND_WORKERS: int = Field(default=4)
    CAMERA_RUNTIME_SHARED_PROCESSING_WORKERS: int = Field(default=4)
    CAMERA_RUNTIME_VISITOR_EXEMPTION_REFRESH_SECONDS: int = Field(default=5)
    CAMERA_RUNTIME_LATEST_ONLY_MODE: bool = Field(default=True)
    CAMERA_RUNTIME_MAX_MAILBOX_FRAMES: int = Field(default=1)
    OPENCV_NUM_THREADS: int = Field(default=1)
    TORCH_NUM_THREADS: int = Field(default=1)
    TORCH_INTEROP_THREADS: int = Field(default=1)
    ONNX_INTRA_OP_THREADS: int = Field(default=1)
    ONNX_INTER_OP_THREADS: int = Field(default=1)
    FACE_IDENTITY_CACHE_TTL_SECONDS: float = Field(default=8.0)
    FACE_IDENTITY_REFRESH_INTERVAL_FRAMES: int = Field(default=8)
    FACE_DETECTION_INTERVAL_FRAMES: int = Field(default=3)
    FACE_RETRY_INTERVAL_FRAMES: int = Field(default=10)
    CAMERA_RUNTIME_METRICS_WINDOW_SIZE: int = Field(default=30)
    CAMERA_RUNTIME_SUMMARY_INTERVAL_SECONDS: int = Field(default=60)
    CAMERA_LATENCY_DEGRADE_P95_MS_L1: float = Field(default=850.0)
    CAMERA_LATENCY_DEGRADE_P95_MS_L2: float = Field(default=1200.0)
    CAMERA_LATENCY_DEGRADE_P95_MS_L3: float = Field(default=1700.0)
    CAMERA_LATENCY_RECOVER_P95_MS: float = Field(default=500.0)
    CAMERA_COMPLEXITY_DEGRADE_THRESHOLD: float = Field(default=5.0)
    CAMERA_DEGRADE_ENTER_HOLD_SECONDS: float = Field(default=10.0)
    CAMERA_DEGRADE_RECOVER_HOLD_SECONDS: float = Field(default=25.0)
    CAMERA_DEGRADE_MIN_DWELL_SECONDS: float = Field(default=15.0)
    CAMERA_HOT_AVG_INFERENCE_TOTAL_MS: float = Field(default=600.0)
    CAMERA_HOT_P95_INFERENCE_TOTAL_MS: float = Field(default=1200.0)
    CAMERA_HOT_AVG_INFERENCE_BATCH_WAIT_MS: float = Field(default=250.0)
    CAMERA_HOT_P95_INFERENCE_BATCH_WAIT_MS: float = Field(default=600.0)
    CAMERA_HOT_LATEST_FRAME_AGE_MS: float = Field(default=900.0)
    CAMERA_HOT_AVG_PERSON_COUNT: float = Field(default=1.5)
    CAMERA_HOT_AVG_BACKLOG: float = Field(default=0.5)
    CAMERA_HOT_BACKPRESSURE_SKIP_COUNT: int = Field(default=3)
    CAMERA_RUNTIME_HOT_CAMERA_SCORE_THRESHOLD: float = Field(default=3.0)
    CAMERA_RUNTIME_FORCE_PROFILE_OVERRIDES: Dict[str, str] = Field(default_factory=dict)
    CAMERA_SHARD_OVERRIDES: Dict[str, int] = Field(default_factory=dict)
    MAX_PERSONS_PER_FRAME_FOR_FULL_INFERENCE: int = Field(default=4)
    MAX_PERSONS_PER_FRAME_FOR_IDENTITY: int = Field(default=2)
    MAX_PERSONS_PER_FRAME_FAST_PROFILE: int = Field(default=2)
    IDENTITY_USE_PERSON_CROPS_FIRST: bool = Field(default=True)
    IDENTITY_MAX_CROPS_PER_FRAME: int = Field(default=2)
    SKIP_POSE_WHEN_PERSON_COUNT_GE: int = Field(default=3)
    SKIP_SEGMENTATION_WHEN_PERSON_COUNT_GE: int = Field(default=5)
    EVENT_VIDEO_CAPTURE_FPS: int = Field(default=10)
    EVENT_VIDEO_PRE_SECONDS: int = Field(default=3)
    EVENT_VIDEO_POST_SECONDS: int = Field(default=3)
    INSPECTION_CHECK_INTERVAL_SECONDS: int = Field(default=30)
    WORKSHOP_OVERCAPACITY_CHECK_INTERVAL_SECONDS: int = Field(default=5)
    VIOLATION_ALERT_COOLDOWN_SECONDS: int = Field(default=300)
    PERSON_LAST_SEEN_UPDATE_SECONDS: int = Field(default=30)
    CAMERA_READ_FAILURE_THRESHOLD: int = Field(default=5)
    CAMERA_RUNTIME_PROCESS_QUEUE_SIZE: int = Field(default=4)
    POSTPROCESS_THREAD_POOL_WORKERS: int = Field(default=4)
    HIKVISION_SDK_DIR: Optional[Path] = None
    HIKVISION_SDK_PORT: int = Field(default=8000)
    HIKVISION_DECODE_TARGET_FPS: int = Field(default=10)

    # Multi-scale detection
    MULTI_SCALE_ENABLED: bool = Field(default=True)
    MULTI_SCALE_FACTORS: List[float] = Field(default=[1.0, 1.5, 2.0])
    MULTI_SCALE_NMS_THRESHOLD: float = Field(default=0.5)

    # PPE configuration
    PPE_PROMPTS: List[str] = Field(
        default=["mask", "hardhat", "protective_clothing"]
    )
    REQUIRED_PPE: List[str] = Field(
        default=["mask", "hardhat", "protective_clothing"]
    )

    PPE_CLASS_MAP: Dict[str, str] = Field(
        default={
            "Mask": "mask",
            "Hardhat": "hardhat",
            "Safety Vest": "protective_clothing",
            "Work Clothes": "protective_clothing",
        }
    )

    VIOLATION_CLASSES: List[str] = Field(
        default=["NO-Mask", "NO-Hardhat", "NO-Protective Clothing"]
    )

    ACTION_VIOLATION_CLASSES: List[str] = Field(default=[])

    # Paths
    BASE_DIR: Path = BACKEND_DIR
    WEIGHTS_DIR: Optional[Path] = None
    DATA_DIR: Optional[Path] = None
    VIDEOS_DIR: Optional[Path] = None
    SNAPSHOTS_DIR: Optional[Path] = None
    PROCESSED_DIR: Optional[Path] = None
    LOGS_DIR: Optional[Path] = None
    LIVE_PREVIEW_DIR: Optional[Path] = None
    ENABLE_SNAPSHOT_CAPTURE: bool = True
    YOLOV11_MODEL_PATH: Optional[Path] = None

    # MinIO snapshot storage
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "lab-safety-monitor"
    MINIO_VIDEO_BUCKET: str = "lab-safety-videos"
    MINIO_FACE_BUCKET: str = "lab-safety-faces"
    MINIO_SECURE: bool = False
    MINIO_PRESIGNED_EXPIRE_SECONDS: int = 3600

    @model_validator(mode="after")
    def set_derived_paths(self):
        """Set derived paths after initialization."""
        if self.WEIGHTS_DIR is None:
            object.__setattr__(self, "WEIGHTS_DIR", self.BASE_DIR / "weights")

        if self.DATA_DIR is None:
            object.__setattr__(self, "DATA_DIR", self.BASE_DIR.parent / "data")

        if self.VIDEOS_DIR is None:
            object.__setattr__(self, "VIDEOS_DIR", self.DATA_DIR / "videos")

        if self.SNAPSHOTS_DIR is None:
            object.__setattr__(self, "SNAPSHOTS_DIR", self.DATA_DIR / "snapshots")

        if self.PROCESSED_DIR is None:
            object.__setattr__(self, "PROCESSED_DIR", self.DATA_DIR / "processed")

        if self.LOGS_DIR is None:
            object.__setattr__(self, "LOGS_DIR", self.DATA_DIR / "logs")

        if self.LIVE_PREVIEW_DIR is None:
            object.__setattr__(self, "LIVE_PREVIEW_DIR", self.DATA_DIR / "live_preview")

        if self.HIKVISION_SDK_DIR is None:
            object.__setattr__(
                self,
                "HIKVISION_SDK_DIR",
                self.BASE_DIR.parent
                / "HCNetSDKV6.1.11.5_build20251204_linux64_ZH"
                / "库文件",
            )

        if self.SAM2_MODEL_PATH is None:
            object.__setattr__(
                self,
                "SAM2_MODEL_PATH",
                self.WEIGHTS_DIR / "sam2" / "sam2.1_hiera_base_plus.pt",
            )

        if self.SAM3_MODEL_PATH is None:
            object.__setattr__(
                self, "SAM3_MODEL_PATH", self.WEIGHTS_DIR / "sam3" / "sam3.pt"
            )

        if self.POSE_MODEL_PATH is None:
            object.__setattr__(
                self,
                "POSE_MODEL_PATH",
                self.WEIGHTS_DIR / "pose" / "yolo11n-pose.pt",
            )

        if self.REID_MODEL_PATH is None:
            object.__setattr__(
                self,
                "REID_MODEL_PATH",
                self.WEIGHTS_DIR / "reid" / f"{self.REID_MODEL_NAME}_imagenet.pth",
            )

        # Set YOLOv11 model path (prefer .pt over .onnx)
        if self.YOLOV11_MODEL_PATH is None:
            pt_path = self.WEIGHTS_DIR / "ppe_detector" / "best.pt"
            alt_path = self.WEIGHTS_DIR / "ppe_detector" / "YOLOv8 Finetuning for PPE detection.pt"
            onnx_path = self.WEIGHTS_DIR / "ppe_detector" / "best.onnx"

            if pt_path.exists():
                object.__setattr__(self, "YOLOV11_MODEL_PATH", pt_path)
            elif alt_path.exists():
                object.__setattr__(self, "YOLOV11_MODEL_PATH", alt_path)
            elif onnx_path.exists():
                object.__setattr__(self, "YOLOV11_MODEL_PATH", onnx_path)
            else:
                object.__setattr__(self, "YOLOV11_MODEL_PATH", pt_path)

        return self

    model_config = {
        "env_file": str(BACKEND_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
