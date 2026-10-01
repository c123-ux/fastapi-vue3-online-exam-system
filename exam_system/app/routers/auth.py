"""认证路由：注册 / 登录（D1 FR-01）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from sqlalchemy.exc import IntegrityError

from app import time_utils
from app.config import settings
from app.exceptions import AppError
from app.middleware import account_rate_limit
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

logger = logging.getLogger("exam.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=200, summary="注册", description="注册新用户；教师角色需要提供注册码。")
def register(payload: RegisterIn, request: Request, db: DbSession) -> UserOut:
    username = normalize_username(payload.username)
    # V-03：按账号限流，防单一攻击者耗尽某账号配额（账号级，不按 IP 全局连坐）
    account_rate_limit(request, username, "/api/auth/register")
    if payload.role == Role.TEACHER:
        _check_teacher_code(payload.teacher_code)
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
    try:
        db.commit()
    except IntegrityError as exc:
        # V-01 TOCTOU 竞态兜底：并发同名时唯一约束在此处命中，
        # 映射为 409 USERNAME_EXISTS（不把 DB 原文透出）。
        db.rollback()
        logger.info("并发同名注册被唯一约束拦截 username=%s", username)
        raise AppError.make("USERNAME_EXISTS", "用户名已被占用（大小写不敏感）") from exc
    db.refresh(user)
    return user


def _check_teacher_code(teacher_code: str | None) -> None:
    if not settings.teacher_register_code:
        raise AppError.make(
            "INVALID_TEACHER_CODE", "服务端未配置 TEACHER_REGISTER_CODE，暂不开放教师注册"
        )
    if teacher_code != settings.teacher_register_code:
        raise AppError.make("INVALID_TEACHER_CODE", "教师注册码缺失或错误")


@router.post("/token", response_model=TokenOut, summary="登录", description="用户名密码登录，返回 JWT access_token。")
def login(payload: TokenIn, request: Request, db: DbSession) -> TokenOut:
    # V-03：按账号限流，防单一攻击者用错误密码耗尽某账号配额后锁死该账号登录
    account_rate_limit(request, payload.username, "/api/auth/token")
    user = find_user_by_username(db, payload.username)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise AppError.make("INVALID_CREDENTIALS", "用户名或密码错误")
    return TokenOut(access_token=create_access_token(user), role=user.role)
