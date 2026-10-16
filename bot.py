import os

from flask import Flask, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from openai import OpenAI

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
# For production across multiple workers, configure RATELIMIT_STORAGE_URI to Redis.
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

@app.get("/app-icon")
def app_icon():
    return send_from_directory(
        app.root_path,
        "C8671EEC-8C80-4721-B2CD-732CAE507F1C.png",
        mimetype="image/png"
    )
@app.get("/")
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>fishyeah Chat</title>
<link rel="apple-touch-icon" sizes="180x180" href="/app-icon?v=2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="fishyeah Chat">
<style>
            body {
                font-family: Arial, sans-serif;
                max-width: 700px;
                margin: 40px auto;
                padding: 20px;
            }
            h1 {
                text-align: center;
            }
            #chat {
                min-height: 300px;
                border: 1px solid #ddd;
                padding: 15px;
                margin-bottom: 15px;
                border-radius: 12px;
            }
            input {
                width: 75%;
                padding: 12px;
                box-sizing: border-box;
            }
            button {
                width: 20%;
                padding: 12px;
            }
        </style>
    </head>
    <body>
        <h1>fishyeah Chat</h1>

        <div id="chat">
            <p>🤖 你好，我是 fishyeah Chat。</p>
        </div>

        <input id="message" placeholder="输入你的问题...">
        <button onclick="sendMessage()">发送</button>

        <script>
            async function sendMessage() {
                const input = document.getElementById("message");
                const chat = document.getElementById("chat");
                const message = input.value.trim();

                if (!message) return;

                const userLine = document.createElement("p");
                userLine.textContent = "🧑 " + message;
                chat.appendChild(userLine);
                input.value = "";

                const response = await fetch("/chat", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        message: message
                    })
                });

                const data = await response.json();

                if (data.reply) {
                    const botLine = document.createElement("p");
                    botLine.textContent = "🤖 " + data.reply;
                    chat.appendChild(botLine);
                } else {
                    const errorLine = document.createElement("p");
                    errorLine.textContent = "❌ " + (data.error || "发生错误");
                    chat.appendChild(errorLine);
                }
            }
        </script>
    </body>
    </html>
    """
    

@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/chat")
@limiter.limit("10 per hour")
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message is required"}), 400

    if len(message) > 2000:
        return jsonify({"error": "message too long (max 2000 characters)"}), 400

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