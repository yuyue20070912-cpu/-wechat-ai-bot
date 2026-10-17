"""Integration tests use a new, disposable PostgreSQL schema per test only."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest
import bot

INIT_DB = bot.init_db


@pytest.fixture
def database(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to a disposable local PostgreSQL database")
    config = conninfo_to_dict(dsn)
    if config.get("host") not in {"localhost", "127.0.0.1", "::1"} or not config.get("dbname", "").endswith("_test"):
        pytest.fail("Integration tests require a loopback host and database ending in _test")
    schema = "test_" + uuid4().hex
    with psycopg.connect(dsn) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")
    monkeypatch.setattr(bot, "db_connection", connect)
    INIT_DB()
    # Schema exists; avoid re-running DDL on each concurrent test request.
    try:
        yield connect
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def register(client, phone="13800000000"):
    return client.post("/auth/register", json={"phone": phone, "password": "safe-password123"})


def scalar(database, query, params=()):
    with database() as conn:
        return conn.execute(query, params).fetchone()[0]


def test_register_login_change_password(client, database):
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    assert client.get("/auth/me").json["phone"] == "13800000000"
    stored = scalar(database, "SELECT password_hash FROM users")
    assert stored != "safe-password123"
    assert client.post("/auth/logout", json={}).status_code == 200
    assert client.post("/auth/login", json={"phone": "13800000000", "password": "wrong-password"}).status_code == 401
    assert client.post("/auth/login", json={"phone": "13800000000", "password": "safe-password123"}).status_code == 200
    assert client.post("/auth/change-password", json={"old_password": "safe-password123", "new_password": "changed-password123"}).status_code == 200
    assert client.get("/auth/me").json["authenticated"] is False
    assert client.post("/auth/login", json={"phone": "13800000000", "password": "safe-password123"}).status_code == 401
    assert client.post("/auth/login", json={"phone": "13800000000", "password": "changed-password123"}).status_code == 200


def test_login_rate_limit(client, database):
    for _ in range(8):
        assert client.post("/auth/login", json={"phone": "13800000000", "password": "wrong-password"}).status_code == 401
    assert client.post("/auth/login", json={"phone": "13800000000", "password": "wrong-password"}).status_code == 429


def test_five_free_messages_under_concurrency(client, database):
    register(client)
    user_id = scalar(database, "SELECT id FROM users")
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(bot.reserve_message, [user_id] * 15))
    assert sum(r[2] is not None for r in results) == 5
    assert scalar(database, "SELECT used FROM daily_message_usage") == 5
    usage = client.get("/account/usage").json
    assert usage["plan"] == "free" and usage["remaining"] == 0


def test_refund_targets_reservation_day(client, database):
    register(client)
    user_id = scalar(database, "SELECT id FROM users")
    with database() as conn:
        today = conn.execute("SELECT (NOW() AT TIME ZONE 'UTC')::date").fetchone()[0]
        yesterday = today - timedelta(days=1)
        conn.execute("INSERT INTO daily_message_usage VALUES (%s,%s,5),(%s,%s,2)", (user_id, yesterday, user_id, today))
    bot.refund_message(user_id, "free", yesterday)
    assert scalar(database, "SELECT used FROM daily_message_usage WHERE usage_day=%s", (yesterday,)) == 4
    assert scalar(database, "SELECT used FROM daily_message_usage WHERE usage_day=%s", (today,)) == 2


def test_paid_quota_and_expiration(client, database):
    register(client)
    user_id = scalar(database, "SELECT id FROM users")
    with database() as conn:
        sub_id = conn.execute("INSERT INTO subscriptions (user_id,plan,starts_at,ends_at,message_limit,payment_reference) VALUES (%s,'basic',NOW()-INTERVAL '1 day',NOW()+INTERVAL '1 day',3,'test-payment') RETURNING id", (user_id,)).fetchone()[0]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(bot.reserve_message, [user_id] * 8))
    assert sum(r[2] is not None for r in results) == 3
    assert all(r[0] == "paid" for r in results)
    assert scalar(database, "SELECT count(*) FROM daily_message_usage") == 0
    bot.refund_message(user_id, "paid", sub_id)
    assert client.get("/account/usage").json["remaining"] == 1
    with database() as conn:
        conn.execute("UPDATE subscriptions SET ends_at=NOW()-INTERVAL '1 hour'")
    assert bot.reserve_message(user_id)[0] == "free"


def order(client):
    return client.post("/membership/orders", json={"plan": "basic", "payment_method": "wechat"})


def test_concurrent_orders_create_only_one(client, database):
    register(client)
    user_id = scalar(database, "SELECT id FROM users")
    def create(_):
        with bot.app.test_client() as other:
            with other.session_transaction() as session:
                session.update(user_id=user_id, phone="13800000000")
            return order(other).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(create, range(8)))
    assert statuses.count(201) == 1
    assert statuses.count(409) == 7
    assert scalar(database, "SELECT count(*) FROM membership_orders") == 1


def test_order_ownership_review_and_idempotency(client, database):
    register(client)
    order_id = order(client).json["order_id"]
    assert client.post(f"/membership/orders/{order_id}/payment-note", json={"note": "测试付款时间及凭证123456"}).status_code == 200
    with bot.app.test_client() as other:
        register(other, "13900000000")
        assert other.get("/membership/orders").json["orders"] == []
        assert other.post(f"/membership/orders/{order_id}/payment-note", json={"note": "不可修改其他用户订单"}).status_code == 404
    headers = {"X-Admin-Token": os.environ["ADMIN_REVIEW_TOKEN"]}
    assert len(client.get("/admin/membership/orders", headers=headers).json["orders"]) == 1
    url = f"/admin/membership/orders/{order_id}/review"
    payload = {"decision": "approve", "payment_reference": "  transaction123  "}
    assert client.post(url, json=payload, headers=headers).status_code == 200
    assert client.post(url, json=payload, headers=headers).status_code == 409
    assert scalar(database, "SELECT count(*) FROM subscriptions") == 1
    assert scalar(database, "SELECT payment_reference FROM subscriptions") == "transaction123"
    assert client.get("/account/usage").json["plan"] == "basic"
    second_id = order(client).json["order_id"]
    assert client.post(f"/admin/membership/orders/{second_id}/review", json=payload, headers=headers).status_code == 409
    assert scalar(database, "SELECT status FROM membership_orders WHERE id=%s", (second_id,)) == "pending"
    assert client.post(f"/admin/membership/orders/{second_id}/review", json={"decision": "reject"}, headers=headers).status_code == 200


def test_chat_charges_and_refunds_real_database(client, database, monkeypatch):
    register(client)
    ai = MagicMock()
    ai.return_value.__enter__.return_value.responses.create.return_value = SimpleNamespace(output_text="测试回复", status="completed")
    monkeypatch.setattr(bot, "OpenAI", ai)
    assert client.post("/chat", json={"message": "你好"}).status_code == 200
    ai.return_value.__enter__.return_value.responses.create.side_effect = TimeoutError()
    assert client.post("/chat", json={"message": "失败测试"}).status_code == 502
    assert client.get("/account/usage").json["used"] == 1
