"""Verified email binding and recovery, backed by durable single-use challenges."""
import hmac
import json
import re
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from secrets import token_urlsafe, randbelow

from flask import current_app, jsonify, request, session, send_from_directory, redirect
from werkzeug.security import generate_password_hash, check_password_hash
import email_delivery

PHONE = re.compile(r'1[3-9][0-9]{9}')
SEND_MESSAGE = '若该账号已绑定并验证邮箱，验证码将发送至该邮箱，5分钟内有效。未收到请稍后重试或联系管理员。'


# A small, bounded queue keeps public recovery responses independent of SMTP latency.
_delivery_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='account-email')
_delivery_slots = BoundedSemaphore(20)


def submit_delivery(task):
    if not _delivery_slots.acquire(blocking=False):
        return False
    def run():
        try:
            task()
        finally:
            _delivery_slots.release()
    try:
        _delivery_pool.submit(run)
        return True
    except RuntimeError:
        _delivery_slots.release()
        return False


def create_schema(cur):
    cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version BIGINT NOT NULL DEFAULT 0')
    cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT')
    cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ')
    cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS email_version BIGINT NOT NULL DEFAULT 0')
    cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_verified_email ON users (LOWER(email)) WHERE email_verified_at IS NOT NULL')
    # Separate from the retired SMS table: no legacy SMS challenge or grant can authorize reset.
    cur.execute('''CREATE TABLE IF NOT EXISTS email_password_recoveries (
        phone VARCHAR(11) PRIMARY KEY,
        user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
        email TEXT,
        challenge_hash CHAR(64) NOT NULL,
        code_hash CHAR(64),
        expires_at TIMESTAMPTZ NOT NULL,
        sent BOOLEAN NOT NULL DEFAULT FALSE,
        attempts INTEGER NOT NULL DEFAULT 0,
        grant_hash CHAR(64),
        grant_expires_at TIMESTAMPTZ,
        session_version BIGINT NOT NULL DEFAULT 0,
        email_version BIGINT NOT NULL DEFAULT 0
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS account_email_challenges (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        email TEXT NOT NULL,
        challenge_hash CHAR(64) NOT NULL,
        code_hash CHAR(64),
        expires_at TIMESTAMPTZ NOT NULL,
        sent BOOLEAN NOT NULL DEFAULT FALSE,
        attempts INTEGER NOT NULL DEFAULT 0,
        session_version BIGINT NOT NULL,
        email_version BIGINT NOT NULL
    )''')


def digest(value):
    return sha256(value.encode()).hexdigest()


def code_digest(purpose, challenge, email, code):
    # Six digit codes need a server-keyed MAC to resist offline enumeration after a DB leak.
    key = current_app.secret_key
    if not key:
        raise RuntimeError('Server session secret required')
    if isinstance(key, str):
        key = key.encode()
    payload = json.dumps([purpose, challenge, email, code], ensure_ascii=True).encode()
    return hmac.new(key, payload, sha256).hexdigest()


def new_code():
    return f'{randbelow(1000000):06d}'


