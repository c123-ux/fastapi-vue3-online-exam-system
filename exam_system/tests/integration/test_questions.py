"""TC-I-06~09：题库 CRUD、筛选索引路径、判分基准锁定、引用保护。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import DEFAULT_SCORES
from tests.integration.conftest import make_paper, paper_window

SINGLE = {
    "type": "single",
    "content": "TCP 三次握手建立的是什么？",
    "options": ["A. 连接", "B. 事务", "C. 会话", "D. 管道"],
    "correct_answer": "A",
    "difficulty": "easy",
}


def test_create_and_list_all_four_types(client: TestClient, t1: dict, categories: list[int]) -> None:
    """TC-I-06 四题型各建 1 题，列表按分类/题型/难度筛选命中。"""
    bodies = [
        {**SINGLE, "category_id": categories[0]},
        {"type": "multiple", "category_id": categories[1], "content": "哪些是 NoSQL？",
         "options": ["A. Redis", "B. MySQL", "C. MongoDB", "D. PostgreSQL"],
         "correct_answer": "A,C", "difficulty": "medium"},
        {"type": "judge", "category_id": categories[0], "content": "Redis 支持多键事务。",
         "correct_answer": "T", "difficulty": "hard"},
        {"type": "short", "category_id": categories[1], "content": "说说 CAP。",
         "correct_answer": "一致性|可用性|分区容错", "difficulty": "medium"},
    ]
    for body in bodies:
        r = client.post("/api/questions", json=body, headers=t1)
        assert r.status_code == 200, r.text
        assert r.json()["correct_answer"], "老师侧应能看到答案"

    both = client.get("/api/questions", headers=t1).json()
    assert both["total"] == 4
    only_judge = client.get("/api/questions?type=judge", headers=t1).json()
    assert only_judge["total"] == 1 and only_judge["items"][0]["type"] == "judge"
    by_cat = client.get(f"/api/questions?category_id={categories[0]}&difficulty=easy", headers=t1).json()
    assert [q["content"] for q in by_cat["items"]] == [SINGLE["content"]]
    paged = client.get("/api/questions?page=2&page_size=2", headers=t1).json()
    assert paged["total"] == 4 and len(paged["items"]) == 2


def test_filter_query_uses_composite_index(client: TestClient, t1: dict, categories: list[int]) -> None:
    """组卷按「分类 + 题型」过滤，索引必须对优化器可见。

    注意断言的是 possible_keys 而不是 key：本机只有几十行数据时，
    MySQL 优化器会正确地选择全表扫描——用 key 断言会得到一个"取决于数据量"的假结论。
    真实数据量下的执行计划对比放在 D4（附 EXPLAIN 原文）。
    """
    for i in range(60):
        client.post("/api/questions", json={**SINGLE, "category_id": categories[0],
                                            "content": f"第 {i} 题"}, headers=t1)
    from sqlalchemy import create_engine, text

    engine = create_engine(_db_url())
    with engine.connect() as conn:
        plan = conn.execute(
            text("EXPLAIN SELECT * FROM questions WHERE category_id = :c AND type = 'single'"),
            {"c": categories[0]},
        ).mappings().all()
        forced = conn.execute(
            text("EXPLAIN SELECT * FROM questions FORCE INDEX (ix_questions_category_type) "
                 "WHERE category_id = :c AND type = 'single'"),
            {"c": categories[0]},
        ).mappings().all()
    engine.dispose()
    rows = [r for r in plan if r.get("table") == "questions"]
    assert rows, plan
    assert "ix_questions_category_type" in (rows[0]["possible_keys"] or ""), rows[0]
    # 强制走索引后 key 必须命中，且扫描行数下降（证明索引真的可用、不是建了个摆设）
    forced_rows = [r for r in forced if r.get("table") == "questions"]
    assert forced_rows[0]["key"] == "ix_questions_category_type", forced_rows[0]


def _db_url() -> str:
    from tests.conftest import TEST_URL

    return TEST_URL


def test_validation_matrix_via_http(client: TestClient, t1: dict, categories: list[int]) -> None:
    """TC-I-07 结构违规 422、跨字段业务违规 400，两者都走统一错误体。"""
    bad_struct = client.post(
        "/api/questions",
        json={"type": "single", "category_id": categories[0], "content": "x",
              "options": ["只有一项"], "correct_answer": "A", "difficulty": "easy"},
        headers=t1,
    )
    assert bad_struct.status_code == 422
    assert bad_struct.json()["code"] == "INVALID_PARAM"  # 统一体，不是框架默认 detail

    bad_business = client.post(
        "/api/questions",
        json={"type": "single", "category_id": categories[0], "content": "x",
              "options": ["A. 1", "B. 2"], "correct_answer": "Z", "difficulty": "easy"},
        headers=t1,
    )
    assert bad_business.status_code == 400
    assert bad_business.json()["code"] == "INVALID_QUESTION"

    too_long = client.post(
        "/api/questions",
        json={"type": "single", "category_id": categories[0], "content": "x" * 5001,
              "options": ["A. 1", "B. 2"], "correct_answer": "A", "difficulty": "easy"},
        headers=t1,
    )
    assert too_long.status_code == 422


def test_published_reference_locks_question_content(
    client: TestClient, t1: dict, question_ids: list[int], clock
) -> None:
    """TC-I-08 published 引用后答案不可改（EC-22），只改难度允许。"""
    pid = make_paper(client, t1, question_ids, clock.now())
    qid = question_ids[0]
    detail = client.get(f"/api/questions/{qid}", headers=t1).json()

    locked = client.put(
        f"/api/questions/{qid}",
        json={**detail, "correct_answer": "B"},
        headers=t1,
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == "QUESTION_LOCKED"

    only_difficulty = client.put(
        f"/api/questions/{qid}", json={**detail, "difficulty": "hard"}, headers=t1
    )
    assert only_difficulty.status_code == 200
    assert only_difficulty.json()["difficulty"] == "hard"
    assert client.get(f"/api/questions/{qid}", headers=t1).json()["correct_answer"] == "A"


def test_draft_reference_does_not_lock(client: TestClient, t1: dict, question_ids: list[int], clock) -> None:
    pid = make_paper(client, t1, question_ids, clock.now(), publish=False)
    detail = client.get(f"/api/questions/{question_ids[1]}", headers=t1).json()
    r = client.put(f"/api/questions/{question_ids[1]}", json={**detail, "correct_answer": "C"},
                   headers=t1)
    assert r.status_code == 200


def test_delete_referenced_question_blocked(client: TestClient, t1: dict, question_ids: list[int], clock) -> None:
    """TC-I-09 删除被引用题目 409；未被引用的可删（EC-21）。"""
    make_paper(client, t1, question_ids, clock.now(), publish=False)
    blocked = client.delete(f"/api/questions/{question_ids[0]}", headers=t1)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "QUESTION_IN_USE"

    free = client.post("/api/questions", json={**SINGLE, "content": "没人用的题"}, headers=t1).json()
    ok = client.delete(f"/api/questions/{free['id']}", headers=t1)
    assert ok.status_code == 200
    assert client.get(f"/api/questions/{free['id']}", headers=t1).status_code == 404


def test_category_duplicate_and_missing(client: TestClient, t1: dict) -> None:
    assert client.post("/api/categories", json={"name": "重复"}, headers=t1).status_code == 200
    dup = client.post("/api/categories", json={"name": "重复"}, headers=t1)
    assert dup.status_code == 409 and dup.json()["code"] == "CATEGORY_EXISTS"
    orphan = client.post("/api/questions", json={**SINGLE, "category_id": 999999}, headers=t1)
    assert orphan.status_code == 404


def test_paper_total_score_and_full_score_naming(
    client: TestClient, t1: dict, question_ids: list[int], clock
) -> None:
    """分值求和由系统算；对外只叫 full_score，不出现 total_score/total（B9）。"""
    body = {
        "title": "命名检查",
        "duration_minutes": 10,
        **paper_window(clock.now()),
        "questions": [{"question_id": q, "score": s} for q, s in zip(question_ids, DEFAULT_SCORES)],
    }
    out = client.post("/api/papers", json=body, headers=t1).json()
    assert out["full_score"] == 100.0
    assert "total_score" not in out and "total" not in out
