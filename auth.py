import os
import re
import secrets
from functools import wraps

from flask import Blueprint, current_app, jsonify, request, session
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import update
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()
auth = Blueprint("auth", __name__)
auth_limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))
PHONE_RE = re.compile(r"^1[3-9][0-9]{9}$")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(11), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    credits = db.Column(db.Integer, nullable=False, default=5)


def setup_auth(app):
    secret = os.getenv("SECRET_KEY")
    if not secret or len(secret) < 32:
        raise RuntimeError("Set a random SECRET_KEY (at least 32 characters) in environment")
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database")
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]
    app.config.update(
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
    )
    db.init_app(app)
    auth_limiter.init_app(app)
    app.register_blueprint(auth)
    with app.app_context():
        db.create_all()


def current_user():
    user_id = session.get("uid")
    return db.session.get(User, user_id) if isinstance(user_id, int) else None


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            return jsonify(error="请先登录"), 401
        return fn(*args, **kwargs)
    return wrapped


@auth.get("/csrf")
def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return jsonify(csrf_token=session["csrf"])


@auth.before_app_request
def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        expected = session.get("csrf")
        provided = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            return jsonify(error="安全验证失败，请刷新页面"), 403


@auth.post("/register")
@auth_limiter.limit("3 per hour")
def register():
    payload = request.get_json(silent=True) or {}
    phone, password = payload.get("phone"), payload.get("password")
    if not isinstance(phone, str) or not PHONE_RE.fullmatch(phone):
        return jsonify(error="请输入有效的中国大陆手机号"), 400
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        return jsonify(error="密码长度需要在10到128位之间"), 400
    if db.session.execute(db.select(User.id).filter_by(phone=phone)).first():
        return jsonify(error="该手机号已注册"), 409
    user = User(phone=phone, password_hash=generate_password_hash(password), credits=5)
    db.session.add(user)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify(error="注册失败或手机号已注册"), 409
    session.clear()
    session["uid"] = user.id
    session["csrf"] = secrets.token_urlsafe(32)
    return jsonify(phone=user.phone, credits=user.credits, csrf_token=session["csrf"]), 201


@auth.post("/login")
@auth_limiter.limit("5 per 15 minutes")
def login():
    payload = request.get_json(silent=True) or {}
    phone, password = payload.get("phone"), payload.get("password")
    if not isinstance(phone, str) or not isinstance(password, str):
        return jsonify(error="手机号或密码错误"), 401
    user = db.session.execute(db.select(User).filter_by(phone=phone)).scalar_one_or_none()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify(error="手机号或密码错误"), 401
    session.clear()
    session["uid"] = user.id
    session["csrf"] = secrets.token_urlsafe(32)
    return jsonify(phone=user.phone, credits=user.credits, csrf_token=session["csrf"])


@auth.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@auth.get("/me")
@login_required
def me():
    user = current_user()
    return jsonify(phone=user.phone, credits=user.credits)


def spend_credit(user_id):
    result = db.session.execute(
        update(User).where(User.id == user_id, User.credits > 0)
        .values(credits=User.credits - 1)
    )
    db.session.commit()
    return result.rowcount == 1


def refund_credit(user_id):
    db.session.execute(update(User).where(User.id == user_id).values(credits=User.credits + 1))
    db.session.commit()
