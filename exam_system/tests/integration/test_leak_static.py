"""TC-I-49~52 + TC-S-01~08：泄露防线、依赖故障、判分基线隔离、结构与静态检查。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL
from tests.integration.test_exam_flow import db_row

APP_DIR = Path(__file__).resolve().parents[2] / "app"
BANNED_WORDS = ("correct_answer", "reference_answer", "snapshot")


def _get(client: TestClient, url: str, headers: dict) -> str:
    r = client.get(url, headers=headers)
    assert r.status_code in (200, 404), f"{url} -> {r.status_code} {r.text[:120]}"
    return r.text


# ------------------------------------------------------------------ TC-I-49
def test_student_never_sees_answers(client: TestClient, s1: dict, open_exam: dict,
                                   question_ids: list[int]) -> None:
    """学生身份遍历所有可达接口，正文不得出现答案类字段或答案文本。"""
    rid = open_exam["record_id"]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[0], "answer": "A"}, headers=s1)
    client.post(f"/api/exams/{rid}/submit", headers=s1)
    urls = [
        "/api/exams",
        f"/api/exams/{rid}",
        f"/api/exams/{rid}/score",
        f"/api/exams/{open_exam['paper_id']}/start",
        "/api/papers",
    ]
    bodies = [_get(client, u, s1) for u in urls if "start" not in u]
    bodies.append(client.post(urls[3], headers=s1).text)
    joined = "\n".join(bodies)
    for word in BANNED_WORDS:
        assert word not in joined, f"学生侧响应泄露了 {word}"
    assert "A. 列原子性" in joined, "题面本身应当可见（断言没白测）"


def test_teacher_side_is_the_only_answer_source(client: TestClient, t1: dict, s1: dict,
                                                open_exam: dict, question_ids: list[int]) -> None:
    """/answers 是唯一能读到学生作答与参考答案的入口，且只给创建者。"""
    rid = open_exam["record_id"]
    client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert "reference_answer" in client.get(f"/api/exams/{rid}/answers", headers=t1).text
    assert client.get(f"/api/exams/{rid}/score", headers=t1).text.count("correct_answer") == 0


# ------------------------------------------------------------------ TC-I-50
def test_redis_outage_fail_fast_on_write_and_reads_survive(
    client: TestClient, raw_client: TestClient, s1: dict, paper_id: int,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis 未起：写路径 500（含启动提示），读路径不受影响；恢复后幂等 start 救回记录。"""
    import redis as redis_lib

    from app import redis_client

    healthy = redis_client.get_redis()
    broken = redis_lib.Redis.from_url(
        "redis://127.0.0.1:6399/0", protocol=2, socket_connect_timeout=1, socket_timeout=1
    )
    monkeypatch.setattr(redis_client, "_client", broken)

    r = raw_client.post(f"/api/exams/{paper_id}/start", headers=s1)
    assert r.status_code == 500
    assert r.json()["code"] == "REDIS_UNAVAILABLE"
    assert "redis-server" in r.json()["detail"], "错误提示要给出可复制的启动命令"

    # 记录已经落库（DB 才是事实源）；恢复 Redis 后再次 start 走幂等分支找回
    listing = client.get("/api/exams", headers=s1)
    assert listing.status_code == 200 and listing.json()["total"] == 1
    rid = listing.json()["items"][0]["record_id"]
    assert client.get(f"/api/exams/{rid}/score", headers=s1).status_code == 200

    monkeypatch.setattr(redis_client, "_client", healthy)
    again = client.post(f"/api/exams/{paper_id}/start", headers=s1)
    assert again.status_code == 200 and again.json()["record_id"] == rid
    assert redis_client.session_ttl(rid) > 0


