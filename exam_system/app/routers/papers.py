"""试卷路由：建卷、列表、详情、发布、关闭（D1 FR-03/FR-04）。

本期故意不提供 PUT/DELETE /api/papers —— published 后题集与每题分值不可变，
没有接口就没有破坏判分基准的后门（D2 §6.6）。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.models import PaperStatus
from app.schemas import (
    PaperDetailOut,
    PaperIn,
    PaperItemOut,
    PaperOut,
    build_paper_out,
)
from app.security import CurrentTeacher, CurrentUser, DbSession
from app.services import paper_svc

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.post("", response_model=PaperOut, summary="创建试卷", description="教师创建试卷并加入题目，每题独立设分值。")
def create_paper(payload: PaperIn, db: DbSession, teacher: CurrentTeacher) -> PaperOut:
    """返回新建试卷；初始状态 draft，需 publish 后才对学生可见。"""
    return build_paper_out(paper_svc.create_paper(db, teacher.id, payload))


@router.get("", response_model=list[PaperOut], summary="试卷列表", description="教师看自己创建的全部；学生只看已发布试卷。")
def list_papers(db: DbSession, user: CurrentUser) -> list[PaperOut]:
    return [
        build_paper_out(p) for p in paper_svc.list_papers_for_user(db, user.id, user.role)
    ]


@router.get("/{paper_id}", response_model=PaperDetailOut, summary="试卷详情", description="含题面、分值、正确答案；仅创建者老师可见。")
def paper_detail(paper_id: int, db: DbSession, teacher: CurrentTeacher) -> PaperDetailOut:
    paper = paper_svc.get_paper_or_not_visible(db, paper_id, teacher.id, as_student=False)
    base = build_paper_out(paper).model_dump()
    items = [
        PaperItemOut(
            question_id=i["question_id"],
            sort_order=i["sort_order"],
            score=float(i["score"]),
            type=i["type"],
            content=i["content"],
            options=i["options"],
            correct_answer=i["correct_answer"],
        )
        for i in paper_svc.list_items(db, paper_id)
    ]
    return PaperDetailOut(**base, creator_id=paper.creator_id, items=items)


@router.post("/{paper_id}/publish", response_model=PaperOut, summary="发布试卷", description="发布后对学生可见；发布后题集与分值不可变更。")
def publish(paper_id: int, db: DbSession, teacher: CurrentTeacher) -> PaperOut:
    return build_paper_out(
        paper_svc.transition(db, paper_id, teacher.id, PaperStatus.PUBLISHED)
    )


@router.post("/{paper_id}/close", response_model=PaperOut, summary="关闭试卷", description="关闭后学生不能再开考；进行中记录按原 deadline 自然清算。")
def close(paper_id: int, db: DbSession, teacher: CurrentTeacher) -> PaperOut:
    return build_paper_out(
        paper_svc.transition(db, paper_id, teacher.id, PaperStatus.CLOSED)
    )
