"""认证路由：注册 / 登录（D1 FR-01）。"""
from __future__ import annotations

from fastapi import APIRouter

from app import time_utils
from app.config import settings
from app.exceptions import AppError
from app.models import Role, User
from app.schemas import RegisterIn, TokenIn, TokenOut, UserOut
from app.security import (
    DbSession,
    create_access_token,
    find_user_by_username,
    hash_password,
    normalize_username,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=200)
def register(payload: RegisterIn, db: DbSession) -> User:
    if payload.role == Role.TEACHER:
        _check_teacher_code(payload.teacher_code)
    username = normalize_username(payload.username)
    if find_user_by_username(db, username) is not None:
        raise AppError.make("USERNAME_EXISTS", "用户名已被占用（大小写不敏感）")
    user = User(
        username=username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        full_name=payload.full_name,
        created_at=time_utils.now(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _check_teacher_code(teacher_code: str | None) -> None:
    if not settings.teacher_register_code:
        raise AppError.make(
            "INVALID_TEACHER_CODE", "服务端未配置 TEACHER_REGISTER_CODE，暂不开放教师注册"
        )
    if teacher_code != settings.teacher_register_code:
        raise AppError.make("INVALID_TEACHER_CODE", "教师注册码缺失或错误")


@router.post("/token", response_model=TokenOut)
def login(payload: TokenIn, db: DbSession) -> TokenOut:
    user = find_user_by_username(db, payload.username)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise AppError.make("INVALID_CREDENTIALS", "用户名或密码错误")
    return TokenOut(access_token=create_access_token(user), role=user.role)