def test_health_reports_degraded_without_200_change(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 挂掉时 /health 仍是 200，只把 status 变成 degraded（liveness 语义）。"""
    import redis as redis_lib

    from app import redis_client

    healthy = redis_client.get_redis()
    monkeypatch.setattr(
        redis_client, "_client",
        redis_lib.Redis.from_url("redis://127.0.0.1:6399/0", protocol=2, socket_connect_timeout=1, socket_timeout=1),
    )
    body = client.get("/health")
    monkeypatch.setattr(redis_client, "_client", healthy)
    assert body.status_code == 200
    assert body.json()["deps"]["redis"] == "down"
    assert body.json()["status"] == "degraded"
    assert body.json()["deps"]["mysql"] == "ok"


# ------------------------------------------------------------------ TC-I-52
def test_grading_reads_snapshot_not_live_question(
    client: TestClient, t1: dict, s1: dict, paper_id: int, question_ids: list[int]
) -> None:
    """绕过 API 直接改题库答案：判分仍按判分基线（B6 的反向验证）。"""
    rid = client.post(f"/api/exams/{paper_id}/start", headers=s1).json()["record_id"]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[0], "answer": "A"}, headers=s1)

    engine = create_engine(TEST_URL)
    with engine.begin() as conn:  # 正常业务路径做不到这件事（EC-22 会 409）
        conn.execute(
            text("UPDATE questions SET correct_answer='B' WHERE id=:i"), {"i": question_ids[0]}
        )
    engine.dispose()

    body = client.post(f"/api/exams/{rid}/submit", headers=s1).json()
    assert body["auto_score"] == 5.0, "判分必须用开考时冻结的答案，而不是被改后的答案"
    # 而且改卷没有后门：PUT/DELETE /api/papers 不存在
    assert client.put(f"/api/papers/{paper_id}", json={}, headers=t1).status_code == 405
    assert client.delete(f"/api/papers/{paper_id}", headers=t1).status_code == 405
    assert client.delete(f"/api/questions/{question_ids[0]}", headers=t1).status_code == 409


# ------------------------------------------------------------------ TC-S 静态检查
def _app_sources() -> list[Path]:
    return sorted(p for p in APP_DIR.rglob("*.py"))


def _code_only(path: Path) -> str:
    """只保留代码：去掉注释与 docstring。

    否则解释性注释里出现的函数名会让静态检查误报（第一版就踩了：
    time_utils 的文档字符串写着禁用 NOW()，把检查自己判死了）。
    """
    import ast

    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        holder = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        if isinstance(node, holder) and node.body and ast.get_docstring(node, clean=False):
            stmt = node.body[0]
            doc_lines.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
    kept = []
    for lineno, line in enumerate(src.splitlines(), start=1):
        if lineno in doc_lines:
            continue
        kept.append(re.sub(r"#.*$", "", line))
    return "\n".join(kept)


def test_tc_s01_no_sql_time_functions() -> None:
    """N9/N13：SQL 里不得出现数据库端时间函数，否则注入的假时钟不生效。"""
    patterns = ("CURRENT_TIMESTAMP", "func.now", "NOW()", "SYSDATE(", "UTC_TIMESTAMP")
    offenders = []
    for path in _app_sources():
        code = _code_only(path)
        offenders += [f"{path.name}:{token}" for token in patterns if token in code]
    assert not offenders, offenders


def test_tc_s02_route_declaration_order_no_shadowing() -> None:
    """N2：字面量路由必须早于同层参数路由（实测反序会得到 422）。"""
    from app.main import app

    paths = [getattr(r, "path", "") for r in app.routes]
    literal = paths.index("/api/exams")
    param = paths.index("/api/exams/{record_id}")
    assert literal < param, (literal, param)
    # 同层不能残留会被遮蔽的 /api/exams/my
    assert "/api/exams/my" not in paths


def test_tc_s03_no_response_model_exposes_snapshot() -> None:
    """N4：任何响应模型都不得有 snapshot_json 字段（判分基线只活在 service 内部）。"""
    from app.main import app

    checked = 0
    for route in app.routes:
        model = getattr(route, "response_model", None)
        if model is None:
            continue
        checked += 1
        fields = set(getattr(model, "model_fields", {}) or {})
        assert not {"snapshot_json", "snapshot", "hashed_password"} & fields, (route.path, fields)
    assert checked > 15, checked


def test_tc_s04_migration_is_replayable() -> None:
    """C4/TC-S-04：同一套迁移能在另一 schema 上 upgrade → downgrade → upgrade。"""
    import pymysql
    from alembic import command
    from alembic.config import Config

    from tests.conftest import MYSQL_ARGS

    conn = pymysql.connect(**MYSQL_ARGS)
    with conn.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS exam_test_replay")
        cur.execute("CREATE DATABASE exam_test_replay CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn.close()

    url = TEST_URL.rsplit("/", 1)[0] + "/exam_test_replay?charset=utf8mb4"
    cfg = Config(str(APP_DIR.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_DIR.parent / "alembic"))
    import os

    os.environ["ALEMBIC_DATABASE_URL"] = url
    try:
        command.upgrade(cfg, "head")
        assert _tables(url), "迁移没有建出表"
        command.downgrade(cfg, "base")
        assert not _tables(url), "downgrade 没有清干净"
        command.upgrade(cfg, "head")
    finally:
        conn = pymysql.connect(**MYSQL_ARGS)
        with conn.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS exam_test_replay")
        conn.close()
        os.environ.pop("ALEMBIC_DATABASE_URL", None)


def _tables(url: str) -> list[str]:
    engine = create_engine(url)
    with engine.connect() as conn:
        names = [
            row[0]
            for row in conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()")
            )
        ]
    engine.dispose()
    return [n for n in names if n != "alembic_version"]


def test_tc_s05_indexes_and_uniques_exist() -> None:
    """D2 §4 的约束与索引必须真在库里；FK 列也要有索引支撑（InnoDB 硬性要求）。"""
    named = {
        "users": {"uq_users_username"},
        "categories": {"uq_categories_name"},
        "questions": {"ix_questions_category_type"},
        "papers": {"ix_papers_creator_status"},
        "paper_questions": {"uq_pq_paper_question"},
        "exam_records": {"uq_exam_paper_student", "ix_exam_status_deadline", "ix_exam_student_started"},
        "answers": {"uq_answers_record_question"},
    }
    fk_columns = {
        "questions": ("category_id", "created_by"),
        "papers": ("creator_id",),
        "paper_questions": ("paper_id", "question_id"),
        "exam_records": ("paper_id", "student_id"),
        "answers": ("record_id", "question_id"),
    }
    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        for table, names in named.items():
            rows = conn.execute(text(f"SHOW INDEX FROM {table}")).mappings().all()
            present = {r["Key_name"] for r in rows}
            assert names <= present, (table, names - present)
        for table, columns in fk_columns.items():
            rows = conn.execute(text(f"SHOW INDEX FROM {table}")).mappings().all()
            first_col = {r["Column_name"] for r in rows if r["Seq_in_index"] == 1}
            for column in columns:
                assert column in first_col, (table, column, first_col)
    engine.dispose()


def test_tc_s06_utf8mb4_chinese_roundtrip(client: TestClient, t1: dict) -> None:
    """中文与 4 字节字符（emoji）能原样存取（A5：客户端 gbk 是真坑）。"""
    text_probe = "下列哪些属于「事务隔离级别」？🙂 Ω≤∞"
    body = {"type": "single", "content": text_probe,
            "options": ["A. 读未提交 🙂", "B. 已提交", "C. 可重复读", "D. 串行化"],
            "correct_answer": "C", "difficulty": "medium"}
    qid = client.post("/api/questions", json=body, headers=t1).json()["id"]
    got = client.get(f"/api/questions/{qid}", headers=t1).json()
    assert got["content"] == text_probe
    assert got["options"][0] == "A. 读未提交 🙂"

    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        collation = conn.execute(
            text("SELECT table_collation FROM information_schema.tables "
                 "WHERE table_schema=DATABASE() AND table_name='questions'")
        ).scalar()
    engine.dispose()
    assert collation == "utf8mb4_unicode_ci"


def test_tc_s07_real_sql_mode_assumption() -> None:
    """本机 sql_mode 前提必须成立，否则统计用例的"绿"没有意义（B12）。"""
    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        mode = conn.execute(text("SELECT @@sql_mode")).scalar()
    engine.dispose()
    assert "ONLY_FULL_GROUP_BY" in mode, mode
    assert "STRICT_TRANS_TABLES" in mode, mode


def test_tc_s08_no_print_and_redis_is_resp2() -> None:
    """需求文档 8.4 禁 print；redis 客户端必须 RESP2（本机 Redis 5.0 不认 HELLO）。"""
    src = "\n".join(p.read_text(encoding="utf-8") for p in _app_sources())
    assert not re.search(r"^\s*print\(", src, flags=re.M), "生产代码里不许 print"
    assert "protocol=2" in (APP_DIR / "redis_client.py").read_text(encoding="utf-8")

    from app import redis_client

    kwargs = redis_client.get_redis().connection_pool.connection_kwargs
    assert kwargs.get("protocol") == 2, kwargs
    assert kwargs.get("socket_timeout") == 1


def test_all_error_codes_are_registered() -> None:
    """服务里 raise 的每个错误码都必须在 ERRORS 表里（实测漏一个就变 500 KeyError）。"""
    from app.exceptions import ERRORS

    used: set[str] = set()
    for path in _app_sources():
        used |= set(re.findall(r'AppError\.make\(\s*"([A-Z0-9_]+)"', path.read_text(encoding="utf-8")))
    assert used, "扫描没抓到任何错误码，检查正则"
    unknown = used - set(ERRORS)
    assert not unknown, f"未登记的错误码：{sorted(unknown)}"
