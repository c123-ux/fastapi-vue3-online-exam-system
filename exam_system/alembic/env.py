"""Alembic 环境配置：metadata 来自 app.models，URL 可由 ALEMBIC_DATABASE_URL 覆盖。

覆盖机制是给 tests/conftest.py 用的——测试要把同一套迁移重放到 exam_test，
这样"迁移可重放"（TC-S-04）才是被真正验证过的事实，而不是文档里的一句话。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from app import models  # noqa: E402,F401  注册到 Base.metadata
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    return os.getenv("ALEMBIC_DATABASE_URL", "").strip() or settings.database_url


def configure(connection) -> None:  # noqa: ANN001
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=False,
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        configure(connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
