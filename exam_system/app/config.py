"""配置：全部来自环境变量 / .env，禁止硬编码密钥（需求文档 8.1）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_BASE = Path(__file__).resolve().parent.parent
load_dotenv(_BASE / ".env")

REQUIRED = ("DATABASE_URL", "SECRET_KEY")


class ConfigError(RuntimeError):
    """配置缺失。报错里给出可复制的修复命令，避免只抛裸异常。"""


def _req(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise ConfigError(
            f"缺少环境变量 {key}。请复制 .env.example 为 .env 并填写；"
            f"SECRET_KEY 可用 python -c \"import secrets;print(secrets.token_hex(32))\" 生成。"
        )
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    exam_grace_seconds: int
    teacher_register_code: str
    test_database_url: str = ""
    test_redis_url: str = ""
    min_exam_window_seconds: int = 60
    min_window_ratio: float = 0.1
    env: str = "prod"  # dev=开放 /docs 等交互文档；prod=关闭（V-07）
    base_dir: Path = field(default=_BASE, repr=False)


def get_settings() -> Settings:
    return Settings(
        database_url=_req("DATABASE_URL"),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        secret_key=_req("SECRET_KEY"),
        algorithm=os.getenv("ALGORITHM", "HS256"),
        access_token_expire_minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "240")),
        exam_grace_seconds=int(os.getenv("EXAM_GRACE_SECONDS", "30")),
        teacher_register_code=os.getenv("TEACHER_REGISTER_CODE", ""),
        test_database_url=os.getenv("TEST_DATABASE_URL", ""),
        test_redis_url=os.getenv("TEST_REDIS_URL", ""),
        env=os.getenv("ENV", "prod").strip().lower(),
    )


settings = get_settings()
