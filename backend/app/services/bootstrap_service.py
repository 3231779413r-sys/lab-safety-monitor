from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.security import get_password_hash
from ..models.user import User


async def ensure_initial_admin(session: AsyncSession) -> bool:
    if not settings.INIT_ADMIN_ENABLED:
        return False

    existing = (
        await session.execute(
            select(User).where(
                (User.username == settings.INIT_ADMIN_USERNAME)
                | (User.email == settings.INIT_ADMIN_EMAIL)
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    admin = User(
        username=settings.INIT_ADMIN_USERNAME,
        email=settings.INIT_ADMIN_EMAIL,
        password_hash=get_password_hash(settings.INIT_ADMIN_PASSWORD),
        full_name=settings.INIT_ADMIN_FULL_NAME,
        department="系统管理",
        job_title="管理员",
        responsibilities="平台初始化与运维",
        role="admin",
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    return True
