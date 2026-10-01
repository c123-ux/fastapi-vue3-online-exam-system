"""考试读模型：续考快照、成绩、作答明细、我的记录列表。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlalchemy import func, select

from app import snapshot_guard, time_utils
from app.models import Answer, ExamRecord, Paper, QType, RecordStatus, ReviewState, ReviewStatus, User


def build_resume_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
    snapshot_guard.verified_snapshot(record)
    answers = {
        str(a.question_id): a.student_answer
        for a in db.scalars(select(Answer).where(Answer.record_id == record.id))
        if a.student_answer
    }
    paper = db.get(Paper, record.paper_id)
    # 已交卷的记录回看时不该再显示倒计时（实测：交卷后仍显示 598 秒会让用户以为还能继续考）
    if record.status == RecordStatus.IN_PROGRESS:
        remaining = _remaining_seconds(time_utils.now(), record.deadline_at)
    else:
        remaining = 0
    return {
        "record_id": record.id,
        "paper_id": record.paper_id,
        "paper_title": paper.title if paper else "",
        "duration_minutes": record.snapshot_json["duration_minutes"],
        "remaining_seconds": remaining,
        "deadline_at": record.deadline_at,
        "resumed": bool(answers),
        "questions": [
            {
                "question_id": i["question_id"],
                "sort_order": i["sort_order"],
                "type": i["type"],
                "score": float(i["score"]),
                "content": i["content"],
                "options": i["options"],
            }
            for i in sorted(record.snapshot_json["items"], key=lambda x: x["sort_order"])
        ],
        "answers": answers,
    }


def build_score_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
    snapshot_guard.verified_snapshot(record)
    paper = db.get(Paper, record.paper_id)
    answers = {
        a.question_id: a for a in db.scalars(select(Answer).where(Answer.record_id == record.id))
    }
    details = []
    for i in sorted(record.snapshot_json["items"], key=lambda x: x["sort_order"]):
        a = answers.get(i["question_id"])
        details.append(
            {
                "question_id": i["question_id"],
                "type": i["type"],
                "sort_order": i["sort_order"],
                "full_score": float(i["score"]),
                "score": None if a is None or a.score is None else float(a.score),
                "is_correct": a.is_correct if a else None,
                "review_status": a.review_status if a else None,
                "review_comment": a.review_comment if a else None,
            }
        )
    return {
        "record_id": record.id,
        "paper_id": record.paper_id,
        "paper_title": paper.title if paper else "",
        "status": record.status,
        "submit_kind": record.submit_kind,
        "review_state": record.review_state,
        "auto_score": float(record.auto_score) if record.auto_score is not None else None,
        "manual_score": float(_manual_sum(db, record.id)),
        "earned_score": float(record.earned_score) if record.earned_score is not None else None,
        "full_score": float(record.snapshot_json["full_score"]),
        "pending_review_count": _pending_count(db, record.id),
        "submitted_at": record.submitted_at,
        "deadline_at": record.deadline_at,
        "is_finalized": record.review_state == ReviewState.FINAL,
        "details": details,
    }


def build_answers_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
    snapshot_guard.verified_snapshot(record)
    student = db.get(User, record.student_id)
    answers = {
        a.question_id: a for a in db.scalars(select(Answer).where(Answer.record_id == record.id))
    }
    items = [
        {
            "question_id": i["question_id"],
            "type": i["type"],
            "sort_order": i["sort_order"],
            "full_score": float(i["score"]),
            "student_answer": getattr(answers.get(i["question_id"]), "student_answer", ""),
            "review_status": getattr(answers.get(i["question_id"]), "review_status", None),
            "score": _opt_float(getattr(answers.get(i["question_id"]), "score", None)),
            "review_comment": getattr(answers.get(i["question_id"]), "review_comment", None),
            "reference_answer": i["correct_answer"],
        }
        for i in sorted(record.snapshot_json["items"], key=lambda x: x["sort_order"])
    ]
    return {
        "record_id": record.id,
        "paper_id": record.paper_id,
        "student": student,
        "pending_review_count": _pending_count(db, record.id),
        "items": items,
    }


def build_my_records(
    db: Session, student: User, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    from app.services.exam_admin_svc import reap_expired

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    reap_expired(db, student_id=student.id)
    total = int(
        db.scalar(
            select(func.count()).select_from(ExamRecord).where(ExamRecord.student_id == student.id)
        )
        or 0
    )
    records = list(
        db.scalars(
            select(ExamRecord)
            .where(ExamRecord.student_id == student.id)
            .order_by(ExamRecord.started_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    titles = {
        p.id: p.title
        for p in db.scalars(
            select(Paper).where(Paper.id.in_([r.paper_id for r in records] or [-1]))
        )
    }
    now = time_utils.now()
    for r in records:
        snapshot_guard.verified_snapshot(r)
    out = [
        {
            "record_id": r.id,
            "paper_id": r.paper_id,
            "paper_title": titles.get(r.paper_id, ""),
            "status": r.status,
            "submit_kind": r.submit_kind,
            "review_state": r.review_state,
            "earned_score": float(r.earned_score) if r.earned_score is not None else None,
            "full_score": float(r.snapshot_json["full_score"]),
            "started_at": r.started_at,
            "deadline_at": r.deadline_at,
            "submitted_at": r.submitted_at,
            "remaining_seconds": (
                _remaining_seconds(now, r.deadline_at)
                if r.status == RecordStatus.IN_PROGRESS
                else 0
            ),
            "cheat_count": r.cheat_count,
        }
        for r in records
    ]
    return out, total


# ------------------------------------------------------------------ 私有工具
def _remaining_seconds(now: datetime, deadline_at: datetime) -> int:
    return max(0, int((deadline_at - now).total_seconds()))


def _manual_sum(db: Session, record_id: int) -> float:
    from decimal import Decimal, ROUND_HALF_UP

    rows = db.scalars(
        select(Answer.score).where(
            Answer.record_id == record_id, Answer.review_status == ReviewStatus.REVIEWED
        )
    )
    total = sum((Decimal(str(s)) for s in rows if s is not None), Decimal("0"))
    return float(total.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _pending_count(db: Session, record_id: int) -> int:
    from sqlalchemy import func

    return int(
        db.scalar(
            select(func.count())
            .select_from(Answer)
            .where(Answer.record_id == record_id, Answer.review_status == ReviewStatus.NEEDS_REVIEW)
        )
        or 0
    )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)
