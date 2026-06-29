from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import cv2
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from . import models as _models
from .api.routes.persons import (
    _build_external_person_row,
    _build_person_row,
    _collect_violation_counts,
)
from .api.routes.supervision import (
    FaceMatchCandidate as SupervisionFaceMatchCandidate,
    FaceMatchResponse,
    _build_external_response,
)
from .core.config import settings
from .core.database import async_session, init_db
from .core.logging_setup import configure_logging
from .core.runtime_tuning import configure_runtime_tuning
from .api.routes.cameras import (
    _candidate_image_url,
    _open_manual_frame_source,
    _select_face_match_candidate,
    _similarity_to_score,
    CameraFaceMatchResponse,
    FaceMatchCandidate as CameraFaceMatchCandidate,
    LivePersonOverlayResponse,
)
from .ml.face_recognition import FaceRecognizer
from .models.external_person import ExternalPerson
from .models.person import Person
from .models.supervision import ExternalPersonnelRegistration
from .services.camera_service import CameraService, get_camera_config, test_camera_connection, update_camera_config
from .services.face_registry_service import get_face_registry_service
from .services.identity_broker import get_identity_broker
from .services.inference_broker import get_inference_broker
from .services.worker_sharding import camera_belongs_to_shard

logger = logging.getLogger(__name__)


def _get_camera_runtime_registry():
    from .services.camera_runtime import camera_runtime_registry

    return camera_runtime_registry


def _camera_owned_by_this_worker(camera_id: str) -> bool:
    return camera_belongs_to_shard(
        camera_id,
        int(getattr(settings, "CAMERA_MONITOR_SHARD_INDEX", 0)),
        max(1, int(getattr(settings, "CAMERA_MONITOR_SHARD_COUNT", 1))),
    )


def _ensure_camera_owned(camera_id: str) -> None:
    if _camera_owned_by_this_worker(camera_id):
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"Camera {camera_id} is assigned to another monitor worker "
            f"(shard {settings.CAMERA_MONITOR_SHARD_INDEX}/{settings.CAMERA_MONITOR_SHARD_COUNT})"
        ),
    )


def _candidate_image_url(
    storage: str | None,
    bucket: str | None,
    object_key: str | None,
) -> str | None:
    if storage == "minio" and bucket and object_key:
        return f"/api/events/objects/{bucket}/{object_key}"
    return None


def _verify_worker_token(x_worker_token: str = Header(...)) -> None:
    if x_worker_token != settings.WORKER_INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid worker token")


class CameraConfigRequest(BaseModel):
    video_encoding: str | None = None
    video_resolution_width: int | None = None
    video_resolution_height: int | None = None
    frame_rate: int | None = None
    max_bitrate: int | None = None
    bit_rate: int | None = None
    gov_length: int | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(f"backend/worker-shard-{int(getattr(settings, 'CAMERA_MONITOR_SHARD_INDEX', 0))}")
    configure_runtime_tuning("worker")
    await init_db()
    settings.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    settings.WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LIVE_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()
    registry = _get_camera_runtime_registry()
    registry.bind_loop(loop)
    shard_index = int(getattr(settings, "CAMERA_MONITOR_SHARD_INDEX", 0))
    shard_count = max(1, int(getattr(settings, "CAMERA_MONITOR_SHARD_COUNT", 1)))
    logger.info(
        "Starting monitor worker shard index=%s count=%s shared_frame_dir=%s",
        shard_index,
        shard_count,
        settings.SHARED_FRAME_DIR,
    )
    broker = None
    identity_broker = None
    if settings.INFERENCE_BACKEND.lower() == "queue":
        broker = get_inference_broker()
        await broker.start(loop=loop, result_handler=registry.handle_inference_result)
        registry.bind_inference_broker(broker)
    if settings.IDENTITY_BACKEND.lower() == "queue":
        identity_broker = get_identity_broker()
        await identity_broker.start(loop=loop, result_handler=registry.handle_identity_result)
        registry.bind_identity_broker(identity_broker)
    async with async_session() as session:
        service = CameraService(session)
        cameras = await service.list_cameras()
        started = 0
        for camera in cameras:
            if not camera.enabled:
                continue
            if not _camera_owned_by_this_worker(camera.id):
                continue
            if started >= settings.CAMERA_MONITOR_MAX_CAMERAS:
                break
            registry.start_camera(camera)
            started += 1

    yield
    registry.stop_all()
    if identity_broker is not None:
        await identity_broker.stop()
    if broker is not None:
        await broker.stop()


