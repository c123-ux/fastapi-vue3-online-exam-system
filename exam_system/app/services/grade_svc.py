"""判分引擎（纯函数，零 DB / 零 Redis 依赖，D2 §6.1）。

输入输出全部是 dataclass，因此 TC-U-01~08 可完全脱库单测；
service 层负责把「判分基线 + answers 行」转成这些 dataclass。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from app.models import QType, ReviewStatus

_TRUE_TOKENS = {"T", "TRUE", "1", "对", "正确", "是", "√"}
_FALSE_TOKENS = {"F", "FALSE", "0", "错", "错误", "否", "×"}


@dataclass(frozen=True)
class QuestionSpec:
    """判分基线里的一道题。"""

    question_id: int
    type: str
    correct_answer: str
    full_score: Decimal
    sort_order: int = 0


@dataclass(frozen=True)
class GradeItem:
    question_id: int
    normalized_answer: str
    is_correct: bool | None
    score: Decimal
    review_status: str | None


@dataclass(frozen=True)
class GradeResult:
    items: tuple[GradeItem, ...]
    auto_score: Decimal
    pending_review_count: int

    @property
    def needs_review(self) -> bool:
        return self.pending_review_count > 0


def _to_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "T" if raw else "F"
    if isinstance(raw, (list, tuple, set)):
        return ",".join(str(x) for x in raw)
    return str(raw)


def normalize_single(raw: Any) -> str:
    return _to_text(raw).strip().upper()


def normalize_judge(raw: Any) -> str:
    token = _to_text(raw).strip().upper()
    if token in _TRUE_TOKENS:
        return "T"
    if token in _FALSE_TOKENS:
        return "F"
    return token


def normalize_multiple(raw: Any) -> str:
    """数组 → 去重、转大写、排序、逗号拼接（与 correct_answer 存储规范一致）。"""
    if raw is None:
        return ""
    parts = _to_text(raw).replace("，", ",").split(",")
    letters = {p.strip().upper() for p in parts if p.strip()}
    return ",".join(sorted(letters))


def normalize_answer(qtype: str, raw: Any) -> str:
    if qtype == QType.SINGLE:
        return normalize_single(raw)
    if qtype == QType.JUDGE:
        return normalize_judge(raw)
    if qtype == QType.MULTIPLE:
        return normalize_multiple(raw)
    # 简答题存原文但去掉首尾空白：只空一格/一个换行等同于没答（EC-14）
    return _to_text(raw).strip()


def grade_question(spec: QuestionSpec, raw_answer: Any) -> GradeItem:
    """单题判分。未答：客观题 0 分；简答 skipped 0 分（EC-14/16）。"""
    answer = normalize_answer(spec.type, raw_answer)
    answered = bool(answer)

    if spec.type == QType.SHORT:
        if not answered:
            return GradeItem(spec.question_id, "", False, Decimal("0"), ReviewStatus.SKIPPED)
        return GradeItem(spec.question_id, answer, None, None, ReviewStatus.NEEDS_REVIEW)

    correct = answered and answer == normalize_answer(spec.type, spec.correct_answer)
    return GradeItem(
        question_id=spec.question_id,
        normalized_answer=answer,
        is_correct=correct,
        score=spec.full_score if correct else Decimal("0"),
        review_status=None,
    )


def grade_paper(specs: Iterable[QuestionSpec], answers: dict[int, Any]) -> GradeResult:
    """整卷判分：未作答的题也会被补齐，保证 answers 覆盖判分基线全卷（EC-12/16）。"""
    items: list[GradeItem] = []
    auto = Decimal("0")
    pending = 0
    for spec in specs:
        item = grade_question(spec, answers.get(spec.question_id))
        items.append(item)
        if item.score is not None and spec.type != QType.SHORT:
            auto += item.score
        if item.review_status == ReviewStatus.NEEDS_REVIEW:
            pending += 1
    return GradeResult(items=tuple(items), auto_score=auto, pending_review_count=pending)


def review_score_allowed(score: Decimal, full_score: Decimal) -> bool:
    """批改给分边界：0 ≤ score ≤ 该题卷面分值（EC-27）。"""
    return Decimal("0") <= score <= full_score
