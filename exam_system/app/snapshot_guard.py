"""快照完整性守卫：判分基线的 HMAC-SHA256 指纹（复核 B6 第三层防线）。

威胁模型：拿到数据库写权限的人直接改 exam_records.snapshot_json（正确答案/分值/题干）
→ 判分按被改的基线执行（教学实验实证：改快照答案，错题当场得满分）。
对策：开考建快照时计算 HMAC-SHA256 指纹存 snapshot_hash；任何读取快照的路径
（判分/批改/续考会话/成绩/作答明细/我的记录/全班成绩）先经 verified_snapshot() 验签，
签名缺失或不匹配一律 409 SNAPSHOT_TAMPERED，不给脏基线任何落库机会。
密钥复用 JWT 的 SECRET_KEY（单机项目足够；生产应使用独立密钥并限制 DB 写权限）。
验签时点统一在"抢闸/写状态之前"——脏基线的记录连提交状态都进不去。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.config import settings
from app.exceptions import AppError


def canonical_json(snapshot: dict[str, Any]) -> str:
    """规范化序列化：键排序 + 紧凑分隔符，保证"同一内容 = 同一指纹"（与键序无关）。"""
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_snapshot(snapshot: dict[str, Any]) -> str:
    """计算快照指纹。仅在开考建快照（exam_svc.start_exam）与迁移回填时调用。"""
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        canonical_json(snapshot).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verified_snapshot(record: Any) -> dict[str, Any]:
    """验签并返回快照。签名缺失视同被篡改——不给"未签名基线"留绕行口子。"""
    snapshot = record.snapshot_json
    digest = record.snapshot_hash
    if not digest or not hmac.compare_digest(digest, sign_snapshot(snapshot)):
        raise AppError.make("SNAPSHOT_TAMPERED", "判分基线完整性校验失败：快照缺失签名或已被篡改")
    return snapshot