app = FastAPI(
    title=f"{settings.APP_NAME} Worker",
    description="Internal monitor worker service",
    version="1.0.0",
    lifespan=lifespan,
)


async def _get_camera(camera_id: str):
    _ensure_camera_owned(camera_id)
    async with async_session() as session:
        service = CameraService(session)
        camera = await service.get_camera(camera_id)
        if not camera or camera.source_type != "camera":
            raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")
        return camera


async def _get_camera_from_session(camera_id: str, session):
    _ensure_camera_owned(camera_id)
    service = CameraService(session)
    camera = await service.get_camera(camera_id)
    if not camera or camera.source_type != "camera":
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")
    return camera


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "mode": "worker"}


@app.get("/internal/cameras/runtime/all/status", dependencies=[Depends(_verify_worker_token)])
async def list_camera_runtime_status():
    return {"cameras": _get_camera_runtime_registry().list_statuses()}


@app.get("/internal/cameras/runtime/all/summary", dependencies=[Depends(_verify_worker_token)])
async def list_camera_runtime_summary():
    registry = _get_camera_runtime_registry()
    return {
        "shard": registry.shard_summary_snapshot(),
        "cameras": registry.list_runtime_summaries(),
    }


@app.get("/internal/cameras/{camera_id}/runtime/status", dependencies=[Depends(_verify_worker_token)])
async def get_camera_runtime_status(camera_id: str):
    return _get_camera_runtime_registry().get_status(camera_id)


@app.post("/internal/cameras/{camera_id}/start", dependencies=[Depends(_verify_worker_token)])
async def start_camera(camera_id: str):
    camera = await _get_camera(camera_id)
    _get_camera_runtime_registry().start_camera(camera)
    return {"success": True, "status": _get_camera_runtime_registry().get_status(camera_id)}


@app.post("/internal/cameras/{camera_id}/stop", dependencies=[Depends(_verify_worker_token)])
async def stop_camera(camera_id: str):
    _get_camera_runtime_registry().stop_camera(camera_id)
    return {"success": True, "status": _get_camera_runtime_registry().get_status(camera_id)}


@app.post("/internal/cameras/{camera_id}/restart", dependencies=[Depends(_verify_worker_token)])
async def restart_camera(camera_id: str):
    camera = await _get_camera(camera_id)
    _get_camera_runtime_registry().restart_camera(camera)
    return {"success": True, "status": _get_camera_runtime_registry().get_status(camera_id)}


