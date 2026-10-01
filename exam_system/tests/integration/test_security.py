"""安全与中间件测试（TC-S-03~08）。"""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient


def test_cors_headers_present(client: TestClient) -> None:
    """TC-S-03 预检请求返回 CORS 头。"""
    response = client.options(
        "/api/auth/register",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_request_id_injected(client: TestClient) -> None:
    """TC-S-04 响应带 X-Request-ID。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) == 12


def test_process_time_header(client: TestClient) -> None:
    """TC-S-05 响应带 X-Process-Time。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Process-Time" in response.headers
    process_time = float(response.headers["X-Process-Time"])
    assert process_time >= 0


def test_rate_limit_on_auth(client: TestClient) -> None:
    """TC-S-06 认证接口限流（testclient 跳过，这里直接测限流器逻辑）。"""
    from app.middleware import _ip_limiter
    _ip_limiter.reset()
    for _ in range(10):
        assert _ip_limiter.is_allowed("1.2.3.4:/api/auth/token", 10, 60)
    assert not _ip_limiter.is_allowed("1.2.3.4:/api/auth/token", 10, 60)
    _ip_limiter.reset()


def test_sql_injection_in_question_keyword(client: TestClient, t1: dict, categories: list[int]) -> None:
    """TC-S-07 SQL 注入字符串被当作文本存储，ORM 参数化保证不执行。"""
    payload = {"category_id": categories[0], "type": "single", "content": "'; DROP TABLE questions; --", "options": ["A", "B"], "correct_answer": "A"}
    r = client.post("/api/questions", json=payload, headers=t1)
    assert r.status_code == 200
    # 验证表还在，字符串原样存储
    assert r.json()["content"] == "'; DROP TABLE questions; --"
    r2 = client.get(f"/api/questions/{r.json()['id']}", headers=t1)
    assert r2.status_code == 200
    assert r2.json()["content"] == "'; DROP TABLE questions; --"


def test_xss_in_question_content(client: TestClient, t1: dict, categories: list[int]) -> None:
    """TC-S-08 XSS 脚本作为题面存储，响应时原样返回（前端负责转义）。"""
    payload = {"category_id": categories[0], "type": "single", "content": "<script>alert(1)</script>", "options": ["A", "B"], "correct_answer": "A"}
    r = client.post("/api/questions", json=payload, headers=t1)
    assert r.status_code == 200
    assert r.json()["content"] == "<script>alert(1)</script>"


def test_long_password_rejected(client: TestClient) -> None:
    """TC-S-09 密码超 72 字节被 bcrypt 上限拦截。"""
    payload = {
        "username": "longpw",
        "password": "a" * 73,
        "role": "student",
    }
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 422


def test_malformed_token_rejected(client: TestClient) -> None:
    """TC-S-10 畸形 token 返回 401 统一体。"""
    r = client.get("/api/exams", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
    assert r.json()["code"] == "TOKEN_INVALID"


def test_missing_auth_header(client: TestClient) -> None:
    """TC-S-11 未带 Authorization 返回 401。"""
    r = client.get("/api/exams")
    assert r.status_code == 401
    assert r.json()["code"] == "TOKEN_INVALID"


def test_health_endpoint_always_200(client: TestClient) -> None:
    """TC-S-12 /health 恒 200，即使依赖故障。"""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "degraded")
    assert "mysql" in r.json()["deps"]
    assert "redis" in r.json()["deps"]
