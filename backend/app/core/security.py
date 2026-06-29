import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from .config import settings
from ..models.user import User
from ..api.deps import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
ALGORITHM = "HS256"
PASSWORD_HASH_PREFIX = "bcrypt_sha256$"
MAX_PASSWORD_BYTES = 4096


def _sha256_password(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def validate_password_input(password: str) -> None:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail="密码过长")


def needs_password_rehash(hashed_password: str) -> bool:
    return not hashed_password.startswith(PASSWORD_HASH_PREFIX)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    validate_password_input(plain_password)
    if hashed_password.startswith(PASSWORD_HASH_PREFIX):
        inner_hash = hashed_password[len(PASSWORD_HASH_PREFIX):].encode("utf-8")
        return bcrypt.checkpw(_sha256_password(plain_password), inner_hash)
    if hashed_password.startswith("$2"):
        password_bytes = plain_password.encode("utf-8")
        if len(password_bytes) > 72:
            raise HTTPException(status_code=400, detail="密码过长")
        return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
    return False


def get_password_hash(password: str) -> str:
    validate_password_input(password)
    hashed = bcrypt.hashpw(_sha256_password(password), bcrypt.gensalt())
    return f"{PASSWORD_HASH_PREFIX}{hashed.decode('utf-8')}"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta is None:
        if settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES > 0:
            expires_delta = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
        to_encode.update({"exp": expire})
    to_encode.update({"type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = (
        await db.execute(select(User).where(User.id == int(user_id_str)))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    return user


async def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user
