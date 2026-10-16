import os

from flask import Flask, jsonify, request
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")


@app.get("/")
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>fishyeah Chat</title>
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

                chat.innerHTML += "<p>🧑 " + message + "</p>";
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
                    chat.innerHTML += "<p>🤖 " + data.reply + "</p>";
                } else {
                    chat.innerHTML += "<p>❌ " + (data.error || "发生错误") + "</p>";
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