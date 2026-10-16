import os
import re
from datetime import timedelta
from hashlib import sha256
from functools import wraps

import psycopg
from flask import Flask, jsonify, request, send_from_directory, session
from openai import OpenAI
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SESSION_SECRET_KEY"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=16 * 1024,
)
MODEL = os.getenv("OPENAI_MODEL", "gpt-6")
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def rate_limit(action, identity, max_attempts, minutes=15):
    key = sha256(identity.encode("utf-8")).hexdigest()
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO auth_rate_limits (action, identity_hash, attempts, reset_at)
                VALUES (%s, %s, 1, NOW() + (%s * INTERVAL '1 minute'))
                ON CONFLICT (action, identity_hash) DO UPDATE SET
                    attempts = CASE WHEN auth_rate_limits.reset_at <= NOW()
                        THEN 1 ELSE auth_rate_limits.attempts + 1 END,
                    reset_at = CASE WHEN auth_rate_limits.reset_at <= NOW()
                        THEN NOW() + (%s * INTERVAL '1 minute')
                        ELSE auth_rate_limits.reset_at END
                RETURNING attempts
            """, (action, key, minutes, minutes))
            return cur.fetchone()[0] <= max_attempts


def allow_auth(action, phone):
    ip = request.remote_addr or "unknown"
    return (rate_limit(action + ":ip", ip, 20 if action == "login" else 5)
            and rate_limit(action + ":phone", phone, 8 if action == "login" else 3))


def db_connection():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=8)


def init_db():
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    phone VARCHAR(11) UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS auth_rate_limits (
                    action TEXT NOT NULL,
                    identity_hash CHAR(64) NOT NULL,
                    attempts INTEGER NOT NULL,
                    reset_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (action, identity_hash)
                )
            """)


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify(error="请先登录"), 401
        return fn(*args, **kwargs)
    return wrapper


@app.get("/app-icon")
def app_icon():
    return send_from_directory(app.root_path, "C8671EEC-8C80-4721-B2CD-732CAE507F1C.png", mimetype="image/png")


