"""考试编排：开考/续考/作答/交卷/惰性清算（D2 §2.2、§2.5、§6.3~6.6）。

三条不可让步的正确性约束：
- deadline_at 只在 T1 首次开考写入，幂等分支永不更新（A7 限时绕过）；
- 权威闸门是 DB 条件更新 `WHERE status='in_progress'`，Redis 只做快速失败（B2/B3）；
- 判分与批改只读记录的判分基线 snapshot_json，不读当前题库（B6）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app import redis_client, snapshot_guard, time_utils
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
    User,
)
from app.services import grade_svc, paper_svc

logger = logging.getLogger("exam.exam_svc")


# ------------------------------------------------------------------ 纯时间函数
def compute_deadline(started_at: datetime, duration_minutes: int, end_at: datetime) -> datetime:
    """deadline = min(开考 + 时长, 窗口结束)（B7）。"""
    return min(started_at + timedelta(minutes=duration_minutes), end_at)


def window_too_short(
    now: datetime,
    end_at: datetime,
    duration_minutes: int,
    *,
    min_seconds: int | None = None,
    ratio: float | None = None,
) -> bool:
    """EC-31：剩余窗口短于下限就不允许开考，避免"点开即超时"的 0 分噪声记录。"""
    floor_seconds = min(
        min_seconds if min_seconds is not None else settings.min_exam_window_seconds,
        int(duration_minutes * 60 * (ratio if ratio is not None else settings.min_window_ratio)),
    )
    return (end_at - now).total_seconds() < max(1, floor_seconds)


def remaining_seconds(now: datetime, deadline_at: datetime) -> int:
    """N5：纯 DB 计算，不查 Redis。"""
    return max(0, int((deadline_at - now).total_seconds()))


def session_ttl(now: datetime, deadline_at: datetime, grace: int) -> int:
    """会话 TTL；返回 ≤0 表示"已过宽限期，应直接走超时判定"（A3c）。"""
    return int((deadline_at - now).total_seconds()) + grace


def _grace() -> timedelta:
    return timedelta(seconds=settings.exam_grace_seconds)


# ------------------------------------------------------------------ T1/T2/T3
def start_exam(db: Session, student: User, paper_id: int) -> tuple[ExamRecord, bool]:
    """返回 (记录, 是否为续考分支)。"""
    paper = db.get(Paper, paper_id)
    if paper is None or paper.status == PaperStatus.DRAFT:
        raise AppError.make("PAPER_NOT_VISIBLE")
    if paper.status == PaperStatus.CLOSED:
        raise AppError.make("EXAM_CLOSED")

    existing = _find_record(db, paper_id, student.id)
    if existing is not None:
        return _resume_existing(db, existing), True

    now = time_utils.now()
    if now < paper.start_at:
        raise AppError.make("EXAM_NOT_STARTED")
    if now > paper.end_at:
        raise AppError.make("EXAM_ENDED")
    if window_too_short(now, paper.end_at, paper.duration_minutes):
        raise AppError.make("EXAM_WINDOW_TOO_SHORT")

    snapshot = paper_svc.build_snapshot(db, paper)
    record = ExamRecord(
        paper_id=paper.id,
        student_id=student.id,
        started_at=now,
        deadline_at=compute_deadline(now, paper.duration_minutes, paper.end_at),
        status=RecordStatus.IN_PROGRESS,
        cheat_count=0,
        snapshot_json=snapshot,
        snapshot_hash=snapshot_guard.sign_snapshot(snapshot),
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        # 并发双开同一张卷：UNIQUE(paper_id, student_id) 兜底，退回幂等分支
        db.rollback()
        again = _find_record(db, paper_id, student.id)
        if again is None:
            raise
        return _resume_existing(db, again), True
    db.refresh(record)
    _ensure_session(record)
    logger.info("开考 paper_id=%s record_id=%s deadline=%s", paper.id, record.id, record.deadline_at)
    return record, False


def _find_record(db: Session, paper_id: int, student_id: int) -> ExamRecord | None:
    return db.scalar(
        select(ExamRecord).where(
            ExamRecord.paper_id == paper_id, ExamRecord.student_id == student_id
        )
    )


def _resume_existing(db: Session, record: ExamRecord) -> ExamRecord:
    if record.status != RecordStatus.IN_PROGRESS:
        raise AppError.make("ALREADY_SUBMITTED", _submitted_hint(record))
    ttl = session_ttl(time_utils.now(), record.deadline_at, settings.exam_grace_seconds)
    if ttl <= 0:
        from app.services.exam_admin_svc import settle_as_timeout
        settle_as_timeout(db, record)
        raise AppError.make("ALREADY_SUBMITTED", _submitted_hint(record))
    current = _safe_session_ttl(record.id)
    if current == -1:  # 异常态：会话存在但永不过期（EC-30）
        redis_client.refresh_session_ttl(record.id, ttl)
    elif current in (-2, None):  # 会话丢失且未超期 → 按剩余重建，TTL 只减不增
        redis_client.write_session(record.id, _session_payload(record), ttl)
    return record


def _safe_session_ttl(rid: int) -> int | None:
    """Redis 不可用时读路径不炸（NFR-08）：返回 None 表示"无法判活"。"""
    try:
        return redis_client.session_ttl(rid)
    except Exception as exc:  # pragma: no cover - 依赖故障路径
        logger.error("读取会话 TTL 失败 record_id=%s：%s", rid, exc)
        return None


def _session_payload(record: ExamRecord) -> dict[str, Any]:
    snapshot_guard.verified_snapshot(record)
    return {
        "record_id": record.id,
        "started_at": record.started_at.isoformat(),
        "deadline_at": record.deadline_at.isoformat(),
        "grace_seconds": settings.exam_grace_seconds,
        "duration_minutes": record.snapshot_json["duration_minutes"],
    }


def _ensure_session(record: ExamRecord) -> None:
    ttl = session_ttl(time_utils.now(), record.deadline_at, settings.exam_grace_seconds)
    if ttl > 0:
        redis_client.write_session(record.id, _session_payload(record), ttl)


# ------------------------------------------------------------------ T4/T5
def save_answer(db: Session, student: User, record_id: int, question_id: int, raw: Any) -> int:
    record = _load_student_record(db, record_id, student.id)
    locked = _record_for_update(db, record.id)
    if locked.status != RecordStatus.IN_PROGRESS:
        raise AppError.make("NOT_IN_PROGRESS", "记录已结束，不能再作答")
    now = time_utils.now()
    if now > locked.deadline_at + _grace():
        from app.services.exam_admin_svc import settle_as_timeout
        settle_as_timeout(db, locked)
        raise AppError.make("EXAM_TIME_UP", _submitted_hint(locked))

    snapshot = snapshot_guard.verified_snapshot(locked)
    item = paper_svc.snapshot_item(snapshot, question_id)
    normalized = grade_svc.normalize_answer(item["type"], raw)
    # V-02 修复：用 MySQL 原生 `INSERT ... ON DUPLICATE KEY UPDATE` 做原子 upsert。
    #   手动"先 select 看不存在再 insert"在 REPEATABLE READ 下是快照读，看不到并发事务刚插入的
    #   同一 (record_id, question_id) 行，于是两条并发都走 insert → 命中唯一约束 → 500 并泄露 DB 原文。
    #   原生 upsert 由 InnoDB 在索引冲突时原子判重并更新，天然免去该竞态。
    stmt = mysql_insert(Answer).values(
        record_id=locked.id, question_id=question_id,
        student_answer=normalized, answered_at=now,
    )
    stmt = stmt.on_duplicate_key_update(
        student_answer=stmt.inserted.student_answer,
        answered_at=stmt.inserted.answered_at,
    )
    db.execute(stmt)
    db.commit()
    return answered_count(db, locked.id)


def answered_count(db: Session, record_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Answer)
            .where(Answer.record_id == record_id, Answer.student_answer != "")
        )
        or 0
    )


# ------------------------------------------------------------------ T6/T7/T8
def _claim(db: Session, record_id: int, kind: str, now: datetime) -> bool:
    """DB 条件更新抢闸：rowcount==1 才拥有判分权（B2）。不 commit，行锁留到事务结束。"""
    result = db.execute(
        update(ExamRecord)
        .where(ExamRecord.id == record_id, ExamRecord.status == RecordStatus.IN_PROGRESS)
        .values(status=RecordStatus.FINAL, submit_kind=kind, submitted_at=now)
    )
    return int(result.rowcount or 0) == 1


def submit_exam(db: Session, student: User, record_id: int) -> dict[str, Any]:
    record = _load_student_record(db, record_id, student.id)
    if record.status != RecordStatus.IN_PROGRESS:
        raise AppError.make("ALREADY_SUBMITTED", _submitted_hint(record))
    if _submit_marked(record.id):
        db.refresh(record)
        if record.status == RecordStatus.FINAL:
            raise AppError.make("ALREADY_SUBMITTED", _submitted_hint(record))
    now = time_utils.now()
    kind = SubmitKind.NORMAL if now <= record.deadline_at + _grace() else SubmitKind.TIMEOUT
    payload = _settle(db, record.id, kind, now)
    assert payload is not None  # 非 quiet 模式必成功或抛 409
    return payload


def _submit_marked(rid: int) -> bool:
    try:
        return redis_client.submit_marked(rid)
    except Exception as exc:  # pragma: no cover
        logger.error("读取提交标记失败 record_id=%s：%s", rid, exc)
        return False


def _settle(
    db: Session, record_id: int, kind: str, now: datetime, *, quiet: bool = False
) -> dict[str, Any] | None:
    # 验签先于抢闸：脏基线的记录连提交状态都进不去，事务零副作用
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
        redis_client.mark_submitted(rid)
    except Exception as exc:  # pragma: no cover - 依赖故障路径
        logger.error("Redis 提交标记写入失败 record_id=%s：%s（不影响成绩）", rid, exc)


# ------------------------------------------------------------------ 读模型
def resume_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
    from app.services.exam_reader_svc import build_resume_payload
    return build_resume_payload(db, record)


def score_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
    from app.services.exam_reader_svc import build_score_payload
    return build_score_payload(db, record)


def answers_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
    from app.services.exam_reader_svc import build_answers_payload
    return build_answers_payload(db, record)


def list_my_records(
    db: Session, student: User, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    from app.services.exam_reader_svc import build_my_records
    return build_my_records(db, student, page, page_size)


def record_or_404(db: Session, record_id: int) -> ExamRecord:
    record = db.get(ExamRecord, record_id)
    if record is None:
        raise AppError.make("RECORD_NOT_VISIBLE")
    return record


# ------------------------------------------------------------------ 管理操作
def reap_expired(
    db: Session, *, paper_id: int | None = None, student_id: int | None = None
) -> int:
    from app.services.exam_admin_svc import reap_expired as _reap
    return _reap(db, paper_id=paper_id, student_id=student_id)


def review_answer(
    db: Session,
    teacher: User,
    record_id: int,
    question_id: int,
    score: float,
    comment: str | None,
) -> dict[str, Any]:
    from app.services.exam_admin_svc import review_answer as _review
    return _review(db, teacher, record_id, question_id, score, comment)


def report_cheat(db: Session, student: User, record_id: int) -> int:
    from app.services.exam_admin_svc import report_cheat as _cheat
    return _cheat(db, student, record_id)


def settle_as_timeout(db: Session, record: ExamRecord) -> dict[str, Any] | None:
    from app.services.exam_admin_svc import settle_as_timeout as _settle_timeout
    return _settle_timeout(db, record)


# ------------------------------------------------------------------ 记录存取
def _load_student_record(db: Session, record_id: int, student_id: int) -> ExamRecord:
    record = db.get(ExamRecord, record_id)
    if record is None or record.student_id != student_id:
        raise AppError.make("RECORD_NOT_VISIBLE")
    return record


def _load_creator_record(db: Session, record_id: int, teacher_id: int) -> ExamRecord:
    record = db.get(ExamRecord, record_id)
    paper = None if record is None else db.get(Paper, record.paper_id)
    if record is None or paper is None or paper.creator_id != teacher_id:
        raise AppError.make("RECORD_NOT_VISIBLE")
    return record


def load_record_for_reader(db: Session, record_id: int, user: User) -> ExamRecord:
    """学生读自己的；老师读自己试卷的；其余一律同一形态 404（EC-19）。"""
    if user.role == Role.STUDENT:
        return _load_student_record(db, record_id, user.id)
    return _load_creator_record(db, record_id, user.id)


def _record_for_update(db: Session, record_id: int) -> ExamRecord:
    """行锁加载：批改与作答的读-改-写必须在同一事务内原子（B11）。"""
    record = db.get(ExamRecord, record_id, with_for_update=True)
    if record is None:  # pragma: no cover - 调用方已判 404
        raise AppError.make("RECORD_NOT_VISIBLE")
    return record


def _snapshot_item(record: ExamRecord, question_id: int) -> dict[str, Any]:
    for item in record.snapshot_json["items"]:
        if item["question_id"] == question_id:
            return item
    raise AppError.make("QUESTION_NOT_IN_PAPER", f"题目 {question_id} 不在本卷判分基线内")


def _submitted_hint(record: ExamRecord) -> dict[str, Any]:
    return {
        "record_id": record.id,
        "submitted_at": record.submitted_at.isoformat() if record.submitted_at else None,
        "earned_score": float(record.earned_score) if record.earned_score is not None else None,
    }
