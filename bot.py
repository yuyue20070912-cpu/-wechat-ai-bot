import os

from flask import Flask, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from openai import OpenAI
from auth import setup_auth, login_required, current_user, spend_credit, refund_credit

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
setup_auth(app)
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
            #account { display: grid; gap: 8px; margin-bottom: 20px; }
            #account input { width: 100%; }
            #account button { width: 48%; }
            #account-actions { display: flex; justify-content: space-between; }
            #logout { width: auto; }
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

        <div id="account">
          <input id="phone" type="tel" inputmode="numeric" maxlength="11" autocomplete="tel" placeholder="中国大陆手机号">
          <input id="password" type="password" minlength="10" autocomplete="current-password" placeholder="密码（至少10位）">
          <div id="account-actions"><button onclick="accountAction('register')">注册</button><button onclick="accountAction('login')">登录</button></div>
          <p id="account-status" aria-live="polite">请先登录</p>
        </div>
        <div id="signed-in" hidden><span id="user-info"></span> <button id="logout" onclick="logout()">退出</button></div>
        <div id="chat">
            <p>🤖 你好，我是 fishyeah Chat。</p>
        </div>

        <input id="message" placeholder="输入你的问题..." disabled>
        <button id="send" onclick="sendMessage()" disabled>发送</button>

        <script>
            let csrfToken = "";
            let loggedIn = false;
            function showStatus(text) {
                document.getElementById("account-status").textContent = text;
            }
            function showUser(data) {
                loggedIn = true;
                document.getElementById("account").hidden = true;
                document.getElementById("signed-in").hidden = false;
                document.getElementById("message").disabled = false;
                document.getElementById("send").disabled = false;
                document.getElementById("user-info").textContent = data.phone + " · 剩余 " + data.credits + " 次";
            }
            async function api(path, payload) {
                const response = await fetch(path, {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (data.csrf_token) csrfToken = data.csrf_token;
                if (!response.ok) throw new Error(data.error || "请求失败");
                return data;
            }
            async function accountAction(type) {
                try {
                    const data = await api("/" + type, {
                        phone: document.getElementById("phone").value.trim(),
                        password: document.getElementById("password").value
                    });
                    document.getElementById("password").value = "";
                    showUser(data);
                } catch (err) { showStatus(err.message); }
            }
            async function logout() {
                try {
                    await api("/logout", {});
                    window.location.reload();
                } catch (err) { alert(err.message); }
            }
            async function init() {
                try {
                    const csrf = await fetch("/csrf");
                    csrfToken = (await csrf.json()).csrf_token;
                    const response = await fetch("/me");
                    if (response.ok) showUser(await response.json());
                } catch (err) { showStatus("网络错误，请刷新重试"); }
            }
            init();
            async function sendMessage() {
                if (!loggedIn) return;
                const input = document.getElementById("message");
                const chat = document.getElementById("chat");
                const message = input.value.trim();

                if (!message) return;

                const userLine = document.createElement("p");
                userLine.textContent = "🧑 " + message;
                chat.appendChild(userLine);
                input.value = "";

                let data;
                try {
                    data = await api("/chat", {message: message});
                } catch (err) {
                    const errorLine = document.createElement("p");
                    errorLine.textContent = "❌ " + err.message;
                    chat.appendChild(errorLine);
                    return;
                }
                const me = await fetch("/me");
                if (me.ok) showUser(await me.json());

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
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message is required"}), 400

    if len(message) > 2000:
        return jsonify({"error": "message too long (max 2000 characters)"}), 400

    user = current_user()
    if not spend_credit(user.id):
        return jsonify({"error": "免费次数已用完，请等待付费功能上线"}), 402

    try:
        response = client.responses.create(
            model=MODEL,
            input=message.strip(),
        )
        return jsonify({"reply": response.output_text})
    except Exception:
        refund_credit(user.id)
        return jsonify({"error": "AI request failed"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)