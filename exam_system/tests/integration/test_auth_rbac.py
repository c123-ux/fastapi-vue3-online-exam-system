"""TC-I-01~05：注册、登录、教师注册码、RBAC 判定顺序、存在性隐藏。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import CODE, PASSWORD


def test_student_and_teacher_register_and_login(client: TestClient, t1: dict, s1: dict) -> None:
    """TC-I-01 两个角色都能注册登录，token 里 role 正确，注册响应不含密码哈希。"""
    assert _role_from_token(t1["Authorization"][7:]) == "teacher"
    assert _role_from_token(s1["Authorization"][7:]) == "student"
    body = client.post(
        "/api/auth/register",
        json={"username": "stu09", "password": PASSWORD, "role": "student", "full_name": "孙九"},
    ).json()
    assert body["role"] == "student" and body["full_name"] == "孙九"
    assert "hashed_password" not in body and "password" not in body


def _role_from_token(token: str) -> str:
    from app.security import decode_token

    return str(decode_token(token)["role"])


def test_teacher_register_code_required(client: TestClient) -> None:
    """TC-I-02 无码/错码注册 teacher → 403（EC-26）。"""
    for code in (None, "wrong"):
        body = {"username": f"teach_{code}", "password": PASSWORD, "role": "teacher"}
        if code is not None:
            body["teacher_code"] = code
        r = client.post("/api/auth/register", json=body)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "INVALID_TEACHER_CODE"
    ok = client.post(
        "/api/auth/register",
        json={"username": "teach_ok", "password": PASSWORD, "role": "teacher", "teacher_code": CODE},
    )
    assert ok.status_code == 200


def test_username_is_case_insensitive(client: TestClient) -> None:
    """TC-I-03 先注册 ABC 再注册 abc → 409（应用层 lower 归一 + collation 双保险）。"""
    first = client.post(
        "/api/auth/register", json={"username": "ABCdef", "password": PASSWORD, "role": "student"}
    )
    assert first.status_code == 200
    assert first.json()["username"] == "abcdef"
    second = client.post(
        "/api/auth/register", json={"username": "ABCDEF", "password": PASSWORD, "role": "student"}
    )
    assert second.status_code == 409
    assert second.json()["code"] == "USERNAME_EXISTS"
    # 用任意大小写都能登录（行为契约）
    login = client.post("/api/auth/token", json={"username": "AbCdEf", "password": PASSWORD})
    assert login.status_code == 200


def test_student_cannot_touch_teacher_apis(client: TestClient, s1: dict) -> None:
    """TC-I-04 学生访问老师接口一律 403（角色不符优先于 404）。"""
    calls = [
        ("post", "/api/questions", {"type": "single", "content": "x",
                                    "options": ["A. 1", "B. 2"], "correct_answer": "A",
                                    "difficulty": "easy"}),
        ("post", "/api/categories", {"name": "新分类"}),
        ("post", "/api/papers", {"title": "t", "duration_minutes": 10,
                                 "start_at": "2026-03-01T08:00:00", "end_at": "2026-03-01T09:00:00",
                                 "questions": [{"question_id": 1, "score": 5}]}),
    ]
    for method, url, body in calls:
        r = getattr(client, method)(url, json=body, headers=s1)
        assert r.status_code == 403, f"{url} -> {r.status_code} {r.text}"
        assert r.json()["code"] == "ROLE_FORBIDDEN"
    for url in ("/api/papers/1/stats", "/api/papers/1/results", "/api/exams/1/answers"):
        assert client.get(url, headers=s1).status_code == 403


def test_auth_required_before_anything_else(client: TestClient) -> None:
    """TC-I-05 判定顺序：无 token=401（不是 403/404）；伪造=401（EC-21/25）。"""
    assert client.get("/api/questions").status_code == 401
    assert client.get("/api/exams/1/score").status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    r = client.get("/api/questions", headers=bad)
    assert r.status_code == 401
    assert r.json()["code"] == "TOKEN_INVALID"
    # 错误密码不区分"用户不存在"与"密码错"，避免用户名枚举
    wrong = client.post("/api/auth/token", json={"username": "ghost", "password": "whatever123"})
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"


def test_health_is_liveness_and_always_200(client: TestClient) -> None:
    """/health 恒 200，依赖状态写在 body 里（D2 §5.2 / B17）。"""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert set(body["deps"]) == {"mysql", "redis"}
