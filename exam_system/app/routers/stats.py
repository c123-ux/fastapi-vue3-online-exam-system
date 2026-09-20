"""成绩与统计路由（仅该卷创建者；非创建者 404 隐藏存在性）。"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas import ResultsOut, StatsOut
from app.security import CurrentTeacher, DbSession
from app.services import stats_svc

router = APIRouter(prefix="/api/papers", tags=["stats"])


@router.get("/{paper_id}/results", response_model=ResultsOut)
def results(paper_id: int, db: DbSession, teacher: CurrentTeacher) -> ResultsOut:
    return ResultsOut.model_validate(stats_svc.paper_results(db, teacher.id, paper_id))


@router.get("/{paper_id}/stats", response_model=StatsOut)
def stats(paper_id: int, db: DbSession, teacher: CurrentTeacher) -> StatsOut:
    return StatsOut.model_validate(stats_svc.paper_stats(db, teacher.id, paper_id))
