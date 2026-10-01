"""组卷与试卷业务：分值求和、发布状态机、判分基线构建（D1 FR-03/FR-04）。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import time_utils
from app.exceptions import AppError
from app.models import Paper, PaperQuestion, PaperStatus, Question
from app.schemas import PaperIn

TRANSITIONS: dict[str, str] = {PaperStatus.DRAFT: PaperStatus.PUBLISHED, PaperStatus.PUBLISHED: PaperStatus.CLOSED}


def create_paper(db: Session, creator_id: int, payload: PaperIn) -> Paper:
    if payload.end_at <= payload.start_at:
        raise AppError.make("INVALID_PAPER", "end_at 必须晚于 start_at")
    ids = [item.question_id for item in payload.questions]
    if len(ids) != len(set(ids)):
        raise AppError.make("DUPLICATE_QUESTION_IN_PAPER", "同一题目不能重复加入试卷")

    found = {
        q.id: q
        for q in db.scalars(select(Question).where(Question.id.in_(ids)))
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise AppError.make("NOT_FOUND", f"题目不存在：{missing}")

    paper = Paper(
        title=payload.title,
        creator_id=creator_id,
        duration_minutes=payload.duration_minutes,
        start_at=payload.start_at,
        end_at=payload.end_at,
        status=PaperStatus.DRAFT,
        total_score=Decimal("0"),
        created_at=time_utils.now(),
    )
    db.add(paper)
    db.flush()

    total = Decimal("0")
    for order, item in enumerate(payload.questions, start=1):
        if item.score <= 0:
            raise AppError.make("INVALID_SCORE", "每题分值必须大于 0")
        db.add(
            PaperQuestion(
                paper_id=paper.id,
                question_id=item.question_id,
                score=Decimal(str(item.score)),
                sort_order=order,
            )
        )
        total += Decimal(str(item.score))
    paper.total_score = total
    db.commit()
    db.refresh(paper)
    return paper


def get_paper_or_not_visible(db: Session, paper_id: int, user_id: int, *, as_student: bool) -> Paper:
    """存在性隐藏：学生只看得见 published；老师只看得见自己创建的。"""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise AppError.make("PAPER_NOT_VISIBLE", "试卷不存在或不可见")
    if as_student:
        if paper.status != PaperStatus.PUBLISHED:
            raise AppError.make("PAPER_NOT_VISIBLE", "试卷不存在或不可见")
        return paper
    if paper.creator_id != user_id:
        raise AppError.make("PAPER_NOT_VISIBLE", "试卷不存在或不可见")
    return paper


def transition(db: Session, paper_id: int, user_id: int, target: str) -> Paper:
    paper = get_paper_or_not_visible(db, paper_id, user_id, as_student=False)
    if TRANSITIONS.get(paper.status) != target:
        raise AppError.make(
            "PAPER_STATE_CONFLICT", f"不允许的状态流转：{paper.status} → {target}"
        )
    if target == PaperStatus.PUBLISHED:
        count, total = _paper_score(db, paper.id)
        if count < 1 or total <= 0:
            raise AppError.make("EMPTY_PAPER", "试卷没有题目或总分为 0，不能发布")
        if paper.end_at <= time_utils.now():
            raise AppError.make("EXAM_ENDED", "考试窗口已结束，不能发布")
    paper.status = target
    db.commit()
    db.refresh(paper)
    return paper


def _paper_score(db: Session, paper_id: int) -> tuple[int, Decimal]:
    count = db.scalar(
        select(func.count()).select_from(PaperQuestion).where(PaperQuestion.paper_id == paper_id)
    )
    total = db.scalar(
        select(func.coalesce(func.sum(PaperQuestion.score), 0)).where(
            PaperQuestion.paper_id == paper_id
        )
    )
    return int(count or 0), Decimal(str(total or 0))


def paper_total_score(db: Session, paper_id: int) -> Decimal:
    _, total = _paper_score(db, paper_id)
    return total


def list_items(db: Session, paper_id: int) -> list[dict[str, Any]]:
    """卷面明细（题面 + 分值 + 答案），用于卷面详情与生成判分基线。"""
    rows = db.execute(
        select(PaperQuestion, Question)
        .join(Question, Question.id == PaperQuestion.question_id)
        .where(PaperQuestion.paper_id == paper_id)
        .order_by(PaperQuestion.sort_order)
    ).all()
    return [
        {
            "question_id": pq.question_id,
            "sort_order": pq.sort_order,
            "score": Decimal(str(pq.score)),
            "type": q.type,
            "content": q.content,
            "options": q.options_json,
            "correct_answer": q.correct_answer,
            "difficulty": q.difficulty,
        }
        for pq, q in rows
    ]


def build_snapshot(db: Session, paper: Paper) -> dict[str, Any]:
    """判分基线：开考瞬间冻结的题集 + 每题分值 + 正确答案（D2 §6.6）。"""
    items = list_items(db, paper.id)
    if not items:
        raise AppError.make("EMPTY_PAPER", "试卷没有题目，不能开考")
    return {
        "paper_id": paper.id,
        "title": paper.title,
        "duration_minutes": paper.duration_minutes,
        "full_score": float(sum((i["score"] for i in items), Decimal("0"))),
        "items": [
            {
                "question_id": i["question_id"],
                "sort_order": i["sort_order"],
                "score": float(i["score"]),
                "type": i["type"],
                "content": i["content"],
                "options": i["options"],
                "correct_answer": i["correct_answer"],
            }
            for i in items
        ],
    }


def snapshot_specs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """判分基线 → grade_svc.QuestionSpec 入参字典列表。"""
    from app.services.grade_svc import QuestionSpec

    return [
        QuestionSpec(
            question_id=i["question_id"],
            type=i["type"],
            correct_answer=i["correct_answer"],
            full_score=Decimal(str(i["score"])),
            sort_order=i["sort_order"],
        )
        for i in snapshot["items"]
    ]


def snapshot_item(snapshot: dict[str, Any], question_id: int) -> dict[str, Any]:
    """从判分基线中取一题。"""
    for item in snapshot["items"]:
        if item["question_id"] == question_id:
            return item
    raise AppError.make("QUESTION_NOT_IN_PAPER", f"题目 {question_id} 不在本卷判分基线内")


def list_papers_for_user(db: Session, user_id: int, role: str) -> list[Paper]:
    if role == "teacher":
        return list(db.scalars(select(Paper).where(Paper.creator_id == user_id).order_by(Paper.id.desc())))
    return list(
        db.scalars(
            select(Paper)
            .where(Paper.status == PaperStatus.PUBLISHED)
            .order_by(Paper.start_at.desc())
        )
    )
