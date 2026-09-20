"""单点时钟：全项目唯一取"当前时间"的入口（D2 §6.5）。

约定：
- 业务代码一律 `from app import time_utils` 后调 `time_utils.now()`，
  不要 `from app.time_utils import now`——那样调用方持有的是函数对象引用，
  测试 monkeypatch 模块属性时不会生效。
- SQL 里禁止出现 NOW()/CURRENT_TIMESTAMP/func.now()（TC-S-01 静态断言），
  否则注入的假时间不生效，超时用例会退化成真等。
- 时间统一为服务端本地 naive datetime（D1 附录 B-8）。
"""
from __future__ import annotations

from datetime import datetime, timedelta


def now() -> datetime:
    """当前服务端时间（naive 本地）。"""
    return datetime.now()


def plus(seconds: float) -> datetime:
    """now() + seconds，用于算 deadline。"""
    return now() + timedelta(seconds=seconds)


def delta_seconds(later: datetime, earlier: datetime) -> float:
    """两个时间点相差的秒数（later - earlier）。"""
    return (later - earlier).total_seconds()
