"""Pydantic 请求/响应模型。

两条硬规则（D2 §5.4）：
1. 所有长度上限在这里先拦（MySQL STRICT 模式下超长是 1406 报错而不是截断）；
2. 响应一律用白名单模型，绝不直出 exam_records（该表 snapshot_json 含正确答案）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.models import Difficulty, QType, Role


# --------------------------------------------------------------------------- 入参
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=8, max_length=64)
    role: Literal[Role.TEACHER, Role.STUDENT]
    full_name: str | None = Field(default=None, max_length=50)
    teacher_code: str | None = Field(default=None, max_length=64)


class TokenIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=64)


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class QuestionIn(BaseModel):
    """题型结构校验（矩阵见 D1 FR-02）。跨字段业务校验在 question_svc，返回 400。"""

    type: Literal[QType.SINGLE, QType.MULTIPLE, QType.JUDGE, QType.SHORT]
    content: str = Field(min_length=1, max_length=5000)
    options: list[str] | None = Field(default=None, max_length=6)
    correct_answer: str = Field(default="", max_length=255)
    category_id: int | None = None
    difficulty: Literal[Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD] = Difficulty.MEDIUM

    @field_validator("options")
    @classmethod
    def _options_shape(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) < 2:
            raise ValueError("选择题选项至少 2 个")
        if any((not item) or len(item) > 500 for item in v):
            raise ValueError("选项不能为空且每个不超过 500 字符")
        return v


class PaperQuestionIn(BaseModel):
    question_id: int
    score: float = Field(gt=0, le=9999)


class PaperIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    duration_minutes: int = Field(gt=0, le=600)
    start_at: datetime
    end_at: datetime
    questions: list[PaperQuestionIn] = Field(min_length=1)


class AnswerIn(BaseModel):
    question_id: int
    answer: str | list[str] | bool | None = None


class ReviewIn(BaseModel):
    question_id: int
    score: float = Field(ge=0, le=9999)
    comment: str | None = Field(default=None, max_length=255)


class CheatIn(BaseModel):
    reason: str | None = Field(default=None, max_length=100)


# -------------------------------------------------------------------------- 出参
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    full_name: str | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class QuestionOut(BaseModel):
    """老师侧题目（含答案）。学生侧任何响应不使用本模型。

    options 在 ORM 上叫 options_json：不加 AliasChoices 时 from_attributes 会静默取不到值，
    于是 PUT 回来的 body 缺 options → 被 400 挡下（实测踩到，见 D4 问题清单）。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: int
    type: str
    content: str
    options: Any | None = Field(
        default=None, validation_alias=AliasChoices("options", "options_json")
    )
    correct_answer: str
    difficulty: str
    category_id: int | None = None


class QuestionBrief(BaseModel):
    """学生侧题面白名单：没有 correct_answer，也没有 difficulty。"""

    question_id: int
    sort_order: int
    type: str
    score: float
    content: str
    options: Any | None = None


class QuestionListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[QuestionOut]


class PaperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    duration_minutes: int
    start_at: datetime
    end_at: datetime
    status: str
    full_score: float


class PaperItemOut(BaseModel):
    question_id: int
    sort_order: int
    score: float
    type: str
    content: str
    options: Any | None = None
    correct_answer: str | None = None


class PaperDetailOut(PaperOut):
    creator_id: int
    items: list[PaperItemOut]


class PaperListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    duration_minutes: int
    start_at: datetime
    end_at: datetime
    status: str


def build_paper_out(paper: Any) -> PaperOut:
    """papers.total_score（DB 名）→ full_score（对外唯一名），避免同名两义（B9）。"""
    return PaperOut(
        id=paper.id,
        title=paper.title,
        duration_minutes=paper.duration_minutes,
        start_at=paper.start_at,
        end_at=paper.end_at,
        status=paper.status,
        full_score=float(paper.total_score),
    )


class StartOut(BaseModel):
    record_id: int
    paper_id: int
    paper_title: str
    duration_minutes: int
    remaining_seconds: int
    deadline_at: datetime
    resumed: bool
    questions: list[QuestionBrief]
    answers: dict[str, str] = Field(default_factory=dict)


class SavedOut(BaseModel):
    saved: bool
    answered_count: int
    remaining_seconds: int


class SubmitOut(BaseModel):
    record_id: int
    status: str
    submit_kind: str
    auto_score: float
    earned_score: float
    full_score: float
    pending_review_count: int
    review_state: str
    submitted_at: datetime | None = None


class ScoreDetailOut(BaseModel):
    question_id: int
    type: str
    sort_order: int
    full_score: float
    score: float | None = None
    is_correct: bool | None = None
    review_status: str | None = None
    review_comment: str | None = None


class ScoreOut(BaseModel):
    """学生/老师通用：不含答案，也不含 student_answer。"""

    record_id: int
    paper_id: int
    paper_title: str
    status: str
    submit_kind: str | None = None
    review_state: str | None = None
    auto_score: float | None = None
    manual_score: float = 0.0
    earned_score: float | None = None
    full_score: float
    pending_review_count: int
    submitted_at: datetime | None = None
    deadline_at: datetime
    is_finalized: bool
    details: list[ScoreDetailOut]


class AnswerItemOut(BaseModel):
    question_id: int
    type: str
    sort_order: int
    full_score: float
    student_answer: str
    review_status: str | None = None
    score: float | None = None
    review_comment: str | None = None
    reference_answer: str | None = None


class AnswersOut(BaseModel):
    """批改入口，仅该卷创建者可见。"""

    record_id: int
    paper_id: int
    student: UserOut
    pending_review_count: int
    items: list[AnswerItemOut]


class RecordBriefOut(BaseModel):
    record_id: int
    paper_id: int
    paper_title: str
    status: str
    submit_kind: str | None = None
    review_state: str | None = None
    earned_score: float | None = None
    full_score: float
    started_at: datetime
    deadline_at: datetime
    submitted_at: datetime | None = None
    remaining_seconds: int
    cheat_count: int


class RecordListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[RecordBriefOut]


class ResultRowOut(BaseModel):
    record_id: int
    student_id: int
    username: str
    full_name: str | None = None
    status: str
    submit_kind: str | None = None
    review_state: str | None = None
    earned_score: float | None = None
    auto_score: float | None = None
    full_score: float
    submitted_at: datetime | None = None
    cheat_count: int


class ResultsOut(BaseModel):
    paper_id: int
    finished_count: int
    reaped_count: int
    rows: list[ResultRowOut]


class QuestionStatOut(BaseModel):
    """客观题给 correct_count/correct_rate（简答题这两项为 null）；简答题给 avg_score。"""

    question_id: int
    type: str
    full_score: float
    answered_count: int | None = None
    correct_count: int | None = None
    correct_rate: float | None = None
    avg_score: float | None = None


class StatsOut(BaseModel):
    paper_id: int
    finished_count: int
    timeout_count: int
    graded_count: int
    pending_review_count: int
    reaped_count: int
    avg_score: float | None = None
    avg_scored_count: int = 0
    max_score: float | None = None
    min_score: float | None = None
    avg_auto_score: float | None = None
    question_stats: list[QuestionStatOut]


class ReviewOut(BaseModel):
    record_id: int
    question_id: int
    score: float
    earned_score: float
    pending_review_count: int
    review_state: str


class CheatOut(BaseModel):
    record_id: int
    cheat_count: int


class OkOut(BaseModel):
    ok: bool = True


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    deps: dict[str, str]
