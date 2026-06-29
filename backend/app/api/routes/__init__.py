from .events import router as events_router
from .persons import router as persons_router
from .stats import router as stats_router
from .cameras import router as cameras_router
from .websocket import router as ws_router
from .auth import router as auth_router
from .supervision import router as supervision_router

__all__ = [
    "events_router",
    "persons_router",
    "stats_router",
    "cameras_router",
    "ws_router",
    "auth_router",
    "supervision_router",
]
