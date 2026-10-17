"""Offline smoke tests: never use deployment credentials or databases."""
import importlib.util
import socket
from pathlib import Path

import psycopg
import pytest
import openai


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("External network, database and AI access are forbidden in smoke tests")

    # Install guards BEFORE importing the application, including import-time code.
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(openai, "OpenAI", forbidden)
    for name in ("DATABASE_URL", "OPENAI_API_KEY", "ADMIN_REVIEW_TOKEN", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SESSION_SECRET_KEY", "offline-test-secret-never-use-in-production")
    spec = importlib.util.spec_from_file_location("smoke_bot", Path(__file__).resolve().parents[1] / "bot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True)
    with module.app.test_client() as test_client:
        yield test_client
