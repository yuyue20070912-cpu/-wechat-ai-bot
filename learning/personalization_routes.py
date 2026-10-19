import json
import os
from datetime import date
from functools import wraps
from secrets import compare_digest
from uuid import UUID, NAMESPACE_URL, uuid5

from flask import current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from . import accounting, config, diagnostic_repository, diagnostics, insights, plans, preferences, reviews
from .catalog import EXAMS


def register_personalization(bp, connection, require_login, rate_limit, ai_provider, model):
    def checked(fn):
        @wraps(fn)
        @require_login
        def wrapped(*args, **kwargs):
            if 'exam' in kwargs and kwargs['exam'] not in {e['id'] for e in EXAMS}:
                return jsonify(error='不支持的考试类型'), 404
            if request.method not in ('GET','HEAD'):
                token = request.headers.get('X-Learning-CSRF', '')
                if (request.headers.get('Sec-Fetch-Site') == 'cross-site' or not token
                        or not compare_digest(token.encode(), session.get('learning_csrf', '').encode())):
                    return jsonify(error='页面验证已失效，请刷新重试'), 403
            try:
                return fn(*args, **kwargs)
            except accounting.PracticeError as error:
                return jsonify(error=str(error)), error.status
            except (ValueError, TypeError, AttributeError):
                return jsonify(error='提交格式错误，请检查选项、日期和请求编号'), 400
            except HTTPException:
                raise
            except Exception:
                current_app.logger.warning('Learning personalization unavailable')
                return jsonify(error='学习服务暂不可用，请稍后重试'), 503
        return wrapped

    def payload(fields):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != set(fields):
            raise accounting.PracticeError('提交字段不完整或包含不支持的字段', 400)
        return data

    def limit(action, maximum=60):
        if not rate_limit('learning:' + action, str(session['user_id']), maximum):
            raise accounting.PracticeError('操作过于频繁，请稍后重试', 429)

    @bp.get('/api/learning/diagnostics/<exam>')
    @checked
    def diagnostic_test(exam):
        with connection() as conn:
            index = diagnostic_repository.next_index(conn,session['user_id'],exam)
        return jsonify(test=diagnostics.public_test(exam,index))

    @bp.get('/api/learning/preferences')
    @checked
    def get_preferences():
        with connection() as conn:
            return jsonify(preferences=preferences.get(conn,session['user_id']))

    @bp.put('/api/learning/preferences')
    @checked
    def save_preferences():
        data=payload(['timezone','expected_version']);limit('preferences')
        return jsonify(preferences=preferences.save(connection,session['user_id'],data['timezone'],data['expected_version']))

    @bp.post('/api/learning/diagnostics/<exam>')
    @checked
    def diagnostic_submit(exam):
        data = payload(['request_id', 'version', 'answers'])
        request_id = str(UUID(data['request_id']))
        limit('diagnostic', 20)
        return jsonify(diagnostic_repository.submit(connection, session['user_id'], request_id,
                       exam, data['version'], data['answers']))

    def ai_id(diagnostic_id):
        # One bounded AI interpretation per diagnostic, also across tabs/retries.
        return str(uuid5(NAMESPACE_URL, f'fishyeah:diagnostic:{session["user_id"]}:{diagnostic_id}'))

    @bp.get('/api/learning/diagnostic-results/<diagnostic_id>')
    @checked
    def diagnostic_result(diagnostic_id):
        diagnostic_id = str(UUID(diagnostic_id))
        report = diagnostic_repository.get(connection, session['user_id'], diagnostic_id)
        accounting.recover(connection)
        attempts = accounting.history(connection, session['user_id'], ai_id(diagnostic_id))
        return jsonify(diagnostic=report, ai_attempt=attempts[0] if attempts else None,
                       ai_enabled=config.enabled() and os.getenv('LEARNING_DIAGNOSTIC_AI_ENABLED', '0') == '1')

    @bp.post('/api/learning/diagnostic-results/<diagnostic_id>/ai')
    @checked
    def diagnostic_ai(diagnostic_id):
        payload([])
        diagnostic_id = str(UUID(diagnostic_id))
        report = diagnostic_repository.get(connection, session['user_id'], diagnostic_id)
        if os.getenv('LEARNING_DIAGNOSTIC_AI_ENABLED', '0') != '1':
            return jsonify(error='付费 AI 诊断解读默认关闭，仍可使用本地诊断和学习计划'), 503
        facts = report['result']
        question = dict(id='diagnostic-' + diagnostic_id, exam_type=report['exam_type'], skill='reading',
            diagnostic=True, prompt='解读这份文字诊断，结合目标和薄弱项给出有限、具体的学习建议。不要换算官方成绩、CEFR 或推断语音能力。',
            material=json.dumps(facts, ensure_ascii=False), reference='只依据提供的小样本诊断，不改写本地判分。')
        answer = json.dumps({k: facts[k] for k in ('accuracy', 'level', 'weak_skills', 'profile')}, ensure_ascii=False)
        from .feedback import execute
        return execute(connection, rate_limit, ai_provider, model, question, answer, ai_id(diagnostic_id))

    @bp.post('/api/learning/plans/<exam>')
    @checked
    def daily_plan(exam):
        payload([]); limit('plan')
        return jsonify(plan=plans.today(connection, session['user_id'], exam))

    @bp.get('/api/learning/plans/<exam>')
    @checked
    def existing_plan(exam):
        return jsonify(plan=plans.get_today(connection, session['user_id'], exam))

    @bp.put('/api/learning/tasks/<exam>/<day>/<task_id>')
    @checked
    def task_complete(exam, day, task_id):
        data = payload(['completed', 'expected_version'])
        if type(data['completed']) is not bool or type(data['expected_version']) is not int or not 0 <= data['expected_version'] <= 1000000:
            raise ValueError()
        day = date.fromisoformat(day)
        limit('task', 120)
        return jsonify(plan=plans.complete(connection, session['user_id'], exam, day, task_id,
                       data['completed'], data['expected_version']))

    @bp.get('/api/learning/overview/<exam>')
    @checked
    def overview(exam):
        return jsonify(overview=insights.overview(connection, session['user_id'], exam))

    @bp.get('/api/learning/reviews/<exam>')
    @checked
    def review_list(exam):
        offset = int(request.args.get('offset', '0'))
        if not 0 <= offset <= 100000:
            raise ValueError()
        return jsonify(reviews.list_items(connection, session['user_id'], exam, offset))

    @bp.post('/api/learning/reviews/<exam>/sync')
    @checked
    def review_sync(exam):
        payload([]); limit('review-sync', 12)
        return jsonify(imported=reviews.sync(connection, session['user_id'], exam))

    @bp.post('/api/learning/reviews/save')
    @checked
    def review_save():
        data = payload(['request_id'])
        request_id = str(UUID(data['request_id']))
        limit('review-save')
        # Ownership and completed status checked before any snapshot is imported.
        attempts = accounting.history(connection, session['user_id'], request_id)
        if not attempts or attempts[0]['status'] != 'completed' or attempts[0]['purpose']!='practice':
            raise accounting.PracticeError('可收藏的练习记录不存在', 404)
        with connection() as conn:
            inserted = reviews.capture_attempt(conn, session['user_id'], request_id, manual=True)
        return jsonify(saved=True, created=inserted)

    @bp.post('/api/learning/review-responses')
    @checked
    def review_respond():
        data = payload(['request_id', 'item_key', 'expected_version', 'answer', 'self_rating'])
        request_id = str(UUID(data['request_id']))
        if (not isinstance(data['item_key'], str) or len(data['item_key']) > 160
                or type(data['expected_version']) is not int or not 0 <= data['expected_version'] <= 1000000
                or not isinstance(data['answer'], str) or not 1 <= len(data['answer'].strip()) <= 4000
                or (data['self_rating'] is not None and (not isinstance(data['self_rating'], str)
                    or data['self_rating'] not in ('again', 'hard', 'good', 'easy')))):
            raise ValueError()
        limit('review-response', 120)
        return jsonify(reviews.respond(connection, session['user_id'], data['item_key'], request_id,
                       data['expected_version'], data['answer'].strip(), data['self_rating']))

    from .mock_routes import register_mocks
    register_mocks(bp,connection,checked,payload,limit,rate_limit,ai_provider,model)
