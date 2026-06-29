from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any, Optional

import aio_pika

from .core.config import settings
from .core.logging_setup import configure_logging
from .core.runtime_tuning import configure_runtime_tuning
from .identity_payloads import parse_identity_task_message
from .inference_payloads import decode_frame_from_jpeg_bytes
from .services.shared_frame_store import get_shared_frame_store
from .telemetry import clone_telemetry, mark_telemetry, merge_telemetry
from .ml.identity_batch import get_identity_batch_engine

logger = logging.getLogger(__name__)


@dataclass
class QueuedIdentityMessage:
    message: aio_pika.abc.AbstractIncomingMessage
    payload: dict[str, Any]


@dataclass
class CollectedIdentityBatch:
    items: list[QueuedIdentityMessage]
    collect_started_at: datetime
    collected_at: datetime
    collect_ms: float
    received_count: int


class IdentityBatchWorker:
    def __init__(self) -> None:
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractRobustChannel] = None
        self._queue = None
        self._result_exchange = None
        self._incoming: asyncio.Queue[QueuedIdentityMessage] = asyncio.Queue()
        self._ready_batches: asyncio.Queue[CollectedIdentityBatch] = asyncio.Queue(maxsize=4)
        self._engine = get_identity_batch_engine()
        self._frame_store = get_shared_frame_store()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(settings, "IDENTITY_WORKER_EXECUTOR_WORKERS", 1))),
            thread_name_prefix="identity-worker",
        )
        self._stats_lock = Lock()
        self._batch_stats_window_started_at = time.monotonic()
        self._batch_stats = {
            "batches": 0,
            "received": 0,
            "valid": 0,
            "invalid": 0,
        }

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        configure_logging("identity")
        configure_runtime_tuning("identity")
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
                        "IDENTITY_QUEUE_PREFETCH",
                        max(4, int(getattr(settings, "IDENTITY_BATCH_SIZE", 4)) * 2),
                    )
                ),
            )
        )
        self._queue = await self._channel.declare_queue(settings.RABBITMQ_IDENTITY_QUEUE, durable=True)
        await self._channel.declare_queue(settings.RABBITMQ_IDENTITY_RESULT_QUEUE, durable=True)
        self._result_exchange = self._channel.default_exchange
        await self._queue.consume(self._on_message)
        logger.info(
            "Identity batch worker started batch_size=%s timeout_ms=%s shared_frame_dir=%s",
            settings.IDENTITY_BATCH_SIZE,
            settings.IDENTITY_BATCH_TIMEOUT_MS,
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

    async def _on_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            payload = parse_identity_task_message(json.loads(message.body.decode("utf-8")))
            payload["telemetry"] = mark_telemetry(
                payload.get("telemetry"),
                "identity_worker_received_at",
            )
        except Exception:
            logger.exception("Failed to decode identity task message")
            await message.ack()
            return
        await self._incoming.put(QueuedIdentityMessage(message=message, payload=payload))

    async def _collect_batch(self) -> CollectedIdentityBatch:
        first = await self._incoming.get()
        collect_started_at = datetime.now()
        collect_started_monotonic = time.perf_counter()
        batch = [first]
        target_batch_size = max(
            1,
            min(
                int(getattr(settings, "IDENTITY_MAX_BATCH_SIZE", settings.IDENTITY_BATCH_SIZE)),
                max(
                    int(settings.IDENTITY_BATCH_SIZE),
                    int(settings.IDENTITY_BATCH_SIZE) + self._incoming.qsize(),
                ),
            ),
        )
        deadline = asyncio.get_running_loop().time() + max(1, int(settings.IDENTITY_BATCH_TIMEOUT_MS)) / 1000.0
        while len(batch) < target_batch_size:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(self._incoming.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            batch.append(item)
        collected_at = datetime.now()
        return CollectedIdentityBatch(
            items=batch,
            collect_started_at=collect_started_at,
            collected_at=collected_at,
            collect_ms=round((time.perf_counter() - collect_started_monotonic) * 1000.0, 1),
            received_count=len(batch),
        )

    def _process_batch_sync(self, batch: CollectedIdentityBatch) -> None:
        valid_tasks: list[dict[str, Any]] = []
        valid_messages: list[aio_pika.abc.AbstractIncomingMessage] = []
        batch_started_at = datetime.now()
        invalid_count = 0
        base_batch_telemetry = {
            "identity_batch_collect_started_at": batch.collect_started_at.isoformat(timespec="milliseconds"),
            "identity_batch_collected_at": batch.collected_at.isoformat(timespec="milliseconds"),
            "identity_batch_collect_ms": batch.collect_ms,
            "identity_batch_received_count": batch.received_count,
        }
        for item in batch.items:
            frame_path = item.payload.get("frame_path")
            frame = None
            error_type: str | None = None
            error_detail: str | None = None
            persons = list(item.payload.get("persons") or [])
            has_inline_crops = any(person.get("crop_jpeg") for person in persons)
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
            if frame is None and not has_inline_crops:
                invalid_count += 1
                logger.warning(
                    "Identity task invalid frame camera=%s request_id=%s error_type=%s frame_path=%s",
                    item.payload.get("camera_id"),
                    item.payload.get("request_id"),
                    error_type or "missing_shared_frame",
                    frame_path,
                )
                self._publish_error_sync(
                    item.payload,
                    error=error_type or "missing_shared_frame",
                    error_type=error_type,
                    error_detail=error_detail,
                    telemetry=merge_telemetry(
                        clone_telemetry(item.payload.get("telemetry")),
                        base_batch_telemetry,
                        {
                            "identity_batch_started_at": batch_started_at.isoformat(timespec="milliseconds"),
                            "identity_batch_finished_at": batch_started_at.isoformat(timespec="milliseconds"),
                            "identity_batch_size": 0,
                            "identity_batch_valid_count": 0,
                            "identity_batch_invalid_count": 1,
                        },
                    ),
                )
                self._ack_message_sync(item.message)
                continue
            item.payload["frame"] = frame
            item.payload["identity_transport_mode"] = (
                "person_crops"
                if has_inline_crops and frame is None
                else "person_crops+frame_jpeg"
                if has_inline_crops and frame is not None
                else "frame_jpeg"
                if frame_jpeg
                else "shared_frame"
            )
            valid_tasks.append(item.payload)
            valid_messages.append(item.message)
        if not valid_tasks:
            self._record_batch_stats(
                received=batch.received_count,
                valid=0,
                invalid=invalid_count,
                collect_ms=batch.collect_ms,
            )
            return
        try:
            results = self._engine.enrich_batch(valid_tasks)
        except Exception as exc:
            logger.exception("Identity batch processing failed for %s task(s)", len(valid_tasks))
            results = []
            for task in valid_tasks:
                self._publish_error_sync(
                    task,
                    error=str(exc),
                    error_type="identity_failed",
                    error_detail=str(exc),
                    telemetry=merge_telemetry(
                        clone_telemetry(task.get("telemetry")),
                        base_batch_telemetry,
                        {
                            "identity_batch_started_at": batch_started_at.isoformat(timespec="milliseconds"),
                            "identity_batch_finished_at": datetime.now().isoformat(timespec="milliseconds"),
                            "identity_batch_size": len(valid_tasks),
                            "identity_batch_valid_count": len(valid_tasks),
                            "identity_batch_invalid_count": invalid_count,
                        },
                    ),
                )
        else:
            batch_finished_at = datetime.now()
            self._record_batch_stats(
                received=batch.received_count,
                valid=len(valid_tasks),
                invalid=invalid_count,
                collect_ms=batch.collect_ms,
            )
            for payload in results:
                payload["telemetry"] = merge_telemetry(
                    clone_telemetry(payload.get("telemetry")),
                    base_batch_telemetry,
                    {
                        "identity_batch_started_at": batch_started_at.isoformat(timespec="milliseconds"),
                        "identity_batch_finished_at": batch_finished_at.isoformat(timespec="milliseconds"),
                        "identity_batch_size": len(valid_tasks),
                        "identity_batch_valid_count": len(valid_tasks),
                        "identity_batch_invalid_count": invalid_count,
                    },
                )
                self._publish_json_sync(
                    str(payload.get("result_queue") or settings.RABBITMQ_IDENTITY_RESULT_QUEUE),
                    payload,
                )
        for message in valid_messages:
            self._ack_message_sync(message)

    def _record_batch_stats(
        self,
        *,
        received: int,
        valid: int,
        invalid: int,
        collect_ms: float,
    ) -> None:
        with self._stats_lock:
            self._batch_stats["batches"] += 1
            self._batch_stats["received"] += received
            self._batch_stats["valid"] += valid
            self._batch_stats["invalid"] += invalid
            elapsed = time.monotonic() - self._batch_stats_window_started_at
            if elapsed < 5.0:
                return
            summary = dict(self._batch_stats)
            self._batch_stats_window_started_at = time.monotonic()
            self._batch_stats = {
                "batches": 0,
                "received": 0,
                "valid": 0,
                "invalid": 0,
            }
        logger.info(
            "Identity batch summary batches=%s received=%s valid=%s invalid=%s last_collect_ms=%.1f incoming_queue=%s ready_queue=%s",
            summary["batches"],
            summary["received"],
            summary["valid"],
            summary["invalid"],
            collect_ms,
            self._incoming.qsize(),
            self._ready_batches.qsize(),
        )

    def _publish_error_sync(
        self,
        task: dict[str, Any],
        error: str,
        error_type: str | None = None,
        error_detail: str | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        if self._loop is None:
            raise RuntimeError("Identity worker loop is not initialized")
        future = asyncio.run_coroutine_threadsafe(
            self._publish_error(
                task,
                error,
                error_type=error_type,
                error_detail=error_detail,
                telemetry=telemetry,
            ),
            self._loop,
        )
        future.result()

    def _publish_json_sync(self, routing_key: str, payload: dict[str, Any]) -> None:
        if self._loop is None:
            raise RuntimeError("Identity worker loop is not initialized")
        future = asyncio.run_coroutine_threadsafe(
            self._publish_json(routing_key, payload),
            self._loop,
        )
        future.result()

    def _ack_message_sync(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        if self._loop is None:
            raise RuntimeError("Identity worker loop is not initialized")
        future = asyncio.run_coroutine_threadsafe(message.ack(), self._loop)
        future.result()

    async def _publish_error(
        self,
        task: dict[str, Any],
        error: str,
        *,
        error_type: str | None = None,
        error_detail: str | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "request_id": str(task.get("request_id") or ""),
            "camera_id": str(task.get("camera_id") or ""),
            "video_source": str(task.get("video_source") or ""),
            "frame_path": str(task.get("frame_path") or ""),
            "appearance_features_b64": [],
            "detected_faces": [],
            "submitted_at": task.get("submitted_at"),
            "completed_at": None,
            "error": error,
            "error_type": error_type,
            "error_detail": error_detail,
            "telemetry": merge_telemetry(clone_telemetry(task.get("telemetry")), telemetry),
        }
        await self._publish_json(
            str(task.get("result_queue") or settings.RABBITMQ_IDENTITY_RESULT_QUEUE),
            payload,
        )

    async def _publish_json(self, routing_key: str, payload: dict[str, Any]) -> None:
        if self._result_exchange is None:
            raise RuntimeError("RabbitMQ result exchange is not initialized")
        payload["telemetry"] = mark_telemetry(payload.get("telemetry"), "identity_result_published_at")
        body = json.dumps(payload).encode("utf-8")
        await self._result_exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
            ),
            routing_key=routing_key,
        )


async def _main() -> None:
    worker = IdentityBatchWorker()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(_main())
