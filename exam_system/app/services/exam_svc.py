"""考试编排：开考/续考/作答/交卷/惰性清算/批改/切屏（D2 §2.2、§2.5、§6.3~6.6）。

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
from sqlalchemy.orm import Session

from app import redis_client, time_utils
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

    record = ExamRecord(
        paper_id=paper.id,
        student_id=student.id,
        started_at=now,
        deadline_at=compute_deadline(now, paper.duration_minutes, paper.end_at),
        status=RecordStatus.IN_PROGRESS,
        cheat_count=0,
        snapshot_json=paper_svc.build_snapshot(db, paper),
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
        settle_as_timeout(db, locked)
        raise AppError.make("EXAM_TIME_UP", _submitted_hint(locked))

    item = _snapshot_item(locked, question_id)
    normalized = grade_svc.normalize_answer(item["type"], raw)
    row = db.scalar(
        select(Answer).where(Answer.record_id == locked.id, Answer.question_id == question_id)
    )
    if row is None:
        row = Answer(record_id=locked.id, question_id=question_id)
        db.add(row)
    row.student_answer = normalized
    row.answered_at = now
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


def settle_as_timeout(db: Session, record: ExamRecord) -> dict[str, Any] | None:
    """惰性清算入口。**只在真的越过宽限期时**才清算（T12：未过期不得误清）。"""
    if time_utils.now() <= record.deadline_at + _grace():
        return None
    return _settle(db, record.id, SubmitKind.TIMEOUT, time_utils.now(), quiet=True)


def _submit_marked(rid: int) -> bool:
    try:
        return redis_client.submit_marked(rid)
    except Exception as exc:  # pragma: no cover
        logger.error("读取提交标记失败 record_id=%s：%s", rid, exc)
        return False


def _settle(
    db: Session, record_id: int, kind: str, now: datetime, *, quiet: bool = False
) -> dict[str, Any] | None:
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


def reap_expired(
    db: Session, *, paper_id: int | None = None, student_id: int | None = None
) -> int:
    """读路径兜底清算。时间比较在 Python 侧做（N9：SQL 内禁用 NOW()）。"""
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


# ------------------------------------------------------------------ T9 批改
def review_answer(
    db: Session,
    teacher: User,
    record_id: int,
    question_id: int,
    score: float,
    comment: str | None,
) -> dict[str, Any]:
    _load_creator_record(db, record_id, teacher.id)
    record = _record_for_update(db, record_id)
    if record.status != RecordStatus.FINAL:
        raise AppError.make("NOT_IN_PROGRESS", "记录尚未交卷，不能批改")
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


# ------------------------------------------------------------------ T11 切屏
def report_cheat(db: Session, student: User, record_id: int) -> int:
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


# ------------------------------------------------------------------ 读模型
def resume_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
    answers = {
        str(a.question_id): a.student_answer
        for a in db.scalars(select(Answer).where(Answer.record_id == record.id))
        if a.student_answer
    }
    paper = db.get(Paper, record.paper_id)
    # 已交卷的记录回看时不该再显示倒计时（实测：交卷后仍显示 598 秒会让用户以为还能继续考）
    if record.status == RecordStatus.IN_PROGRESS:
        remaining = remaining_seconds(time_utils.now(), record.deadline_at)
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


def score_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
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


def answers_payload(db: Session, record: ExamRecord) -> dict[str, Any]:
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


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def list_my_records(
    db: Session, student: User, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
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
                remaining_seconds(now, r.deadline_at)
                if r.status == RecordStatus.IN_PROGRESS
                else 0
            ),
            "cheat_count": r.cheat_count,
        }
        for r in records
    ]
    return out, total


def record_or_404(db: Session, record_id: int) -> ExamRecord:
    record = db.get(ExamRecord, record_id)
    if record is None:
        raise AppError.make("RECORD_NOT_VISIBLE")
    return record
