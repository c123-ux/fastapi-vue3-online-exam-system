"""统计与全班成绩（D1 FR-08/FR-09、D2 §6.7）。

口径（写死，勿改）：
- finished_count = status=final 的记录数（**含超时**）→ 每题正确率的分母；
- avg/max/min 仅统计 review_state=final，并随 avg_scored_count 回报样本数；
- avg_auto_score 覆盖全部已交卷记录；
- 题面与每题满分读 paper_questions（判分才读判分基线）。
所有 GROUP BY 都符合 ONLY_FULL_GROUP_BY（本机 sql_mode 实测开启）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.exceptions import AppError
from app.models import (
    Answer,
    ExamRecord,
    Paper,
    PaperQuestion,
    QType,
    Question,
    RecordStatus,
    ReviewState,
    SubmitKind,
    User,
)
from app.services import exam_svc


def _require_creator(db: Session, paper_id: int, teacher_id: int) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None or paper.creator_id != teacher_id:
        raise AppError.make("PAPER_NOT_VISIBLE", "试卷不存在或不属于你")
    return paper


def _finished_records(db: Session, paper_id: int) -> list[ExamRecord]:
    return list(
        db.scalars(
            select(ExamRecord).where(
                ExamRecord.paper_id == paper_id, ExamRecord.status == RecordStatus.FINAL
            )
        )
    )


def paper_results(db: Session, teacher_id: int, paper_id: int) -> dict[str, Any]:
    _require_creator(db, paper_id, teacher_id)
    reaped = exam_svc.reap_expired(db, paper_id=paper_id)
    records = _finished_records(db, paper_id)
    students = {
        u.id: u
        for u in db.scalars(
            select(User).where(User.id.in_([r.student_id for r in records] or [-1]))
        )
    }
    rows = [
        {
            "record_id": r.id,
            "student_id": r.student_id,
            "username": students[r.student_id].username if r.student_id in students else "",
            "full_name": students[r.student_id].full_name if r.student_id in students else None,
            "status": r.status,
            "submit_kind": r.submit_kind,
            "review_state": r.review_state,
            "earned_score": _f(r.earned_score),
            "auto_score": _f(r.auto_score),
            "full_score": float(r.snapshot_json["full_score"]),
            "submitted_at": r.submitted_at,
            "cheat_count": r.cheat_count,
        }
        for r in sorted(records, key=lambda x: (-(x.earned_score or 0), x.id))
    ]
    return {
        "paper_id": paper_id,
        "finished_count": len(rows),
        "reaped_count": reaped,
        "rows": rows,
    }


def paper_stats(db: Session, teacher_id: int, paper_id: int) -> dict[str, Any]:
    _require_creator(db, paper_id, teacher_id)
    reaped = exam_svc.reap_expired(db, paper_id=paper_id)
    records = _finished_records(db, paper_id)
    finished_ids = [r.id for r in records]
    finalized = [r for r in records if r.review_state == ReviewState.FINAL]

    scores = [_f(r.earned_score) for r in finalized if r.earned_score is not None]
    autos = [_f(r.auto_score) for r in records if r.auto_score is not None]
    out: dict[str, Any] = {
        "paper_id": paper_id,
        "finished_count": len(records),
        "timeout_count": sum(1 for r in records if r.submit_kind == SubmitKind.TIMEOUT),
        "graded_count": len(finalized),
        "pending_review_count": len(records) - len(finalized),
        "reaped_count": reaped,
        "avg_score": _round1(sum(scores) / len(scores)) if scores else None,
        "avg_scored_count": len(scores),
        "max_score": max(scores) if scores else None,
        "min_score": min(scores) if scores else None,
        "avg_auto_score": _round1(sum(autos) / len(autos)) if autos else None,
        "question_stats": _question_stats(db, paper_id, finished_ids),
    }
    return out


def _question_stats(db: Session, paper_id: int, finished_ids: list[int]) -> list[dict[str, Any]]:
    denominator = len(finished_ids)
    on_clause = and_(Answer.question_id == PaperQuestion.question_id)
    if finished_ids:
        on_clause = and_(on_clause, Answer.record_id.in_(finished_ids))
    else:
        on_clause = and_(on_clause, Answer.id.is_(None))  # 无已交卷记录时不匹配任何作答行

    rows = db.execute(
        select(
            PaperQuestion.question_id,
            Question.type,
            PaperQuestion.score,
            PaperQuestion.sort_order,
            func.coalesce(func.sum(case((Answer.is_correct.is_(True), 1), else_=0)), 0).label(
                "correct_count"
            ),
            func.coalesce(func.sum(case((Answer.student_answer != "", 1), else_=0)), 0).label(
                "answered_count"
            ),
            func.avg(Answer.score).label("avg_score"),
        )
        .join(Question, Question.id == PaperQuestion.question_id)
        .outerjoin(Answer, on_clause)
        .where(PaperQuestion.paper_id == paper_id)
        .group_by(
            PaperQuestion.question_id,
            Question.type,
            PaperQuestion.score,
            PaperQuestion.sort_order,
        )
        .order_by(PaperQuestion.sort_order)
    ).all()

    stats = []
    for qid, qtype, full_score, _order, correct_count, answered_count, avg_score in rows:
        item: dict[str, Any] = {
            "question_id": qid,
            "type": qtype,
            "full_score": float(full_score),
        }
        if qtype == QType.SHORT:
            item["avg_score"] = _round1(float(avg_score)) if avg_score is not None else None
        else:
            item["answered_count"] = int(answered_count or 0)
            item["correct_count"] = int(correct_count or 0)
            item["correct_rate"] = (
                _round4(int(correct_count or 0) / denominator) if denominator else 0.0
            )
        stats.append(item)
    return stats


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _round1(value: float) -> float:
    return round(value, 1)


def _round4(value: float) -> float:
    return round(value, 4)
