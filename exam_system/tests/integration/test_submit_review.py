"""TC-I-29~38：提交判分、防重复提交（含并发）、批改闭环。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import DEFAULT_SCORES, FakeClock
from tests.integration.test_exam_flow import answer_rows, db_row

# 固定题集的正确答案（Q1..Q7, Q9, Q10 客观；Q8 简答）
CORRECT = {0: "A", 1: "B", 2: "C", 3: ["A", "C"], 4: ["B", "D"], 5: "T", 6: "F", 8: "D", 9: ["A", "B", "C"]}


def submit_all_correct(client: TestClient, headers: dict, rid: int, qids: list[int],
                       *, skip: set[int] | None = None, short: str | None = "事务有四个特性") -> dict:
    for idx, ans in CORRECT.items():
        if idx in (skip or set()):
            continue
        r = client.post(f"/api/exams/{rid}/answer", json={"question_id": qids[idx], "answer": ans}, headers=headers)
        assert r.status_code == 200, r.text
    if short is not None:
        client.post(f"/api/exams/{rid}/answer", json={"question_id": qids[7], "answer": short}, headers=headers)
    return client.post(f"/api/exams/{rid}/submit", headers=headers)


def test_sample_s3_mixed_submit(client: TestClient, s1: dict, open_exam: dict, question_ids: list[int]) -> None:
    """TC-I-29 少选/错选/多选/漏答混合：earned=30，无待批即定稿。"""
    rid = open_exam["record_id"]
    answers = {0: "A", 2: "C", 3: ["A"], 4: ["B", "D"], 5: "对", 6: "F", 8: "A", 9: ["A", "B", "C", "D"]}
    for idx, ans in answers.items():
        client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[idx], "answer": ans}, headers=s1)
    r = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auto_score"] == 30.0 and body["earned_score"] == 30.0
    assert body["full_score"] == 100.0
    assert body["pending_review_count"] == 0 and body["review_state"] == "final"
    assert body["submit_kind"] == "normal" and body["status"] == "final"
    assert len(answer_rows(rid)) == 10, "判分必须补齐全卷"


def test_sample_s1_with_short_goes_pending(client: TestClient, s1: dict, open_exam: dict, question_ids: list[int]) -> None:
    """TC-I-30 客观全对 70 分 + 简答已答 → 待批，未定稿。"""
    body = submit_all_correct(client, s1, open_exam["record_id"], question_ids).json()
    assert body["auto_score"] == 70.0
    assert body["pending_review_count"] == 1
    assert body["review_state"] == "pending"
    assert body["earned_score"] == 70.0


def test_blank_paper_submit(client: TestClient, s1: dict, open_exam: dict) -> None:
    """TC-I-31 空白卷提交：0 分、全题补齐、直接定稿。"""
    rid = open_exam["record_id"]
    r = client.post(f"/api/exams/{rid}/submit", headers=s1)
    body = r.json()
    assert body["auto_score"] == 0.0 and body["review_state"] == "final"
    rows = answer_rows(rid)
    assert len(rows) == 10
    assert [x for x in rows if x["review_status"] == "skipped"][0]["score"] == pytest.approx(0.0)


def test_duplicate_submit_is_409_and_carries_first_result(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-32 第二次提交 409，携带首次时间与得分，不产生第二份成绩。"""
    rid = open_exam["record_id"]
    first = submit_all_correct(client, s1, rid, question_ids).json()
    second = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "ALREADY_SUBMITTED"
    assert body["detail"]["record_id"] == rid
    assert body["detail"]["earned_score"] == first["earned_score"]
    assert body["detail"]["submitted_at"]
    assert db_row("exam_records", rid)["submitted_at"] is not None
    # 再开考也被拒（T3）
    assert client.post(f"/api/exams/{open_exam['paper_id']}/start", headers=s1).status_code == 409


