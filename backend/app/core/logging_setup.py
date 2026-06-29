import logging
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Optional, TextIO

from .config import settings


class HourlyDirectoryFileHandler(logging.Handler):
    def __init__(self, log_root: Path, component: str) -> None:
        super().__init__()
        self.log_root = Path(log_root)
        self.component = component.strip("/ ")
        self._lock = RLock()
        self._stream: Optional[TextIO] = None
        self._current_path: Optional[Path] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = self.format(record)
            with self._lock:
                stream = self._ensure_stream(record.created)
                stream.write(payload + "\n")
                stream.flush()
        except Exception:
            self.handleError(record)

    def _ensure_stream(self, created_ts: float) -> TextIO:
        path = self._path_for_timestamp(created_ts)
        if self._stream is not None and self._current_path == path:
            return self._stream
        self._close_stream()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = open(path, "a", encoding="utf-8")
        self._current_path = path
        return self._stream

    def _path_for_timestamp(self, created_ts: float) -> Path:
        when = datetime.fromtimestamp(created_ts)
        return (
            self.log_root
            / self.component
            / when.strftime("%Y")
            / when.strftime("%m")
            / when.strftime("%d")
            / f"{when.strftime('%H')}.log"
        )

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None
                self._current_path = None

    def close(self) -> None:
        with self._lock:
            self._close_stream()
        super().close()


def configure_logging(component: str) -> None:
    log_root = Path(settings.LOGS_DIR)
    log_root.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    if getattr(root_logger, "_lab_logging_configured", False):
        return

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = HourlyDirectoryFileHandler(log_root, component)
    file_handler.setFormatter(formatter)

    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    root_logger._lab_logging_configured = True  # type: ignore[attr-defined]
