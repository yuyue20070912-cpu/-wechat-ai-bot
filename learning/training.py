import os
from secrets import compare_digest
from uuid import UUID

from flask import current_app, jsonify, request, session

from . import accounting, config, services
from .questions import QUESTIONS, SKILLS, public_question


def register_training(bp, connection, require_login, rate_limit, ai_provider, model):
    def valid_csrf():
        token = request.headers.get('X-Learning-CSRF', '')
        return (request.headers.get('Sec-Fetch-Site') != 'cross-site' and token
                and compare_digest(token.encode(), session.get('learning_csrf', '').encode()))

    def free_limit():
        # Same allowance setting as the host app; injected at registration below.
        return current_app.config['LEARNING_FREE_MESSAGES']()

    @bp.get('/api/learning/questions')
    @require_login
    def questions():
        exam, skill = request.args.get('exam'), request.args.get('skill')
        items = [public_question(q) for q in QUESTIONS.values()
                 if q['exam_type'] == exam and q['skill'] == skill]
        if not items:
            return jsonify(error='不支持的考试或训练类型'), 400
        return jsonify(questions=items)

    @bp.get('/api/learning/practice')
    @require_login
    def practice():
        try:
            limits = config.daily_limits()
            accounting.recover(connection)
            return jsonify(enabled=config.enabled(), skills=SKILLS,
                           usage=accounting.usage(connection, session['user_id'], limits[0], free_limit()),
                           attempts=accounting.history(connection, session['user_id']))
        except Exception:
            return jsonify(error='练习服务暂不可用，请稍后重试'), 503

    @bp.get('/api/learning/attempts/<request_id>')
    @require_login
    def attempt(request_id):
        try:
            request_id = str(UUID(request_id))
        except (ValueError, TypeError):
            return jsonify(error='请求编号格式错误'), 400
        try:
            accounting.recover(connection)
            items = accounting.history(connection, session['user_id'], request_id)
            if not items:
                return jsonify(error='练习记录不存在'), 404
            return jsonify(attempt=items[0])
        except Exception:
            return jsonify(error='练习记录暂不可用'), 503

    @bp.post('/api/learning/attempts')
    @require_login
    def submit():
        if not valid_csrf():
            return jsonify(error='页面验证已失效，请刷新后重试'), 403
        # Only this route allows a little extra JSON overhead for 4000 Unicode chars.
        request.max_content_length = 20 * 1024
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'request_id', 'question_id', 'answer'}:
            return jsonify(error='请提交题目、答案和请求编号'), 400
        answer, question_id = data['answer'], data['question_id']
        if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= config.MAX_ANSWER:
            return jsonify(error='请输入 1–4000 字的答案'), 400
        question = QUESTIONS.get(question_id) if isinstance(question_id, str) else None
        if not question:
            return jsonify(error='题目不存在'), 404
        try:
            request_id = str(UUID(data['request_id']))
        except (ValueError, TypeError, AttributeError):
            return jsonify(error='请求编号格式错误'), 400
        if not config.enabled():
            return jsonify(error='AI 练习暂未开放，仍可浏览文字题目'), 503
        reserved = False
        try:
            limits = config.daily_limits()
            if not rate_limit('learning:ai', str(session['user_id']), 12, minutes=1):
                return jsonify(error='提交过于频繁，请稍后重试'), 429
            selected_model = os.getenv('LEARNING_MODEL') or model()
            feedback = accounting.reserve(connection, session['user_id'], request_id,
                question, answer.strip(), selected_model, limits, free_limit())
            if feedback is not None:
                return jsonify(request_id=request_id, feedback=feedback, replayed=True)
            reserved = True
            feedback, tokens = services.analyze(ai_provider, selected_model, question, answer.strip())
            feedback.update(reference_answer=question['reference'],
                            score_label='本题练习参考分，非官方成绩', text_only=True)
            if not accounting.finish(connection, session['user_id'], request_id, feedback, tokens):
                raise RuntimeError('Reservation expired')
            reserved = False
            return jsonify(request_id=request_id, feedback=feedback, replayed=False)
        except accounting.PracticeError as error:
            return jsonify(error=str(error)), error.status
        except Exception:
            # Avoid logging learner answers, provider responses or credentials.
            current_app.logger.warning('Learning feedback unavailable')
            return jsonify(error='AI 反馈暂不可用。请查看练习记录确认状态；未完成的分析将退还消息额度。'), 502 if reserved else 503
        finally:
            if reserved:
                try:
                    accounting.finish(connection, session['user_id'], request_id)
                except Exception:
                    current_app.logger.warning('Learning refund deferred to recovery')
