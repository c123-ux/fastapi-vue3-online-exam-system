"""7 张表 ORM（唯一事实来源，Alembic autogenerate 目标）。

约定（D2 §4）：utf8mb4；所有 String 显式长度（MySQL 必需，SQLite 不报是隐蔽坑）；
分值 DECIMAL；时间 DATETIME(3) 且一律由应用层 time_utils.now() 写入，
表内不设数据库端默认时间（TC-S-01 静态断言会扫这一点）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# DATETIME(3)：消除秒级截断导致的宽限期边界抖动（D2 §4 通用约定）
DT3 = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql")
BigIntPK = BigInteger()


class Role:
    TEACHER = "teacher"
    STUDENT = "student"
    ALL = (TEACHER, STUDENT)


class QType:
    SINGLE = "single"
    MULTIPLE = "multiple"
    JUDGE = "judge"
    SHORT = "short"
    OBJECTIVE = (SINGLE, MULTIPLE, JUDGE)
    ALL = (SINGLE, MULTIPLE, JUDGE, SHORT)


class Difficulty:
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ALL = (EASY, MEDIUM, HARD)


class PaperStatus:
    DRAFT = "draft"
    PUBLISHED = "published"
    CLOSED = "closed"


class RecordStatus:
    IN_PROGRESS = "in_progress"
    FINAL = "final"


class SubmitKind:
    NORMAL = "normal"
    TIMEOUT = "timeout"


class ReviewState:
    """记录级批改进度。"""

    PENDING = "pending"
    FINAL = "final"


class ReviewStatus:
    """题级批阅状态（v1.2 与记录级刻意不同名不同值域，N12）。"""

    NEEDS_REVIEW = "needs_review"
    REVIEWED = "reviewed"
    SKIPPED = "skipped"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, comment="teacher|student")
    full_name: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DT3, nullable=False)

    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        {"comment": "用户；username 统一小写存储"},
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (UniqueConstraint("name", name="uq_categories_name"),)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    type: Mapped[str] = mapped_column(String(10), nullable=False, comment="single|multiple|judge|short")
    content: Mapped[str] = mapped_column(String(5000), nullable=False)
    options_json: Mapped[Any | None] = mapped_column(JSON, comment='2~6 项，如 ["A. x","B. y"]')
    correct_answer: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="规范见 D2 §6.1；short 允许空串"
    )
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DT3, nullable=False)

    __table_args__ = (
        Index("ix_questions_category_type", "category_id", "type"),
        # created_by 是 FK，InnoDB 会自动建索引；再显式声明一个单列索引是冗余
        # （实测冗余索引还会让 alembic downgrade 报 1553 "needed in a foreign key constraint"）
        {"comment": "题库"},
    )


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DT3, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DT3, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, comment="draft|published|closed")
    total_score: Mapped[Decimal] = mapped_column(Numeric(6, 1), nullable=False, comment="对外一律叫 full_score")
    created_at: Mapped[datetime] = mapped_column(DT3, nullable=False)

    __table_args__ = (
        Index("ix_papers_creator_status", "creator_id", "status"),
        {"comment": "试卷"},
    )


class PaperQuestion(Base):
    __tablename__ = "paper_questions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 1), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        # 左前缀已覆盖按 paper_id 的查询，不再单建普通索引（对需求文档的有意偏离，D2 §10.2 #5）；
        # question_id 由 FK 自动索引支撑引用检查与聚合，不重复声明
        UniqueConstraint("paper_id", "question_id", name="uq_pq_paper_question"),
        {"comment": "试卷-题目关联，逐题分值"},
    )


class ExamRecord(Base):
    __tablename__ = "exam_records"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DT3, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(
        DT3, nullable=False, comment="=min(started+duration,end_at)；仅 T1 写入，永不更新"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DT3)
    status: Mapped[str] = mapped_column(String(12), nullable=False, comment="in_progress|final")
    submit_kind: Mapped[str | None] = mapped_column(String(10), comment="normal|timeout；NULL=未交")
    review_state: Mapped[str | None] = mapped_column(String(10), comment="pending|final；NULL=未交")
    auto_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    earned_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), comment="已得总分")
    cheat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_json: Mapped[Any] = mapped_column(
        JSON, nullable=False, comment="判分基线（含正确答案，绝不出现在任何响应里）"
    )

    __table_args__ = (
        UniqueConstraint("paper_id", "student_id", name="uq_exam_paper_student"),
        Index("ix_exam_status_deadline", "status", "deadline_at"),
        Index("ix_exam_student_started", "student_id", "started_at"),
        {"comment": "考试记录（核心表，两维状态机）"},
    )


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("exam_records.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False)
    student_answer: Mapped[str] = mapped_column(String(1000), nullable=False, comment="未答=''")
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), comment="NULL=待批")
    review_status: Mapped[str | None] = mapped_column(
        String(15), comment="仅 short：needs_review|reviewed|skipped"
    )
    review_comment: Mapped[str | None] = mapped_column(String(255))
    answered_at: Mapped[datetime] = mapped_column(DT3, nullable=False)

    __table_args__ = (
        UniqueConstraint("record_id", "question_id", name="uq_answers_record_question"),
        # question_id 的聚合与引用检查由 FK 自动索引支撑，不重复声明（见 questions 表同款注释）
        {"comment": "作答明细（upsert 语义，支撑续考与判分补齐）"},
    )
