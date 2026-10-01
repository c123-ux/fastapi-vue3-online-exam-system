"""快照指纹红队（SNAPSHOT_TAMPERED）：判分基线完整性校验。

威胁模型与教学实验一致：绕过应用层直接改库（snapshot_json 或 snapshot_hash）
→ 任何读取判分基线的路径必须拒绝服务，且不产生任何状态副作用。
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL


def _tamper_content(rid: int) -> None:
    """模拟攻击者：把快照里第一题的正确答案改成 D（内容变 → 指纹失配）。"""
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE exam_records SET snapshot_json = "
                "JSON_SET(snapshot_json, '$.items[0].correct_answer', 'D') WHERE id = :i"
            ),
            {"i": rid},
        )
    engine.dispose()


def _break_hash(rid: int) -> None:
    """模拟攻击者：连指纹一起改（指纹与内容失配）。"""
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE exam_records SET snapshot_hash = '0' WHERE id = :i"), {"i": rid}
        )
    engine.dispose()


def _record_status(rid: int) -> str | None:
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        out = conn.execute(
            text("SELECT status FROM exam_records WHERE id = :i"), {"i": rid}
        ).scalar()
    engine.dispose()
    return out


def test_tamper_content_blocks_submit(client: TestClient, s1: dict[str, str], open_exam: dict[str, Any]) -> None:
    """改快照答案后交卷 → 409，且记录保持 in_progress（验签先于抢闸，零副作用）。"""
    rid = open_exam["record_id"]
    _tamper_content(rid)
    r = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "SNAPSHOT_TAMPERED"
    assert _record_status(rid) == "in_progress"


def test_tamper_hash_blocks_score_read(client: TestClient, s1: dict[str, str], open_exam: dict[str, Any]) -> None:
    """正常交卷后再改指纹 → 连读成绩都被拒绝（读模型同样验签）。"""
    rid = open_exam["record_id"]
    sub = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert sub.status_code == 200, sub.text
    _break_hash(rid)
    r = client.get(f"/api/exams/{rid}/score", headers=s1)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "SNAPSHOT_TAMPERED"


def test_untampered_record_still_submits(client: TestClient, s1: dict[str, str], open_exam: dict[str, Any]) -> None:
    """对照组：未篡改的记录交卷照常出分——守卫不误伤正常路径。"""
    rid = open_exam["record_id"]
    sub = client.post(f"/api/exams/{rid}/submit", headers=s1)
    assert sub.status_code == 200, sub.text
    assert sub.json()["status"] == "final"
