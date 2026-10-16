import os
import unittest
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")
os.environ["RATELIMIT_STORAGE_URI"] = "memory://"

from bot import app, limiter


class ChatSafetyTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        limiter.reset()

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_empty_message_rejected(self):
        response = self.client.post("/chat", json={"message": " "})
        self.assertEqual(response.status_code, 400)

    def test_oversized_message_rejected(self):
        response = self.client.post("/chat", json={"message": "x" * 2001})
        self.assertEqual(response.status_code, 400)

    def test_chat_success_without_paid_api_call(self):
        with patch("bot.client.responses.create") as mocked:
            mocked.return_value.output_text = "Test response"
            response = self.client.post("/chat", json={"message": "Hi"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "Test response")
        mocked.assert_called_once()

    def test_rate_limit(self):
        with patch("bot.client.responses.create") as mocked:
            mocked.return_value.output_text = "ok"
            responses = [self.client.post("/chat", json={"message": "hi"}) for _ in range(11)]
        self.assertTrue(all(r.status_code == 200 for r in responses[:10]))
        self.assertEqual(responses[10].status_code, 429)
        self.assertEqual(mocked.call_count, 10)

    def test_html_is_rendered_as_text(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn('userLine.textContent', html)
        self.assertIn('botLine.textContent', html)
        self.assertNotIn('chat.innerHTML +=', html)
        self.assertIn('initial-scale=1.0', html)


if __name__ == "__main__":
    unittest.main()
