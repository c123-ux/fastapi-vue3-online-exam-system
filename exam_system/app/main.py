"""FastAPI 入口：路由挂载、日志、健康检查、统一异常处理（D2 §3、§5.1）。

启动期依赖不可用不阻塞启动（NFR-08）：
本机原生 Redis 默认不运行，若 import 期强连 Redis，S1 就起不来。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.exceptions import register_exception_handlers
from app.routers import auth, exams, papers, questions, stats
from app.schemas import HealthOut

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)
    for noisy in ("uvicorn.access",):  # pragma: no cover
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _mysql_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - 依赖故障路径
        logging.getLogger("exam.health").error("MySQL 不可用：%s", exc)
        return False


def _redis_ok() -> bool:
    from app import redis_client

    return redis_client.ping()


@asynccontextmanager
async def lifespan(_: FastAPI) -> Iterator[None]:
    setup_logging()
    logger = logging.getLogger("exam.startup")
    logger.info(
        "在线考试系统启动：MySQL=%s Redis=%s（Redis 未起时写路径将 fail-fast 500）",
        "ok" if _mysql_ok() else "down",
        "ok" if _redis_ok() else "down",
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="在线考试系统",
        version="1.0",
        description="RBAC + 判分引擎 + Redis 限时 + 幂等防重（教学练手项目三）",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    # 挂载顺序即路由匹配顺序（D2 §5.5）：auth → questions → papers → exams → stats
    app.include_router(auth.router)
    app.include_router(questions.router)
    app.include_router(papers.router)
    app.include_router(exams.router)
    app.include_router(stats.router)

    @app.get("/health", response_model=HealthOut)
    def health() -> HealthOut:
        deps = {"mysql": "ok" if _mysql_ok() else "down", "redis": "ok" if _redis_ok() else "down"}
        # liveness 语义：依赖故障也返回 200，由 status/degraded 表达（D2 §5.2）
        status = "ok" if all(v == "ok" for v in deps.values()) else "degraded"
        return HealthOut(status=status, deps=deps)  # type: ignore[arg-type]

    return app


app = create_app()

__all__ = ["app", "create_app", "settings", "setup_logging"]
