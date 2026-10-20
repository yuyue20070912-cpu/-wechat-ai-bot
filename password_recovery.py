"""Phone recovery: provider verification, durable limits and single-use grants."""
import os
import re
from hashlib import sha256
from secrets import token_urlsafe

from flask import jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

ENV_KEYS = ('ALIBABA_CLOUD_ACCESS_KEY_ID', 'ALIBABA_CLOUD_ACCESS_KEY_SECRET',
            'ALIYUN_PNVS_SIGN_NAME', 'ALIYUN_PNVS_TEMPLATE_CODE', 'ALIYUN_PNVS_SCHEME_NAME')


def configured():
    return all(os.getenv(key, '').strip() for key in ENV_KEYS)


def sms_call(action, phone, out_id, code=None):
    # Lazy imports allow the login page to remain usable when SMS is unconfigured.
    from alibabacloud_dypnsapi20170525.client import Client
    from alibabacloud_dypnsapi20170525 import models
    from alibabacloud_tea_openapi.models import Config
    from alibabacloud_tea_util.models import RuntimeOptions
    client = Client(Config(access_key_id=os.environ[ENV_KEYS[0]],
                           access_key_secret=os.environ[ENV_KEYS[1]],
                           endpoint='dypnsapi.aliyuncs.com', protocol='https'))
    common = dict(phone_number=phone, country_code='86', out_id=out_id,
                  scheme_name=os.environ['ALIYUN_PNVS_SCHEME_NAME'])
    runtime = RuntimeOptions(connect_timeout=3000, read_timeout=5000, autoretry=False)
    if action == 'send':
        req = models.SendSmsVerifyCodeRequest(
            **common, sign_name=os.environ['ALIYUN_PNVS_SIGN_NAME'],
            template_code=os.environ['ALIYUN_PNVS_TEMPLATE_CODE'],
            template_param='{"code":"##code##","min":"5"}',
            code_type=1, code_length=6, valid_time=300, interval=60,
            duplicate_policy=1, return_verify_code=False)
        body = client.send_sms_verify_code_with_options(req, runtime).body
        return body.success is True and body.code == 'OK'
    req = models.CheckSmsVerifyCodeRequest(**common, verify_code=code)
    body = client.check_sms_verify_code_with_options(req, runtime).body
    return (body.success is True and body.code == 'OK' and body.model is not None
            and body.model.verify_result == 'PASS')


def create_schema(cur):
    cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version BIGINT NOT NULL DEFAULT 0')
    cur.execute('''CREATE TABLE IF NOT EXISTS password_recoveries (
        phone VARCHAR(11) PRIMARY KEY,
        user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
        challenge_hash CHAR(64) NOT NULL,
        out_id TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        sent BOOLEAN NOT NULL DEFAULT FALSE,
        attempts INTEGER NOT NULL DEFAULT 0,
        grant_hash CHAR(64),
        grant_expires_at TIMESTAMPTZ,
        session_version BIGINT NOT NULL DEFAULT 0
    )''')


def digest(value):
    return sha256(value.encode()).hexdigest()


