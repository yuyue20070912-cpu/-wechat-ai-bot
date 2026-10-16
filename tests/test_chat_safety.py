import os
import unittest
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")
os.environ["RATELIMIT_STORAGE_URI"] = "memory://"
os.environ["SECRET_KEY"] = "testing-only-secret-key-32-characters-minimum"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["COOKIE_SECURE"] = "false"

from bot import app, limiter
from auth import db, User


class ChatSafetyTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        limiter.reset()
        with app.app_context():
            db.drop_all()
            db.create_all()
        self.csrf = self.client.get("/csrf").get_json()["csrf_token"]
        self.register()

    def post(self, path, payload):
        return self.client.post(path, json=payload, headers={"X-CSRF-Token": self.csrf})

    def register(self, phone="13800138000", password="test-password-123"):
        response = self.post("/register", {"phone": phone, "password": password})
        if response.status_code == 201:
            self.csrf = response.get_json()["csrf_token"]
        return response

    def test_registration_and_login(self):
        self.assertEqual(self.client.get("/me").get_json()["credits"], 5)
        self.assertEqual(self.post("/logout", {}).status_code, 200)
        self.csrf = self.client.get("/csrf").get_json()["csrf_token"]
        bad = self.post("/login", {"phone": "13800138000", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)
        good = self.post("/login", {"phone": "13800138000", "password": "test-password-123"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.get_json()["credits"], 5)

    def test_csrf_required(self):
        response = self.client.post("/chat", json={"message": "hi"})
        self.assertEqual(response.status_code, 403)

    def test_free_quota(self):
        with patch("bot.client.responses.create") as mocked:
            mocked.return_value.output_text = "ok"
            results = [self.post("/chat", {"message": "hi"}) for _ in range(6)]
        self.assertTrue(all(r.status_code == 200 for r in results[:5]))
        self.assertEqual(results[5].status_code, 402)
        self.assertEqual(mocked.call_count, 5)

    def test_failed_api_call_refunds(self):
        with patch("bot.client.responses.create", side_effect=RuntimeError("API error")):
            response = self.post("/chat", {"message": "hi"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get("/me").get_json()["credits"], 5)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_empty_message_rejected(self):
        response = self.post("/chat", {"message": " "})
        self.assertEqual(response.status_code, 400)

    def test_oversized_message_rejected(self):
        response = self.post("/chat", {"message": "x" * 2001})
        self.assertEqual(response.status_code, 400)

    def test_chat_success_without_paid_api_call(self):
        with patch("bot.client.responses.create") as mocked:
            mocked.return_value.output_text = "Test response"
            response = self.post("/chat", {"message": "Hi"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "Test response")
        mocked.assert_called_once()

    def test_rate_limit(self):
        with patch("bot.client.responses.create") as mocked:
            mocked.return_value.output_text = "ok"
            responses = [self.post("/chat", {"message": "hi"}) for _ in range(11)]
        self.assertTrue(all(r.status_code == 200 for r in responses[:5]))
        self.assertEqual(responses[5].status_code, 402)
        self.assertEqual(mocked.call_count, 5)

    def test_html_is_rendered_as_text(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn('userLine.textContent', html)
        self.assertIn('botLine.textContent', html)
        self.assertNotIn('chat.innerHTML +=', html)
        self.assertIn('initial-scale=1.0', html)


if __name__ == "__main__":
    unittest.main()
