"""TC-I-14~28：开考、限时会话、续考不续时（A7 回归）、作答守卫、找回答、切屏。"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL, FakeClock
from tests.integration.conftest import make_paper


def db_row(table: str, row_id: int) -> dict[str, Any]:
    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {table} WHERE id = :i"), {"i": row_id}
        ).mappings().first()
    engine.dispose()
    return dict(row) if row else {}


def answer_rows(record_id: int) -> list[dict[str, Any]]:
    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT question_id, student_answer, score, review_status FROM answers "
                "WHERE record_id = :r ORDER BY question_id"
            ),
            {"r": record_id},
        ).mappings().all()
    engine.dispose()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ T1
def test_start_creates_record_snapshot_and_session(
    client: TestClient, s1: dict, open_exam: dict
) -> None:
    """TC-I-14 开考成功：DB 有记录、Redis 会话带 TTL、响应不含答案。"""
    from app import redis_client

    rid = open_exam["record_id"]
    record = db_row("exam_records", rid)
    assert record["status"] == "in_progress" and record["submit_kind"] is None
    assert record["deadline_at"] == record["started_at"] + timedelta(minutes=10)

    ttl = redis_client.session_ttl(rid)
    assert 600 <= ttl <= 630, f"会话必须原子带上 TTL（实测漏 EXPIRE 会得到 -1，永不超时）：{ttl}"

    assert open_exam["remaining_seconds"] == 600 and open_exam["resumed"] is False
    assert all("correct_answer" not in q for q in open_exam["questions"])
    assert "snapshot" not in str(open_exam)


def test_start_blocked_by_window_and_status(
    client: TestClient, t1: dict, s1: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """TC-I-15 未开始 / 已结束 / 已关闭 / 未发布 / 不存在（EC-01~04）。"""
    future_window = {
        "start_at": (clock.now() + timedelta(hours=1)).isoformat(),
        "end_at": (clock.now() + timedelta(hours=5)).isoformat(),
    }
    pid = _paper(client, t1, question_ids, future_window, 30)
    r = client.post(f"/api/exams/{pid}/start", headers=s1)
    assert r.status_code == 400 and r.json()["code"] == "EXAM_NOT_STARTED"

    past_pid = make_paper(client, t1, question_ids, clock.now(), duration=10)
    clock.advance(11 * 3600)
    r = client.post(f"/api/exams/{past_pid}/start", headers=s1)
    assert r.status_code == 400 and r.json()["code"] == "EXAM_ENDED"

    closed_pid = make_paper(client, t1, question_ids, clock.now(), duration=10)
    assert client.post(f"/api/papers/{closed_pid}/close", headers=t1).status_code == 200
    r = client.post(f"/api/exams/{closed_pid}/start", headers=s1)
    assert r.status_code == 400 and r.json()["code"] == "EXAM_CLOSED"

    draft_pid = make_paper(client, t1, question_ids, clock.now(), publish=False)
    r = client.post(f"/api/exams/{draft_pid}/start", headers=s1)
    assert r.status_code == 404 and r.json()["code"] == "PAPER_NOT_VISIBLE"
    r = client.post("/api/exams/999999/start", headers=s1)
    assert r.status_code == 404 and r.json()["code"] == "PAPER_NOT_VISIBLE"


def _paper(client: TestClient, t1: dict, qids: list[int], window: dict, duration: int) -> int:
    body = {
        "title": "窗口用例",
        "duration_minutes": duration,
        **window,
        "questions": [{"question_id": q, "score": 5} for q in qids],
    }
    pid = client.post("/api/papers", json=body, headers=t1).json()["id"]
    assert client.post(f"/api/papers/{pid}/publish", headers=t1).status_code == 200
    return pid


# ------------------------------------------------------------------ T2/T3（A7）
def test_repeated_start_never_extends_time(
    client: TestClient, s1: dict, open_exam: dict, clock: FakeClock
) -> None:
    """TC-I-16 连续三次 start 并中间流逝时间：deadline 不变、remaining 单调不增。"""
    rid = open_exam["record_id"]
    before = db_row("exam_records", rid)["deadline_at"]
    seen = [open_exam["remaining_seconds"]]
    for _ in range(3):
        clock.advance(45)
        r = client.post(f"/api/exams/{open_exam['paper_id']}/start", headers=s1)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["record_id"] == rid, "幂等分支必须返回同一条记录"
        assert body["resumed"] is True
        seen.append(body["remaining_seconds"])
    assert seen == [600, 555, 510, 465], seen
    assert db_row("exam_records", rid)["deadline_at"] == before


def test_session_rebuild_only_shrinks(
    client: TestClient, s1: dict, open_exam: dict, clock: FakeClock
) -> None:
    """TC-I-18 会话丢失且未超期 → 按剩余时间重建，TTL 只减不增（D2 §2.4）。"""
    from app import redis_client

    rid = open_exam["record_id"]
    original = redis_client.session_ttl(rid)
    redis_client.delete_session(rid)
    assert redis_client.session_ttl(rid) == -2

    clock.advance(120)
    r = client.post(f"/api/exams/{open_exam['paper_id']}/start", headers=s1)
    assert r.status_code == 200
    rebuilt = redis_client.session_ttl(rid)
    assert 0 < rebuilt < original, (original, rebuilt)
    assert rebuilt == 510 and r.json()["remaining_seconds"] == 480


def test_ttl_minus_one_anomaly_is_repaired(
    client: TestClient, s1: dict, open_exam: dict
) -> None:
    """TC-I-20 会话存在但 TTL=-1（漏 EXPIRE 异常态）→ 按 DB 重设（EC-30）。"""
    from app import redis_client

    rid = open_exam["record_id"]
    redis_client.delete_session(rid)
    redis_client.get_redis().set(redis_client.SESSION_KEY.format(rid=rid), '{"x":1}')
    assert redis_client.session_ttl(rid) == -1

    r = client.post(f"/api/exams/{open_exam['paper_id']}/start", headers=s1)
    assert r.status_code == 200
    assert redis_client.session_ttl(rid) > 0, "TTL=-1 不能当作会话有效放过"


def test_session_lost_after_deadline_settles_as_timeout(
    client: TestClient, s1: dict, open_exam: dict, clock: FakeClock
) -> None:
    """TC-I-19 会话丢失且已过宽限 → 不重建，开考动作兜底清算（A3c）。"""
    from app import redis_client

    rid = open_exam["record_id"]
    redis_client.delete_session(rid)
    clock.advance(700)
    r = client.post(f"/api/exams/{open_exam['paper_id']}/start", headers=s1)
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_SUBMITTED"
    record = db_row("exam_records", rid)
    assert record["status"] == "final" and record["submit_kind"] == "timeout"
    assert redis_client.session_ttl(rid) == -2, "清算后不该再写会话"


# ------------------------------------------------------------------ T4/T5
def test_answer_upsert_and_guards(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-21/22/25 同题覆盖保存（upsert）、越界题目 400、已交卷记录 409。"""
    rid = open_exam["record_id"]
    url = f"/api/exams/{rid}/answer"
    q1, q2 = question_ids[0], question_ids[1]
    for value in ("A", "B", "C"):
        r = client.post(url, json={"question_id": q1, "answer": value}, headers=s1)
        assert r.status_code == 200, r.text
    rows = answer_rows(rid)
    assert len(rows) == 1 and rows[0]["student_answer"] == "C"
    assert client.post(url, json={"question_id": q1, "answer": "C"}, headers=s1).json()["answered_count"] == 1

    foreign = client.post(url, json={"question_id": 999999, "answer": "A"}, headers=s1)
    assert foreign.status_code == 400 and foreign.json()["code"] == "QUESTION_NOT_IN_PAPER"

    assert client.post(f"/api/exams/{rid}/submit", headers=s1).status_code == 200
    late = client.post(url, json={"question_id": q2, "answer": "B"}, headers=s1)
    assert late.status_code == 409 and late.json()["code"] == "NOT_IN_PROGRESS"


