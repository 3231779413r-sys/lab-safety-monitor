import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from .core.config import settings
from .core.logging_setup import configure_logging
from .core.runtime_tuning import configure_runtime_tuning
from .core.realtime_bus import PostgresNotificationRelay
from .core.websocket import manager as websocket_manager
from .core.database import init_db, async_session
from . import models as _models
from .services.person_service import PersonService
from .services.bootstrap_service import ensure_initial_admin
from .services.camera_service import CameraService
from .api.routes import (
    auth_router,
    cameras_router,
    events_router,
    persons_router,
    stats_router,
    supervision_router,
    ws_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动和关闭事件"""
    component = "backend/api" if settings.BACKEND_MODE == "api" else "backend"
    configure_logging(component)
    configure_runtime_tuning(component)
    print(f"Starting {settings.APP_NAME}...")
    await init_db()
    print("数据库初始化完成")

    # 确保目录存在
    settings.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    settings.WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LIVE_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    relay = PostgresNotificationRelay(websocket_manager.broadcast)
    await relay.start()

    async with async_session() as session:
        created_admin = await ensure_initial_admin(session)
        if created_admin:
            print(
                "Initialized admin user:",
                settings.INIT_ADMIN_USERNAME,
                settings.INIT_ADMIN_EMAIL,
            )

    if settings.BACKEND_MODE != "api":
        from .ml.pipeline import get_pipeline
        from .services.camera_runtime import camera_runtime_registry

        pipeline = get_pipeline()
        pipeline.initialize()
        async with async_session() as session:
            person_service = PersonService(session)
            embeddings = await person_service.get_all_embeddings()
            if embeddings:
                pipeline.load_known_persons(embeddings)
                print(f"已加载 {len(embeddings)} 个已知人员")

        camera_runtime_registry.bind_loop(asyncio.get_running_loop())
        if settings.CAMERA_MONITOR_AUTO_START:
            async with async_session() as session:
                camera_service = CameraService(session)
                cameras = await camera_service.list_cameras()
                started = 0
                for camera in cameras:
                    if not camera.enabled:
                        continue
                    if started >= settings.CAMERA_MONITOR_MAX_CAMERAS:
                        break
                    camera_runtime_registry.start_camera(camera)
                    started += 1
                print(f"Started {started} camera monitor runtime(s)")

    yield
    await relay.stop()
    if settings.BACKEND_MODE != "api":
        from .services.camera_runtime import camera_runtime_registry

        camera_runtime_registry.stop_all()
    print("系统关闭中...")


app = FastAPI(title=settings.APP_NAME, description="AI驱动的安全合规监测系统", version="1.0.0", lifespan=lifespan)

# CORS 配置
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# 注册路由
app.include_router(events_router, prefix="/api")
app.include_router(persons_router, prefix="/api")
app.include_router(stats_router, prefix="/api")
app.include_router(cameras_router, prefix="/api")
app.include_router(ws_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(supervision_router, prefix="/api")


@app.get("/")
async def root():
    """根路径"""
    return {"name": settings.APP_NAME, "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


@app.get("/api/system/status")
async def system_status():
    """系统状态"""
    return {
        "status": "running",
        "app_name": settings.APP_NAME,
        "version": "1.0.0",
        "mode": {
            "mock_detector": settings.USE_MOCK_DETECTOR,
            "mock_face": settings.USE_MOCK_FACE,
            "sam3_enabled": settings.USE_SAM3,
            "sam2_enabled": settings.USE_SAM2,
        },
        "performance": {
            "live_stream_display_fps": settings.LIVE_STREAM_DISPLAY_FPS,
            "live_stream_process_fps": settings.LIVE_STREAM_PROCESS_FPS,
            "multi_scale_enabled": settings.MULTI_SCALE_ENABLED,
        },
        "detection": {
            "confidence_threshold": settings.DETECTION_CONFIDENCE_THRESHOLD,
            "violation_threshold": settings.VIOLATION_CONFIDENCE_THRESHOLD,
            "temporal_fusion": settings.TEMPORAL_FUSION_STRATEGY,
        },
        "deployment": {
            "backend_mode": settings.BACKEND_MODE,
        },
    }
