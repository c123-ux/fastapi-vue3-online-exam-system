"""集成测试 fixture：真实 MySQL(exam_test) + 真实 Redis(db1)。

约定（D3 §2.2）：
- 表结构由 alembic `upgrade head` 建，不用 create_all；
- 依赖不通直接 exit 并打印启动命令（fail-fast，不算 skip）；
- 每用例前清表 + flushdb（只清 db1）。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    CODE,
    DEFAULT_SCORES,
    MYSQL_ARGS,
    PASSWORD,
    TABLES,
    TEST_REDIS_URL,
    TEST_URL,
)


def _assert_infra() -> None:
    problems: list[str] = []
    try:
        import pymysql

        pymysql.connect(**MYSQL_ARGS).close()
    except Exception as exc:
        problems.append(f"MySQL 3307 不通：{exc}")
    try:
        import redis

        r = redis.Redis.from_url(TEST_REDIS_URL, protocol=2, socket_connect_timeout=1)
        r.ping()
        r.close()
    except Exception as exc:
        problems.append(
            f"Redis 不通：{exc}\n     启动：cd D:\\Redis; .\\redis-server.exe .\\redis.windows.conf"
            "\n     或：powershell -File scripts\\start_redis.ps1"
        )
    if problems:
        pytest.exit("测试依赖不可用（fail-fast，非 skip）：\n  " + "\n  ".join(problems), returncode=1)


def _rebuild_schema() -> None:
    import pymysql
    from alembic import command
    from alembic.config import Config

    conn = pymysql.connect(**MYSQL_ARGS)
    with conn.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS exam_test")
        cur.execute(
            "CREATE DATABASE exam_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    conn.commit()
    conn.close()

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _bootstrap() -> None:
    _assert_infra()
    _rebuild_schema()


@pytest.fixture(scope="session")
def client(_bootstrap: None) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean(_bootstrap: None) -> Iterator[None]:
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in TABLES:
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    engine.dispose()

    import redis

    r = redis.Redis.from_url(TEST_REDIS_URL, protocol=2, decode_responses=True)
    r.flushdb()
    r.close()
    yield


# ------------------------------------------------------------------ 账号
def register_and_login(client: TestClient, username: str, role: str) -> dict[str, str]:
    payload: dict[str, Any] = {"username": username, "password": PASSWORD, "role": role}
    if role == "teacher":
        payload["teacher_code"] = CODE
    reg = client.post("/api/auth/register", json=payload)
    assert reg.status_code == 200, reg.text
    tok = client.post("/api/auth/token", json={"username": username, "password": PASSWORD})
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


@pytest.fixture
def t1(client: TestClient) -> dict[str, str]:
    return register_and_login(client, "teacher01", "teacher")


@pytest.fixture
def t2(client: TestClient) -> dict[str, str]:
    return register_and_login(client, "teacher02", "teacher")


@pytest.fixture
def s1(client: TestClient) -> dict[str, str]:
    return register_and_login(client, "stu01", "student")


@pytest.fixture
def s2(client: TestClient) -> dict[str, str]:
    return register_and_login(client, "stu02", "student")


@pytest.fixture
def s3(client: TestClient) -> dict[str, str]:
    return register_and_login(client, "stu03", "student")


@pytest.fixture
def s4(client: TestClient) -> dict[str, str]:
    return register_and_login(client, "stu04", "student")


# ------------------------------------------------------------------ 题集与试卷
def _q(qtype: str, content: str, options: list[str] | None, answer: str, cat: int,
       difficulty: str = "medium") -> dict[str, Any]:
    return {
        "type": qtype,
        "content": content,
        "options": options,
        "correct_answer": answer,
        "category_id": cat,
        "difficulty": difficulty,
    }


SPECS = [
    _q("single", "1NF 的要求是什么？", ["A. 列原子性", "B. 表连接", "C. 有索引", "D. 有视图"], "A", 1, "easy"),
    _q("single", "MySQL 默认事务隔离级别？", ["A. READ UNCOMMITTED", "B. REPEATABLE READ", "C. SERIALIZABLE", "D. NONE"], "B", 1, "easy"),
    _q("single", "HTTP 403 表示什么？", ["A. 未认证", "B. 资源不存在", "C. 角色无权", "D. 请求过多"], "C", 2, "medium"),
    _q("multiple", "下列哪些属于 ACID？", ["A. 原子性", "B. 索引性", "C. 隔离性", "D. 并发性"], "A,C", 1, "medium"),
    _q("multiple", "哪些是有效的 HTTP 方法？", ["A. POST", "B. GET", "C. FETCH", "D. PUT"], "B,D", 2, "hard"),
    _q("judge", "Redis 单线程模型下命令是原子执行的。", None, "T", 1, "easy"),
    _q("judge", "MySQL 的 MyISAM 引擎支持事务。", None, "F", 1, "easy"),
    _q("short", "简述数据库事务的四个特性。", None, "原子性|一致性|隔离性|持久性", 1, "hard"),
    _q("single", "下列哪种写法容易让索引失效？", ["A. 前缀匹配", "B. 覆盖索引", "C. 最左前缀", "D. 对列使用函数"], "D", 2, "medium"),
    _q("multiple", "哪些手段能防重复提交？", ["A. 状态机", "B. Redis SETNX", "C. 唯一索引", "D. 前端置灰"], "A,B,C", 1, "hard"),
]


@pytest.fixture
def categories(client: TestClient, t1: dict[str, str]) -> list[int]:
    out = []
    for name in ("数据库", "Web 基础"):
        r = client.post("/api/categories", json={"name": name}, headers=t1)
        assert r.status_code == 200, r.text
        out.append(r.json()["id"])
    return out


@pytest.fixture
def question_ids(client: TestClient, t1: dict[str, str], categories: list[int]) -> list[int]:
    """SPECS 里的 category_id 只是占位（1/2），建题时映射到真实分类 id。"""
    ids: list[int] = []
    for spec in SPECS:
        body = {**spec, "category_id": categories[spec["category_id"] - 1]}
        r = client.post("/api/questions", json=body, headers=t1)
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    assert len(ids) == 10
    return ids


def paper_window(now) -> dict[str, str]:  # noqa: ANN001 - 传入 clock.now()
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=10)
    return {"start_at": start.isoformat(), "end_at": end.isoformat()}


def make_paper(client: TestClient, headers: dict[str, str], qids: list[int], now,
               *, publish: bool = True, duration: int = 10, title: str = "期中测试") -> int:
    body = {
        "title": title,
        "duration_minutes": duration,
        **paper_window(now),
        "questions": [{"question_id": q, "score": s} for q, s in zip(qids, DEFAULT_SCORES)],
    }
    r = client.post("/api/papers", json=body, headers=headers)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    if publish:
        pub = client.post(f"/api/papers/{pid}/publish", headers=headers)
        assert pub.status_code == 200, pub.text
    return pid


@pytest.fixture
def paper_id(client: TestClient, t1: dict[str, str], question_ids: list[int], clock) -> int:
    return make_paper(client, t1, question_ids, clock.now())


@pytest.fixture
def open_exam(client: TestClient, s1: dict[str, str], paper_id: int) -> dict[str, Any]:
    r = client.post(f"/api/exams/{paper_id}/start", headers=s1)
    assert r.status_code == 200, r.text
    return r.json()
