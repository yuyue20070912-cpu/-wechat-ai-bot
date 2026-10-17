from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import bot


@pytest.mark.parametrize("path", ["/auth/register", "/auth/login", "/auth/change-password", "/auth/logout", "/chat", "/membership/orders", "/membership/orders/1/payment-note", "/admin/membership/orders/1/review"])
@pytest.mark.parametrize("payload", [[], [1], "text", 42, None])
def test_non_object_json_rejected(client, path, payload):
    import json
    response = client.post(path, data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 400
    assert response.is_json


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://[invalid"}])
def test_cross_origin_write_rejected(client, headers):
    assert client.post("/auth/logout", json={}, headers=headers).status_code == 403


def test_same_origin_write_and_secure_cookie(logged_in):
    response = logged_in.post("/auth/logout", json={}, headers={"Origin": "https://localhost"})
    assert response.status_code == 200
    assert "Secure" in response.headers["Set-Cookie"]
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert logged_in.get("/auth/me").json == {"authenticated": False}


@pytest.mark.parametrize("path", ["/chat", "/membership/orders", "/auth/change-password"])
def test_login_required(client, path):
    assert client.post(path, json={}).status_code == 401


def test_private_responses_not_cached(logged_in):
    response = logged_in.get("/auth/me")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_invalid_admin_token_never_crashes(client):
    assert client.get("/admin/membership/orders", headers={"X-Admin-Token": "无效"}).status_code == 403


@pytest.mark.parametrize("plan", [[], {}, 1, None])
def test_invalid_order_plan(logged_in, plan):
    assert logged_in.post("/membership/orders", json={"plan": plan, "payment_method": "wechat"}).status_code == 400


def test_empty_payment_reference_rejected(client):
    import os
    response = client.post("/admin/membership/orders/1/review", json={"decision": "approve", "payment_reference": " " * 8}, headers={"X-Admin-Token": os.environ["ADMIN_REVIEW_TOKEN"]})
    assert response.status_code == 400


@pytest.mark.parametrize("password", ["short", "1" * 129])
def test_login_password_bounds(client, password):
    assert client.post("/auth/login", json={"phone": "13800000000", "password": password}).status_code == 401


@pytest.mark.parametrize("message", ["", " ", "a" * 4001, [], None])
def test_invalid_chat(logged_in, message):
    assert logged_in.post("/chat", json={"message": message}).status_code == 400


def setup_chat(monkeypatch, reservation=("free", date(2026, 10, 16), 4)):
    reserve = Mock(return_value=reservation)
    refund = Mock()
    monkeypatch.setattr(bot, "reserve_message", reserve)
    monkeypatch.setattr(bot, "refund_message", refund)
    ai = MagicMock()
    ai.return_value.__enter__.return_value.responses.create.return_value = SimpleNamespace(output_text="你好", status="completed")
    monkeypatch.setattr(bot, "OpenAI", ai)
    return reserve, refund, ai


def test_missing_ai_configuration_does_not_charge(logged_in, monkeypatch):
    reserve, _, ai = setup_chat(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY")
    assert logged_in.post("/chat", json={"message": "hi"}).status_code == 503
    reserve.assert_not_called()
    ai.assert_not_called()


def test_chat_success(logged_in, monkeypatch):
    _, refund, ai = setup_chat(monkeypatch)
    response = logged_in.post("/chat", json={"message": " hi "})
    assert response.json == {"reply": "你好", "remaining": 4}
    refund.assert_not_called()
    assert ai.call_args.kwargs["timeout"] == 20.0
    assert ai.call_args.kwargs["max_retries"] == 0
    ai.return_value.__enter__.return_value.responses.create.assert_called_once_with(model=bot.MODEL, input="hi", max_output_tokens=2048)


@pytest.mark.parametrize("reply,status", [("", "completed"), (" ", "completed"), (None, "completed"), ("partial", "incomplete"), ("text", "failed")])
def test_unusable_ai_reply_refunded(logged_in, monkeypatch, reply, status):
    _, refund, ai = setup_chat(monkeypatch)
    ai.return_value.__enter__.return_value.responses.create.return_value = SimpleNamespace(output_text=reply, status=status)
    assert logged_in.post("/chat", json={"message": "hi"}).status_code == 502
    refund.assert_called_once_with(1, "free", date(2026, 10, 16))


@pytest.mark.parametrize("kind,token", [("free", date(2026, 10, 16)), ("paid", 99)])
def test_upstream_failure_refunds_original_reservation(logged_in, monkeypatch, kind, token):
    _, refund, ai = setup_chat(monkeypatch, (kind, token, 4))
    ai.return_value.__enter__.return_value.responses.create.side_effect = TimeoutError("private upstream detail")
    response = logged_in.post("/chat", json={"message": "hi"})
    assert response.status_code == 502
    assert "private" not in response.get_data(as_text=True)
    refund.assert_called_once_with(1, kind, token)


def test_exhausted_quota_does_not_call_ai(logged_in, monkeypatch):
    _, refund, ai = setup_chat(monkeypatch, ("free", None, None))
    assert logged_in.post("/chat", json={"message": "hi"}).status_code == 429
    ai.assert_not_called()
    refund.assert_not_called()


def test_database_failure_returns_safe_error(logged_in, monkeypatch):
    reserve, refund, ai = setup_chat(monkeypatch)
    reserve.side_effect = RuntimeError("private database detail")
    response = logged_in.post("/chat", json={"message": "hi"})
    assert response.status_code == 503
    assert "private" not in response.get_data(as_text=True)
    ai.assert_not_called()
    refund.assert_not_called()


def test_malformed_and_oversize_json(client):
    assert client.post("/auth/login", data="{", content_type="application/json").status_code == 400
    assert client.post("/auth/login", json={"password": "x" * 17000}).status_code == 413
