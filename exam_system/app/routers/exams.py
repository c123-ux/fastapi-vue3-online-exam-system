"""考试路由：开考/续考、作答、交卷、成绩、批改、切屏。

路由声明顺序（D2 §5.5）：
无参数段路由 `GET /api/exams` 必须先于 `GET /api/exams/{record_id}`，
否则字面量路径会被参数路由吃掉并返回 422（实测）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app import time_utils
from app.schemas import (
    AnswerIn,
    AnswersOut,
    CheatIn,
    CheatOut,
    RecordListOut,
    ReviewIn,
    ReviewOut,
    SavedOut,
    ScoreOut,
    StartOut,
    SubmitOut,
)
from app.security import (
    CurrentStudent,
    CurrentTeacher,
    CurrentUser,
    DbSession,
)
from app.services import exam_svc

router = APIRouter(prefix="/api/exams", tags=["exams"])


# ---------------------------------------------------------------- 学生：开考与作答
@router.post("/{paper_id}/start", response_model=StartOut)
def start(paper_id: int, db: DbSession, student: CurrentStudent) -> StartOut:
    record, resumed = exam_svc.start_exam(db, student, paper_id)
    payload = exam_svc.resume_payload(db, record)
    payload["resumed"] = resumed
    return StartOut.model_validate(payload)


@router.get("", response_model=RecordListOut)
def my_records(
    db: DbSession,
    student: CurrentStudent,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RecordListOut:
    items, total = exam_svc.list_my_records(db, student, page, page_size)
    return RecordListOut(total=total, page=page, page_size=page_size, items=items)


@router.get("/{record_id}", response_model=StartOut)
def resume(record_id: int, db: DbSession, student: CurrentStudent) -> StartOut:
    record = exam_svc.load_record_for_reader(db, record_id, student)
    return StartOut.model_validate(exam_svc.resume_payload(db, record))


@router.post("/{record_id}/answer", response_model=SavedOut)
def answer(record_id: int, payload: AnswerIn, db: DbSession, student: CurrentStudent) -> SavedOut:
    count = exam_svc.save_answer(db, student, record_id, payload.question_id, payload.answer)
    record = exam_svc.record_or_404(db, record_id)
    return SavedOut(
        saved=True,
        answered_count=count,
        remaining_seconds=exam_svc.remaining_seconds(time_utils.now(), record.deadline_at),
    )


@router.post("/{record_id}/submit", response_model=SubmitOut)
def submit(record_id: int, db: DbSession, student: CurrentStudent) -> SubmitOut:
    return SubmitOut.model_validate(exam_svc.submit_exam(db, student, record_id))


@router.post("/{record_id}/cheat-report", response_model=CheatOut)
def cheat_report(
    record_id: int, payload: CheatIn, db: DbSession, student: CurrentStudent
) -> CheatOut:
    return CheatOut(record_id=record_id, cheat_count=exam_svc.report_cheat(db, student, record_id))


# ---------------------------------------------------------------- 成绩与批改
@router.get("/{record_id}/score", response_model=ScoreOut)
def score(record_id: int, db: DbSession, user: CurrentUser) -> ScoreOut:
    record = exam_svc.load_record_for_reader(db, record_id, user)
    exam_svc.settle_as_timeout(db, record)
    db.refresh(record)
    return ScoreOut.model_validate(exam_svc.score_payload(db, record))


@router.get("/{record_id}/answers", response_model=AnswersOut)
def answers(record_id: int, db: DbSession, teacher: CurrentTeacher) -> AnswersOut:
    record = exam_svc.load_record_for_reader(db, record_id, teacher)
    return AnswersOut.model_validate(exam_svc.answers_payload(db, record))


@router.post("/{record_id}/review", response_model=ReviewOut)
def review(
    record_id: int, payload: ReviewIn, db: DbSession, teacher: CurrentTeacher
) -> ReviewOut:
    result = exam_svc.review_answer(
        db, teacher, record_id, payload.question_id, payload.score, payload.comment
    )
    return ReviewOut.model_validate(result)