@app.get("/internal/cameras/{camera_id}/live/feed", dependencies=[Depends(_verify_worker_token)])
async def live_camera_feed(
    camera_id: str,
    raw: bool = Query(default=False),
):
    camera = await _get_camera(camera_id)
    if not camera.enabled:
        raise HTTPException(status_code=400, detail="Camera is disabled")
    if _get_camera_runtime_registry().get_status(camera_id)["status"] == "stopped":
        _get_camera_runtime_registry().start_camera(camera)

    async def cached_frame_generator():
        import asyncio
        import cv2
        import numpy as np

        stream_fps = min(
            max(1, settings.LIVE_STREAM_DISPLAY_FPS),
            max(1, settings.CAMERA_MONITOR_DISPLAY_FPS),
        )
        delay = 1.0 / stream_fps
        while True:
            frame_bytes = _get_camera_runtime_registry().get_latest_frame_jpeg(camera_id, raw=raw)
            if frame_bytes is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                status = _get_camera_runtime_registry().get_status(camera_id)
                message = f"Camera {status['status']}"
                cv2.putText(
                    frame,
                    message,
                    (40, 240),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2,
                )
                _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                frame_bytes = buffer.tobytes()
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            await asyncio.sleep(delay)

    return StreamingResponse(
        cached_frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get(
    "/internal/cameras/{camera_id}/live/people",
    dependencies=[Depends(_verify_worker_token)],
    response_model=LivePersonOverlayResponse,
)
async def live_people(camera_id: str):
    payload = _get_camera_runtime_registry().get_latest_person_overlays(camera_id)
    return LivePersonOverlayResponse(**payload)


@app.get(
    "/internal/cameras/live/people",
    dependencies=[Depends(_verify_worker_token)],
)
async def list_live_people():
    return {"cameras": _get_camera_runtime_registry().list_latest_person_overlays()}


@app.get(
    "/internal/cameras/{camera_id}/live/frame.jpg",
    dependencies=[Depends(_verify_worker_token)],
)
async def live_frame_image(camera_id: str, raw: bool = Query(default=False)):
    frame_bytes = _get_camera_runtime_registry().get_latest_frame_jpeg(camera_id, raw=raw)
    if frame_bytes is None:
        raise HTTPException(status_code=404, detail="Frame not available")
    return Response(content=frame_bytes, media_type="image/jpeg")


@app.get("/internal/cameras/{camera_id}/config", dependencies=[Depends(_verify_worker_token)])
async def get_cam_config(camera_id: str):
    async with async_session() as session:
        service = CameraService(session)
        camera = await _get_camera_from_session(camera_id, session)
        result = await get_camera_config(camera)
        if result.success and result.config:
            await service.sync_camera_config(camera, result.config)
        return {
            "success": result.success,
            "message": result.message,
            "config": result.config,
            "error": result.error,
        }


@app.put("/internal/cameras/{camera_id}/config", dependencies=[Depends(_verify_worker_token)])
async def put_cam_config(camera_id: str, request: CameraConfigRequest):
    async with async_session() as session:
        service = CameraService(session)
        camera = await _get_camera_from_session(camera_id, session)
        config = {key: value for key, value in request.model_dump().items() if value is not None}
        if request.video_resolution_width and request.video_resolution_height:
            camera.video_resolution = f"{request.video_resolution_width}x{request.video_resolution_height}"
        result = await update_camera_config(camera, config)
        if result.success and result.config:
            await service.sync_camera_config(camera, result.config)
        return {
            "success": result.success,
            "message": result.message,
            "config": result.config,
            "error": result.error,
        }


@app.post("/internal/cameras/{camera_id}/test", dependencies=[Depends(_verify_worker_token)])
async def test_camera(camera_id: str):
    async with async_session() as session:
        service = CameraService(session)
        camera = await _get_camera_from_session(camera_id, session)
        result = await test_camera_connection(camera)
        await service.update_test_status(camera, result.success, result.error)
        return {
            "success": result.success,
            "message": result.message,
            "stream_url": result.stream_url,
            "device_info": result.device_info,
            "error": result.error,
        }


@app.get("/internal/cameras/{camera_id}/face-preview/feed", dependencies=[Depends(_verify_worker_token)])
async def face_preview_feed(
    camera_id: str,
    raw: bool = Query(default=True),
):
    return await live_camera_feed(camera_id=camera_id, raw=raw)


@app.get("/internal/cameras/{camera_id}/preview/feed", dependencies=[Depends(_verify_worker_token)])
async def preview_feed(
    camera_id: str,
    raw: bool = Query(default=True),
):
    return await live_camera_feed(camera_id=camera_id, raw=raw)


@app.get(
    "/internal/cameras/{camera_id}/face-match",
    dependencies=[Depends(_verify_worker_token)],
    response_model=CameraFaceMatchResponse,
)
async def face_match(camera_id: str):
    async with async_session() as session:
        camera = await _get_camera_from_session(camera_id, session)

        frame = None
        source = None
        try:
            source = _open_manual_frame_source(camera)
            for _ in range(10):
                frame = source.read()
                if frame is not None:
                    break
                await asyncio.sleep(0.1)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"无法打开摄像头画面: {exc}") from exc
        finally:
            if source is not None:
                source.close()

        if frame is None:
            raise HTTPException(status_code=503, detail="暂未获取到摄像头画面，请稍后重试")

        recognizer = FaceRecognizer()
        detections = recognizer.detect_faces(frame)
        if not detections:
            return CameraFaceMatchResponse(
                camera_id=camera_id,
                matched=False,
                best_match=None,
                candidates=[],
                face_detected=False,
            )

        best_face = max(detections, key=lambda item: float(item.get("score", 0.0)))
        embedding = best_face.get("embedding")
        if embedding is None:
            return CameraFaceMatchResponse(
                camera_id=camera_id,
                matched=False,
                best_match=None,
                candidates=[],
                face_detected=False,
            )

        candidates: list[CameraFaceMatchCandidate] = []

        employee_rows = list(
            (
                await session.execute(
                    select(Person).where(
                        Person.face_embedding.isnot(None),
                        Person.is_employee == True,
                    )
                )
            ).scalars().all()
        )
        for person in employee_rows:
            stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
            similarity = recognizer.compare_embeddings(embedding, stored_embedding)
            candidates.append(
                CameraFaceMatchCandidate(
                    subject_id=person.id,
                    subject_type="employee",
                    name=person.name or person.id,
                    organization=person.workshop,
                    similarity=round(_similarity_to_score(similarity), 1),
                    cosine_similarity=round(similarity, 4),
                    face_image_url=_candidate_image_url(
                        getattr(person, "face_image_storage", None),
                        getattr(person, "face_image_bucket", None),
                        getattr(person, "face_image_object_key", None),
                    ),
                )
            )

        external_rows = list(
            (
                await session.execute(
                    select(ExternalPerson).where(ExternalPerson.face_embedding.isnot(None))
                )
            ).scalars().all()
        )
        for person in external_rows:
            stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
            similarity = recognizer.compare_embeddings(embedding, stored_embedding)
            candidates.append(
                CameraFaceMatchCandidate(
                    subject_id=person.id,
                    subject_type="external_person",
                    name=person.name,
                    organization=person.organization,
                    similarity=round(_similarity_to_score(similarity), 1),
                    cosine_similarity=round(similarity, 4),
                    face_image_url=_candidate_image_url(
                        getattr(person, "face_image_storage", None),
                        getattr(person, "face_image_bucket", None),
                        getattr(person, "face_image_object_key", None),
                    ),
                )
            )

        registration_rows = list(
            (
                await session.execute(
                    select(ExternalPersonnelRegistration).where(
                        ExternalPersonnelRegistration.face_embedding.isnot(None)
                    )
                )
            ).scalars().all()
        )
        for person in registration_rows:
            stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
            similarity = recognizer.compare_embeddings(embedding, stored_embedding)
            candidates.append(
                CameraFaceMatchCandidate(
                    subject_id=person.id,
                    subject_type="external_registration",
                    name=person.name,
                    organization=person.organization,
                    similarity=round(_similarity_to_score(similarity), 1),
                    cosine_similarity=round(similarity, 4),
                    face_image_url=_candidate_image_url(
                        getattr(person, "face_image_storage", None),
                        getattr(person, "face_image_bucket", None),
                        getattr(person, "face_image_object_key", None),
                    ),
                )
            )

        candidates.sort(key=lambda item: item.similarity, reverse=True)
        best_match = _select_face_match_candidate(candidates)
        return CameraFaceMatchResponse(
            camera_id=camera_id,
            matched=best_match is not None,
            best_match=best_match,
            candidates=candidates[:5],
            face_detected=True,
        )


