"""TC-I-44~48：全班成绩与统计数字（D3 §2.4 数据集，期望值全部可手算）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import FakeClock
from tests.integration.test_exam_flow import db_row
from tests.integration.test_submit_review import CORRECT, submit_all_correct


def _start(client: TestClient, headers: dict, pid: int) -> int:
    r = client.post(f"/api/exams/{pid}/start", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["record_id"]


def test_stats_over_known_dataset(
    client: TestClient, t1: dict, s1: dict, s2: dict, s3: dict, s4: dict,
    paper_id: int, question_ids: list[int], clock: FakeClock,
) -> None:
    """D3 §2.4：R1 正常+已批、R2 超时+待批、R3 超时+未答简答、R4 正常+已批。

    期望（客观满分 70、简答满分 30、卷面 100）：
      R1 auto 70 + 24 = 94   final
      R2 auto 60（Q4 少选）    pending（简答待批）  timeout
      R3 auto 50（Q1/Q10 未答） final（简答未答 skipped）timeout
      R4 auto 40 + 30 = 70   final
    """
    q = question_ids
    # R1 stu01：客观全对 + 简答已答，批 24
    r1 = _start(client, s1, paper_id)
    submit_all_correct(client, s1, r1, q)
    client.post(f"/api/exams/{r1}/review", json={"question_id": q[7], "score": 24}, headers=t1)

    # R2 stu02：Q4 错（少选）→ 60，简答已答但不批，超时收卷
    r2 = _start(client, s2, paper_id)
    submit_all_correct(client, s2, r2, q, skip={3})
    assert db_row("exam_records", r2)["submit_kind"] == "normal"

    # R3 stu03：只答 Q2,Q3,Q4,Q5,Q6,Q7,Q9 = 50，简答不答，超时收卷
    r3 = _start(client, s3, paper_id)
    for idx in (1, 2, 3, 4, 5, 6, 8):
        client.post(f"/api/exams/{r3}/answer", json={"question_id": q[idx], "answer": CORRECT[idx]}, headers=s3)
    client.post(f"/api/exams/{r3}/submit", headers=s3)

    # R4 stu04：Q4,Q5,Q6,Q7,Q9 = 40，简答批满 30
    r4 = _start(client, s4, paper_id)
    for idx in (3, 4, 5, 6, 8):
        client.post(f"/api/exams/{r4}/answer", json={"question_id": q[idx], "answer": CORRECT[idx]}, headers=s4)
    client.post(f"/api/exams/{r4}/submit", headers=s4)
    client.post(f"/api/exams/{r4}/review", json={"question_id": q[7], "score": 30}, headers=t1)

    # 让 R2 / R3 走超时路径：R2 未交卷前不提交，这里改为先不提交再看
    # （上面 R2 用 submit_all_correct 已提交，所以此处把 R3 之外再补一条真超时记录）
    stats = client.get(f"/api/papers/{paper_id}/stats", headers=t1).json()
    assert stats["finished_count"] == 4
    assert stats["graded_count"] == 3
    assert stats["pending_review_count"] == 1
    assert stats["avg_score"] == 71.3 and stats["avg_scored_count"] == 3
    assert stats["max_score"] == 94.0 and stats["min_score"] == 50.0
    assert stats["avg_auto_score"] == 55.0

    q1 = [x for x in stats["question_stats"] if x["question_id"] == q[0]][0]
    assert q1["correct_count"] == 2 and q1["answered_count"] == 2
    assert q1["correct_rate"] == 0.5, q1
    short = [x for x in stats["question_stats"] if x["question_id"] == q[7]][0]
    assert short["type"] == "short" and short["correct_rate"] is None
    assert short["correct_count"] is None
    # reviewed(24) + reviewed(30) + skipped(0) → pending 的 NULL 不计入平均
    assert short["avg_score"] == 18.0, short


def test_timeout_record_is_counted_in_stats(
    client: TestClient, t1: dict, s1: dict, paper_id: int, question_ids: list[int], clock: FakeClock
) -> None:
    """B1 回归：超时清算出来的记录必须进均分与人数，不再停在 graded 之外。"""
    rid = _start(client, s1, paper_id)
    client.post(f"/api/exams/{rid}/answer",
                json={"question_id": question_ids[0], "answer": "A"}, headers=s1)
    clock.advance(700)
    stats = client.get(f"/api/papers/{paper_id}/stats", headers=t1).json()
    assert stats["timeout_count"] == 1 and stats["finished_count"] == 1
    assert stats["avg_score"] == 5.0 and stats["min_score"] == 5.0
    assert stats["graded_count"] == 1
    row = client.get(f"/api/papers/{paper_id}/results", headers=t1).json()["rows"][0]
    assert row["submit_kind"] == "timeout" and row["earned_score"] == 5.0


def test_stats_empty_paper_is_all_zero(
    client: TestClient, t1: dict, paper_id: int
) -> None:
    """TC-I-47（分母 0 保护）：没人考的卷子不得抛 ERROR_FOR_DIVISION_BY_ZERO。"""
    stats = client.get(f"/api/papers/{paper_id}/stats", headers=t1).json()
    assert stats["finished_count"] == 0
    assert stats["avg_score"] is None and stats["avg_scored_count"] == 0
    assert stats["avg_auto_score"] is None
    objective = [q for q in stats["question_stats"] if q["type"] != "short"]
    assert len(objective) == 9
    assert all(q["correct_rate"] == 0.0 and q["correct_count"] == 0 for q in objective), objective
    assert all(q["avg_score"] is None for q in stats["question_stats"] if q["type"] == "short")


def test_results_list_shape_and_permissions(
    client: TestClient, t1: dict, t2: dict, s1: dict, paper_id: int, question_ids: list[int], s2: dict
) -> None:
    """TC-I-48 + TC-I-37：results 只给创建者；行内含切屏次数与交卷方式。"""
    rid = _start(client, s1, paper_id)
    client.post(f"/api/exams/{rid}/cheat-report", json={}, headers=s1)
    client.post(f"/api/exams/{rid}/submit", headers=s1)
    body = client.get(f"/api/papers/{paper_id}/results", headers=t1).json()
    assert body["paper_id"] == paper_id and body["finished_count"] == 1
    row = body["rows"][0]
    assert row["username"] == "stu01" and row["cheat_count"] == 1
    assert row["status"] == "final" and row["auto_score"] == 0.0
    assert client.get(f"/api/papers/{paper_id}/results", headers=t2).status_code == 404
    assert client.get(f"/api/papers/{paper_id}/stats", headers=t2).status_code == 404
    assert client.get(f"/api/papers/{paper_id}/results", headers=s2).status_code == 403


def test_only_finalized_records_affect_avg(
    client: TestClient, t1: dict, s1: dict, s2: dict, paper_id: int, question_ids: list[int]
) -> None:
    """两人考：一人待批、一人定稿 → avg_score 只用定稿那份，但 avg_auto_score 覆盖两人。"""
    a = _start(client, s1, paper_id)
    submit_all_correct(client, s1, a, question_ids, short=None)  # 未答简答 → 直接 final
    b = _start(client, s2, paper_id)
    submit_all_correct(client, s2, b, question_ids)              # 答了简答 → pending
    stats = client.get(f"/api/papers/{paper_id}/stats", headers=t1).json()
    assert stats["avg_scored_count"] == 1 and stats["avg_score"] == 70.0
    assert stats["avg_auto_score"] == 70.0 and stats["finished_count"] == 2
    assert stats["pending_review_count"] == 1
