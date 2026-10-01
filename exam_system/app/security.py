"""认证与授权：bcrypt 密码、JWT 签发校验、角色依赖（D1 §2.2 判定顺序）。

判定顺序固定：401（未认证）→ 403（角色不符）→ 404（不归属，由 service 抛）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.exceptions import AppError
from app.models import Role, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# passlib 1.7.4 用 bcrypt.__about__.__version__ 探测版本，bcrypt 4.1+ 已移除该模块，
# 首次哈希会打一行 "(trapped) error reading bcrypt version" 假警报（trapped=内部已捕获，功能不受影响）。
# 压到 ERROR 级：正常路径不刷屏；passlib 真出事时 ERROR 日志仍会露出。
logging.getLogger("passlib").setLevel(logging.ERROR)
_bearer = HTTPBearer(auto_error=False)

# bcrypt 只处理前 72 字节；schemas 已限长 8~64，这里再兜一层防越界
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return pwd_context.hash(_check_length(password))


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(_check_length(plain), hashed)
    except (ValueError, TypeError, AppError):  # pragma: no cover - 非法哈希串/超长密码
        # V-10 修复：>72 字节密码在 _check_length 抛 AppError，之前冒泡成 400（可区分错误路径）。
        # 这里统一吞掉返回 False，使登录失败一律 401 INVALID_CREDENTIALS。
        return False


def _check_length(password: str) -> str:
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise AppError.make("INVALID_PARAM", "密码长度超过 bcrypt 上限 72 字节")
    return password


def normalize_username(username: str) -> str:
    """用户名大小写归一（utf8mb4_unicode_ci 本身不敏感，应用层再归一，D2 §4.1）。"""
    return username.strip().lower()


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "role": user.role,
        "username": user.username,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError as exc:
        raise AppError.make("TOKEN_INVALID", f"token 无法解析：{exc}") from exc


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise AppError.make("TOKEN_INVALID", "缺少 Authorization: Bearer <token>")
    payload = decode_token(credentials.credentials)
    try:
        user_id = int(payload["sub"])
        role = str(payload["role"])
    except (KeyError, ValueError) as exc:
        raise AppError.make("TOKEN_INVALID", "token claims 不完整") from exc
    user = db.get(User, user_id)
    if user is None or user.role != role:
        raise AppError.make("TOKEN_INVALID", "用户不存在或角色已变更")
    return user


def require_role(role: str):
    """生成"必须是某角色"的依赖。"""

    def _dep(current: Annotated[User, Depends(get_current_user)]) -> User:
        if current.role != role:
            raise AppError.make("ROLE_FORBIDDEN", f"该接口仅限 {role} 角色")
        return current

    return _dep


require_teacher = require_role(Role.TEACHER)
require_student = require_role(Role.STUDENT)

CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentTeacher = Annotated[User, Depends(require_teacher)]
CurrentStudent = Annotated[User, Depends(require_student)]
DbSession = Annotated[Session, Depends(get_db)]


def find_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == normalize_username(username)))
