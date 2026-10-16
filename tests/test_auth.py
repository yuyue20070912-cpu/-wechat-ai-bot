"""Offline authentication tests: no real phone, database, or OpenAI calls."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-real-secret-" + "x" * 48)
os.environ.setdefault("OPENAI_API_KEY", "test-placeholder")
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")

import bot


class Cursor:
    def __init__(self, row):
        self.row = row
        self.executed = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, params=None): self.executed.append((query, params))
    def fetchone(self): return self.row


class Connection:
    def __init__(self, row): self.cursor_obj = Cursor(row)
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return self.cursor_obj


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()

    def test_health(self):
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_anonymous_chat_is_rejected(self):
        self.assertEqual(self.client.post("/chat", json={"message": "hi"}).status_code, 401)

    def test_invalid_phone_rejected(self):
        response = self.client.post("/auth/register", json={"phone": "123", "password": "Password123!"})
        self.assertEqual(response.status_code, 400)

    def test_weak_password_rejected(self):
        response = self.client.post("/auth/register", json={"phone": "13800138000", "password": "12345678"})
        self.assertEqual(response.status_code, 400)

    def test_register_and_logout(self):
        with patch.object(bot, "init_db"), patch.object(bot, "allow_auth", return_value=True), patch.object(bot, "db_connection", return_value=Connection((7,))):
            response = self.client.post("/auth/register", json={"phone": "13800138000", "password": "StrongPassword123!"})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.client.get("/auth/me").json["authenticated"])
        self.assertEqual(self.client.post("/auth/logout").status_code, 200)
        self.assertFalse(self.client.get("/auth/me").json["authenticated"])

    def test_duplicate_registration(self):
        with patch.object(bot, "init_db"), patch.object(bot, "allow_auth", return_value=True), patch.object(bot, "db_connection", return_value=Connection(None)):
            response = self.client.post("/auth/register", json={"phone": "13800138000", "password": "StrongPassword123!"})
        self.assertEqual(response.status_code, 409)

    def test_login_wrong_password(self):
        from werkzeug.security import generate_password_hash
        with patch.object(bot, "init_db"), patch.object(bot, "allow_auth", return_value=True), patch.object(bot, "db_connection", return_value=Connection((7, generate_password_hash("CorrectPassword123!")))):
            response = self.client.post("/auth/login", json={"phone": "13800138000", "password": "WrongPassword123!"})
        self.assertEqual(response.status_code, 401)

    def test_login_rate_limit(self):
        with patch.object(bot, "init_db"), patch.object(bot, "allow_auth", return_value=False):
            response = self.client.post("/auth/login", json={"phone": "13800138000", "password": "WrongPassword123!"})
        self.assertEqual(response.status_code, 429)

    def test_registration_rate_limit(self):
        with patch.object(bot, "init_db"), patch.object(bot, "allow_auth", return_value=False):
            response = self.client.post("/auth/register", json={"phone": "13800138000", "password": "StrongPassword123!"})
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
