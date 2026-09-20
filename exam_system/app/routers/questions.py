"""题库与分类路由（仅 teacher；越权由依赖挡成 403）。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas import (
    CategoryIn,
    CategoryOut,
    OkOut,
    QuestionIn,
    QuestionListOut,
    QuestionOut,
)
from app.security import CurrentTeacher, DbSession
from app.services import question_svc

router = APIRouter(prefix="/api", tags=["questions"])


@router.post("/categories", response_model=CategoryOut)
def create_category(payload: CategoryIn, db: DbSession, _: CurrentTeacher) -> CategoryOut:
    return question_svc.create_category(db, payload.name)


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: DbSession, _: CurrentTeacher) -> list[CategoryOut]:
    return question_svc.list_categories(db)


@router.post("/questions", response_model=QuestionOut)
def create_question(payload: QuestionIn, db: DbSession, teacher: CurrentTeacher) -> QuestionOut:
    return question_svc.create_question(db, teacher.id, payload)


@router.get("/questions", response_model=QuestionListOut)
def list_questions(
    db: DbSession,
    _: CurrentTeacher,
    category_id: int | None = Query(default=None),
    type: str | None = Query(default=None, max_length=10),
    difficulty: str | None = Query(default=None, max_length=10),
    keyword: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> QuestionListOut:
    rows, total = question_svc.list_questions(
        db,
        category_id=category_id,
        qtype=type,
        difficulty=difficulty,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return QuestionListOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[QuestionOut.model_validate(q) for q in rows],
    )


@router.get("/questions/{question_id}", response_model=QuestionOut)
def get_question(question_id: int, db: DbSession, _: CurrentTeacher) -> QuestionOut:
    return question_svc.get_question_or_404(db, question_id)


@router.put("/questions/{question_id}", response_model=QuestionOut)
def update_question(
    question_id: int, payload: QuestionIn, db: DbSession, _: CurrentTeacher
) -> QuestionOut:
    return question_svc.update_question(db, question_id, payload)


@router.delete("/questions/{question_id}", response_model=OkOut)
def delete_question(question_id: int, db: DbSession, _: CurrentTeacher) -> OkOut:
    question_svc.get_question_or_404(db, question_id)
    question_svc.delete_question(db, question_id)
    return OkOut()
