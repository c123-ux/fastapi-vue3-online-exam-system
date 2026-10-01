"""统一业务异常与错误响应体（D2 §5.1）。

响应体固定三字段：code=英文短码、message=英文短语、detail=中文可读说明。
422（Pydantic 结构校验）也渲染成同一结构，否则测试按统一体断言必红。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("exam.exceptions")

# code -> (http_status, message)
ERRORS: dict[str, tuple[int, str]] = {
    # 400 业务规则
    "INVALID_PARAM": (400, "invalid request parameters"),
    "INVALID_QUESTION": (400, "question content does not match its type rules"),
    "INVALID_SCORE": (400, "score out of allowed range"),
    "NOT_SHORT_QUESTION": (400, "only short questions can be reviewed manually"),
    "EMPTY_PAPER": (400, "paper has no question or zero total score"),
    "INVALID_PAPER": (400, "paper fields are invalid"),
    "EXAM_NOT_STARTED": (400, "exam has not started yet"),
    "EXAM_ENDED": (400, "exam window has ended"),
    "EXAM_CLOSED": (400, "exam is closed"),
    "EXAM_WINDOW_TOO_SHORT": (400, "remaining exam window is too short to start"),
    "QUESTION_NOT_IN_PAPER": (400, "question does not belong to this paper snapshot"),
    # 401 未认证
    "INVALID_CREDENTIALS": (401, "username or password is wrong"),
    "TOKEN_INVALID": (401, "authentication token missing or invalid"),
    # 403 角色不符
    "ROLE_FORBIDDEN": (403, "current role is not allowed to access this resource"),
    "INVALID_TEACHER_CODE": (403, "teacher register code is missing or wrong"),
    # 404 不存在 / 跨用户资源（存在性隐藏：两种情况返回完全相同）
    "NOT_FOUND": (404, "resource not found"),
    "PAPER_NOT_VISIBLE": (404, "paper not found or not visible to you"),
    "RECORD_NOT_VISIBLE": (404, "exam record not found or not visible to you"),
    # 409 重复与状态冲突
    "USERNAME_EXISTS": (409, "username already taken"),
    "CATEGORY_EXISTS": (409, "category name already exists"),
    "DUPLICATE_QUESTION_IN_PAPER": (409, "question already added to this paper"),
    "QUESTION_IN_USE": (409, "question is referenced by papers"),
    "QUESTION_LOCKED": (409, "question is locked because a published paper references it"),
    "PAPER_STATE_CONFLICT": (409, "paper status transition is not allowed"),
    "ALREADY_SUBMITTED": (409, "exam record has already been submitted"),
    "NOT_IN_PROGRESS": (409, "exam record is not in progress"),
    "EXAM_TIME_UP": (409, "exam time is up, record has been settled as timeout"),
    "SNAPSHOT_TAMPERED": (409, "grading baseline integrity check failed"),
    # 429 限流
    "RATE_LIMIT": (429, "too many requests"),
    # 500 依赖
    "REDIS_UNAVAILABLE": (500, "cache service is unavailable"),
    "DB_CONFLICT": (500, "database integrity conflict"),
}

REDIS_HINT = "启动 Redis：cd D:\\Redis; .\\redis-server.exe .\\redis.windows.conf"


class AppError(Exception):
    """业务异常。用 make(code) 构造，避免为每个错误码写一个类。"""

    def __init__(self, code: str, http_status: int, message: str, detail: Any = None) -> None:
        super().__init__(f"{code}: {detail or message}")
        self.code = code
        self.http_status = http_status
        self.message = message
        self.detail = detail

    @classmethod
    def make(cls, code: str, detail: Any = None) -> "AppError":
        if code not in ERRORS:
            raise KeyError(f"未登记的错误码 {code}")
        http_status, message = ERRORS[code]
        return cls(code=code, http_status=http_status, message=message, detail=detail)


def error_body(code: str, message: str, detail: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "detail": detail}


def _safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Pydantic 的 errors() 里 ctx 带 ValueError 对象，直接进 JSON 会 TypeError（实测）。"""
    out = []
    for err in exc.errors():
        out.append(
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return out


def register_exception_handlers(app: FastAPI) -> None:
    """把 AppError / 422 / RedisError / IntegrityError 统一成一种响应体。"""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status, content=error_body(exc.code, exc.message, exc.detail)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body(
                "INVALID_PARAM", "request body or path parameter failed validation", _safe_errors(exc)
            ),
        )

    @app.exception_handler(RedisError)
    async def _redis_error(_: Request, exc: RedisError) -> JSONResponse:  # pragma: no cover
        # 不透出 exc 原文（含连接地址/命令细节），仅给可复制的启动提示（EC-29 / NFR-08）
        logger.error("RedisError: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("REDIS_UNAVAILABLE", ERRORS["REDIS_UNAVAILABLE"][1], REDIS_HINT),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        # 不透出 str(exc.orig)（MySQL 1062/表名/约束名等引擎细节），统一给中文说明（V-01/V-02 信息泄露修复）
        logger.error("IntegrityError: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body(
                "DB_CONFLICT", ERRORS["DB_CONFLICT"][1],
                "数据约束冲突，请检查提交内容是否重复。",
            ),
        )
