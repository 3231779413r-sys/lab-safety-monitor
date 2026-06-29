"""
实时视频流的异步处理器

将帧捕获与ML处理解耦，实现高FPS平滑流传输
同时以较低速率进行处理。
"""

import asyncio
import cv2
import numpy as np
import logging
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from queue import Queue, Empty
from threading import Thread, Lock

from ..core.database import async_session
from ..services.persistence import PersistenceManager


# Preload CJK font once at module level (expensive to do per-frame)
_cjk_font_path = None
for _fp in [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]:
    try:
        ImageFont.truetype(_fp, 24)
        _cjk_font_path = _fp
        break
    except (IOError, OSError):
        continue

logger = logging.getLogger(__name__)


class StreamProcessor:
    """
    实时视频流的异步处理器。
    
    通过使用处理队列和结果缓存，将帧捕获（高FPS）与ML处理（低FPS）分离。
    """
    
    def __init__(
        self,
        pipeline,
        display_fps: int = 30,
        process_fps: int = 3,
        queue_size: int = 2,
        interpolate: bool = False
    ):
        """
        参数：
            pipeline: ML检测管道实例
            display_fps: 目标显示帧率
            process_fps: ML处理帧率（较低=较少延迟）
            queue_size: 处理队列中的最大帧数
            interpolate: 是否对边界框进行插值
        """
        self.pipeline = pipeline
        self.display_fps = display_fps
        self.process_fps = process_fps
        self.queue_size = queue_size
        self.interpolate = interpolate
        
        # 处理队列和结果缓存
        self.frame_queue = Queue(maxsize=queue_size)
        self.result_cache: Optional[Dict[str, Any]] = None
        self.result_lock = Lock()
        self._pending_persist = 0  # 限制并发持久化任务数量
        self._max_pending_persist = 4

        # 工作线程
        self.worker_thread: Optional[Thread] = None
        self.running = False
        
        logger.info(
            f"StreamProcessor初始化: "
            f"display={display_fps}fps, process={process_fps}fps, "
            f"queue_size={queue_size}, interpolate={interpolate}"
        )
    
    def start(self, loop: asyncio.AbstractEventLoop = None):
        """启动处理工作线程"""
        if self.running:
            return

        self._main_loop = loop or asyncio.get_event_loop()
        self.running = True
        self.worker_thread = Thread(target=self._processing_worker, daemon=True)
        self.worker_thread.start()
        logger.info("StreamProcessor工作线程已启动")
    
    def stop(self):
        """停止处理工作线程"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)
        logger.info("StreamProcessor工作线程已停止")
    
    def _processing_worker(self):
        """从队列处理帧的后台工作线程"""
        try:
            while self.running:
                try:
                    frame, video_source = self.frame_queue.get(timeout=0.5)

                    result = self.pipeline.process_frame(frame, video_source=video_source)
                    result["video_source"] = video_source

                    # 异步提交持久化，限制并发数防止无限积累
                    if self._pending_persist < self._max_pending_persist:
                        self._pending_persist += 1

                        async def _persist_and_dec(r, f, proc=self):
                            try:
                                await proc._persist_result(r, f)
                            finally:
                                proc._pending_persist -= 1

                        asyncio.run_coroutine_threadsafe(
                            _persist_and_dec(result, frame),
                            self._main_loop,
                        )

                    with self.result_lock:
                        self.result_cache = result

                    self.frame_queue.task_done()

                except Empty:
                    continue
                except Exception as e:
                    logger.error(f"处理工作线程错误: {e}", exc_info=True)
        finally:
            pass
    
    async def _persist_result(self, result: Dict[str, Any], frame: np.ndarray):
        """
        将检测结果持久化到数据库。
        
        参数：
            result: ML管道的检测结果
            frame: 原始帧（用于存储带标注的帧）
        """
        async with async_session() as session:
            persistence = PersistenceManager(session)
            await persistence.persist_frame_results(result, frame)
    
    def submit_frame(self, frame: np.ndarray, video_source: str = "webcam"):
        """
        提交帧进行处理。
        
        如果队列已满，丢弃最旧的帧以避免阻塞。
        
        参数：
            frame: 要处理的视频帧
            video_source: 源标识符
        """
        try:
            # 尝试将帧添加到队列（非阻塞）
            self.frame_queue.put_nowait((frame, video_source))
        except:
            # 队列已满 - 丢弃最旧的帧并添加新帧
            try:
                self.frame_queue.get_nowait()  # 移除最旧的
                self.frame_queue.put_nowait((frame, video_source))
                logger.debug("帧队列已满，已丢弃最旧的帧")
            except:
                pass
    
    def get_latest_result(self, fallback_frame: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """
        获取最新的处理结果。

        参数：
            fallback_frame: 如果尚未缓存结果，则使用的帧

        返回：
            带有标注帧的最新检测结果
        """
        with self.result_lock:
            if self.result_cache is not None:
                return self.result_cache
            else:
                # No result yet — draw "initializing" overlay (PIL for Chinese)
                annotated = fallback_frame
                if annotated is not None and annotated.size > 0:
                    annotated = annotated.copy()
                    h, w = annotated.shape[:2]

                    # Darken the frame
                    annotated = cv2.addWeighted(
                        np.full_like(annotated, 0, dtype=np.uint8), 0.4,
                        annotated, 0.6, 0,
                    )

                    if _cjk_font_path:
                        pil_img = Image.fromarray(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB))
                        draw = ImageDraw.Draw(pil_img)
                        title_size = max(24, min(w, h) // 15)
                        font_title = ImageFont.truetype(_cjk_font_path, title_size)
                        font_sub = ImageFont.truetype(_cjk_font_path, max(16, title_size * 3 // 5))

                        text = "AI 检测初始化中..."
                        sub = "正在加载模型，请稍候..."

                        tb = draw.textbbox((0, 0), text, font=font_title)
                        tw, th = tb[2] - tb[0], tb[3] - tb[1]
                        tx, ty = (w - tw) // 2, (h - th) // 2 - th // 3
                        draw.text((tx, ty), text, fill=(255, 255, 255), font=font_title)

                        sb = draw.textbbox((0, 0), sub, font=font_sub)
                        sw, sh = sb[2] - sb[0], sb[3] - sb[1]
                        sx, sy = (w - sw) // 2, ty + th + th // 4
                        draw.text((sx, sy), sub, fill=(200, 200, 200), font=font_sub)

                        annotated = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
                    else:
                        cv2.putText(annotated, "AI Loading...", (w // 5, h // 2),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

                return {
                    "persons": [],
                    "annotated_frame": annotated,
                    "frame_number": 0,
                    "timestamp": datetime.now().isoformat(),
                }


async def generate_live_stream(
    cap: cv2.VideoCapture,
    pipeline,
    video_source: str = "webcam",
    display_fps: int = 30,
    process_fps: int = 3,
    queue_size: int = 2,
    interpolate: bool = False,
):
    """
    带ML处理的实时视频流的异步生成器。

    生成用于MJPEG流的JPEG编码帧。

    参数：
        cap: OpenCV VideoCapture实例
        pipeline: ML检测管道
        display_fps: 目标显示帧率
        process_fps: ML处理帧率
        queue_size: 处理队列中的最大帧数
        interpolate: 是否对边界框进行插值

    生成：
        多部分格式的JPEG编码帧字节
    """
    # 确保最小缓冲以实现低延迟
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    processor = StreamProcessor(
        pipeline=pipeline,
        display_fps=display_fps,
        process_fps=process_fps,
        queue_size=queue_size,
        interpolate=interpolate
    )

    # 启动后台处理
    loop = asyncio.get_running_loop()
    processor.start(loop)

    try:
        # 计算处理的帧跳过
        # 根据fps比率处理每N帧
        if process_fps >= display_fps:
            process_skip = 1  # 处理每一帧
        else:
            process_skip = max(1, int(display_fps / process_fps))

        frame_count = 0

        while cap.isOpened():
            # 在线程池中运行阻塞的cap.read()以避免阻塞事件循环
            ret, frame = await loop.run_in_executor(None, cap.read)
            if not ret:
                break

            frame_count += 1

            # Downscale oversized frames for smoother streaming
            h, w = frame.shape[:2]
            max_width = 960
            if w > max_width:
                scale = max_width / w
                frame = cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_LINEAR)

            # Submit frames to ML at reduced rate
            if frame_count % process_skip == 0:
                processor.submit_frame(frame, video_source=video_source)

            # Always get latest result for display
            result = processor.get_latest_result(fallback_frame=frame)
            annotated = result.get("annotated_frame", frame)

            # Encode as JPEG — lower quality for faster encoding + smaller payload
            _, buffer = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
            frame_bytes = buffer.tobytes()

            # 以多部分格式生成帧
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n'
            )

    except Exception as e:
        logger.error(f"实时流生成器错误: {e}", exc_info=True)
    finally:
        # 清理
        processor.stop()
        logger.info("实时流生成器已停止")
