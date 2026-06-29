from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)

_configured = False


def configure_runtime_tuning(component: str) -> None:
    global _configured
    if _configured:
        return

    cv_threads = max(1, int(getattr(settings, "OPENCV_NUM_THREADS", 1)))
    torch_threads = max(1, int(getattr(settings, "TORCH_NUM_THREADS", 1)))
    torch_interop_threads = max(1, int(getattr(settings, "TORCH_INTEROP_THREADS", 1)))

    try:
        import cv2

        cv2.setNumThreads(cv_threads)
        try:
            cv2.ocl.setUseOpenCL(False)
        except Exception:
            pass
    except Exception:
        logger.debug("OpenCV runtime tuning unavailable", exc_info=True)

    try:
        import torch

        torch.set_num_threads(torch_threads)
        try:
            torch.set_num_interop_threads(torch_interop_threads)
        except RuntimeError:
            # PyTorch only allows this before parallel work starts.
            pass
    except Exception:
        logger.debug("PyTorch runtime tuning unavailable", exc_info=True)

    _configured = True
    logger.info(
        "Runtime tuning applied component=%s opencv_threads=%s torch_threads=%s torch_interop_threads=%s",
        component,
        cv_threads,
        torch_threads,
        torch_interop_threads,
    )
