"""Redis 封装：限时会话与提交标记（D2 §6.2）。

三条实测前提：
1. 本机 Redis 5.0.14.1 不认 HELLO ⇒ 客户端必须 `protocol=2`，否则连接阶段直接抛错；
2. Hash 不会自带 TTL（HSET 后 TTL=-1）⇒ 会话用 `SET <json> EX <ttl>` 原子写入；
3. TTL 语义：>0 剩余秒；-1 存在但永不过期（异常态）；-2 不存在。
"""
from __future__ import annotations

import json
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.config import settings

SESSION_KEY = "exam:session:{rid}"
SUBMIT_KEY = "exam:submit:{rid}"

_client: Redis | None = None


def get_redis() -> Redis:
    """惰性单例。调用方一律 `redis_client.get_redis()`，便于测试替换。"""
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            protocol=2,  # 必须：Redis 5.0 无 HELLO/RESP3
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            health_check_interval=30,
        )
    return _client


def reset_client() -> None:
    """测试切换 REDIS_URL 后重建连接池。"""
    global _client
    if _client is not None:  # pragma: no cover
        _client.close()
    _client = None


def ping() -> bool:
    try:
        return bool(get_redis().ping())
    except RedisError:
        return False


def write_session(rid: int, payload: dict[str, Any], ttl: int) -> None:
    """原子写入会话并带 TTL；ttl 必须 >= 1（<=0 由调用方先判超时）。"""
    key = SESSION_KEY.format(rid=rid)
    get_redis().set(key, json.dumps(payload, ensure_ascii=False), ex=max(1, int(ttl)))


def read_session(rid: int) -> dict[str, Any] | None:
    raw = get_redis().get(SESSION_KEY.format(rid=rid))
    if raw is None:
        return None
    return json.loads(raw)


def session_ttl(rid: int) -> int:
    return int(get_redis().ttl(SESSION_KEY.format(rid=rid)))


def refresh_session_ttl(rid: int, ttl: int) -> None:
    """TTL=-1 异常态：按 DB deadline 重设过期（D2 §6.3.4）。"""
    get_redis().expire(SESSION_KEY.format(rid=rid), max(1, int(ttl)))


def delete_session(rid: int) -> None:
    get_redis().delete(SESSION_KEY.format(rid=rid))


def submit_marked(rid: int) -> bool:
    return bool(get_redis().exists(SUBMIT_KEY.format(rid=rid)))


def mark_submitted(rid: int, ttl: int = 86400) -> None:
    """只在 DB 事务 commit 成功之后调用（D2 §6.4 第 6 步）。"""
    get_redis().set(SUBMIT_KEY.format(rid=rid), str(int(_now_ts())), ex=ttl)


def clear_submit_mark(rid: int) -> None:
    get_redis().delete(SUBMIT_KEY.format(rid=rid))


def _now_ts() -> float:
    from app import time_utils

    return time_utils.now().timestamp()
