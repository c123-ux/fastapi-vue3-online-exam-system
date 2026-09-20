"""种子数据：给 D3 的统计断言与 D4 的演示/截图提供真实数据集。

设计约束：
- 只允许连 exam_db / exam_test，别的库名直接退出（防误连本机其他项目的库）；
- 全程走 service 层真实路径：开考写判分基线、作答 upsert、交卷过 DB 条件更新闸门、
  超时靠 reap_expired、批改靠 review_answer —— 不手写"看起来像成绩的字段"；
- 固定随机种子，重复跑得到同一份数据。

用法（在项目根 exam_system 下）：
    .venv\\Scripts\\python.exe scripts\\seed.py            # 建数据并打印统计
    .venv\\Scripts\\python.exe scripts\\seed.py --reset    # 先清空业务表再灌
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import time_utils  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Answer,
    Category,
    ExamRecord,
    Paper,
    PaperStatus,
    QType,
    Role,
    User,
)
from app.schemas import PaperIn, PaperQuestionIn, QuestionIn  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import exam_svc, paper_svc, question_svc, stats_svc  # noqa: E402

ALLOWED_SCHEMA = {"exam_db", "exam_test"}
PASSWORD = "Passw0rd!"

# (type, content, options, correct_answer, difficulty, 卷面分值) —— 客观 70 + 简答 30 = 100
BANK: list[tuple[str, str, list[str] | None, str, str, float]] = [
    ("single", "1NF 的核心要求是什么？", ["A. 列不可再分", "B. 消除部分依赖", "C. 消除传递依赖", "D. 建立外键"], "A", "easy", 5),
    ("single", "MySQL InnoDB 默认隔离级别？", ["A. RU", "B. RC", "C. RR", "D. SERIALIZABLE"], "C", "easy", 5),
    ("single", "HTTP 429 表示？", ["A. 未认证", "B. 请求过多被限流", "C. 服务不可用", "D. 找不到资源"], "B", "medium", 5),
    ("single", "EXPLAIN 里 type=ALL 意味着？", ["A. 走覆盖索引", "B. 全表扫描", "C. 范围扫描", "D. 走主键"], "B", "medium", 5),
    ("single", "最左前缀原则适用于哪种索引？", ["A. 哈希索引", "B. 联合 B+Tree", "C. 全文索引", "D. 空间索引"], "B", "medium", 5),
    ("multiple", "哪些属于 ACID？", ["A. 原子性", "B. 一致性", "C. 可用性", "D. 持久性"], "A,B,D", "medium", 10),
    ("multiple", "哪些手段能防重复提交？", ["A. 状态机", "B. Redis SETNX", "C. 唯一索引", "D. 客户端重试"], "A,B,C", "hard", 10),
    ("multiple", "Redis 支持哪些数据结构？", ["A. String", "B. Hash", "C. Bitmap", "D. B 树索引"], "A,B,C", "easy", 15),
    ("judge", "Redis 单线程模型下命令是原子执行的。", None, "T", "easy", 5),
    ("judge", "MySQL 的 MyISAM 引擎支持事务。", None, "F", "easy", 5),
    ("short", "简述事务隔离级别各自要解决的问题。", None, "脏读|不可重复读|幻读", "hard", 15),
    ("short", "说说你会怎么设计一个限时交卷功能。", None, "服务端时钟|TTL|惰性清算|幂等提交", "hard", 15),
]


def guard_schema() -> str:
    name = engine.url.database or ""
    if name not in ALLOWED_SCHEMA:
        raise SystemExit(f"拒绝执行：当前库是 {name!r}，只允许 {sorted(ALLOWED_SCHEMA)}")
    return name


def reset(db: Session) -> None:
    db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    for table in ("answers", "exam_records", "paper_questions", "papers", "questions", "categories", "users"):
        db.execute(text(f"DELETE FROM {table}"))
    db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    db.commit()


def make_users(db: Session) -> tuple[list[User], list[User]]:
    now = time_utils.now()
    teachers = [User(username=f"teacher0{i}", hashed_password=hash_password(PASSWORD),
                     role=Role.TEACHER, full_name=f"老师0{i}", created_at=now) for i in (1, 2)]
    students = [User(username=f"stu{i:02d}", hashed_password=hash_password(PASSWORD),
                     role=Role.STUDENT, full_name=f"学生{i:02d}", created_at=now)
                for i in range(1, 41)]
    db.add_all(teachers + students)
    db.commit()
    return teachers, students


def make_questions(db: Session, teacher: User) -> list[int]:
    cats = {}
    for name in ("数据库", "Web 基础"):
        cat = db.scalar(select(Category).where(Category.name == name))
        if cat is None:
            cat = question_svc.create_category(db, name)
        cats[name] = cat.id
    ids: list[int] = []
    for idx, (qtype, content, options, answer, difficulty, _score) in enumerate(BANK):
        payload = QuestionIn(
            type=qtype, content=content, options=options, correct_answer=answer,
            category_id=cats["Web 基础"] if idx % 3 else cats["数据库"], difficulty=difficulty,
        )
        ids.append(question_svc.create_question(db, teacher.id, payload).id)
    return ids


def make_paper(db: Session, teacher: User, qids: list[int]) -> Paper:
    now = time_utils.now()
    payload = PaperIn(
        title="期末模拟卷",
        duration_minutes=30,
        start_at=now - timedelta(minutes=5),
        end_at=now + timedelta(days=1),
        questions=[PaperQuestionIn(question_id=q, score=s)
                   for q, (_t, _c, _o, _a, _d, s) in zip(qids, BANK)],
    )
    return paper_svc.create_paper(db, teacher.id, payload)


def wrong_answer(qtype: str, correct: str, rng: random.Random) -> str:
    """造一个"答错"的答案：单选/判断换值，多选少选一项。"""
    if qtype == QType.MULTIPLE:
        parts = [p for p in correct.split(",") if p]
        return ",".join(parts[1:]) or parts[0]  # 去掉首项 = 少选，判 0 分
    if qtype == QType.JUDGE:
        return "F" if correct == "T" else "T"
    pool = [c for c in "ABCD" if c != correct]
    return rng.choice(pool)


def fill_answers(db: Session, record: ExamRecord, rng: random.Random, ratio: float,
                 *, short_answered: bool) -> None:
    now = time_utils.now()
    for item in record.snapshot_json["items"]:
        qtype, correct = item["type"], item["correct_answer"]
        if qtype == QType.SHORT:
            value = "我把四个特性都写了一遍" if short_answered else ""
        else:
            value = correct if rng.random() < ratio else wrong_answer(qtype, correct, rng)
        db.add(Answer(record_id=record.id, question_id=item["question_id"],
                      student_answer=value, answered_at=now))
    db.commit()


def build_records(db: Session, paper: Paper, students: list[User], rng: random.Random) -> dict[str, int]:
    """40 人分布：20 正常交卷 / 8 超时交卷 / 2 过期未清算 / 5 进行中 / 5 缺考。"""
    plan = [("normal", 20), ("timeout", 8), ("stale", 2), ("running", 5)]
    counts: dict[str, int] = {k: 0 for k, _ in plan}
    counts["absent"] = 0
    cursor = 0
    for kind, amount in plan:
        for _ in range(amount):
            student = students[cursor]
            cursor += 1
            counts[kind] += 1
            record, _ = exam_svc.start_exam(db, student, paper.id)
            if kind == "running":
                continue
            fill_answers(db, record, rng, rng.choice([0.4, 0.55, 0.7, 0.85, 1.0]),
                         short_answered=(kind != "stale" and rng.random() < 0.6))
            if kind in ("timeout", "stale"):
                _expire(db, record)
            if kind == "timeout":
                # 已过期 + 立刻有读路径 → 当场被惰性清算成 timeout
                exam_svc.reap_expired(db, paper_id=paper.id)
            elif kind == "normal":
                exam_svc.submit_exam(db, student, record.id)
            # stale：过期但没人访问，留给主流程最后一次的兜底清算
    counts["absent"] = len(students) - cursor
    return counts


def _expire(db: Session, record: ExamRecord) -> None:
    """把 deadline 挪到过去（真实时间无法等 30 分钟；这也是 D3 §2.1 的用例手段）。"""
    db.execute(
        text("UPDATE exam_records SET deadline_at = :d WHERE id = :i"),
        {"d": time_utils.now() - timedelta(minutes=5), "i": record.id},
    )
    db.commit()


def review_half(db: Session, teacher: User) -> int:
    """一半待批简答批改掉，让两级均分的差异在演示数据里看得见。"""
    rows = db.execute(
        select(Answer.record_id, Answer.question_id)
        .where(Answer.review_status == "needs_review")
        .order_by(Answer.id)
    ).all()
    done = 0
    for i, (record_id, question_id) in enumerate(rows):
        if i % 2 == 0:
            # 12 分：必须 ≤ 简答题卷面分值，否则 review_answer 会挡（实测真的挡了）
            exam_svc.review_answer(db, teacher, record_id, question_id, 12.0, "要点基本齐全")
            done += 1
    return done


def report(db: Session, paper: Paper, teacher: User) -> dict[str, object]:
    data = stats_svc.paper_stats(db, teacher.id, paper.id)
    return {k: data[k] for k in ("finished_count", "timeout_count", "graded_count",
                                 "pending_review_count", "avg_score", "avg_scored_count",
                                 "avg_auto_score", "max_score", "min_score")}


def main() -> None:
    parser = argparse.ArgumentParser(description="灌入演示数据集")
    parser.add_argument("--reset", action="store_true", help="先清空业务表")
    args = parser.parse_args()
    schema = guard_schema()
    rng = random.Random(20260920)
    db = SessionLocal()
    try:
        if args.reset:
            reset(db)
        teachers, students = make_users(db)
        qids = make_questions(db, teachers[0])
        paper = make_paper(db, teachers[0], qids)
        paper = paper_svc.transition(db, paper.id, teachers[0].id, PaperStatus.PUBLISHED)
        counts = build_records(db, paper, students, rng)
        reviewed = review_half(db, teachers[0])
        reaped = exam_svc.reap_expired(db, paper_id=paper.id)
        print(f"[seed] schema={schema} paper_id={paper.id} 卷面满分={paper.total_score} 题数={len(qids)}")
        print(f"[seed] 记录分布={counts}")
        print(f"[seed] 批改待批={reviewed} 组，本次兜底清算={reaped} 条")
        print(f"[seed] 统计={report(db, paper, teachers[0])}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
