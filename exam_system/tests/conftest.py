"""全局测试基建（D3 §2）。

本文件只做两件事，保证**单元测试真的零外部依赖**：
1. 在 import app.* 之前把环境变量指向 exam_test 与 Redis db1；
2. 提供唯一的时间注入点 FakeClock。

需要真实 MySQL/Redis 的 fixture 全部在 tests/integration/conftest.py，
所以 `pytest tests/unit` 在没起 Redis 的机器上也能跑。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402

_ENV = dotenv_values(ROOT / ".env")
TEST_DB = _ENV.get("TEST_DATABASE_URL", "")
TEST_REDIS = _ENV.get("TEST_REDIS_URL", "")

if not TEST_DB or not TEST_REDIS:
    pytest.exit("缺少 TEST_DATABASE_URL / TEST_REDIS_URL，请检查 exam_system/.env", returncode=1)

# config.py 的 load_dotenv 不覆盖已有环境变量，所以这里先设即可生效
os.environ["DATABASE_URL"] = TEST_DB
os.environ["REDIS_URL"] = TEST_REDIS
os.environ["ALEMBIC_DATABASE_URL"] = TEST_DB
os.environ["TEACHER_REGISTER_CODE"] = "TC0DE-EXAM"
os.environ.setdefault("EXAM_GRACE_SECONDS", "30")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "240")


def parse_password(url: str) -> str:
    head = url.split("://", 1)[-1]
    cred = head.split("@", 1)[0]
    return cred.split(":", 1)[1] if ":" in cred else ""


MYSQL_ARGS = {
    "host": "127.0.0.1",
    "port": 3307,
    "user": "root",
    "password": parse_password(TEST_DB),
    "charset": "utf8mb4",
}
TEST_URL = TEST_DB
TEST_REDIS_URL = TEST_REDIS
TABLES = (
    "answers",
    "exam_records",
    "paper_questions",
    "papers",
    "questions",
    "categories",
    "users",
)
PASSWORD = "Passw0rd!"
CODE = "TC0DE-EXAM"
DEFAULT_SCORES = [5, 5, 5, 10, 10, 5, 5, 30, 10, 15]  # 客观 70 + 简答 30 = 满分 100


class FakeClock:
    """唯一时间注入点：patch app.time_utils.now（D2 §6.5 / N9）。"""

    def __init__(self) -> None:
        self._origin = datetime(2026, 3, 1, 9, 0, 0)
        self._offset = 0.0

    def now(self) -> datetime:
        return self._origin + timedelta(seconds=self._offset)

    def advance(self, seconds: float) -> None:
        self._offset += seconds

    def set(self, moment: datetime) -> None:
        self._origin = moment
        self._offset = 0.0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    import app.time_utils as tu

    fake = FakeClock()
    monkeypatch.setattr(tu, "now", fake.now)
    return fake
