import os
import re
from datetime import timedelta
from secrets import token_urlsafe, compare_digest
from io import BytesIO
import qrcode
from hashlib import sha256
from functools import wraps

import psycopg
from flask import Flask, jsonify, request, send_from_directory, send_file, session
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
MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
FREE_DAILY_MESSAGES = 5  # UTC calendar day
PAID_PLANS = {"basic": {"price_cny_fen": 990, "messages": 300}, "pro": {"price_cny_fen": 1990, "messages": 1000}}
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
def payments_ready():
    return len(os.getenv("ADMIN_REVIEW_TOKEN", "")) >= 32

PAYMENT_QR_CONTENT = {"alipay": "https://qr.alipay.com/fkx10355uzbazrfvvys4232", "wechat": "wxp://f2f0HdmMjE2AuF0qjzSMF2YbY-BSZrmgPWRK5RjtWFpWH0M"}


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
                CREATE TABLE IF NOT EXISTS daily_message_usage (
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    usage_day DATE NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                    PRIMARY KEY (user_id, usage_day)
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

            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    plan TEXT NOT NULL CHECK (plan IN ('basic', 'pro')),
                    starts_at TIMESTAMPTZ NOT NULL,
                    ends_at TIMESTAMPTZ NOT NULL,
                    message_limit INTEGER NOT NULL CHECK (message_limit > 0),
                    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                    payment_reference TEXT UNIQUE NOT NULL,
                    CHECK (ends_at > starts_at)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_active ON subscriptions (user_id, ends_at DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS membership_orders (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    plan TEXT NOT NULL CHECK (plan IN ('basic','pro')),
                    amount_fen INTEGER NOT NULL,
                    payment_method TEXT NOT NULL CHECK (payment_method IN ('wechat','alipay')),
                    reference TEXT UNIQUE NOT NULL,
                    payer_note TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

def get_active_subscription(user_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, plan, ends_at, message_limit, used
                FROM subscriptions
                WHERE user_id=%s AND starts_at <= NOW() AND ends_at > NOW()
                ORDER BY ends_at DESC, id DESC LIMIT 1
            """, (user_id,))
            return cur.fetchone()


def reserve_message(user_id):
    """Reserve a message atomically; paid users use period allowance, free users daily allowance."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, plan, message_limit FROM subscriptions
                WHERE user_id=%s AND starts_at <= NOW() AND ends_at > NOW()
                ORDER BY ends_at DESC, id DESC LIMIT 1 FOR UPDATE
            """, (user_id,))
            sub = cur.fetchone()
            if sub:
                cur.execute("""
                    UPDATE subscriptions SET used=used+1
                    WHERE id=%s AND used < message_limit RETURNING message_limit-used
                """, (sub[0],))
                row = cur.fetchone()
                return ("paid", sub[0], row[0] if row else None)
    used = reserve_free_message(user_id)
    return ("free", None, FREE_DAILY_MESSAGES-used if used is not None else None)


def refund_message(user_id, kind, subscription_id):
    if kind == "paid":
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE subscriptions SET used=GREATEST(0,used-1) WHERE id=%s AND user_id=%s", (subscription_id,user_id))
    else:
        refund_free_message(user_id)


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify(error="请先登录"), 401
        return fn(*args, **kwargs)
    return wrapper


@app.get("/membership/payment-qr/<method>")
@require_login
def payment_qr(method):
    if not payments_ready():
        return jsonify(error="支付暂未开放"),503
    if method not in PAYMENT_QR_CONTENT:
        return jsonify(error="不支持的支付方式"),404
    img=qrcode.make(PAYMENT_QR_CONTENT[method])
    buf=BytesIO()
    img.save(buf,format="PNG")
    buf.seek(0)
    return send_file(buf,mimetype="image/png",max_age=300)


@app.get("/membership/plans")
def membership_plans():
    return jsonify(plans=[
        {"id":"free","price_cny_fen":0,"messages_per_day":FREE_DAILY_MESSAGES},
        *[{"id":k,**v,"period_days":30} for k,v in PAID_PLANS.items()]
    ], payments_enabled=payments_ready())



@app.post("/membership/orders")
@require_login
def create_membership_order():
    if not payments_ready():
        return jsonify(error="支付尚未开放，请稍后再试"),503
    data = request.get_json(silent=True) or {}
    plan, method = data.get("plan"), data.get("payment_method")
    if plan not in PAID_PLANS or method not in ("wechat", "alipay"):
        return jsonify(error="请选择有效套餐和付款方式"), 400
    reference = token_urlsafe(18)
    try:
        init_db()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,plan,payment_method,amount_fen FROM membership_orders WHERE user_id=%s AND status='pending' ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (session["user_id"],))
                existing=cur.fetchone()
                if existing:
                    return jsonify(error="你已有待审核订单 #"+str(existing[0])+"，请勿重复付款。请先联系管理员处理原订单。"),409
                cur.execute("INSERT INTO membership_orders (user_id,plan,amount_fen,payment_method,reference) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                            (session["user_id"],plan,PAID_PLANS[plan]["price_cny_fen"],method,reference))
                order_id = cur.fetchone()[0]
        return jsonify(order_id=order_id,reference=reference,amount_cny=PAID_PLANS[plan]["price_cny_fen"]/100,
                       status="pending",instruction="请使用收款二维码付款，备注订单编号，等待人工核实；不要重复付款。"),201
    except Exception:
        app.logger.exception("Membership order failed")
        return jsonify(error="订单创建失败"),503


@app.post("/membership/orders/<int:order_id>/payment-note")
@require_login
def submit_payment_note(order_id):
    data=request.get_json(silent=True) or {}
    note=data.get("note")
    if not isinstance(note,str) or not 6 <= len(note.strip()) <= 160:
        return jsonify(error="请填写支付时间及支付凭证信息（6-160字）"),400
    try:
        init_db()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE membership_orders SET payer_note=%s
                    WHERE id=%s AND user_id=%s AND status='pending'
                    RETURNING id
                """,(note.strip(),order_id,session["user_id"]))
                if cur.fetchone() is None:
                    return jsonify(error="订单不存在或已审核"),404
        return jsonify(ok=True,status="pending")
    except Exception:
        app.logger.exception("Payment note failed")
        return jsonify(error="提交付款信息失败"),503


@app.get("/membership/orders")
@require_login
def membership_orders():
    try:
        init_db()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,plan,amount_fen,payment_method,reference,status FROM membership_orders WHERE user_id=%s ORDER BY id DESC LIMIT 20",(session["user_id"],))
                rows=cur.fetchall()
        return jsonify(orders=[dict(id=r[0],plan=r[1],amount_cny=r[2]/100,payment_method=r[3],reference=r[4],status=r[5]) for r in rows])
    except Exception:
        app.logger.exception("Membership order list failed")
        return jsonify(error="查询订单失败"),503


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = os.getenv("ADMIN_REVIEW_TOKEN", "")
        provided = request.headers.get("X-Admin-Token", "")
        if len(expected) < 32 or not provided or not compare_digest(provided, expected):
            return jsonify(error="无权访问"), 403
        return fn(*args, **kwargs)
    return wrapper


@app.get("/admin")
def admin_dashboard():
    response = app.make_response("""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>fishyeah · 订单审核</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#15171b;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:640px;margin:auto;padding:22px 16px 60px}h1{font-size:25px;margin:10px 0 6px}p{line-height:1.5;color:#68717d}
.panel,.order{background:#fff;border:1px solid #e8eaf0;border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 3px 12px #10182808}
label{display:block;font-size:13px;font-weight:650;margin:14px 0 6px}
input{width:100%;font:inherit;padding:12px;border:1px solid #dce0e7;border-radius:10px}
button{border:0;border-radius:11px;padding:12px 16px;font:inherit;font-weight:650;background:#191d27;color:white;cursor:pointer;margin:10px 6px 0 0}
button.secondary{background:#eef0f4;color:#171b25}button.danger{background:#a52b36}
button:disabled{opacity:.45}.meta{font-size:13px;color:#657080;line-height:1.9;overflow-wrap:anywhere}
strong{font-size:18px}.empty{padding:24px 0;text-align:center;color:#7a8290}
</style></head><body><main>
<h1>fishyeah · 订单审核</h1><p>仅管理员使用。核对微信或支付宝实际到账记录后，再批准订单。</p>
<section class="panel"><label for="token">管理员密钥</label><input id="token" type="password" autocomplete="off" placeholder="输入 Render 中的 ADMIN_REVIEW_TOKEN">
<button id="load">查看待审核订单</button><p id="status" role="status">密钥仅用于本次页面操作，不会保存到浏览器。</p></section>
<section id="orders"></section>
</main><script>
const el=id=>document.getElementById(id);
async function call(path,method="GET",body){
 const token=el("token").value.trim();
 if(!token)throw Error("请先输入管理员密钥");
 const res=await fetch(path,{method,headers:{"X-Admin-Token":token,...(body?{"Content-Type":"application/json"}:{})},body:body?JSON.stringify(body):undefined,cache:"no-store"});
 const d=await res.json();if(!res.ok)throw Error(d.error||"操作失败");return d;
}
function field(container,title,value){const div=document.createElement("div");div.className="meta";div.textContent=title+"："+(value??"未填写");container.appendChild(div);}
async function load(){
 el("status").textContent="正在查询…";el("orders").replaceChildren();
 try{
  const d=await call("/admin/membership/orders");
  el("status").textContent="待审核订单："+d.orders.length+" 笔";
  if(!d.orders.length){const e=document.createElement("p");e.className="empty";e.textContent="暂无待审核订单";el("orders").appendChild(e);}
  for(const o of d.orders){
   const card=document.createElement("article");card.className="order";
   const h=document.createElement("strong");h.textContent="订单 #"+o.id+" · ¥"+o.amount_cny;card.appendChild(h);
   field(card,"会员套餐",o.plan==="pro"?"高级会员":"基础会员");
   field(card,"手机号",o.phone);field(card,"支付方式",o.payment_method==="alipay"?"支付宝":"微信");
   field(card,"付款信息",o.payer_note);field(card,"订单参考号",o.reference);field(card,"创建时间",o.created_at);
   const label=document.createElement("label");label.textContent="核实后的支付流水号";card.appendChild(label);
   const input=document.createElement("input");input.placeholder="至少 6 位，须与真实到账记录一致";card.appendChild(input);
   const yes=document.createElement("button");yes.textContent="确认到账并开通";card.appendChild(yes);
   const no=document.createElement("button");no.textContent="拒绝订单";no.className="danger";card.appendChild(no);
   async function review(decision){
    if(decision==="approve" && input.value.trim().length<6){alert("请先填写核实后的支付流水号");return;}
    if(!confirm(decision==="approve"?"已在收款账户核实到账且金额正确，确认开通会员？":"确定拒绝该订单？"))return;
    yes.disabled=true;no.disabled=true;
    try{await call("/admin/membership/orders/"+o.id+"/review","POST",{decision,payment_reference:input.value.trim()});await load();}
    catch(e){alert(e.message);yes.disabled=false;no.disabled=false;}
   }
   yes.onclick=()=>review("approve");no.onclick=()=>review("reject");el("orders").appendChild(card);
  }
 }catch(e){el("status").textContent=e.message;}
}
el("load").onclick=load;
</script></body></html>""")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/admin/membership/orders")
@require_admin
def admin_membership_orders():
    try:
        init_db()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT o.id,u.phone,o.plan,o.amount_fen,o.payment_method,o.reference,o.status,o.created_at,o.payer_note
                    FROM membership_orders o JOIN users u ON u.id=o.user_id
                    WHERE o.status='pending' ORDER BY o.created_at ASC LIMIT 100
                """)
                rows=cur.fetchall()
        return jsonify(orders=[dict(id=r[0],phone=r[1],plan=r[2],amount_cny=r[3]/100,
                                    payment_method=r[4],reference=r[5],status=r[6],
                                    created_at=r[7].isoformat(),payer_note=r[8]) for r in rows])
    except Exception:
        app.logger.exception("Admin order lookup failed")
        return jsonify(error="订单查询失败"),503


@app.post("/admin/membership/orders/<int:order_id>/review")
@require_admin
def admin_review_membership_order(order_id):
    data=request.get_json(silent=True) or {}
    decision=data.get("decision")
    payment_reference=data.get("payment_reference")
    if decision not in ("approve","reject"):
        return jsonify(error="无效审核结果"),400
    if decision=="approve" and (not isinstance(payment_reference,str) or not 6 <= len(payment_reference) <= 128):
        return jsonify(error="请填写核实过的支付流水号"),400
    try:
        init_db()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id,plan,amount_fen,status FROM membership_orders WHERE id=%s FOR UPDATE",(order_id,))
                row=cur.fetchone()
                if not row:
                    return jsonify(error="订单不存在"),404
                user_id,plan,amount,status=row
                if status!="pending":
                    return jsonify(error="订单已经审核，不能重复操作"),409
                if amount!=PAID_PLANS[plan]["price_cny_fen"]:
                    return jsonify(error="订单金额不匹配"),409
                if decision=="approve":
                    cur.execute("""
                        INSERT INTO subscriptions (user_id,plan,starts_at,ends_at,message_limit,payment_reference)
                        VALUES (%s,%s,NOW(),NOW()+INTERVAL '30 days',%s,%s)
                    """,(user_id,plan,PAID_PLANS[plan]["messages"],payment_reference))
                cur.execute("UPDATE membership_orders SET status=%s WHERE id=%s",
                            ("approved" if decision=="approve" else "rejected",order_id))
        return jsonify(ok=True,status="approved" if decision=="approve" else "rejected")
    except psycopg.errors.UniqueViolation:
        return jsonify(error="该支付流水号已用于其他订单"),409
    except Exception:
        app.logger.exception("Order review failed")
        return jsonify(error="订单审核失败"),503


def reserve_free_message(user_id):
    """Atomic quota reservation. False when five messages already reserved today (UTC)."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO daily_message_usage (user_id, usage_day, used)
                VALUES (%s, (NOW() AT TIME ZONE 'UTC')::date, 1)
                ON CONFLICT (user_id, usage_day)
                DO UPDATE SET used = daily_message_usage.used + 1
                WHERE daily_message_usage.used < %s
                RETURNING used
            """, (user_id, FREE_DAILY_MESSAGES))
            row = cur.fetchone()
            return row[0] if row else None


def refund_free_message(user_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE daily_message_usage SET used = GREATEST(0, used - 1)
                WHERE user_id = %s AND usage_day = (NOW() AT TIME ZONE 'UTC')::date
            """, (user_id,))




@app.get("/account/usage")
@require_login
def account_usage():
    try:
        init_db()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT used FROM daily_message_usage
                    WHERE user_id=%s AND usage_day=(NOW() AT TIME ZONE 'UTC')::date
                """, (session["user_id"],))
                row = cur.fetchone()
        used = row[0] if row else 0
        sub = get_active_subscription(session["user_id"])
        if sub:
            return jsonify(plan=sub[1], limit=sub[3], used=sub[4],
                           remaining=max(0,sub[3]-sub[4]), expires_at=sub[2].isoformat())
        return jsonify(plan="free", limit=FREE_DAILY_MESSAGES,
                       used=used, remaining=max(0, FREE_DAILY_MESSAGES-used),
                       reset="00:00 UTC")
    except Exception:
        app.logger.exception("Usage lookup failed")
        return jsonify(error="暂时无法查询额度"), 503


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
#membership{margin:18px 0;padding:17px;border:1px solid #e6e7ec;border-radius:18px;background:linear-gradient(160deg,#fff,#f5f7ff)}
#membership summary{font-weight:750;font-size:19px;cursor:pointer}
.member-intro{font-size:13px;color:#687080;margin:8px 0 14px}
.member-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0}
.member-card{border:1px solid #e4e5ed;border-radius:15px;padding:13px;background:white;min-width:0}
.member-card.featured{background:#161b29;color:white;border-color:#161b29}
.member-card h3{font-size:14px;margin:0 0 9px}
.member-card .price{font-size:27px;font-weight:800;letter-spacing:-1px}
.member-card .price small{font-size:12px;font-weight:400}
.member-card p{font-size:12px;line-height:1.6;margin:8px 0 0}
#membership select{font:inherit;width:100%;margin:6px 0 12px;padding:12px;border:1px solid #ddd;border-radius:10px;background:white}
#membership label{display:block;font-size:13px;font-weight:600}
#membership button{width:100%;margin:6px 0}
#ordersBtn{background:#e9eaf0;color:#151515}
#orderResult{padding:12px;border-radius:12px;background:#f0f2f6;font-size:13px;line-height:1.7;margin-top:10px}
.member-safe{font-size:12px;color:#747b8b;line-height:1.6}
@media(max-width:390px){.member-cards{grid-template-columns:1fr}}
</style><link rel="stylesheet" href="/static/fishyeah-ui.css?v=1"><link rel="stylesheet" href="/static/chat-focus.css?v=5"></head><body><main><h1>fishyeah Chat</h1>
<section id="auth"><h2 id="authTitle">登录</h2>
<input id="phone" type="tel" inputmode="numeric" maxlength="11" autocomplete="tel" placeholder="中国大陆手机号">
<input id="password" type="password" minlength="8" maxlength="128" autocomplete="current-password" placeholder="密码（新账号至少10位，包含字母和数字）">
<button id="loginBtn">登录</button><button id="registerBtn">注册新账号</button>
<p style="font-size:13px;color:#666">当前不发送短信验证码，手机号未经验证；请勿使用他人的号码注册。</p></section>
<section id="chatArea" class="hidden"><aside class="fish-side" id="fishSide"><div class="side-user">已登录：<span id="sideWho"></span></div><div id="sideContents"></div></aside><button class="fish-side-toggle" id="sideToggle" type="button" aria-expanded="false">☰ 菜单</button><p class="legacy-account">已登录：<span id="who"></span> <button id="logoutBtn">退出</button></p>
<details id="security"><summary>账号安全 · 修改密码</summary><input id="oldPassword" type="password" autocomplete="current-password" placeholder="当前密码"><input id="newPassword" type="password" autocomplete="new-password" placeholder="新密码（至少10位，包含不同类型字符）"><button id="changePasswordBtn">修改密码</button></details>
<details id="membership"><summary>✦ fishyeah 会员中心</summary>
<p class="member-intro">解锁更多对话额度 · 轻松使用 fishyeah Chat</p>
<div class="member-cards">
<div class="member-card"><h3>基础会员</h3><div class="price">¥9.9<small> / 30天</small></div><p>300 条 AI 消息<br>适合日常学习和轻度使用</p></div>
<div class="member-card featured"><h3>✦ 高级会员</h3><div class="price">¥19.9<small> / 30天</small></div><p>1,000 条 AI 消息<br>适合经常使用 AI 的用户</p></div>
</div>
<label for="memberPlan">选择套餐</label>
<select id="memberPlan"><option value="basic">基础会员 · ¥9.9</option><option value="pro">高级会员 · ¥19.9</option></select>
<label for="payMethod">支付方式</label>
<select id="payMethod"><option value="alipay">支付宝</option><option value="wechat">微信支付</option></select>
<button id="createOrderBtn">创建会员订单</button>
<button id="ordersBtn">查看我的订单</button>
<div id="orderResult" aria-live="polite">订单创建后将在这里显示订单信息。</div><div id="qrPanel" class="hidden" style="text-align:center;margin:12px 0"><p id="qrTitle"></p><img id="paymentQR" alt="收款二维码" width="240" height="240" style="max-width:100%;image-rendering:pixelated;border-radius:12px"><p class="member-safe">请先核对金额。付款后填写支付时间和凭证信息，等待人工核实。</p></div><div id="paymentProof" class="hidden"><label for="payerNote">付款信息（付款时间、支付平台订单号等）</label><input id="payerNote" maxlength="160" placeholder="例：10月17日 20:30，支付订单号末6位123456"><button id="submitProofBtn">我已付款，提交核实信息</button></div>
<p class="member-safe">当前使用个人收款码人工审核：请核对收款人及金额，付款后保存支付凭证；到账确认后才开通会员，不会自动扣费。</p>
</details>
<div class="ui-actions"><button id="themeBtn" type="button">◐ 切换主题</button><button id="newChatBtn" type="button">＋ 新对话</button></div><p id="quotaInfo">免费版每天可发送5条消息（UTC 00:00重置）</p><div id="chat"><div class="chat-row from-ai"><img class="chat-avatar" src="/app-icon" alt="fishyeah Chat"><div class="msg">你好，我是 fishyeah Chat。</div></div></div>
<input id="message" placeholder="输入你的问题..."><button id="sendBtn">发送</button></section>
<p id="notice" role="status"></p></main>
<script>
const $=id=>document.getElementById(id);
let activeOrderId=null;

document.getElementById("themeBtn").onclick=()=>{document.body.classList.toggle("dark");};
document.getElementById("newChatBtn").onclick=()=>{document.getElementById("chat").replaceChildren();const el=document.createElement("div");el.className="msg";el.textContent="✦ 你好，我是 fishyeah Chat。有什么可以帮你？";document.getElementById("chat").appendChild(el);};
const side=document.getElementById("fishSide");const sideToggle=document.getElementById("sideToggle");sideToggle.onclick=()=>{const open=side.classList.toggle("open");sideToggle.setAttribute("aria-expanded",String(open));sideToggle.textContent=open?"✕ 关闭":"☰ 菜单";};["security","membership","quotaInfo","logoutBtn"].forEach(id=>document.getElementById("sideContents").appendChild(document.getElementById(id)));
const notice=msg=>$("notice").textContent=msg;
async function api(path,data){
 const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data||{})});
 const body=await response.json().catch(()=>({error:"网络或服务器错误"}));
 if(!response.ok)throw Error(body.error||"请求失败");
 return body;
}
async function refreshMembership(){try{const r=await fetch("/membership/plans");if(r.ok){const d=await r.json();if(!d.payments_enabled){$("createOrderBtn").disabled=true;$("createOrderBtn").textContent="支付尚未开放";$("orderResult").textContent="会员页面已上线，支付尚未开放。请勿转账。";}else{$("createOrderBtn").disabled=false;$("createOrderBtn").textContent="创建会员订单";}}}catch(e){}}
async function refreshQuota(){try{const r=await fetch("/account/usage");if(r.ok){const d=await r.json();$("quotaInfo").textContent=(d.plan==="free"?"免费版":"会员 "+d.plan)+"：剩余 "+d.remaining+"/"+d.limit+" 条"+(d.plan==="free"?"（UTC 00:00重置）":"（当前会员周期）");}}catch(e){}}
function showUser(phone){if(phone){refreshQuota();refreshMembership();}$("auth").classList.toggle("hidden",!!phone);$("chatArea").classList.toggle("hidden",!phone);$("who").textContent=phone||"";$("sideWho").textContent=phone||"";notice("");}
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

$("createOrderBtn").onclick=async()=>{
 $("createOrderBtn").disabled=true;
 $("orderResult").textContent="正在创建订单，请稍候…";
 try{
  const d=await api("/membership/orders",{plan:$("memberPlan").value,payment_method:$("payMethod").value});
  $("orderResult").textContent="订单编号："+d.order_id+"\\n金额：¥"+d.amount_cny+"\\n状态：等待人工核实。付款前请保存订单编号。";
  $("qrTitle").textContent=($("payMethod").value==="alipay"?"支付宝":"微信支付")+" · ¥"+d.amount_cny;
  $("paymentQR").src="/membership/payment-qr/"+$("payMethod").value;
  $("qrPanel").classList.remove("hidden");
  activeOrderId=d.order_id;$("paymentProof").classList.remove("hidden");
 }catch(e){$("orderResult").textContent="创建订单失败："+e.message;notice(e.message);}
 finally{$("createOrderBtn").disabled=false;}
};
$("submitProofBtn").onclick=async()=>{
 if(!activeOrderId){notice("请先创建订单");return;}
 $("submitProofBtn").disabled=true;
 try{
  await api("/membership/orders/"+activeOrderId+"/payment-note",{note:$("payerNote").value.trim()});
  $("orderResult").textContent="付款信息已提交，订单 #"+activeOrderId+" 正在等待人工核实。";
  $("paymentProof").classList.add("hidden");$("qrPanel").classList.add("hidden");
 }catch(e){notice(e.message);}
 finally{$("submitProofBtn").disabled=false;}
};
$("ordersBtn").onclick=async()=>{
 try{
  const r=await fetch("/membership/orders");
  const d=await r.json();
  if(!r.ok)throw Error(d.error||"订单查询失败");
  $("orderResult").textContent=d.orders.length?d.orders.map(o=>"订单 #"+o.id+" · "+o.plan+" · ¥"+o.amount_cny+" · "+o.status).join("\\n"):"暂无订单";
 }catch(e){notice(e.message);}
};
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
 const add=(label,text)=>{const el=document.createElement("div");const isUser=label==="🧑";el.className="msg"+(isUser?" user":label==="❌"?" error":"");el.textContent=text;const row=document.createElement("div");row.className="chat-row "+(isUser?"from-user":"from-ai");const avatar=document.createElement("img");avatar.className="chat-avatar";avatar.alt=isUser?"我的头像":"fishyeah Chat";avatar.src=isUser?"/static/chat-user-avatar.svg":"/app-icon";if(isUser){row.append(el,avatar);}else{row.append(avatar,el);} $("chat").appendChild(row);$("chat").scrollTop=$("chat").scrollHeight;};
 add("🧑",message);$("message").value="";$("sendBtn").disabled=true;
 try{const result=await api("/chat",{message});add("🤖",result.reply);}
 catch(e){add("❌",e.message);}
 finally{$("sendBtn").disabled=false;refreshQuota();}
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
    user_id = session["user_id"]
    try:
        init_db()
        kind, subscription_id, remaining = reserve_message(user_id)
    except Exception:
        app.logger.exception("Quota check failed")
        return jsonify(error="额度检查暂时不可用，请稍后再试"), 503
    if remaining is None:
        return jsonify(error="当前消息额度已用完，请等待重置或续费"), 429
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.responses.create(model=MODEL, input=message.strip())
        return jsonify(reply=response.output_text, remaining=remaining)
    except Exception:
        app.logger.exception("AI request failed")
        try:
            refund_message(user_id, kind, subscription_id)
        except Exception:
            app.logger.exception("Quota refund failed")
        return jsonify(error="AI 请求失败，请稍后再试"), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
