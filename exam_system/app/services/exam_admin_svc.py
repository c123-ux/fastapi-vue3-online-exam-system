"""考试管理：惰性清算、批改、切屏（D2 §6.4~6.6）。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import snapshot_guard, time_utils
from app.config import settings
from app.exceptions import AppError
from app.models import (
    Answer,
    ExamRecord,
    Paper,
    PaperStatus,
    QType,
    RecordStatus,
    ReviewState,
    ReviewStatus,
    Role,
    SubmitKind,
)
from app.services import grade_svc, paper_svc

logger = logging.getLogger("exam.exam_admin_svc")


def reap_expired(
    db: Session, *, paper_id: int | None = None, student_id: int | None = None
) -> int:
    """读路径兜底清算。时间比较在 Python 侧做（N9：SQL 内禁用 NOW()）。"""
    from app.services.exam_svc import _claim, _grace, _persist_grade, _apply_scores, _mark_after_commit

    conds = [ExamRecord.status == RecordStatus.IN_PROGRESS]
    if paper_id is not None:
        conds.append(ExamRecord.paper_id == paper_id)
    if student_id is not None:
        conds.append(ExamRecord.student_id == student_id)
    now = time_utils.now()
    grace = _grace()
    reaped = 0
    for record in list(db.scalars(select(ExamRecord).where(*conds))):
        if now <= record.deadline_at + grace:
            continue
        if settle_as_timeout(db, record) is not None:
            reaped += 1
    if reaped:
        logger.warning("惰性清算完成 %s 条超时记录", reaped)
    return reaped


def settle_as_timeout(db: Session, record: ExamRecord) -> dict[str, Any] | None:
    """惰性清算入口。**只在真的越过宽限期时**才清算（T12：未过期不得误清）。"""
    from app.services.exam_svc import _grace
    if time_utils.now() <= record.deadline_at + _grace():
        return None
    return _settle(db, record.id, SubmitKind.TIMEOUT, time_utils.now(), quiet=True)


def _settle(
    db: Session, record_id: int, kind: str, now: datetime, *, quiet: bool = False
) -> dict[str, Any] | None:
    from app.services.exam_svc import _claim

    # 验签先于抢闸（与 exam_svc._settle 同规）：脏基线不进交卷状态机
    pre = db.get(ExamRecord, record_id)
    if pre is not None:
        snapshot_guard.verified_snapshot(pre)
    if not _claim(db, record_id, kind, now):
        db.rollback()
        if quiet:
            return None
        raise AppError.make("ALREADY_SUBMITTED", _submitted_hint(db.get(ExamRecord, record_id)))

    record = db.get(ExamRecord, record_id)
    answers = {
        a.question_id: a.student_answer
        for a in db.scalars(select(Answer).where(Answer.record_id == record_id))
    }
    result = grade_svc.grade_paper(
        paper_svc.snapshot_specs(record.snapshot_json), answers
    )
    _persist_grade(db, record_id, result, now)
    payload = _apply_scores(record, result)
    db.commit()
    (logger.warning if kind == SubmitKind.TIMEOUT else logger.info)(
        "%s record_id=%s auto_score=%s pending=%s",
        "超时自动交卷" if kind == SubmitKind.TIMEOUT else "交卷判分完成",
        record_id,
        payload["auto_score"],
        payload["pending_review_count"],
    )
    _mark_after_commit(record_id)
    return payload


def _apply_scores(record: ExamRecord, result: grade_svc.GradeResult) -> dict[str, Any]:
    record.auto_score = result.auto_score
    record.earned_score = result.auto_score
    record.review_state = ReviewState.PENDING if result.needs_review else ReviewState.FINAL
    return {
        "record_id": record.id,
        "paper_id": record.paper_id,
        "status": record.status,
        "submit_kind": record.submit_kind,
        "auto_score": float(result.auto_score),
        "manual_score": 0.0,
        "earned_score": float(result.auto_score),
        "full_score": float(record.snapshot_json["full_score"]),
        "pending_review_count": result.pending_review_count,
        "review_state": record.review_state,
        "submitted_at": record.submitted_at,
    }


def _persist_grade(db: Session, record_id: int, result: grade_svc.GradeResult, now: datetime) -> None:
    existing = {
        a.question_id: a for a in db.scalars(select(Answer).where(Answer.record_id == record_id))
    }
    for item in result.items:
        row = existing.get(item.question_id)
        if row is None:
            row = Answer(record_id=record_id, question_id=item.question_id)
            db.add(row)
        row.student_answer = item.normalized_answer
        row.is_correct = item.is_correct
        row.score = None if item.score is None else Decimal(str(item.score))
        row.review_status = item.review_status
        row.answered_at = now


def _mark_after_commit(rid: int) -> None:
    """标记只在 commit 成功后置（B3）；失败也不影响成绩正确性。"""
    try:
        from app import redis_client
        redis_client.mark_submitted(rid)
    except Exception as exc:  # pragma: no cover - 依赖故障路径
        logger.error("Redis 提交标记写入失败 record_id=%s：%s（不影响成绩）", rid, exc)


def review_answer(
    db: Session,
    teacher: User,
    record_id: int,
    question_id: int,
    score: float,
    comment: str | None,
) -> dict[str, Any]:
    from app.services.exam_svc import _load_creator_record, _record_for_update, _snapshot_item

    _load_creator_record(db, record_id, teacher.id)
    record = _record_for_update(db, record_id)
    if record.status != RecordStatus.FINAL:
        raise AppError.make("NOT_IN_PROGRESS", "记录尚未交卷，不能批改")
    snapshot_guard.verified_snapshot(record)
    item = _snapshot_item(record, question_id)
    if item["type"] != QType.SHORT:
        raise AppError.make("NOT_SHORT_QUESTION", "只有简答题可以人工批改")

    given = Decimal(str(score))
    full = Decimal(str(item["score"]))
    if not grade_svc.review_score_allowed(given, full):
        raise AppError.make("INVALID_SCORE", f"批改分值必须在 0~{full} 之间")

    row = db.scalar(
        select(Answer).where(Answer.record_id == record.id, Answer.question_id == question_id)
    )
    if row is None:  # pragma: no cover - 判分已补齐全卷
        raise AppError.make("NOT_FOUND", "该题作答记录不存在")
    row.score = given
    row.review_status = ReviewStatus.REVIEWED
    row.review_comment = comment
    row.is_correct = given > 0
    # autoflush=False 的会话里，不把上面的改动 flush 出去，紧接着的 COUNT/SUM
    # 会读回旧值，导致"批完仍显示待批 + 总分不涨"（实测踩到，见 D4）
    db.flush()

    pending = _pending_count(db, record.id)
    record.earned_score = Decimal(str(record.auto_score or 0)) + _manual_sum(db, record.id)
    record.review_state = ReviewState.FINAL if pending == 0 else ReviewState.PENDING
    db.commit()
    logger.info("批改完成 record_id=%s question_id=%s earned=%s", record.id, question_id, record.earned_score)
    return {
        "record_id": record.id,
        "question_id": question_id,
        "score": float(given),
        "auto_score": float(record.auto_score or 0),
        "manual_score": float(_manual_sum(db, record.id)),
        "earned_score": float(record.earned_score),
        "full_score": float(record.snapshot_json["full_score"]),
        "pending_review_count": pending,
        "review_state": record.review_state,
    }


# ------------------------------------------------------------------ T11 切屏
def report_cheat(db: Session, student: User, record_id: int) -> int:
    from app.services.exam_svc import _load_student_record

    record = _load_student_record(db, record_id, student.id)
    result = db.execute(
        update(ExamRecord)
        .where(ExamRecord.id == record.id, ExamRecord.status == RecordStatus.IN_PROGRESS)
        .values(cheat_count=ExamRecord.cheat_count + 1)
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        raise AppError.make("NOT_IN_PROGRESS", "记录已结束，切屏不再计数")
    db.commit()
    db.refresh(record)
    return int(record.cheat_count)


# ------------------------------------------------------------------ 私有工具
def _submitted_hint(record: ExamRecord) -> dict[str, Any]:
    return {
        "record_id": record.id,
        "submitted_at": record.submitted_at.isoformat() if record.submitted_at else None,
        "earned_score": float(record.earned_score) if record.earned_score is not None else None,
    }


def _manual_sum(db: Session, record_id: int) -> Decimal:
    rows = db.scalars(
        select(Answer.score).where(
            Answer.record_id == record_id, Answer.review_status == ReviewStatus.REVIEWED
        )
    )
    return sum((Decimal(str(s)) for s in rows if s is not None), Decimal("0"))


def _pending_count(db: Session, record_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Answer)
            .where(Answer.record_id == record_id, Answer.review_status == ReviewStatus.NEEDS_REVIEW)
        )
        or 0
    )
