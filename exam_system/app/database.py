"""数据库连接与会话（SQLAlchemy 2.0 风格）。"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    """ORM 声明式基类；Alembic autogenerate 的唯一目标 metadata。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：事务边界由 service 显式 commit，此处负责收尾与回滚。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
