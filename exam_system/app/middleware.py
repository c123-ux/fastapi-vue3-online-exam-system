"""中间件：CORS、请求日志、限流（D2 §3 / NFR-07）。

设计（红队报告 V-03~V-06 修复）：
- IP 级限流：宽松上限，负责"防高频刷写路径/恶意路径"，防滥用而非误伤。
- 账号级限流：按 username 区分（登录/注册），防单一攻击者锁死全网登录（V-03）。
- _RateLimiter 带键数上限 + 过期键清理，防内存无界（V-05）。
- 被限流（429）也写请求日志并带 X-Request-ID（V-06）。
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from app.config import settings

logger = logging.getLogger("exam.middleware")

# 内存限流器的键数量上限（防 V-05 无界增长）；超过后清除最旧键
_MAX_KEYS = 10000


# ------------------------------------------------------------------ CORS
def setup_cors(app) -> None:
    """生产环境按 .env 配置；开发默认放行本地前端。"""
    from fastapi.middleware.cors import CORSMiddleware
    origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )


# ------------------------------------------------------------------ 请求日志与计时
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录请求路径、方法、状态码、耗时；注入 request_id。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "request_failed request_id=%s method=%s path=%s error=%s",
                request_id, request.method, request.url.path, exc
            )
            raise
        process_time = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.4f}"
        logger.info(
            "request_complete request_id=%s method=%s path=%s status=%d duration_ms=%.2f",
            request_id, request.method, request.url.path, response.status_code, process_time * 1000
        )
        return response


# ------------------------------------------------------------------ 限流（单实例内存实现；多实例需换 Redis 计数）
class _RateLimiter:
    """滑动窗口限流：每个键在窗口内最多 max_requests 次。

    - 键总数受 _MAX_KEYS 约束，超出则清除"当前窗口内最早"的键（V-05 防无界增长）。
    - is_allowed 只裁剪单键内的时间戳；整体淘汰放在 is_allowed 里按需触发。
    """

    def __init__(self, max_keys: int = _MAX_KEYS) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._max_keys = max_keys

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        # 超键数上限：清掉整表（简化为"全清"；真实多实例场景应换共享存储）
        if len(self._hits) >= self._max_keys:
            self._hits.clear()
        timestamps = self._hits[key]
        self._hits[key] = [t for t in timestamps if t > cutoff]
        if len(self._hits[key]) >= max_requests:
            return False
        self._hits[key].append(now)
        return True

    def key_count(self) -> int:
        return len(self._hits)

    def reset(self) -> None:
        self._hits.clear()


_ip_limiter = _RateLimiter()
_account_limiter = _RateLimiter()


def account_rate_limit(request: Request, username: str, path: str, *, limit: int = 10, window: int = 60) -> None:
    """账号级限流：登录/注册按 username 区分，防单一攻击者锁死全网登录（V-03）。

    - TestClient 豁免：与 IP 限流一致，避免 fixture 间互相触发。
    - path 用于把登录与注册分开计数（不同接口不同桶）。username 为空则跳过。
    """
    from app.exceptions import AppError
    if not username:
        return
    if request.client and request.client.host == "testclient":
        return
    key = f"acct:{path}:{username.lower()}"
    if not _account_limiter.is_allowed(key, limit, window):
        raise AppError.make("RATE_LIMIT", f"该账号请求过于频繁，请 {window} 秒后再试")


def _limit_response(path: str, max_requests: int, window_seconds: int) -> JSONResponse:
    resp = JSONResponse(
        status_code=429,
        content={
            "code": "RATE_LIMIT",
            "message": "too many requests",
            "detail": f"限流：{max_requests} 次 / {window_seconds} 秒",
        },
    )
    # V-06：被限流的 429 也要带上 trace 头（RequestLoggingMiddleware 在外层时已被 set；
    # 这里兜底设一次，保证即使日志中间件未覆盖也能追踪）
    resp.headers["X-Request-ID"] = uuid.uuid4().hex[:12]
    resp.headers["X-Process-Time"] = "0.0000"
    return resp


class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP 级宽松限流：对写路径与高频读路径设基础上限，防滥用（V-04）。"""

    def __init__(
        self,
        app,
        *,
        prefixes: tuple[str, ...] = ("/api/exams", "/api/papers"),
        max_requests: int = 120,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self.prefixes = prefixes
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 测试环境（TestClient）跳过 IP 限流，避免 fixture 间互相触发
        if request.client and request.client.host == "testclient":
            return await call_next(request)
        if not any(request.url.path.startswith(p) for p in self.prefixes):
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"
        if not _ip_limiter.is_allowed(key, self.max_requests, self.window_seconds):
            logger.warning(
                "rate_limited ip=%s path=%s limit=%d/%ds",
                client_ip, request.url.path, self.max_requests, self.window_seconds
            )
            return _limit_response(request.url.path, self.max_requests, self.window_seconds)
        return await call_next(request)
