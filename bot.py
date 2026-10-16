import os

from flask import Flask, jsonify, request
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")


@app.get("/")
def home():
    return "WeChat AI Bot is running."


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message is required"}), 400

    try:
        response = client.responses.create(
            model=MODEL,
            input=message.strip(),
        )
        return jsonify({"reply": response.output_text})
    except Exception:
        return jsonify({"error": "AI request failed"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)