@app.get("/")
def home():
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>fishyeah Chat</title>
<link rel="apple-touch-icon" sizes="180x180" href="/app-icon">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="fishyeah Chat">
<style>
body{font-family:-apple-system,BlinkMacSystemFont,Arial,sans-serif;max-width:680px;margin:30px auto;padding:16px;background:#f7f8fa;color:#161616}
main{background:white;padding:20px;border-radius:16px;box-shadow:0 2px 16px #0001}
h1{text-align:center}input,button{font:inherit;padding:12px;border-radius:9px;box-sizing:border-box}
input{border:1px solid #ccc;width:100%;margin:7px 0}
button{border:0;background:#111;color:white;cursor:pointer;margin:6px 4px 6px 0}
button:disabled{opacity:.55}#chat{height:45vh;min-height:220px;overflow:auto;border:1px solid #ddd;padding:12px;border-radius:10px;white-space:pre-wrap}
.msg{padding:9px;margin:7px 0;border-radius:9px;background:#f2f2f2;overflow-wrap:anywhere}
.hidden{display:none}#notice{color:#a02020;min-height:24px}
</style></head><body><main><h1>fishyeah Chat</h1>
<section id="auth"><h2 id="authTitle">登录</h2>
<input id="phone" type="tel" inputmode="numeric" maxlength="11" autocomplete="tel" placeholder="中国大陆手机号">
<input id="password" type="password" minlength="8" maxlength="128" autocomplete="current-password" placeholder="密码（新账号至少10位，包含字母和数字）">
<button id="loginBtn">登录</button><button id="registerBtn">注册新账号</button>
<p style="font-size:13px;color:#666">当前不发送短信验证码，手机号未经验证；请勿使用他人的号码注册。</p></section>
<section id="chatArea" class="hidden"><p>已登录：<span id="who"></span> <button id="logoutBtn">退出</button></p>
<details id="security"><summary>账号安全 · 修改密码</summary><input id="oldPassword" type="password" autocomplete="current-password" placeholder="当前密码"><input id="newPassword" type="password" autocomplete="new-password" placeholder="新密码（至少10位，包含不同类型字符）"><button id="changePasswordBtn">修改密码</button></details>
<div id="chat"><div class="msg">🤖 你好，我是 fishyeah Chat。</div></div>
<input id="message" placeholder="输入你的问题..."><button id="sendBtn">发送</button></section>
<p id="notice" role="status"></p></main>
<script>
const $=id=>document.getElementById(id);
const notice=msg=>$("notice").textContent=msg;
async function api(path,data){
 const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data||{})});
 const body=await response.json().catch(()=>({error:"网络或服务器错误"}));
 if(!response.ok)throw Error(body.error||"请求失败");
 return body;
}
function showUser(phone){$("auth").classList.toggle("hidden",!!phone);$("chatArea").classList.toggle("hidden",!phone);$("who").textContent=phone||"";notice("");}
async function authenticate(mode){
 const phone=$("phone").value.trim(),password=$("password").value;
 if(!/^1[3-9]\d{9}$/.test(phone)){notice("请输入正确的11位中国大陆手机号");return;}
 if(mode==="register" && (password.length<10 || /^\\d+$/.test(password) || /^[a-zA-Z]+$/.test(password))){notice("新密码至少10位，不能只包含数字或字母");return;}
 if(mode==="login" && password.length<8){notice("请输入密码");return;}
 $("loginBtn").disabled=$("registerBtn").disabled=true;
 try{const result=await api("/auth/"+mode,{phone,password});showUser(result.phone);$("password").value="";}
 catch(err){notice(err.message);}
 finally{$("loginBtn").disabled=$("registerBtn").disabled=false;}
}
$("changePasswordBtn").onclick=async()=>{
 const oldPassword=$("oldPassword").value,newPassword=$("newPassword").value;
 if(!oldPassword||newPassword.length<10){notice("请输入当前密码和至少10位的新密码");return;}
 $("changePasswordBtn").disabled=true;
 try{await api("/auth/change-password",{old_password:oldPassword,new_password:newPassword});
 $("oldPassword").value=$("newPassword").value="";
 showUser(null);notice("密码修改成功，请使用新密码重新登录");}
 catch(err){notice(err.message);}
 finally{$("changePasswordBtn").disabled=false;}
};
$("loginBtn").onclick=()=>authenticate("login");
$("registerBtn").onclick=()=>authenticate("register");
$("logoutBtn").onclick=async()=>{try{await api("/auth/logout");showUser(null);}catch(e){notice(e.message);}};
$("sendBtn").onclick=async()=>{
 const message=$("message").value.trim();if(!message)return;
 const add=(label,text)=>{const el=document.createElement("div");el.className="msg";el.textContent=label+" "+text;$("chat").appendChild(el);$("chat").scrollTop=$("chat").scrollHeight;};
 add("🧑",message);$("message").value="";$("sendBtn").disabled=true;
 try{const result=await api("/chat",{message});add("🤖",result.reply);}
 catch(e){add("❌",e.message);}
 finally{$("sendBtn").disabled=false;}
};
fetch("/auth/me").then(r=>r.json()).then(data=>showUser(data.phone||null)).catch(()=>{});
</script></body></html>"""


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/auth/me")
def me():
    if not session.get("user_id"):
        return jsonify(authenticated=False)
    return jsonify(authenticated=True, phone=session.get("phone"))


@app.post("/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    phone, password = data.get("phone"), data.get("password")
    if not isinstance(phone, str) or not PHONE_PATTERN.fullmatch(phone):
        return jsonify(error="手机号格式不正确"), 400
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        return jsonify(error="密码长度需要在8到128位之间"), 400
    if len(password) < 10 or password.isdigit() or password.isalpha():
        return jsonify(error="新密码至少10位，需包含字母及数字或符号"), 400
    try:
        init_db()
        if not allow_auth("register", phone):
            return jsonify(error="注册太频繁，请15分钟后重试"), 429
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO users (phone,password_hash) VALUES (%s,%s) ON CONFLICT (phone) DO NOTHING RETURNING id",
                            (phone, generate_password_hash(password)))
                row = cur.fetchone()
        if row is None:
            return jsonify(error="该手机号已注册"), 409
        session.clear()
        session.permanent = True
        session.update(user_id=row[0], phone=phone)
        return jsonify(phone=phone), 201
    except Exception:
        app.logger.exception("Registration failed")
        return jsonify(error="注册暂时不可用，请稍后重试"), 503


@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    phone, password = data.get("phone"), data.get("password")
    if not isinstance(phone, str) or not PHONE_PATTERN.fullmatch(phone) or not isinstance(password, str):
        return jsonify(error="手机号或密码错误"), 401
    try:
        init_db()
        if not allow_auth("login", phone):
            return jsonify(error="登录太频繁，请15分钟后重试"), 429
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,password_hash FROM users WHERE phone=%s", (phone,))
                row = cur.fetchone()
        if not row or not check_password_hash(row[1], password):
            return jsonify(error="手机号或密码错误"), 401
        session.clear()
        session.permanent = True
        session.update(user_id=row[0], phone=phone)
        return jsonify(phone=phone)
    except Exception:
        app.logger.exception("Login failed")
        return jsonify(error="登录暂时不可用，请稍后重试"), 503


@app.post("/auth/change-password")
@require_login
def change_password():
    data = request.get_json(silent=True) or {}
    old_password, new_password = data.get("old_password"), data.get("new_password")
    if not isinstance(old_password, str) or not isinstance(new_password, str):
        return jsonify(error="请输入当前密码和新密码"), 400
    if not 10 <= len(new_password) <= 128 or new_password.isdigit() or new_password.isalpha():
        return jsonify(error="新密码至少10位，需包含不同类型字符"), 400
    try:
        init_db()
        if not allow_auth("change_password", session["phone"]):
            return jsonify(error="操作太频繁，请15分钟后重试"), 429
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE id=%s FOR UPDATE", (session["user_id"],))
                row = cur.fetchone()
                if not row or not check_password_hash(row[0], old_password):
                    return jsonify(error="当前密码不正确"), 401
                if check_password_hash(row[0], new_password):
                    return jsonify(error="新密码不能与旧密码相同"), 400
                cur.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                            (generate_password_hash(new_password), session["user_id"]))
        session.clear()
        return jsonify(ok=True)
    except Exception:
        app.logger.exception("Password change failed")
        return jsonify(error="修改密码暂时不可用，请稍后重试"), 503


@app.post("/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post("/chat")
@require_login
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        return jsonify(error="请输入不超过4000字的消息"), 400
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.responses.create(model=MODEL, input=message.strip())
        return jsonify(reply=response.output_text)
    except Exception:
        app.logger.exception("AI request failed")
        return jsonify(error="AI 请求失败，请稍后再试"), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
