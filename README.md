# fastapi-vue3-online-exam-system

**全栈在线考试系统** | Full-Stack Online Exam System

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00)
![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-5.x-DC382D?logo=redis&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3.5-4FC08D?logo=vuedotjs&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?logo=typescript&logoColor=white)
![Element Plus](https://img.shields.io/badge/Element_Plus-2.9-409EFF)
![Tests](https://img.shields.io/badge/pytest-140%20passed-brightgreen)

---

## 中文

一个从零实现的教育场景**全栈在线考试系统**：老师建题库、组卷、发布考试；学生在服务端限时内作答、断点续考、交卷；客观题自动判分、简答题人工批改、成绩统计一键生成。定位是**真实可用的业务系统**（带 RBAC 权限、防重复提交、防作弊与判分基线防篡改），不是只读 demo。

### ✨ 功能特性

- **RBAC 双角色**：teacher / student；教师注册必须携带注册码，防止任何人自建教师账号
- **题库管理**：单选 / 多选 / 判断 / 简答四种题型，分类 + 难度标签
- **组卷与发布**：手动选题、逐题设分值；试卷发布后**题目即锁定**（保护判分基准）
- **考试链路**：服务端计时限时、断点续考、超时自动交卷（Redis TTL 驱动）、防重复提交（DB 条件更新权威闸门 + Redis SETNX 快速失败）
- **判分引擎**：客观题自动判（多选全对才给分），简答题留人工批改（仅出卷老师可批）
- **成绩统计**：两级均分口径（仅交卷 / 全量自动分）、每题正确率、最高最低分
- **防作弊**：前端切屏上报、后端计数留存

### 🔒 安全设计

- **快照 HMAC-SHA256 指纹**：开考即对「题集 + 分值 + 答案」快照签名；全项目 **13 处读快照路径全部验签**，验签先于业务闸门——直接改数据库改分会被 `409 SNAPSHOT_TAMPERED` 拒绝
- **权限判定顺序固定**：`401 → 403 → 404 → 400/409`；跨用户资源与"不存在"响应完全同形，杜绝自增 ID 顺序探测
- **纵深防御**：JWT 认证、bcrypt 密码哈希、登录/留言限流中间件、XSS 转义、错误统一体不泄露内部细节
- **无后门设计**：试卷发布后不提供任何修改题集/分值的接口

### 🧪 质量与测试

- **140 个 pytest 用例全绿**（单元 53 例 0.58s 零外部依赖 + 集成 87 例打真实 MySQL 与 Redis）
- `app` 整体覆盖率 **96%**（services 层 95.4%，卡关线 80%）

### 🛠 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic |
| 存储 | MySQL 8.0 · Redis 5.x（会话 / 限时 / 防重标记） |
| 认证 | JWT（python-jose）· passlib/bcrypt |
| 前端 | Vue 3.5 · TypeScript · Vite · Element Plus · Pinia · Axios |
| 测试 | pytest + TestClient（单元/集成分层，真实库集成） |

### 📁 目录结构

```
├── docs/            # D1 需求 / D2 开发 / D3 测试文档
├── exam_system/     # FastAPI 后端（app/ + alembic 迁移 + scripts + tests/）
│   └── README.md    # 详细文档：ER 图、API 一览、Windows 启动手册
└── frontend/        # Vue 3 + TypeScript 前端
```

### 🚀 快速开始

> 前置：本机可用的 MySQL 8.x 与 Redis；完整 Windows 启动手册（含踩坑说明）见 [exam_system/README.md](exam_system/README.md)

**1) 后端**

```powershell
cd exam_system
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # 填 DATABASE_URL / SECRET_KEY / TEACHER_REGISTER_CODE 等
alembic upgrade head          # 建表（需先 CREATE DATABASE exam_db）
uvicorn app.main:app --reload --port 8000
python scripts\seed.py --reset   # 可选：一键演示数据集（2 老师 + 40 学生 + 12 题 + 1 卷）
```

后端跑起来后：Swagger 文档在 `http://127.0.0.1:8000/docs`

**2) 前端**

```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173，/api 已代理到 8000
```

**3) 测试**（需要 .env 中的测试库与测试 Redis）

```powershell
pytest           # 单元 + 集成全量 140 例
```

---

## English

A **full-stack online exam system** for education scenarios, built from scratch: teachers manage question banks, compose papers and publish exams; students take timed exams server-side with resumable progress and auto-submit on timeout. Objective questions are graded automatically, essay questions go through manual review, and score statistics are generated per paper. Built as a **real, usable business system** — with RBAC, duplicate-submission protection, anti-cheating and tamper-proof grading baseline — not a read-only demo.

### ✨ Features

- **RBAC roles**: teacher / student; teacher registration requires an invite code
- **Question bank**: single-choice / multiple-choice / true-false / essay, with categories and difficulty
- **Paper management**: manual question picking with per-question scoring; questions are **locked once published** to protect the grading baseline
- **Exam flow**: server-side timing, resumable answers, auto-submit on timeout (Redis TTL), duplicate-submission guard (DB conditional update + Redis SETNX fast path)
- **Grading engine**: auto-grading for objective questions (multiple-choice requires all-correct), manual review for essays (paper creator only)
- **Statistics**: two-level average score, per-question accuracy, high/low scores
- **Anti-cheating**: tab-switch reporting with server-side counters

### 🔒 Security Design

- **Snapshot HMAC-SHA256 fingerprint**: the grading baseline (questions + scores + answers) is signed at exam start and **verified at all 13 read paths** before business gates — direct DB tampering is rejected with `409 SNAPSHOT_TAMPERED`
- **Fixed decision order** `401 → 403 → 404 → 400/409`; cross-user resources are indistinguishable from "not found" to prevent sequential ID probing
- **Defense in depth**: JWT auth, bcrypt hashing, rate-limiting middleware, XSS escaping, unified error bodies without internal leakage
- **No backdoor by design**: no endpoint can modify a published paper's questions or scores

### 🧪 Quality

- **140 pytest tests green** (53 unit tests, zero external deps, 0.58s + 87 integration tests against real MySQL & Redis)
- **96% overall coverage** on `app` (95.4% on the services layer, quality gate 80%)

### 🛠 Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic |
| Storage | MySQL 8.0 · Redis 5.x (session / timing / submission marks) |
| Auth | JWT (python-jose) · passlib/bcrypt |
| Frontend | Vue 3.5 · TypeScript · Vite · Element Plus · Pinia · Axios |
| Testing | pytest + TestClient (unit / integration against real databases) |

### 🚀 Quick Start

> Prerequisites: local MySQL 8.x and Redis. Full Windows manual (with pitfalls) in [exam_system/README.md](exam_system/README.md)

```powershell
# Backend
cd exam_system
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # fill in DATABASE_URL / SECRET_KEY / TEACHER_REGISTER_CODE
alembic upgrade head
uvicorn app.main:app --reload --port 8000
python scripts\seed.py --reset   # optional demo dataset

# Frontend (new terminal)
cd frontend
npm install
npm run dev      # http://localhost:5173, /api proxied to :8000
```

Swagger docs: `http://127.0.0.1:8000/docs` · Tests: `pytest`