def register(app, db_connection, init_db, rate_limit):
    def error(message, status=400):
        return jsonify(error=message), status

    def data():
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}

    def valid_phone(phone):
        return isinstance(phone, str) and PHONE.fullmatch(phone)

    def valid_token(token):
        return isinstance(token, str) and 20 <= len(token) <= 100

    def valid_code(code):
        return isinstance(code, str) and re.fullmatch(r'[0-9]{6}', code)

    def limits(action, identity):
        ip = request.remote_addr or 'unknown'
        return (rate_limit('email:' + action + ':ip', ip, 20)
                and rate_limit('email:' + action + ':identity', str(identity), 5))

    def recipient_limits(email):
        # Shared across binding and recovery to prevent mailbox bombing via either endpoint.
        return (rate_limit('email:recipient:day', email, 10, 1440)
                and rate_limit('email:recipient:minute', email, 1, 1))

    def ready():
        return email_delivery.configured() and bool(app.secret_key)

    def deliver(email, code, purpose):
        try:
            email_delivery.send_code(email, code, purpose)
            return True
        except Exception:
            # SMTP exceptions may include credentials, mailboxes or the message. Never log them.
            app.logger.warning('Account email delivery unavailable')
            return False

    @app.before_request
    def validate_session():
        if not session.get('user_id'):
            if request.path.startswith('/auth/email/'):
                return error('请先登录', 401)
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
            return error('暂时无法验证登录状态，请稍后重试', 503)

    def protected_page(filename):
        response = send_from_directory(app.static_folder, filename)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get('/forgot-password')
    def page():
        return protected_page('forgot-password.html')

    @app.get('/account/email')
    def binding_page():
        if not session.get('user_id'):
            return redirect('/')
        return protected_page('account-email.html')

    @app.after_request
    def no_cache(response):
        if request.path.startswith(('/auth/recovery/', '/auth/email/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/auth/email/status')
    def email_status():
        try:
            with db_connection() as conn:
                row = conn.execute('SELECT email,email_verified_at FROM users WHERE id=%s', (session['user_id'],)).fetchone()
            return jsonify(email=row[0] if row and row[1] else None, verified=bool(row and row[0] and row[1]))
        except Exception:
            return error('邮箱状态暂时不可用，请稍后重试', 503)

    @app.post('/auth/email/send')
    def send_binding():
        body = data()
        email, password = email_delivery.normalize_email(body.get('email')), body.get('current_password')
        if not email or not isinstance(password, str) or not 1 <= len(password) <= 128:
            return error('请输入有效邮箱及当前密码')
        if not ready():
            return error('邮箱验证服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            user_id = session['user_id']
            if not limits('bind-send', user_id):
                return error('操作过于频繁，请15分钟后再试', 429)
            challenge, code = token_urlsafe(32), new_code()
            with db_connection() as conn:
                # Never rely only on the session: reauthenticate the current password.
                row = conn.execute('SELECT password_hash,session_version,email_version FROM users WHERE id=%s', (user_id,)).fetchone()
                if (not row or row[1] != session.get('session_version', 0)
                        or not check_password_hash(row[0], password)):
                    return error('当前密码不正确或登录已失效，请重新验证', 401)
            if not recipient_limits(email):
                return error('发送过于频繁，请稍后再试（至少间隔60秒）', 429)
            with db_connection() as conn:
                conn.execute('''INSERT INTO account_email_challenges
                    (user_id,email,challenge_hash,code_hash,expires_at,session_version,email_version)
                    VALUES (%s,%s,%s,%s,clock_timestamp()+INTERVAL '5 minutes',%s,%s)
                    ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email,
                    challenge_hash=EXCLUDED.challenge_hash,code_hash=EXCLUDED.code_hash,
                    expires_at=EXCLUDED.expires_at,session_version=EXCLUDED.session_version,
                    email_version=EXCLUDED.email_version,sent=FALSE,attempts=0''',
                    (user_id, email, digest(challenge), code_digest('bind', challenge, email, code), row[1], row[2]))
            if not deliver(email, code, 'bind'):
                return error('邮件暂时无法发送，请稍后重试或联系管理员', 503)
            with db_connection() as conn:
                conn.execute('UPDATE account_email_challenges SET sent=TRUE WHERE user_id=%s AND challenge_hash=%s',
                             (user_id, digest(challenge)))
            return jsonify(challenge=challenge, expires_in=300, retry_after=60,
                           message='验证码已发送，请在5分钟内验证邮箱；验证前不会更改现有绑定。')
        except Exception:
            app.logger.warning('Account email binding unavailable')
            return error('邮箱绑定暂时不可用，请稍后重试', 503)

    @app.post('/auth/email/verify')
    def verify_binding():
        body = data()
        challenge, code = body.get('challenge'), body.get('code')
        if not valid_token(challenge) or not valid_code(code):
            return error('请输入6位邮箱验证码')
        if not ready():
            return error('邮箱验证服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            user_id = session['user_id']
            if not limits('bind-verify', user_id):
                return error('验证次数过多，请15分钟后再试', 429)
            # Increment and validate under one row lock; returning an error still commits attempts.
            with db_connection() as conn:
                row = conn.execute('''UPDATE account_email_challenges SET attempts=attempts+1
                    WHERE user_id=%s AND challenge_hash=%s AND sent=TRUE AND code_hash IS NOT NULL
                    AND expires_at>clock_timestamp() AND attempts<5
                    RETURNING email,code_hash,session_version,email_version''',
                    (user_id, digest(challenge))).fetchone()
                if not row or not hmac.compare_digest(row[1], code_digest('bind', challenge, row[0], code)):
                    return error('验证码不正确、已过期或尝试次数过多')
                user = conn.execute('SELECT session_version,email_version FROM users WHERE id=%s FOR UPDATE', (user_id,)).fetchone()
                if not user or tuple(user) != (row[2], row[3]) or user[0] != session.get('session_version', 0):
                    return error('验证已失效，请重新输入当前密码并获取验证码')
                conn.execute('UPDATE users SET email=%s,email_verified_at=clock_timestamp(),email_version=email_version+1 WHERE id=%s', (row[0], user_id))
                conn.execute('DELETE FROM account_email_challenges WHERE user_id=%s', (user_id,))
            return jsonify(ok=True, email=row[0], message='邮箱绑定成功，可用于找回密码。')
        except Exception as exc:
            # A mailbox can belong to one verified account; do not disclose the other account.
            from psycopg.errors import UniqueViolation
            if isinstance(exc, UniqueViolation):
                return error('该邮箱无法绑定，请使用其他邮箱', 409)
            app.logger.warning('Account email verification unavailable')
            return error('邮箱验证暂时不可用，请稍后重试', 503)

    @app.post('/auth/recovery/send')
    def send():
        phone = data().get('phone')
        if not valid_phone(phone):
            return error('请输入正确的11位中国大陆注册手机号')
        if not ready():
            return error('邮箱找回服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            init_db()
            # All public limits depend on the submitted phone/IP, never account existence.
            if not limits('send', phone) or not rate_limit('email:recovery:day', phone, 10, 1440):
                return error('发送过于频繁，请稍后再试', 429)
            if not rate_limit('email:recovery:minute', phone, 1, 1):
                return error('请等待60秒后再获取验证码', 429)
            challenge, code = token_urlsafe(32), new_code()
            with db_connection() as conn:
                conn.execute("DELETE FROM email_password_recoveries WHERE expires_at < clock_timestamp()-INTERVAL '1 day'")
                conn.execute("DELETE FROM account_email_challenges WHERE expires_at < clock_timestamp()-INTERVAL '1 day'")
                user = conn.execute('''SELECT id,session_version,email,email_version FROM users
                    WHERE phone=%s AND email IS NOT NULL AND email_verified_at IS NOT NULL''', (phone,)).fetchone()
                email = user[2] if user else None
                conn.execute('''INSERT INTO email_password_recoveries
                    (phone,user_id,email,challenge_hash,code_hash,expires_at,session_version,email_version)
                    VALUES (%s,%s,%s,%s,%s,clock_timestamp()+INTERVAL '5 minutes',%s,%s)
                    ON CONFLICT (phone) DO UPDATE SET user_id=EXCLUDED.user_id,email=EXCLUDED.email,
                    challenge_hash=EXCLUDED.challenge_hash,code_hash=EXCLUDED.code_hash,
                    expires_at=EXCLUDED.expires_at,session_version=EXCLUDED.session_version,
                    email_version=EXCLUDED.email_version,sent=FALSE,attempts=0,
                    grant_hash=NULL,grant_expires_at=NULL''',
                    (phone, user[0] if user else None, email, digest(challenge),
                     code_digest('recover', challenge, email, code) if user else None,
                     user[1] if user else 0, user[3] if user else 0))
            # No caller-supplied email can influence the destination. Uniform response also
            # covers recipient limits/SMTP failures, so delivery errors cannot enumerate accounts.
            def send_later():
                try:
                    with db_connection() as conn:
                        active = conn.execute('''SELECT 1 FROM email_password_recoveries r
                            JOIN users u ON u.id=r.user_id
                            WHERE r.phone=%s AND r.challenge_hash=%s AND r.code_hash IS NOT NULL
                            AND r.expires_at>clock_timestamp() AND u.email=r.email
                            AND u.email_verified_at IS NOT NULL
                            AND u.email_version=r.email_version AND u.session_version=r.session_version''',
                            (phone, digest(challenge))).fetchone()
                    if not active or not recipient_limits(email) or not deliver(email, code, 'recover'):
                        return
                    with db_connection() as conn:
                        conn.execute('''UPDATE email_password_recoveries SET sent=TRUE
                            WHERE phone=%s AND challenge_hash=%s AND expires_at>clock_timestamp()''',
                            (phone, digest(challenge)))
                except Exception:
                    app.logger.warning('Recovery email job unavailable')
            # Queue dummy accounts too; no response field or SMTP timing reveals eligibility.
            submit_delivery(send_later)
            return jsonify(challenge=challenge, expires_in=300, retry_after=60, message=SEND_MESSAGE)
        except Exception:
            app.logger.warning('Email password recovery unavailable')
            return error('找回密码服务暂时不可用，请稍后重试', 503)

    @app.post('/auth/recovery/verify')
    def verify():
        body = data()
        phone, challenge, code = (body.get(k) for k in ('phone', 'challenge', 'code'))
        if not valid_phone(phone) or not valid_token(challenge) or not valid_code(code):
            return error('请输入手机号和6位邮箱验证码')
        if not ready():
            return error('邮箱找回服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            init_db()
            if not limits('verify', phone):
                return error('验证次数过多，请15分钟后再试', 429)
            with db_connection() as conn:
                row = conn.execute('''UPDATE email_password_recoveries SET attempts=attempts+1
                    WHERE phone=%s AND challenge_hash=%s AND sent=TRUE AND code_hash IS NOT NULL
                    AND expires_at>clock_timestamp() AND attempts<5 AND grant_hash IS NULL
                    RETURNING user_id,email,code_hash,session_version,email_version''',
                    (phone, digest(challenge))).fetchone()
                if not row or not row[0] or not hmac.compare_digest(row[2], code_digest('recover', challenge, row[1], code)):
                    return error('验证码不正确、已过期或尝试次数过多')
                user = conn.execute('''SELECT session_version,email_version,email,email_verified_at
                    FROM users WHERE id=%s FOR UPDATE''', (row[0],)).fetchone()
                if not user or tuple(user[:3]) != (row[3], row[4], row[1]) or not user[3]:
                    return error('验证码不正确、已过期或尝试次数过多')
                token = token_urlsafe(32)
                conn.execute('''UPDATE email_password_recoveries SET code_hash=NULL,grant_hash=%s,
                    grant_expires_at=clock_timestamp()+INTERVAL '5 minutes' WHERE phone=%s''', (digest(token), phone))
            return jsonify(reset_token=token, expires_in=300)
        except Exception:
            app.logger.warning('Email recovery verification unavailable')
            return error('邮箱验证暂时不可用，请稍后重试', 503)

    @app.post('/auth/recovery/reset')
    def reset():
        body = data()
        phone, token, password = (body.get(k) for k in ('phone', 'reset_token', 'new_password'))
        if not valid_phone(phone) or not valid_token(token):
            return error('请先完成邮箱验证')
        if (not isinstance(password, str) or not 10 <= len(password) <= 128
                or not re.search(r'[A-Za-z]', password) or not re.search(r'[^A-Za-z]', password)):
            return error('新密码需为10–128位，包含字母及数字或符号')
        if not ready():
            return error('邮箱找回服务尚未配置完成，请稍后再试或联系管理员', 503)
        try:
            init_db()
            if not limits('reset', phone):
                return error('操作过于频繁，请15分钟后再试', 429)
            hashed = generate_password_hash(password, method='scrypt')
            with db_connection() as conn:
                row = conn.execute('''SELECT user_id,session_version,email_version,email FROM email_password_recoveries
                    WHERE phone=%s AND grant_hash=%s AND grant_expires_at>clock_timestamp() FOR UPDATE''',
                    (phone, digest(token))).fetchone()
                if not row or not row[0]:
                    return error('验证已失效，请重新获取邮箱验证码')
                user = conn.execute('''SELECT password_hash,session_version,email_version,email,email_verified_at
                    FROM users WHERE id=%s FOR UPDATE''', (row[0],)).fetchone()
                if not user or tuple(user[1:4]) != tuple(row[1:4]) or not user[4]:
                    return error('验证已失效，请重新获取邮箱验证码')
                if check_password_hash(user[0], password):
                    return error('新密码不能与旧密码相同')
                conn.execute('UPDATE users SET password_hash=%s,session_version=session_version+1 WHERE id=%s', (hashed, row[0]))
                conn.execute('DELETE FROM email_password_recoveries WHERE phone=%s', (phone,))
            session.clear()
            return jsonify(ok=True, message='密码已重置，所有设备已退出，请使用新密码登录')
        except Exception:
            app.logger.warning('Email recovery reset unavailable')
            return error('重置密码暂时不可用，请稍后重试', 503)
