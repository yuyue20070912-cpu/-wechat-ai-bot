import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    # No test may accidentally connect to the app's production services.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ADMIN_REVIEW_TOKEN", "test-admin-token-" * 3)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    bot.app.config.update(TESTING=True, SECRET_KEY="isolated-test-session-secret")
    monkeypatch.setattr(bot, "init_db", Mock())
    monkeypatch.setattr(bot, "db_connection", Mock(side_effect=AssertionError("Unmocked DB access")))
    monkeypatch.setattr(bot, "OpenAI", Mock(side_effect=AssertionError("Unmocked AI access")))


@pytest.fixture
def client():
    return bot.app.test_client()


@pytest.fixture
def logged_in(client):
    with client.session_transaction() as session:
        session.update(user_id=1, phone="13800000000")
    return client
