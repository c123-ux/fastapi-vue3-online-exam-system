"""题库业务：题型校验矩阵、引用与锁定检查（D1 FR-02 / D2 §6.6）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import time_utils
from app.exceptions import AppError
from app.models import Category, Paper, PaperQuestion, PaperStatus, QType, Question
from app.schemas import QuestionIn
from app.services.grade_svc import normalize_answer

OPTION_LETTERS = "ABCDEF"
LOCKED_ANSWER_FIELDS = {"type", "content", "options", "correct_answer"}


def validate_question_payload(
    qtype: str, options: list[str] | None, correct_answer: str
) -> str:
    """跨字段业务校验；通过则返回规范化后的 correct_answer（违规 → 400）。"""
    answer = (correct_answer or "").strip()

    if qtype in (QType.SINGLE, QType.MULTIPLE):
        if not options:
            raise AppError.make("INVALID_QUESTION", "选择题必须提供 options")
        letters = set(OPTION_LETTERS[: len(options)])
    elif qtype in (QType.JUDGE, QType.SHORT):
        if options:
            raise AppError.make("INVALID_QUESTION", f"{qtype} 题不接受 options")
        letters = set()
    else:  # pragma: no cover - Literal 已挡
        raise AppError.make("INVALID_QUESTION", f"未知题型 {qtype}")

    if qtype == QType.SHORT:
        return answer

    normalized = normalize_answer(qtype, answer)
    if not normalized:
        raise AppError.make("INVALID_QUESTION", "correct_answer 不能为空")

    if qtype == QType.SINGLE:
        if len(normalized) != 1 or normalized not in letters:
            raise AppError.make("INVALID_QUESTION", f"单选题答案必须是单个且在选项范围内的字母，当前 {normalized}")
        return normalized

    if qtype == QType.JUDGE:
        if normalized not in {"T", "F"}:
            raise AppError.make("INVALID_QUESTION", "判断题答案必须是 T 或 F")
        return normalized

    parts = normalized.split(",")
    if len(parts) < 2:
        raise AppError.make("INVALID_QUESTION", "多选题正确答案至少 2 项")
    if any(p not in letters for p in parts):
        raise AppError.make("INVALID_QUESTION", f"多选题答案含越界字母：{normalized}")
    return normalized


def create_question(db: Session, teacher_id: int, payload: QuestionIn) -> Question:
    if payload.category_id is not None and db.get(Category, payload.category_id) is None:
        raise AppError.make("NOT_FOUND", "分类不存在")
    normalized = validate_question_payload(payload.type, payload.options, payload.correct_answer)
    question = Question(
        category_id=payload.category_id,
        type=payload.type,
        content=payload.content,
        options_json=payload.options,
        correct_answer=normalized,
        difficulty=payload.difficulty,
        created_by=teacher_id,
        created_at=time_utils.now(),
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


def get_question_or_404(db: Session, question_id: int) -> Question:
    question = db.get(Question, question_id)
    if question is None:
        raise AppError.make("NOT_FOUND", "题目不存在")
    return question


def _referenced_paper_status(db: Session, question_id: int) -> list[str]:
    rows = db.scalars(
        select(Paper.status)
        .join(PaperQuestion, PaperQuestion.paper_id == Paper.id)
        .where(PaperQuestion.question_id == question_id)
    )
    return list(rows)


def update_question(
    db: Session, question_id: int, payload: QuestionIn, actor_id: int | None = None
) -> Question:
    question = get_question_or_404(db, question_id)
    changed = _answer_fields_before(question, payload)
    if changed and any(s != PaperStatus.DRAFT for s in _referenced_paper_status(db, question_id)):
        raise AppError.make(
            "QUESTION_LOCKED", "该题已被已发布/已关闭试卷引用，判分基准字段不可修改"
        )
    if payload.category_id is not None and db.get(Category, payload.category_id) is None:
        raise AppError.make("NOT_FOUND", "分类不存在")

    question.type = payload.type
    question.content = payload.content
    question.options_json = payload.options
    question.difficulty = payload.difficulty
    question.category_id = payload.category_id
    question.correct_answer = validate_question_payload(
        payload.type, payload.options, payload.correct_answer
    )
    db.commit()
    db.refresh(question)
    return question


def _answer_fields_before(question: Question, payload: QuestionIn) -> bool:
    return (
        question.type != payload.type
        or question.content != payload.content
        or list(question.options_json or []) != list(payload.options or [])
        or question.correct_answer
        != normalize_answer(payload.type, payload.correct_answer)
    )


def delete_question(db: Session, question_id: int) -> None:
    question = get_question_or_404(db, question_id)
    used = db.scalar(
        select(func.count()).select_from(PaperQuestion).where(PaperQuestion.question_id == question_id)
    )
    if used:
        raise AppError.make("QUESTION_IN_USE", f"该题被 {used} 份试卷引用，不能删除")
    db.delete(question)
    db.commit()


def list_questions(
    db: Session,
    *,
    category_id: int | None = None,
    qtype: str | None = None,
    difficulty: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Question], int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    conds: list[Any] = []
    if category_id is not None:
        conds.append(Question.category_id == category_id)
    if qtype:
        conds.append(Question.type == qtype)
    if difficulty:
        conds.append(Question.difficulty == difficulty)
    if keyword:
        # V-08 修复：把用户输入里的 LIKE 通配符转义为字面量，避免 `%`/`_` 绕过过滤。
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append(Question.content.like(f"%{escaped}%", escape="\\"))
    total = db.scalar(select(func.count()).select_from(Question).where(*conds)) or 0
    rows = list(
        db.scalars(
            select(Question)
            .where(*conds)
            .order_by(Question.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, int(total)


# ------------------------------------------------------------------ 分类
def create_category(db: Session, name: str) -> Category:
    if db.scalar(select(Category).where(Category.name == name)):
        raise AppError.make("CATEGORY_EXISTS", "分类名已存在")
    category = Category(name=name)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def list_categories(db: Session) -> list[Category]:
    return list(db.scalars(select(Category).order_by(Category.id)))


def category_exists(db: Session, category_id: int) -> bool:
    return db.get(Category, category_id) is not None


__all__ = [
    "OPTION_LETTERS",
    "LOCKED_ANSWER_FIELDS",
    "category_exists",
    "create_category",
    "create_question",
    "delete_question",
    "get_question_or_404",
    "list_categories",
    "list_questions",
    "update_question",
    "validate_question_payload",
]