def register(app, db_connection, init_db, rate_limit):
    def error(message, status=400):
        return jsonify(error=message), status

    def limits(action, phone):
        ip = request.remote_addr or 'unknown'
        return (rate_limit('recovery:' + action + ':ip', ip, 20)
                and rate_limit('recovery:' + action + ':phone', phone, 5))

    @app.before_request
    def validate_session():
        if not session.get('user_id'):
            return
        try:
            init_db()
            with db_connection() as conn:
                row = conn.execute('SELECT session_version FROM users WHERE id=%s',
                                   (session['user_id'],)).fetchone()
            if not row or row[0] != session.get('session_version', 0):
                session.clear()
                if request.path == '/auth/me':
                    return jsonify(authenticated=False)
                return error('密码已更新，请重新登录', 401)
        except Exception:
            # Never accept an unverifiable session during a database outage.
            return error('暂时无法验证登录状态，请稍后重试', 503)

    @app.get('/forgot-password')
    def page():
        response = send_from_directory(app.static_folder, 'forgot-password.html')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.after_request
    def no_cache(response):
        if request.path.startswith('/auth/recovery/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/auth/recovery/send')
    def send():
        data = request.get_json(silent=True)
        phone = data.get('phone') if isinstance(data, dict) else None
        if not isinstance(phone, str) or not re.fullmatch(r'1[3-9][0-9]{9}', phone):
            return error('请输入正确的11位中国大陆注册手机号')
        if not configured():
            return error('短信找回服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            init_db()
            if not limits('send', phone) or not rate_limit('recovery:day', phone, 10, 1440):
                return error('发送过于频繁，请稍后再试', 429)
            if not rate_limit('recovery:minute', phone, 1, 1):
                return error('请等待60秒后再获取验证码', 429)
            challenge, out_id = token_urlsafe(32), token_urlsafe(24)
            with db_connection() as conn:
                # A new send immediately invalidates older verification and reset grants.
                conn.execute('DELETE FROM password_recoveries WHERE expires_at < NOW() - INTERVAL \'1 day\'')
                user = conn.execute('SELECT id,session_version FROM users WHERE phone=%s', (phone,)).fetchone()
                conn.execute('''INSERT INTO password_recoveries
                    (phone,user_id,challenge_hash,out_id,expires_at,session_version)
                    VALUES (%s,%s,%s,%s,NOW()+INTERVAL '5 minutes',%s)
                    ON CONFLICT (phone) DO UPDATE SET user_id=EXCLUDED.user_id,
                    challenge_hash=EXCLUDED.challenge_hash,out_id=EXCLUDED.out_id,
                    expires_at=EXCLUDED.expires_at,session_version=EXCLUDED.session_version,
                    sent=FALSE,attempts=0,grant_hash=NULL,grant_expires_at=NULL''',
                    (phone, user[0] if user else None, digest(challenge), out_id, user[1] if user else 0))
            # Same response for unknown accounts; never send SMS to an unregistered phone.
            if user and not sms_call('send', phone, out_id):
                return error('短信暂时无法发送，请稍后重试或联系管理员', 503)
            with db_connection() as conn:
                conn.execute('UPDATE password_recoveries SET sent=TRUE WHERE phone=%s AND challenge_hash=%s',
                             (phone, digest(challenge)))
            return jsonify(challenge=challenge, expires_in=300, retry_after=60,
                           message='若该手机号已注册，验证码将通过短信发送，5分钟内有效')
        except Exception:
            # Provider exceptions can contain credentials, phone or code. Do not log them.
            app.logger.warning('Password recovery send unavailable')
            return error('短信暂时无法发送，请稍后重试或联系管理员', 503)

    @app.post('/auth/recovery/verify')
    def verify():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error('请求格式不正确')
        phone, challenge, code = (data.get(k) for k in ('phone', 'challenge', 'code'))
        if (not isinstance(phone, str) or not re.fullmatch(r'1[3-9][0-9]{9}', phone)
                or not isinstance(challenge, str) or not 20 <= len(challenge) <= 100
                or not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code)):
            return error('请输入手机号和6位短信验证码')
        if not configured():
            return error('短信找回服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            init_db()
            if not limits('verify', phone):
                return error('验证次数过多，请15分钟后再试', 429)
            # Commit each attempt before calling the provider, including timeouts.
            with db_connection() as conn:
                row = conn.execute('''UPDATE password_recoveries SET attempts=attempts+1
                    WHERE phone=%s AND challenge_hash=%s AND sent=TRUE
                    AND expires_at>NOW() AND attempts<5 AND grant_hash IS NULL
                    RETURNING out_id''', (phone, digest(challenge))).fetchone()
            if not row:
                return error('验证码无效、已过期或尝试次数过多，请重新获取')
            with db_connection() as conn:
                # Serialize provider calls and issuance against resends and replays.
                row = conn.execute('''SELECT out_id,user_id FROM password_recoveries
                    WHERE phone=%s AND challenge_hash=%s AND sent=TRUE
                    AND expires_at>NOW() AND grant_hash IS NULL FOR UPDATE''',
                    (phone, digest(challenge))).fetchone()
                if not row or not row[1] or not sms_call('verify', phone, row[0], code):
                    return error('验证码不正确或已过期')
                token = token_urlsafe(32)
                updated = conn.execute('''UPDATE password_recoveries SET grant_hash=%s,
                    grant_expires_at=NOW()+INTERVAL '5 minutes'
                    WHERE phone=%s AND expires_at>NOW() RETURNING phone''',
                    (digest(token), phone)).fetchone()
                if not updated:
                    return error('验证码已过期，请重新获取')
            return jsonify(reset_token=token, expires_in=300)
        except Exception:
            app.logger.warning('Password recovery verification unavailable')
            return error('短信验证暂时不可用，请稍后重试', 503)

    @app.post('/auth/recovery/reset')
    def reset():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error('请求格式不正确')
        phone, token, password = (data.get(k) for k in ('phone', 'reset_token', 'new_password'))
        if (not isinstance(phone, str) or not re.fullmatch(r'1[3-9][0-9]{9}', phone)
                or not isinstance(token, str) or not 20 <= len(token) <= 100):
            return error('请先完成短信验证')
        if (not isinstance(password, str) or not 10 <= len(password) <= 128
                or not re.search(r'[A-Za-z]', password) or not re.search(r'[^A-Za-z]', password)):
            return error('新密码需为10–128位，包含字母及数字或符号')
        if not configured():
            return error('短信找回服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            init_db()
            if not limits('reset', phone):
                return error('操作过于频繁，请15分钟后再试', 429)
            hashed = generate_password_hash(password, method='scrypt')
            with db_connection() as conn:
                row = conn.execute('''SELECT user_id,session_version FROM password_recoveries
                    WHERE phone=%s AND grant_hash=%s AND grant_expires_at>NOW()
                    FOR UPDATE''', (phone, digest(token))).fetchone()
                if not row or not row[0]:
                    return error('验证已失效，请重新获取短信验证码')
                user = conn.execute('SELECT password_hash,session_version FROM users WHERE id=%s FOR UPDATE',
                                    (row[0],)).fetchone()
                if not user or user[1] != row[1]:
                    return error('验证已失效，请重新获取短信验证码')
                if check_password_hash(user[0], password):
                    return error('新密码不能与旧密码相同')
                conn.execute('''UPDATE users SET password_hash=%s,session_version=session_version+1
                    WHERE id=%s''', (hashed, row[0]))
                conn.execute('DELETE FROM password_recoveries WHERE phone=%s', (phone,))
            session.clear()
            return jsonify(ok=True, message='密码已重置，所有设备已退出，请使用新密码登录')
        except Exception:
            app.logger.warning('Password recovery reset unavailable')
            return error('重置密码暂时不可用，请稍后重试', 503)