def test_answer_accepts_bool_and_list_forms(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """判断接受 true/对，多选接受数组与逗号串（D2 §6.1 规范化）。"""
    rid = open_exam["record_id"]
    q5, q6 = question_ids[4], question_ids[5]
    assert client.post(f"/api/exams/{rid}/answer", json={"question_id": q5, "answer": ["D", "B"]}, headers=s1).status_code == 200
    assert client.post(f"/api/exams/{rid}/answer", json={"question_id": q6, "answer": True}, headers=s1).status_code == 200
    got = {a["question_id"]: a["student_answer"] for a in answer_rows(rid)}
    assert got[q5] == "B,D" and got[q6] == "T"


def test_answer_after_grace_settles_then_rejects(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """TC-I-24（EC-11）超宽限作答：先清算成 timeout，再拒绝这次写入。"""
    rid = open_exam["record_id"]
    q1 = question_ids[0]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": q1, "answer": "A"}, headers=s1)
    clock.advance(640)
    r = client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[1], "answer": "B"}, headers=s1)
    assert r.status_code == 409 and r.json()["code"] == "EXAM_TIME_UP"
    record = db_row("exam_records", rid)
    assert record["submit_kind"] == "timeout"
    assert float(record["auto_score"]) == 5.0, "清算按已保存的 Q1 判分"
    assert len(answer_rows(rid)) == 10, "判分补齐覆盖全卷"


