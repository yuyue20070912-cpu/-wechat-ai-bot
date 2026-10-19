from decimal import Decimal, InvalidOperation
from secrets import compare_digest, token_urlsafe

from flask import Blueprint, current_app, jsonify, request, send_from_directory, session

from .catalog import EXAMS, LEVELS
from .repository import list_profiles, save_profile


def create_blueprint(connection, require_login, rate_limit, ai_provider, model):
    bp = Blueprint('learning', __name__)

    @bp.errorhandler(413)
    def too_large(error):
        return jsonify(error='提交内容过大，请缩短答案后重试'), 413

    @bp.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'microphone=(self), camera=()'
        return response

    @bp.get('/learning')
    @require_login
    def center():
        return send_from_directory(current_app.static_folder, 'learning.html')

    @bp.get('/api/learning/exams')
    @require_login
    def exams():
        return jsonify(exams=EXAMS, levels=LEVELS)

    @bp.get('/api/learning/profiles')
    @require_login
    def profiles():
        session.setdefault('learning_csrf', token_urlsafe(32))
        try:
            return jsonify(profiles=list_profiles(connection, session['user_id']),
                           csrf_token=session['learning_csrf'])
        except Exception:
            return jsonify(error='学习档案暂不可用，请稍后重试'), 503

    @bp.put('/api/learning/profiles/<exam>')
    @require_login
    def update_profile(exam):
        token = request.headers.get('X-Learning-CSRF', '')
        if (request.headers.get('Sec-Fetch-Site') == 'cross-site'
                or not token or not compare_digest(token.encode(), session.get('learning_csrf', '').encode())):
            return jsonify(error='页面验证已失效，请刷新后重试'), 403
        spec = next((item for item in EXAMS if item['id'] == exam), None)
        if spec is None:
            return jsonify(error='不支持的考试类型'), 404
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'target_score', 'current_level'}:
            return jsonify(error='请填写目标分数和当前水平'), 400
        level, value = data['current_level'], data['target_score']
        if not isinstance(level, str) or level not in LEVELS or type(value) not in (int, float):
            return jsonify(error='档案字段格式不正确'), 400
        try:
            score = Decimal(str(value))
            step = Decimal(str(spec['score_step']))
            if not score.is_finite() or not step <= score <= spec['max_score'] or score % step:
                raise ValueError()
        except (ValueError, InvalidOperation):
            return jsonify(error='目标分数超出范围或不符合分数步长'), 400
        try:
            if not rate_limit('learning:profile', str(session['user_id']), 60):
                return jsonify(error='保存过于频繁，请稍后再试'), 429
            profile = save_profile(connection, session['user_id'], exam, score, level)
            return jsonify(profile=profile)
        except Exception:
            return jsonify(error='档案保存失败，请稍后重试'), 503

    from .training import register_training
    register_training(bp, connection, require_login, rate_limit, ai_provider, model)
    from .personalization_routes import register_personalization
    register_personalization(bp, connection, require_login, rate_limit, ai_provider, model)
    return bp
