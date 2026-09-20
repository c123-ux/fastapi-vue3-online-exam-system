"""限时与窗口规则单元测试（TC-U-10~13）+ 题型校验矩阵（TC-U-15）。零 DB。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.exceptions import AppError
from app.services.exam_svc import (
    compute_deadline,
    remaining_seconds,
    session_ttl,
    window_too_short,
)
from app.services.question_svc import validate_question_payload

OPTS = ["A. 一", "B. 二", "C. 三", "D. 四"]


# ---------------------------------------------------------------- 时间纯函数
def test_deadline_uses_duration() -> None:
    start = datetime(2026, 3, 1, 9, 0, 0)
    end = start + timedelta(hours=5)
    assert compute_deadline(start, 10, end) == start + timedelta(minutes=10)


def test_deadline_is_capped_by_window() -> None:
    """B7：60 分钟的卷子只剩 20 分钟窗口 → deadline 取 end_at。"""
    start = datetime(2026, 3, 1, 9, 0, 0)
    end = start + timedelta(minutes=20)
    assert compute_deadline(start, 60, end) == end


@pytest.mark.parametrize(("seconds", "expected"), [(0, 600), (300, 300), (700, 0)])
def test_remaining_from_db_only(seconds: int, expected: int) -> None:
    start = datetime(2026, 3, 1, 9, 0, 0)
    deadline = start + timedelta(seconds=600)
    assert remaining_seconds(start + timedelta(seconds=seconds), deadline) == expected


def test_session_ttl_includes_grace() -> None:
    start = datetime(2026, 3, 1, 9, 0, 0)
    deadline = start + timedelta(seconds=600)
    assert session_ttl(start, deadline, 30) == 630


def test_session_ttl_goes_negative_after_grace() -> None:
    """A3(c)：宽限过后 ttl<=0，调用方必须走超时判定而不是写负 TTL。"""
    start = datetime(2026, 3, 1, 9, 0, 0)
    deadline = start + timedelta(seconds=600)
    assert session_ttl(start + timedelta(seconds=700), deadline, 30) == -70


def test_window_too_short() -> None:
    now = datetime(2026, 3, 1, 9, 0, 0)
    assert window_too_short(now, now + timedelta(seconds=30), 10) is True
    assert window_too_short(now, now + timedelta(hours=1), 10) is False


def test_window_floor_is_ratio_when_smaller() -> None:
    """下限取 min(60s, 时长×10%)：60 分钟卷下限 60s，10 分钟卷下限 60s，5 分钟卷下限 30s。"""
    now = datetime(2026, 3, 1, 9, 0, 0)
    assert window_too_short(now, now + timedelta(seconds=25), 5) is True
    assert window_too_short(now, now + timedelta(seconds=35), 5) is False


# ---------------------------------------------------------------- 题型校验矩阵
def test_valid_single_returns_normalized_answer() -> None:
    assert validate_question_payload("single", OPTS, " a ") == "A"


@pytest.mark.parametrize(
    ("qtype", "options", "answer"),
    [
        ("single", OPTS, "E"),      # 越界字母（只有 A~D）
        ("single", OPTS, "A,B"),    # 单选给了两个
        ("single", None, "A"),      # 单选缺 options
        ("multiple", OPTS, "A"),    # 多选正确项 <2
        ("multiple", OPTS, "A,Z"),  # 越界
        ("multiple", None, "A,B"),  # 缺 options
        ("judge", OPTS, "T"),       # 判断禁止 options
        ("judge", None, "Y"),       # 非 T/F
        ("short", OPTS, ""),        # 简答禁止 options
        ("single", OPTS, ""),       # 答案为空
    ],
)
def test_invalid_combos_raise_400(qtype: str, options: list[str] | None, answer: str) -> None:
    with pytest.raises(AppError) as exc:
        validate_question_payload(qtype, options, answer)
    assert exc.value.code == "INVALID_QUESTION"
    assert exc.value.http_status == 400


def test_judge_and_short_accept_valid_forms() -> None:
    assert validate_question_payload("judge", None, "t") == "T"
    assert validate_question_payload("short", None, "") == ""
    assert validate_question_payload("short", None, "原子性|持久性") == "原子性|持久性"
    assert validate_question_payload("multiple", OPTS, "c,a") == "A,C"
