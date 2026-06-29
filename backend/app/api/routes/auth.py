from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ...core.config import settings
from ...core.security import (
    create_access_token,
    get_current_user,
    get_password_hash,
    needs_password_rehash,
    validate_password_input,
    verify_password,
)
from ...models.user import User
from ...schemas.user import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    validate_password_input(user_data.password)

    existing_user = (
        await db.execute(select(User).where(User.username == user_data.username))
    ).scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    existing_email = (
        await db.execute(select(User).where(User.email == user_data.email))
    ).scalar_one_or_none()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists",
        )

    user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        full_name=user_data.full_name,
        department=user_data.department,
        job_title=user_data.job_title,
        responsibilities=user_data.responsibilities,
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    validate_password_input(login_data.password)

    user = (
        await db.execute(select(User).where(User.username == login_data.username))
    ).scalar_one_or_none()
    if not user:
        user = (
            await db.execute(select(User).where(User.email == login_data.username))
        ).scalar_one_or_none()

    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if needs_password_rehash(user.password_hash):
        user.password_hash = get_password_hash(login_data.password)
        await db.commit()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username, "role": user.role},
    )
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=max(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES, 0) * 60,
    )


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    return {"message": "Logout successful"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/password")
async def change_password(
    password_data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    validate_password_input(password_data.current_password)
    validate_password_input(password_data.new_password)

    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.password_hash = get_password_hash(password_data.new_password)
    await db.commit()
    return {"message": "Password updated successfully"}
