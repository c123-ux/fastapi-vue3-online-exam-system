"""TC-I-39~43：超时清算（惰性、并发、不误清、close 不影响进行中）。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient

from tests.conftest import FakeClock
from tests.integration.conftest import make_paper
from tests.integration.test_exam_flow import answer_rows, db_row


def _expire(clock: FakeClock, seconds: int = 700) -> None:
    """越过 deadline + 宽限（假时钟，不 sleep 真实时长，D3 §2.1）。"""
    clock.advance(seconds)


def test_student_score_view_triggers_settle(client: TestClient, s1: dict, open_exam: dict,
                                            question_ids: list[int], clock: FakeClock) -> None:
    """TC-I-39 超时未交卷，学生查成绩时惰性清算并出分（EC-06/07）。"""
    rid = open_exam["record_id"]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[0], "answer": "A"}, headers=s1)
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[3], "answer": ["A", "C"]}, headers=s1)
    _expire(clock)

    body = client.get(f"/api/exams/{rid}/score", headers=s1).json()
    assert body["status"] == "final" and body["submit_kind"] == "timeout"
    assert body["auto_score"] == 15.0
    record = db_row("exam_records", rid)
    assert record["submitted_at"] is not None
    assert float(record["earned_score"]) == 15.0


def test_teacher_stats_triggers_settle_and_counts_it(
    client: TestClient, t1: dict, s1: dict, paper_id: int, question_ids: list[int], clock: FakeClock, s2: dict
) -> None:
    """TC-I-40（B1 回归）超时者照常进入统计：reaped_count 与 finished_count 都对。"""
    r = client.post(f"/api/exams/{paper_id}/start", headers=s1).json()
    client.post(f"/api/exams/{r['record_id']}/answer",
                json={"question_id": question_ids[1], "answer": "B"}, headers=s1)
    _expire(clock)
    stats = client.get(f"/api/papers/{paper_id}/stats", headers=t1).json()
    assert stats["reaped_count"] == 1
    assert stats["finished_count"] == 1
    assert stats["timeout_count"] == 1
    assert stats["avg_auto_score"] == 5.0
    assert stats["avg_score"] == 5.0 and stats["avg_scored_count"] == 1


def test_concurrent_reap_only_one_settles(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """TC-I-41（EC-28）两线程同时清算同一条过期记录：只一方判分，answers 不重复、不 500。"""
    from app.database import SessionLocal
    from app.models import ExamRecord, User
    from app.services import exam_svc

    rid = open_exam["record_id"]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[0], "answer": "A"}, headers=s1)
    _expire(clock)

    def worker() -> str:
        db = SessionLocal()
        try:
            rec = db.get(ExamRecord, rid)
            done = exam_svc.settle_as_timeout(db, rec)
            return "settled" if done else "noop"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(worker), pool.submit(worker)]]
    assert sorted(results) == ["noop", "settled"], results
    rows = answer_rows(rid)
    assert len(rows) == 10 and len({r["question_id"] for r in rows}) == 10
    assert float(db_row("exam_records", rid)["auto_score"]) == 5.0


def test_unexpired_record_is_not_reaped(
    client: TestClient, s1: dict, open_exam: dict, clock: FakeClock
) -> None:
    """TC-I-42（T12）只过了 3 分钟（10 分钟考试）→ 读路径不得误清算。"""
    rid = open_exam["record_id"]
    clock.advance(180)
    body = client.get(f"/api/exams/{rid}/score", headers=s1)
    assert body.status_code == 200
    assert body.json()["status"] == "in_progress"
    assert db_row("exam_records", rid)["submit_kind"] is None


def test_grace_window_still_normal_submit(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """EC-05/06 边界：宽限内 normal，宽限外 timeout（30 秒可配）。"""
    rid = open_exam["record_id"]
    clock.advance(620)  # deadline 600，宽限 30 → 620 仍在宽限内
    body = client.post(f"/api/exams/{rid}/submit", headers=s1).json()
    assert body["submit_kind"] == "normal"


def test_close_does_not_interrupt_running_exam(
    client: TestClient, t1: dict, s1: dict, s2: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """TC-I-43（B7/EC-03）close 只拦新开考，已开始的按原 deadline 正常交卷。"""
    pid = make_paper(client, t1, question_ids, clock.now(), duration=10)
    rid = client.post(f"/api/exams/{pid}/start", headers=s1).json()["record_id"]
    assert client.post(f"/api/papers/{pid}/close", headers=t1).status_code == 200
    assert client.post(f"/api/exams/{pid}/start", headers=s2).status_code == 400

    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[0], "answer": "A"}, headers=s1)
    r = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert r.status_code == 200 and r.json()["submit_kind"] == "normal"
    assert float(r.json()["auto_score"]) == 5.0


def test_deadline_capped_by_exam_window(
    client: TestClient, t1: dict, s1: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """TC-I-51 / B7：只剩 8 分钟窗口时开 60 分钟的卷子，deadline 被 end_at 截断。"""
    body = {
        "title": "窗口截断",
        "duration_minutes": 60,
        "start_at": (clock.now() - timedelta(hours=1)).isoformat(),
        "end_at": (clock.now() + timedelta(minutes=8)).isoformat(),
        "questions": [{"question_id": q, "score": 5} for q in question_ids],
    }
    pid = client.post("/api/papers", json=body, headers=t1).json()["id"]
    assert client.post(f"/api/papers/{pid}/publish", headers=t1).status_code == 200
    r = client.post(f"/api/exams/{pid}/start", headers=s1)
    assert r.status_code == 200
    data = r.json()
    assert data["remaining_seconds"] == 8 * 60
    assert data["deadline_at"][:16] == (clock.now() + timedelta(minutes=8)).isoformat()[:16]


def test_window_too_short_blocks_start(
    client: TestClient, t1: dict, s1: dict, question_ids: list[int], clock: FakeClock
) -> None:
    """TC-I-51（EC-31/N7）窗口只剩 20 秒 → 不允许开考，避免"点开即超时"的 0 分记录。"""
    from datetime import timedelta

    body = {
        "title": "来不及考",
        "duration_minutes": 10,
        "start_at": (clock.now() - timedelta(hours=1)).isoformat(),
        "end_at": (clock.now() + timedelta(seconds=20)).isoformat(),
        "questions": [{"question_id": q, "score": 5} for q in question_ids],
    }
    pid = client.post("/api/papers", json=body, headers=t1).json()["id"]
    assert client.post(f"/api/papers/{pid}/publish", headers=t1).status_code == 200
    r = client.post(f"/api/exams/{pid}/start", headers=s1)
    assert r.status_code == 400 and r.json()["code"] == "EXAM_WINDOW_TOO_SHORT"