@app.post("/internal/persons/{person_id}/face", dependencies=[Depends(_verify_worker_token)])
async def upload_person_face(person_id: str, file: UploadFile = File(...)):
    async with async_session() as session:
        person = await session.get(Person, person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="人员不存在")

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")

        try:
            result = get_face_registry_service().register_face_image(
                content,
                subject_type="employee",
                subject_id=person.id,
                filename=file.filename or f"{person.id}.jpg",
                content_type=file.content_type,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        person.thumbnail = result.thumbnail_bytes
        person.face_embedding = result.embedding_bytes
        person.face_image_storage = "minio"
        person.face_image_bucket = result.stored_object.bucket
        person.face_image_object_key = result.stored_object.object_key
        person.face_image_content_type = result.stored_object.content_type
        person.face_image_size_bytes = result.stored_object.size_bytes
        await session.commit()
        await session.refresh(person)
        counts = await _collect_violation_counts(session, [person.id])
        row = _build_person_row(person, counts)
        return {
            **row.model_dump(),
            "total_events": person.total_events or 0,
            "violation_count": person.violation_count or 0,
            "compliance_rate": person.compliance_rate,
        }


@app.post("/internal/persons/external/{person_id}/face", dependencies=[Depends(_verify_worker_token)])
async def upload_external_person_face(person_id: str, file: UploadFile = File(...)):
    async with async_session() as session:
        person = await session.get(ExternalPerson, person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="外来人员不存在")

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")

        try:
            result = get_face_registry_service().register_face_image(
                content,
                subject_type="external_person",
                subject_id=person.id,
                filename=file.filename or f"{person.id}.jpg",
                content_type=file.content_type,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        person.thumbnail = result.thumbnail_bytes
        person.face_embedding = result.embedding_bytes
        person.face_image_storage = "minio"
        person.face_image_bucket = result.stored_object.bucket
        person.face_image_object_key = result.stored_object.object_key
        person.face_image_content_type = result.stored_object.content_type
        person.face_image_size_bytes = result.stored_object.size_bytes
        await session.commit()
        await session.refresh(person)
        return _build_external_person_row(person).model_dump()


@app.post("/internal/supervision/external/{registration_id}/face", dependencies=[Depends(_verify_worker_token)])
async def upload_external_registration_face(registration_id: str, file: UploadFile = File(...)):
    async with async_session() as session:
        registration = await session.get(ExternalPersonnelRegistration, registration_id)
        if registration is None:
            raise HTTPException(status_code=404, detail="外来人员登记不存在")

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")

        try:
            result = get_face_registry_service().register_face_image(
                content,
                subject_type="external_registration",
                subject_id=registration.id,
                filename=file.filename or f"{registration.id}.jpg",
                content_type=file.content_type,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        registration.face_image = content
        registration.face_embedding = result.embedding_bytes
        registration.face_image_storage = "minio"
        registration.face_image_bucket = result.stored_object.bucket
        registration.face_image_object_key = result.stored_object.object_key
        registration.face_image_content_type = result.stored_object.content_type
        registration.face_image_size_bytes = result.stored_object.size_bytes
        await session.commit()
        await session.refresh(registration)
        return _build_external_response(registration).model_dump()


@app.post("/internal/supervision/face-match", dependencies=[Depends(_verify_worker_token)])
async def compare_face_against_registry(file: UploadFile = File(...)):
    async with async_session() as session:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")

        try:
            recognizer = get_face_registry_service().recognizer
            embedding, _ = recognizer.extract_embedding_from_image_bytes(content)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        candidates: list[SupervisionFaceMatchCandidate] = []

        employee_rows = list(
            (
                await session.execute(
                    select(Person).where(Person.face_embedding.isnot(None), Person.is_employee == True)
                )
            ).scalars().all()
        )
        for person in employee_rows:
            stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
            similarity = recognizer.compare_embeddings(embedding, stored_embedding)
            candidates.append(
                SupervisionFaceMatchCandidate(
                    subject_id=person.id,
                    subject_type="employee",
                    name=person.name or person.id,
                    organization=person.workshop,
                    similarity=round(_similarity_to_score(similarity), 1),
                    cosine_similarity=round(similarity, 4),
                    face_image_url=_candidate_image_url(
                        getattr(person, "face_image_storage", None),
                        getattr(person, "face_image_bucket", None),
                        getattr(person, "face_image_object_key", None),
                    ),
                )
            )

        external_person_rows = list(
            (await session.execute(select(ExternalPerson).where(ExternalPerson.face_embedding.isnot(None)))).scalars().all()
        )
        for person in external_person_rows:
            stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
            similarity = recognizer.compare_embeddings(embedding, stored_embedding)
            candidates.append(
                SupervisionFaceMatchCandidate(
                    subject_id=person.id,
                    subject_type="external_person",
                    name=person.name,
                    organization=person.organization,
                    similarity=round(_similarity_to_score(similarity), 1),
                    cosine_similarity=round(similarity, 4),
                    face_image_url=_candidate_image_url(
                        getattr(person, "face_image_storage", None),
                        getattr(person, "face_image_bucket", None),
                        getattr(person, "face_image_object_key", None),
                    ),
                )
            )

        registration_rows = list(
            (
                await session.execute(
                    select(ExternalPersonnelRegistration).where(
                        ExternalPersonnelRegistration.face_embedding.isnot(None)
                    )
                )
            ).scalars().all()
        )
        for person in registration_rows:
            stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
            similarity = recognizer.compare_embeddings(embedding, stored_embedding)
            candidates.append(
                SupervisionFaceMatchCandidate(
                    subject_id=person.id,
                    subject_type="external_registration",
                    name=person.name,
                    organization=person.organization,
                    similarity=round(_similarity_to_score(similarity), 1),
                    cosine_similarity=round(similarity, 4),
                    face_image_url=_candidate_image_url(
                        getattr(person, "face_image_storage", None),
                        getattr(person, "face_image_bucket", None),
                        getattr(person, "face_image_object_key", None),
                    ),
                )
            )

        candidates.sort(key=lambda item: item.similarity, reverse=True)
        best_match = _select_face_match_candidate(candidates)
        return FaceMatchResponse(
            matched=best_match is not None,
            best_match=best_match,
            candidates=candidates[:5],
        ).model_dump()
