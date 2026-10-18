"""One paid-feedback path for practice and optional diagnostic interpretation."""
import os
from flask import current_app, jsonify, session
from . import accounting, config, services


def execute(connection, rate_limit, ai_provider, model, question, answer, request_id):
    def free_limit():
        return current_app.config['LEARNING_FREE_MESSAGES']()

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
        if question.get('diagnostic'):
            feedback['kind'] = 'diagnostic_interpretation'
            feedback['score_label'] = 'AI 解读参考指标，不是诊断正确率或官方成绩'
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
