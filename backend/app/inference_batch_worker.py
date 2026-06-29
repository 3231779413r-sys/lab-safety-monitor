from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from threading import Lock
from typing import Any, Optional

import aio_pika

from .core.config import settings
from .core.logging_setup import configure_logging
from .core.runtime_tuning import configure_runtime_tuning
from .inference_payloads import (
    build_result_payload,
    decode_frame_from_jpeg_bytes,
    parse_frame_task_message,
)
from .services.shared_frame_store import get_shared_frame_store
from .telemetry import clone_telemetry, mark_telemetry, merge_telemetry
from .ml.batch_inference import get_batch_inference_engine

logger = logging.getLogger(__name__)


@dataclass
class QueuedFrameMessage:
    message: aio_pika.abc.AbstractIncomingMessage
    payload: dict[str, Any]


@dataclass
class CollectedInferenceBatch:
    batch_id: str
    items: list[QueuedFrameMessage]
    collect_started_at: datetime
    collected_at: datetime
    collect_ms: float
    received_count: int
    priority_reason: str
    bucket: str
    hot_camera_count: int
    stale_frame_count: int
    complex_frame_count: int


class InferenceBatchWorker:
    def __init__(self) -> None:
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractRobustChannel] = None
        self._frame_queue = None
        self._result_exchange = None
        self._incoming: asyncio.Queue[QueuedFrameMessage] = asyncio.Queue()
        self._ready_batches: asyncio.Queue[CollectedInferenceBatch] = asyncio.Queue(
            maxsize=max(1, int(getattr(settings, "INFERENCE_BATCH_READY_QUEUE_SIZE", 1)))
        )
        self._staged_messages: deque[QueuedFrameMessage] = deque()
        self._engine = get_batch_inference_engine()
        self._frame_store = get_shared_frame_store()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(settings, "INFERENCE_WORKER_EXECUTOR_WORKERS", 1))),
            thread_name_prefix="inference-worker",
        )
        self._stats_lock = Lock()
        self._batch_stats_window_started_at = time.monotonic()
        self._batch_stats = {
            "batches": 0,
            "received": 0,
            "valid": 0,
            "invalid": 0,
            "complex_frames": 0,
            "multi_camera_batches": 0,
            "hot_batches": 0,
            "stale_batches": 0,
            "fast_dispatch_batches": 0,
            "single_camera_batches": 0,
        }
        self._batch_sequence = 0
        self._recent_batch_compute_ms: deque[float] = deque(maxlen=10)
        self._recent_batch_wait_ms: deque[float] = deque(maxlen=50)
        self._recent_stale_frame_count: deque[int] = deque(maxlen=50)
        self._recent_hot_camera_count: deque[int] = deque(maxlen=50)
        self._bucket_counts: dict[str, int] = {"default": 0}

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        configure_logging("inference")
        configure_runtime_tuning("inference")
        settings.WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
        settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)

        self._engine.initialize()
        self._connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(
            prefetch_count=max(
                1,
                int(
                    getattr(
                        settings,
                        "INFERENCE_QUEUE_PREFETCH",
                        max(8, settings.INFERENCE_BATCH_SIZE * 2),
                    )
                ),
            )
        )
        self._frame_queue = await self._channel.declare_queue(
            settings.RABBITMQ_FRAME_QUEUE,
            durable=True,
        )
        await self._channel.declare_queue(settings.RABBITMQ_RESULT_QUEUE, durable=True)
        self._result_exchange = self._channel.default_exchange
        await self._frame_queue.consume(self._on_frame_message)
        logger.info(
            "Inference batch worker started batch_size=%s timeout_ms=%s shared_frame_dir=%s",
            settings.INFERENCE_BATCH_SIZE,
            settings.INFERENCE_BATCH_TIMEOUT_MS,
            settings.SHARED_FRAME_DIR,
        )

        batcher_task = asyncio.create_task(self._batcher_loop())
        processor_task = asyncio.create_task(self._processor_loop())
        try:
            await asyncio.gather(batcher_task, processor_task)
        finally:
            batcher_task.cancel()
            processor_task.cancel()
            await self.stop()

    async def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._channel is not None:
            await self._channel.close()
        if self._connection is not None:
            await self._connection.close()
        self._channel = None
        self._connection = None
        self._loop = None

    async def _batcher_loop(self) -> None:
        while True:
            batch = await self._collect_batch()
            await self._ready_batches.put(batch)

    async def _processor_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            batch = await self._ready_batches.get()
            try:
                await loop.run_in_executor(self._executor, self._process_batch_sync, batch)
            finally:
                self._ready_batches.task_done()

    async def _on_frame_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            payload = parse_frame_task_message(json.loads(message.body.decode("utf-8")))
            payload["telemetry"] = mark_telemetry(
                payload.get("telemetry"),
                "inference_worker_received_at",
            )
        except Exception:
            logger.exception("Failed to decode inference frame message")
            await message.ack()
            return
        await self._incoming.put(QueuedFrameMessage(message=message, payload=payload))

    async def _collect_batch(self) -> CollectedInferenceBatch:
        first = await self._next_incoming_item()
        collect_started_at = datetime.now()
        collect_started_monotonic = time.perf_counter()
        first_meta = self._message_meta(first)
        batch = [first]
        target_batch_size = self._select_target_batch_size(
            incoming_backlog=self._incoming.qsize(),
            first_meta=first_meta,
        )
        max_frames_per_camera = max(
            1,
            int(getattr(settings, "INFERENCE_MAX_FRAMES_PER_CAMERA_PER_BATCH", 2)),
        )
        max_complex_frames = max(
            1,
            int(getattr(settings, "INFERENCE_BATCH_MAX_COMPLEX_FRAMES", 1)),
        )
        camera_counts: dict[str, int] = {
            str(first.payload.get("camera_id") or ""): 1,
        }
        hot_camera_count = 1 if first_meta["is_hot_camera"] else 0
        stale_frame_count = 1 if first_meta["is_stale_frame"] else 0
        complex_frame_count = 1 if first_meta["is_complex_frame"] else 0
        deferred_items: list[QueuedFrameMessage] = []
        priority_reason = first_meta["priority_reason"]
        bucket = first_meta["bucket"]
        deadline = asyncio.get_running_loop().time() + (
            self._select_batch_timeout_ms(first_meta, incoming_backlog=self._incoming.qsize()) / 1000.0
        )

        while len(batch) < target_batch_size:
            if self._should_dispatch_now(
                batch=batch,
                first_meta=first_meta,
                hot_camera_count=hot_camera_count,
                stale_frame_count=stale_frame_count,
                complex_frame_count=complex_frame_count,
                deadline=deadline,
            ):
                break
            try:
                item = await self._next_incoming_item(deadline=deadline)
            except asyncio.TimeoutError:
                break
            item_meta = self._message_meta(item)
            camera_id = str(item.payload.get("camera_id") or "")
            if camera_id and camera_counts.get(camera_id, 0) >= max_frames_per_camera:
                deferred_items.append(item)
                pending_candidates = len(self._staged_messages) + self._incoming.qsize()
                if len(deferred_items) >= max(1, pending_candidates):
                    break
                continue
            batch.append(item)
            camera_counts[camera_id] = camera_counts.get(camera_id, 0) + 1
            hot_camera_count += 1 if item_meta["is_hot_camera"] else 0
            stale_frame_count += 1 if item_meta["is_stale_frame"] else 0
            complex_frame_count += 1 if item_meta["is_complex_frame"] else 0
        if deferred_items:
            for item in reversed(deferred_items):
                self._staged_messages.appendleft(item)
        collected_at = datetime.now()
        batch_id = f"{int(collected_at.timestamp() * 1000)}-{self._next_batch_sequence()}"
        return CollectedInferenceBatch(
            batch_id=batch_id,
            items=batch,
            collect_started_at=collect_started_at,
            collected_at=collected_at,
            collect_ms=round((time.perf_counter() - collect_started_monotonic) * 1000.0, 1),
            received_count=len(batch),
            priority_reason=priority_reason,
            bucket=bucket,
            hot_camera_count=hot_camera_count,
            stale_frame_count=stale_frame_count,
            complex_frame_count=complex_frame_count,
        )

    async def _next_incoming_item(
        self,
        *,
        deadline: float | None = None,
    ) -> QueuedFrameMessage:
        if self._staged_messages:
            return self._staged_messages.popleft()
        if deadline is None:
            return await self._incoming.get()
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(self._incoming.get(), timeout=remaining)

    def _process_batch_sync(self, batch: CollectedInferenceBatch) -> None:
        valid_messages: list[QueuedFrameMessage] = []
        frames = []
        batch_started_at = datetime.now()
        invalid_count = 0
        base_batch_telemetry = {
            "inference_batch_id": batch.batch_id,
            "inference_batch_collect_started_at": batch.collect_started_at.isoformat(timespec="milliseconds"),
            "inference_batch_collected_at": batch.collected_at.isoformat(timespec="milliseconds"),
            "inference_batch_collect_ms": batch.collect_ms,
            "inference_batch_received_count": batch.received_count,
            "inference_batch_bucket": batch.bucket,
            "inference_batch_priority_reason": batch.priority_reason,
            "inference_batch_hot_camera_count": batch.hot_camera_count,
            "inference_batch_stale_frame_count": batch.stale_frame_count,
            "inference_batch_complex_frame_count": batch.complex_frame_count,
        }

        for item in batch.items:
            frame_path = item.payload.get("frame_path")
            frame = None
            error_type: str | None = None
            error_detail: str | None = None
            frame_jpeg = item.payload.get("frame_jpeg")
            if frame_jpeg:
                frame = decode_frame_from_jpeg_bytes(frame_jpeg)
                if frame is None:
                    error_type = "decode_failed"
                    error_detail = "inline_frame_decode_failed"
            if frame is None and frame_path:
                read_result = self._frame_store.read_frame_result(frame_path)
                frame = read_result.frame
                if frame is None:
                    error_type = read_result.error_type or error_type or "missing_shared_frame"
                    error_detail = read_result.error_detail or error_detail
            if frame is None:
                invalid_count += 1
                logger.warning(
                    "Dropping invalid frame payload camera=%s request_id=%s error_type=%s frame_path=%s",
                    item.payload.get("camera_id"),
                    item.payload.get("request_id"),
                    error_type or "missing_shared_frame",
                    frame_path,
                )
                self._publish_result_sync(
                    item.payload,
                    detections={},
                    error=error_type or "missing_shared_frame",
                    error_type=error_type or "missing_shared_frame",
                    error_detail=error_detail,
                    frame_path=frame_path,
                    telemetry=merge_telemetry(
                        clone_telemetry(item.payload.get("telemetry")),
                        base_batch_telemetry,
                        {
                            "inference_batch_started_at": batch_started_at.isoformat(timespec="milliseconds"),
                            "inference_batch_finished_at": batch_started_at.isoformat(timespec="milliseconds"),
                            "inference_batch_size": 0,
                            "inference_batch_valid_count": 0,
                            "inference_batch_invalid_count": 1,
                        },
                    ),
                )
                self._ack_message_sync(item.message)
                continue
            valid_messages.append(item)
            frames.append(frame)

        if not valid_messages:
            self._record_batch_stats(
                received=batch.received_count,
                valid=0,
                invalid=invalid_count,
                collect_ms=batch.collect_ms,
            )
            return

        detections_batch: list[dict[str, Any]]
        inference_error: Optional[str] = None
        frame_options = [dict(item.payload.get("inference_options") or {}) for item in valid_messages]
        try:
            detections_batch = self._engine.infer_batch(frames, frame_options=frame_options)
        except Exception as exc:
            logger.exception("Batched inference failed for %s frames", len(valid_messages))
            detections_batch = [{} for _ in valid_messages]
            inference_error = str(exc)
        batch_finished_at = datetime.now()
        batch_person_total = sum(
            int((detections or {}).get("person_total_count", len((detections or {}).get("persons") or [])))
            for detections in detections_batch
        )
        batch_max_persons = max(
            (
                int((detections or {}).get("person_total_count", len((detections or {}).get("persons") or [])))
                for detections in detections_batch
            ),
            default=0,
        )
        batch_avg_persons = round(batch_person_total / max(1, len(detections_batch)), 2)
        batch_camera_count = len(
            {
                str(item.payload.get("camera_id") or "")
                for item in valid_messages
                if str(item.payload.get("camera_id") or "")
            }
        )
        complex_threshold = max(
            1,
            int(getattr(settings, "SKIP_POSE_WHEN_PERSON_COUNT_GE", 3)),
        )
        batch_complex_frames = sum(
            1
            for detections in detections_batch
            if int((detections or {}).get("person_total_count", len((detections or {}).get("persons") or [])))
            >= complex_threshold
        )
        batch_compute_ms = round((batch_finished_at - batch_started_at).total_seconds() * 1000.0, 1)
        batch_wait_ms_values = [
            self._duration_ms(
                (item.payload.get("telemetry") or {}).get("inference_worker_received_at"),
                batch_started_at.isoformat(timespec="milliseconds"),
            )
            for item in valid_messages
        ]
        batch_wait_ms_values = [
            float(value)
            for value in batch_wait_ms_values
            if isinstance(value, (int, float))
        ]
        with self._stats_lock:
            self._recent_batch_compute_ms.append(batch_compute_ms)
            if batch_wait_ms_values:
                self._recent_batch_wait_ms.append(max(batch_wait_ms_values))
            self._recent_stale_frame_count.append(batch.stale_frame_count)
            self._recent_hot_camera_count.append(batch.hot_camera_count)
        self._record_batch_stats(
            received=batch.received_count,
            valid=len(valid_messages),
            invalid=invalid_count,
            collect_ms=batch.collect_ms,
            camera_count=batch_camera_count,
            complex_frames=batch_complex_frames,
            bucket=batch.bucket,
            priority_reason=batch.priority_reason,
            hot_camera_count=batch.hot_camera_count,
            stale_frame_count=batch.stale_frame_count,
        )

        for item, detections in zip(valid_messages, detections_batch):
            telemetry = merge_telemetry(
                clone_telemetry(item.payload.get("telemetry")),
                base_batch_telemetry,
                {
                    "inference_batch_started_at": batch_started_at.isoformat(timespec="milliseconds"),
                    "inference_batch_finished_at": batch_finished_at.isoformat(timespec="milliseconds"),
                    "inference_batch_size": len(valid_messages),
                    "inference_batch_valid_count": len(valid_messages),
                    "inference_batch_invalid_count": invalid_count,
                    "inference_batch_person_total": batch_person_total,
                    "inference_batch_camera_count": batch_camera_count,
                    "inference_batch_complex_frame_count": batch_complex_frames,
                    "inference_batch_bucket": batch.bucket,
                    "inference_batch_priority_reason": batch.priority_reason,
                    "inference_batch_hot_camera_count": batch.hot_camera_count,
                    "inference_batch_stale_frame_count": batch.stale_frame_count,
                    "inference_batch_max_persons_per_frame": batch_max_persons,
                    "inference_batch_avg_persons_per_frame": batch_avg_persons,
                },
            )
            engine_profile = dict((detections or {}).pop("_engine_profile", {}) or {})
            if engine_profile:
                telemetry = merge_telemetry(telemetry, engine_profile)
            self._publish_result_sync(
                item.payload,
                detections=detections,
                error=inference_error,
                error_type="inference_failed" if inference_error else None,
                telemetry=telemetry,
                frame_path=str(item.payload.get("frame_path") or ""),
            )
            self._ack_message_sync(item.message)

    def _record_batch_stats(
        self,
        *,
        received: int,
        valid: int,
        invalid: int,
        collect_ms: float,
        camera_count: int = 0,
        complex_frames: int = 0,
        bucket: str = "default",
        priority_reason: str = "default",
        hot_camera_count: int = 0,
        stale_frame_count: int = 0,
    ) -> None:
        with self._stats_lock:
            self._batch_stats["batches"] += 1
            self._batch_stats["received"] += received
            self._batch_stats["valid"] += valid
            self._batch_stats["invalid"] += invalid
            self._batch_stats["complex_frames"] += complex_frames
            if camera_count > 1:
                self._batch_stats["multi_camera_batches"] += 1
            else:
                self._batch_stats["single_camera_batches"] += 1
            if hot_camera_count > 0:
                self._batch_stats["hot_batches"] += 1
            if stale_frame_count > 0:
                self._batch_stats["stale_batches"] += 1
            if "fast_dispatch" in priority_reason or priority_reason.startswith("force_dispatch"):
                self._batch_stats["fast_dispatch_batches"] += 1
            self._bucket_counts[bucket] = self._bucket_counts.get(bucket, 0) + 1
            elapsed = time.monotonic() - self._batch_stats_window_started_at
            if elapsed < 5.0:
                return
            summary = dict(self._batch_stats)
            bucket_counts = dict(self._bucket_counts)
            self._batch_stats_window_started_at = time.monotonic()
            self._batch_stats = {
                "batches": 0,
                "received": 0,
                "valid": 0,
                "invalid": 0,
                "complex_frames": 0,
                "multi_camera_batches": 0,
                "hot_batches": 0,
                "stale_batches": 0,
                "fast_dispatch_batches": 0,
                "single_camera_batches": 0,
            }
            self._bucket_counts = {"default": 0}
        logger.info(
            "Inference batch summary batches=%s received=%s valid=%s invalid=%s complex_frames=%s multi_camera_batches=%s single_camera_batches=%s hot_batches=%s stale_batches=%s fast_dispatch_batches=%s bucket_counts=%s last_collect_ms=%.1f incoming_queue=%s staged_queue=%s ready_queue=%s avg_compute_ms=%.1f avg_wait_ms=%.1f p95_wait_ms=%.1f avg_hot_cameras=%.2f avg_stale_frames=%.2f dynamic_target_batch=%s last_bucket=%s last_priority=%s",
            summary["batches"],
            summary["received"],
            summary["valid"],
            summary["invalid"],
            summary["complex_frames"],
            summary["multi_camera_batches"],
            summary["single_camera_batches"],
            summary["hot_batches"],
            summary["stale_batches"],
            summary["fast_dispatch_batches"],
            bucket_counts,
            collect_ms,
            self._incoming.qsize(),
            len(self._staged_messages),
            self._ready_batches.qsize(),
            round(mean(self._recent_batch_compute_ms), 1) if self._recent_batch_compute_ms else 0.0,
            round(mean(self._recent_batch_wait_ms), 1) if self._recent_batch_wait_ms else 0.0,
            self._percentile(self._recent_batch_wait_ms, 0.95),
            round(mean(self._recent_hot_camera_count), 2) if self._recent_hot_camera_count else 0.0,
            round(mean(self._recent_stale_frame_count), 2) if self._recent_stale_frame_count else 0.0,
            self._select_target_batch_size(incoming_backlog=self._incoming.qsize()),
            bucket,
            priority_reason,
        )

    def _next_batch_sequence(self) -> int:
        with self._stats_lock:
            self._batch_sequence += 1
            return self._batch_sequence

    def _select_target_batch_size(
        self,
        incoming_backlog: int,
        first_meta: dict[str, Any] | None = None,
    ) -> int:
        min_batch = max(1, int(getattr(settings, "INFERENCE_MIN_BATCH_SIZE", 2)))
        max_batch = max(min_batch, int(getattr(settings, "INFERENCE_MAX_BATCH_SIZE", settings.INFERENCE_BATCH_SIZE)))
        default_batch = max(min_batch, min(max_batch, int(settings.INFERENCE_BATCH_SIZE)))
        if not bool(getattr(settings, "INFERENCE_DYNAMIC_BATCH_ENABLED", True)):
            return default_batch
        backlog_threshold = max(1, int(getattr(settings, "INFERENCE_DYNAMIC_BATCH_BACKLOG_THRESHOLD", 8)))
        max_latency_ms = max(1, int(getattr(settings, "INFERENCE_DYNAMIC_BATCH_MAX_LATENCY_MS", 220)))
        recent_compute_avg = (
            sum(self._recent_batch_compute_ms) / len(self._recent_batch_compute_ms)
            if self._recent_batch_compute_ms
            else 0.0
        )
        target = min_batch
        if incoming_backlog >= backlog_threshold:
            backlog_factor = min(
                max_batch - min_batch,
                max(1, incoming_backlog // backlog_threshold),
            )
            target = min(max_batch, min_batch + backlog_factor)
        elif incoming_backlog >= min_batch:
            target = min(default_batch, min_batch + 1)
        if recent_compute_avg >= float(max_latency_ms):
            target = max(min_batch, target - 1)
        return max(min_batch, min(max_batch, target))

    def _select_batch_timeout_ms(self, first_meta: dict[str, Any], *, incoming_backlog: int) -> int:
        default_timeout = max(1, int(getattr(settings, "INFERENCE_BATCH_TIMEOUT_MS", 40)))
        if incoming_backlog <= 1:
            return min(default_timeout, max(1, default_timeout // 2))
        return default_timeout

    def _should_dispatch_now(
        self,
        *,
        batch: list[QueuedFrameMessage],
        first_meta: dict[str, Any],
        hot_camera_count: int,
        stale_frame_count: int,
        complex_frame_count: int,
        deadline: float,
    ) -> bool:
        return asyncio.get_running_loop().time() >= deadline

    def _message_meta(self, item: QueuedFrameMessage) -> dict[str, Any]:
        payload = item.payload or {}
        telemetry = dict(payload.get("telemetry") or {})
        frame_age_ms_at_worker_receive = self._duration_ms(
            telemetry.get("capture_sampled_at"),
            telemetry.get("inference_worker_received_at"),
        )
        latest_frame_age_ms = self._safe_float(payload.get("inference_options", {}).get("latest_frame_age_ms"))
        batch_wait_hint_ms = self._safe_float(payload.get("inference_options", {}).get("recent_batch_wait_ms"))
        hotness_score = self._safe_float(payload.get("inference_options", {}).get("camera_hotness_score")) or 0.0
        camera_profile = str(payload.get("inference_options", {}).get("camera_profile") or "")
        degrade_level = int(payload.get("inference_options", {}).get("degrade_level") or 0)
        max_persons = int(payload.get("inference_options", {}).get("max_persons_for_frame") or 0)
        pose_enabled = bool(payload.get("inference_options", {}).get("pose_enabled"))
        skip_segmentation = bool(payload.get("inference_options", {}).get("skip_segmentation"))
        force_fast_core_ppe_only = bool(payload.get("inference_options", {}).get("force_fast_core_ppe_only"))
        complex_threshold = max(
            1,
            int(getattr(settings, "INFERENCE_BATCH_COMPLEX_FRAME_PERSON_THRESHOLD", 2)),
        )
        stale_threshold_ms = float(getattr(settings, "INFERENCE_BATCH_STALE_FRAME_MS", 700.0))
        is_stale_frame = (
            frame_age_ms_at_worker_receive is not None and frame_age_ms_at_worker_receive >= stale_threshold_ms
        )
        is_complex_frame = (
            max_persons >= complex_threshold
            and (pose_enabled or not skip_segmentation or not force_fast_core_ppe_only)
        )
        bucket = "default"
        priority_reason = "default"
        return {
            "camera_id": str(payload.get("camera_id") or ""),
            "frame_age_ms_at_worker_receive": frame_age_ms_at_worker_receive,
            "latest_frame_age_ms": latest_frame_age_ms,
            "recent_batch_wait_ms": batch_wait_hint_ms,
            "camera_hotness_score": hotness_score,
            "is_hot_camera": False,
            "is_stale_frame": is_stale_frame,
            "force_dispatch": False,
            "is_complex_frame": is_complex_frame,
            "bucket": bucket,
            "priority_reason": priority_reason,
            "camera_profile": camera_profile,
            "degrade_level": degrade_level,
        }

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _duration_ms(start_iso: Any, end_iso: Any) -> float | None:
        try:
            if not start_iso or not end_iso:
                return None
            start = datetime.fromisoformat(str(start_iso))
            end = datetime.fromisoformat(str(end_iso))
            return round((end - start).total_seconds() * 1000.0, 1)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _percentile(values: deque[float], ratio: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(float(value) for value in values)
        if len(ordered) == 1:
            return round(ordered[0], 1)
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio))))
        return round(ordered[index], 1)

    def _publish_result_sync(
        self,
        payload: dict[str, Any],
        *,
        detections: dict[str, Any],
        error: str | None = None,
        error_type: str | None = None,
        error_detail: str | None = None,
        frame_path: str | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        if self._loop is None:
            raise RuntimeError("Inference worker loop is not initialized")
        future = asyncio.run_coroutine_threadsafe(
            self._publish_result(
                payload,
                detections=detections,
                error=error,
                error_type=error_type,
                error_detail=error_detail,
                telemetry=telemetry,
                frame_path=frame_path,
            ),
            self._loop,
        )
        future.result()

    def _ack_message_sync(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        if self._loop is None:
            raise RuntimeError("Inference worker loop is not initialized")
        future = asyncio.run_coroutine_threadsafe(message.ack(), self._loop)
        future.result()

    async def _publish_result(
        self,
        payload: dict[str, Any],
        *,
        detections: dict[str, Any],
        error: str | None = None,
        error_type: str | None = None,
        error_detail: str | None = None,
        frame_path: str | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        if self._result_exchange is None:
            raise RuntimeError("RabbitMQ result exchange is not initialized")

        body = json.dumps(
            build_result_payload(
                request_id=str(payload.get("request_id") or ""),
                camera_id=str(payload.get("camera_id") or ""),
                video_source=str(payload.get("video_source") or ""),
                submitted_at=payload.get("submitted_at"),
                detections=detections,
                completed_at=datetime.now(),
                error=error,
                error_type=error_type,
                error_detail=error_detail,
                frame_path=frame_path,
                telemetry=mark_telemetry(telemetry, "inference_result_published_at"),
            )
        ).encode("utf-8")
        await self._result_exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
                timestamp=datetime.now(),
            ),
            routing_key=str(payload.get("result_queue") or settings.RABBITMQ_RESULT_QUEUE),
        )


async def _main() -> None:
    worker = InferenceBatchWorker()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(_main())