def test_concurrent_double_submit_only_one_wins(
    client: TestClient, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-33（FR-10 / B2）service 层并发双提交：恰一次成功、一次 409、只一份判分结果。"""
    from app.database import SessionLocal
    from app.exceptions import AppError
    from app.models import User
    from app.services import exam_svc

    rid = open_exam["record_id"]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[0], "answer": "A"}, headers=s1)
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[3], "answer": ["A", "C"]}, headers=s1)

    def worker() -> str:
        db = SessionLocal()
        try:
            student = db.query(User).filter(User.username == "stu01").first()
            exam_svc.submit_exam(db, student, rid)
            db.commit()
            return "ok"
        except AppError as exc:
            db.rollback()
            return exc.code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(future.result() for future in [pool.submit(worker), pool.submit(worker)])
    assert results == ["ALREADY_SUBMITTED", "ok"], results
    assert float(db_row("exam_records", rid)["auto_score"]) == 15.0
    assert len(answer_rows(rid)) == 10


def test_grading_failure_leaves_student_retryable(
    client: TestClient, raw_client: TestClient, s1: dict, open_exam: dict,
    question_ids: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-I-34（B3）判分中途抛异常：状态回滚、Redis 标记未置、学生重试可成功。"""
    from app import redis_client
    from app.services import grade_svc

    rid = open_exam["record_id"]
    client.post(f"/api/exams/{rid}/answer", json={"question_id": question_ids[0], "answer": "A"}, headers=s1)

    def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("判分引擎炸了")

    monkeypatch.setattr(grade_svc, "grade_paper", boom)
    failed = raw_client.post(f"/api/exams/{rid}/submit", headers=s1)
    monkeypatch.undo()

    assert failed.status_code == 500
    assert db_row("exam_records", rid)["status"] == "in_progress", "判分失败必须整体回滚"
    assert redis_client.submit_marked(rid) is False, "commit 失败前不得置提交标记"
    ok = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert ok.status_code == 200 and ok.json()["auto_score"] == 5.0


def test_score_view_has_no_answers(
    client: TestClient, s1: dict, s2: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-35 学生成绩视图：有得分与待批数，没有答案也没有 student_answer。"""
    rid = open_exam["record_id"]
    submit_all_correct(client, s1, rid, question_ids)
    body = client.get(f"/api/exams/{rid}/score", headers=s1).json()
    assert body["auto_score"] == 70.0 and body["full_score"] == 100.0
    assert body["pending_review_count"] == 1 and body["is_finalized"] is False
    assert len(body["details"]) == 10
    text = str(body)
    for banned in ("correct_answer", "reference_answer", "student_answer", "事务有四个特性"):
        assert banned not in text, banned
    # 交卷后回看续考响应：不再显示倒计时（避免"以为还能继续考"）
    resume = client.get(f"/api/exams/{rid}", headers=s1).json()
    assert resume["remaining_seconds"] == 0
    listed = client.get("/api/exams", headers=s1).json()["items"][0]
    assert listed["remaining_seconds"] == 0 and listed["status"] == "final"


def test_review_flow_finalizes_score(
    client: TestClient, t1: dict, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-36（T9）批改 24 分 → earned=94、review_state=final；非简答与越界被拒。"""
    rid = open_exam["record_id"]
    submit_all_correct(client, s1, rid, question_ids)

    bad_type = client.post(f"/api/exams/{rid}/review", json={"question_id": question_ids[0], "score": 5}, headers=t1)
    assert bad_type.status_code == 400 and bad_type.json()["code"] == "NOT_SHORT_QUESTION"
    too_much = client.post(f"/api/exams/{rid}/review", json={"question_id": question_ids[7], "score": 31}, headers=t1)
    assert too_much.status_code == 400 and too_much.json()["code"] == "INVALID_SCORE"

    ok = client.post(
        f"/api/exams/{rid}/review",
        json={"question_id": question_ids[7], "score": 24, "comment": "要点齐全"},
        headers=t1,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["earned_score"] == 94.0 and body["pending_review_count"] == 0
    assert body["review_state"] == "final"
    score = client.get(f"/api/exams/{rid}/score", headers=s1).json()
    assert score["manual_score"] == 24.0 and score["is_finalized"] is True
    short_detail = [d for d in score["details"] if d["type"] == "short"][0]
    assert short_detail["score"] == 24.0 and short_detail["review_comment"] == "要点齐全"


def test_only_creator_can_read_and_review(
    client: TestClient, t1: dict, t2: dict, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-37 读作答/批改：非创建者老师 404，学生 403（角色不符优先）。"""
    rid = open_exam["record_id"]
    submit_all_correct(client, s1, rid, question_ids)
    assert client.get(f"/api/exams/{rid}/answers", headers=t2).status_code == 404
    assert client.post(f"/api/exams/{rid}/review", json={"question_id": question_ids[7], "score": 1},
                       headers=t2).status_code == 404
    assert client.get(f"/api/exams/{rid}/answers", headers=s1).status_code == 403
    assert client.get(f"/api/exams/{rid}/answers", headers=t1).status_code == 200


def test_answers_endpoint_exposes_student_text(
    client: TestClient, t1: dict, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-37b（A6 闭环）批改入口能看到学生写了什么 + 参考答案。"""
    rid = open_exam["record_id"]
    submit_all_correct(client, s1, rid, question_ids, short="原子性、一致性、隔离性、持久性")
    body = client.get(f"/api/exams/{rid}/answers", headers=t1).json()
    assert body["student"]["username"] == "stu01"
    assert body["pending_review_count"] == 1
    short_item = [i for i in body["items"] if i["type"] == "short"][0]
    assert short_item["student_answer"] == "原子性、一致性、隔离性、持久性"
    assert short_item["review_status"] == "needs_review"
    assert short_item["full_score"] == 30.0


def test_concurrent_review_does_not_lose_update(
    client: TestClient, t1: dict, s1: dict, open_exam: dict, question_ids: list[int]
) -> None:
    """TC-I-38（B11）并发批改同一题：FOR UPDATE 串行化，最终 earned 与库内分数一致。"""
    from app.database import SessionLocal
    from app.models import User
    from app.services import exam_svc

    rid = open_exam["record_id"]
    submit_all_correct(client, s1, rid, question_ids)

    def review(value: int) -> float:
        db = SessionLocal()
        try:
            teacher = db.query(User).filter(User.username == "teacher01").first()
            return float(exam_svc.review_answer(db, teacher, rid, question_ids[7], value, None)["earned_score"])
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        finals = [f.result() for f in [pool.submit(review, v) for v in (10, 20, 30)]]
    last = float(db_row("exam_records", rid)["earned_score"])
    assert last in finals, finals
    assert last == 70.0 + float([a for a in answer_rows(rid) if a["review_status"] == "reviewed"][0]["score"])
    assert sorted(finals) == [80.0, 90.0, 100.0], finals
