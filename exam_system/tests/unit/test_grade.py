"""判分引擎单元测试（TC-U-01~09、U-16）。零 MySQL / 零 Redis。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models import QType, ReviewStatus
from app.services.grade_svc import (
    GradeResult,
    QuestionSpec,
    grade_paper,
    grade_question,
    normalize_judge,
    normalize_multiple,
    normalize_single,
    review_score_allowed,
)


def spec(qid: int, qtype: str, answer: str, score: str) -> QuestionSpec:
    return QuestionSpec(qid, qtype, answer, Decimal(score), qid)


# ---------------------------------------------------------------- 单选 / 判断
def test_single_correct_and_wrong() -> None:
    q = spec(1, QType.SINGLE, "A", "5")
    assert grade_question(q, "A").is_correct is True
    assert grade_question(q, "A").score == Decimal("5")
    assert grade_question(q, "B").is_correct is False
    assert grade_question(q, "B").score == Decimal("0")


def test_single_normalizes_case_and_blank() -> None:
    q = spec(1, QType.SINGLE, "A", "5")
    assert grade_question(q, " a ").is_correct is True
    assert normalize_single("  b\n") == "B"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("T", "T"), ("true", "T"), ("对", "T"), ("正确", "T"), (True, "T"),
     ("F", "F"), ("false", "F"), ("错", "F"), (False, "F"), ("随便", "随便")],
)
def test_judge_normalization(raw: object, expected: str) -> None:
    assert normalize_judge(raw) == expected


def test_judge_grading() -> None:
    q = spec(6, QType.JUDGE, "T", "5")
    assert grade_question(q, "对").is_correct is True
    assert grade_question(q, "错").is_correct is False


# ---------------------------------------------------------------- 多选（决策 #1）
def test_multiple_order_insensitive_full_match() -> None:
    q = spec(4, QType.MULTIPLE, "A,C", "10")
    assert grade_question(q, ["C", "A"]).is_correct is True
    assert grade_question(q, ["C", "A"]).score == Decimal("10")


@pytest.mark.parametrize("answer", [["A"], ["A", "E"], ["A", "C", "D"], []])
def test_multiple_partial_wrong_extra_blank_all_zero(answer: list[str]) -> None:
    q = spec(4, QType.MULTIPLE, "A,C", "10")
    item = grade_question(q, answer)
    assert item.is_correct is False
    assert item.score == Decimal("0")


def test_multiple_deduplicates() -> None:
    assert normalize_multiple(["A", "A", "c"]) == "A,C"
    q = spec(4, QType.MULTIPLE, "A,C", "10")
    assert grade_question(q, ["A", "A", "C"]).is_correct is True


def test_multiple_accepts_comma_string_and_fullwidth() -> None:
    assert normalize_multiple("c，a") == "A,C"


# ---------------------------------------------------------------- 简答（决策 #2 + EC-14）
def test_short_answered_goes_to_review() -> None:
    q = spec(8, QType.SHORT, "原子性|一致性", "30")
    item = grade_question(q, "事务有四个特性……")
    assert item.review_status == ReviewStatus.NEEDS_REVIEW
    assert item.score is None
    assert item.is_correct is None


def test_short_unanswered_is_skipped_zero() -> None:
    q = spec(8, QType.SHORT, "原子性|一致性", "30")
    for blank in ("", None, "   "):
        item = grade_question(q, blank)
        assert item.review_status == ReviewStatus.SKIPPED
        assert item.score == Decimal("0")


# ---------------------------------------------------------------- 整卷聚合
def object_specs() -> list[QuestionSpec]:
    return [
        spec(1, QType.SINGLE, "A", "5"),
        spec(2, QType.SINGLE, "B", "5"),
        spec(3, QType.SINGLE, "C", "5"),
        spec(4, QType.MULTIPLE, "A,C", "10"),
        spec(5, QType.MULTIPLE, "B,D", "10"),
        spec(6, QType.JUDGE, "T", "5"),
        spec(7, QType.JUDGE, "F", "5"),
        spec(9, QType.SINGLE, "D", "10"),
        spec(10, QType.MULTIPLE, "A,B,C", "15"),
        spec(8, QType.SHORT, "原子性|一致性|隔离性|持久性", "30"),
    ]


def test_sample_s1_all_correct_with_short() -> None:
    answers = {1: "A", 2: "B", 3: "C", 4: ["A", "C"], 5: ["B", "D"], 6: "T", 7: "F",
               8: "事务具有原子性、一致性、隔离性、持久性", 9: "D", 10: ["A", "B", "C"]}
    result = grade_paper(object_specs(), answers)
    assert result.auto_score == Decimal("70")  # 客观题满分 70，简答不参与自动分
    assert result.pending_review_count == 1
    assert result.needs_review is True
    assert len(result.items) == 10


def test_sample_s2_all_correct_without_short() -> None:
    answers = {1: "A", 2: "B", 3: "C", 4: ["A", "C"], 5: ["B", "D"], 6: "T", 7: "F",
               9: "D", 10: ["A", "B", "C"]}
    result = grade_paper(object_specs(), answers)
    assert result.auto_score == Decimal("70")
    assert result.pending_review_count == 0  # 未答简答 skipped，不进待批
    short_item = next(i for i in result.items if i.question_id == 8)
    assert short_item.review_status == ReviewStatus.SKIPPED
    assert short_item.score == Decimal("0")


def test_sample_s3_mixed_mistakes() -> None:
    answers = {1: "A", 2: "X", 3: "C", 4: ["A"], 5: ["B", "D"], 6: "对", 7: "F", 9: "A",
               10: ["A", "B", "C", "D"]}
    result = grade_paper(object_specs(), answers)
    # 5(Q1) + 5(Q3) + 10(Q5) + 5(Q6) + 5(Q7) = 30
    assert result.auto_score == Decimal("30")
    assert result.pending_review_count == 0


def test_blank_paper_covers_every_question() -> None:
    result = grade_paper(object_specs(), {})
    assert len(result.items) == 10
    assert result.auto_score == Decimal("0")
    assert all(i.score is not None for i in result.items)
    assert sum(1 for i in result.items if i.is_correct is False) == 10


def test_grade_result_is_frozen_dataclass() -> None:
    result = grade_paper(object_specs(), {})
    assert isinstance(result, GradeResult)
    with pytest.raises(Exception):
        result.auto_score = Decimal("1")  # type: ignore[misc]


# ---------------------------------------------------------------- 批改边界
@pytest.mark.parametrize(("score", "ok"), [("0", True), ("30", True), ("29.5", True),
                                           ("-1", False), ("30.5", False)])
def test_review_score_bounds(score: str, ok: bool) -> None:
    assert review_score_allowed(Decimal(score), Decimal("30")) is ok