def test_answer_inside_grace_is_normal_submit(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """TC-I-23（EC-05）宽限期内提交：submit_kind=normal。"""
    rid = open_exam["record_id"]
    clock.advance(615)  # 已过 deadline，仍在 30 秒宽限内
    r = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert r.status_code == 200 and r.json()["submit_kind"] == "normal"


# ------------------------------------------------------------------ 找回与越权
def test_resume_payload_restores_answers(
    client: TestClient, s1: dict, s2: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-26/TC-S-02 列表与详情两条路由同时可达（N2），换设备能找回作答。"""
    rid = open_exam["record_id"]
    q4 = question_ids[3]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": q4, "answer": ["A", "C"]}, headers=s1)

    listing = client.get("/api/exams", headers=s1)
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["record_id"] == rid

    resume = client.get(f"/api/exams/{rid}", headers=s1)
    assert resume.status_code == 200, resume.text
    data = resume.json()
    assert data["answers"] == {str(q4): "A,C"} and data["resumed"] is True
    assert len(data["questions"]) == 10
    assert all("correct_answer" not in q for q in data["questions"])


def test_cross_student_access_returns_identical_404(
    client: TestClient, s1: dict, s2: dict, open_exam: dict
) -> None:
    """TC-I-27（B-14）跨用户访问与"不存在"返回完全同形。"""
    rid = open_exam["record_id"]
    assert client.get(f"/api/exams/{rid}/score", headers=s1).status_code == 200
    other = client.get(f"/api/exams/{rid}/score", headers=s2)
    ghost = client.get("/api/exams/987654/score", headers=s2)
    assert other.status_code == ghost.status_code == 404
    assert other.json() == ghost.json()
    assert client.get(f"/api/exams/{rid}", headers=s2).status_code == 404
    assert client.post(f"/api/exams/{rid}/submit", headers=s2).status_code == 404


def test_cheat_report_counts_in_db_only(
    client: TestClient, s1: dict, s2: dict, open_exam: dict
) -> None:
    """TC-I-28（B14）切屏只写 DB：不建 Redis 计数键，他人 404、已交卷 409。"""
    from app import redis_client

    rid = open_exam["record_id"]
    for expected in (1, 2, 3):
        r = client.post(
            f"/api/exams/{rid}/cheat-report", json={"reason": "visibilitychange"}, headers=s1
        )
        assert r.status_code == 200 and r.json()["cheat_count"] == expected
    assert db_row("exam_records", rid)["cheat_count"] == 3
    assert redis_client.get_redis().keys("cheat:*") == []
    assert client.post(f"/api/exams/{rid}/cheat-report", json={}, headers=s2).status_code == 404

    assert client.post(f"/api/exams/{rid}/submit", headers=s1).status_code == 200
    after = client.post(f"/api/exams/{rid}/cheat-report", json={}, headers=s1)
    assert after.status_code == 409 and db_row("exam_records", rid)["cheat_count"] == 3
