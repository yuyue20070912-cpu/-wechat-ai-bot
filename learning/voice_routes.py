import hashlib
import os
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from flask import current_app, jsonify, request, send_from_directory, session
from . import accounting, config, voice


def register_voice(bp, connection, checked, payload, limit, rate_limit, ai_provider, model):
    @bp.get('/api/learning/voice')
    @checked
    def voice_status():
        voice.settings()  # Invalid budget configuration fails closed.
        with connection() as conn:
            rows=conn.execute('''SELECT session_id FROM learning_voice_sessions
                WHERE user_id=%s ORDER BY created_at DESC LIMIT 10''',(session['user_id'],)).fetchall()
            seconds=conn.execute('''SELECT seconds FROM learning_voice_budget WHERE scope=%s
                AND usage_day=(NOW() AT TIME ZONE 'UTC')::date''',('user:'+str(session['user_id']),)).fetchone()
        return jsonify(transcription_enabled=voice.enabled(), feedback_enabled=config.enabled() and os.getenv('LEARNING_VOICE_FEEDBACK_ENABLED','0')=='1',
            max_bytes=voice.MAX_BYTES,max_seconds=voice.MAX_SECONDS,user_seconds_limit=voice.settings()[0],
            seconds_used=seconds[0] if seconds else 0, reset='UTC 00:00',
            sessions=[str(r[0]) for r in rows],pronunciation_assessed=False)

    @bp.post('/api/learning/voice/sessions')
    @checked
    def voice_start():
        data=payload(['session_id']);sid=str(UUID(data['session_id']));limit('voice-start',20)
        with connection() as conn:
            conn.execute('''INSERT INTO learning_voice_sessions(user_id,session_id) VALUES (%s,%s)
                ON CONFLICT DO NOTHING''',(session['user_id'],sid))
            return jsonify(session=voice.session_data(conn,session['user_id'],sid))

    @bp.get('/api/learning/voice/sessions/<sid>')
    @checked
    def voice_get(sid):
        sid=str(UUID(sid))
        with connection() as conn:return jsonify(session=voice.session_data(conn,session['user_id'],sid))

    @bp.delete('/api/learning/voice/sessions/<sid>')
    @checked
    def voice_delete(sid):
        payload([]);sid=str(UUID(sid));limit('voice-delete')
        with connection() as conn:
            voice.session_data(conn,session['user_id'],sid,True)
            # Session and its transcripts/feedback can be removed; accounting totals remain.
            pending=conn.execute("SELECT 1 FROM learning_attempts WHERE user_id=%s AND question_id LIKE %s AND status='pending'",
                                 (session['user_id'],'voice-%-'+sid+'-%')).fetchone()
            if pending:raise accounting.PracticeError('请等待正在处理的语音请求结束后删除',409)
            conn.execute('''UPDATE learning_attempts SET answer='[deleted]',feedback=
                CASE WHEN status='completed' THEN '{"kind":"voice_deleted"}'::jsonb ELSE NULL END
                WHERE user_id=%s AND question_id LIKE %s''',(session['user_id'],'voice-%-'+sid+'-%'))
            conn.execute('DELETE FROM learning_voice_sessions WHERE user_id=%s AND session_id=%s',(session['user_id'],sid))
        return jsonify(deleted=True)

    @bp.post('/api/learning/voice/sessions/<sid>/answers')
    @checked
    def voice_answer(sid):
        data=payload(['request_id','expected_version','answer','prompt']);limit('voice-answer',60)
        return jsonify(session=voice.answer_session(connection,session['user_id'],str(UUID(sid)),
            str(UUID(data['request_id'])),data['expected_version'],data['answer'],data['prompt']))

    def paid(sid,index,kind,answer,operation,extra=None):
        rid=str(uuid5(NAMESPACE_URL,f'fishyeah:voice:{session["user_id"]}:{sid}:{index}:{kind}'))
        question=dict(id=f'voice-{kind}-{sid}-{index}',exam_type='ielts',skill='speaking')
        selected=voice.MODEL if kind=='audio' else os.getenv('LEARNING_MODEL') or model()
        reserved=False
        try:
            if not rate_limit('learning:ai',str(session['user_id']),12,minutes=1):
                raise accounting.PracticeError('提交过于频繁，请稍后再试',429)
            # Serialize against delete, without holding a DB transaction during the API call.
            # Session deletion checks pending rows; reservation also locks/checks its session.
            def guarded(conn,day):
                voice.session_data(conn,session['user_id'],sid,True)
                if extra:extra(conn,day)
            result=accounting.reserve(connection,session['user_id'],rid,question,answer,selected,
                config.daily_limits(),current_app.config['LEARNING_FREE_MESSAGES'](),guarded)
            if result is not None:return jsonify(feedback=result,replayed=True,request_id=rid)
            reserved=True
            result=operation(selected)
            if not accounting.finish(connection,session['user_id'],rid,result):raise RuntimeError('Reservation expired')
            reserved=False
            return jsonify(feedback=result,replayed=False,request_id=rid)
        except accounting.PracticeError:
            raise
        except Exception:
            current_app.logger.warning('Voice provider or reservation unavailable')
            raise accounting.PracticeError('语音分析暂不可用，请使用文字模式。未完成请求将退还消息额度，不会自动重试。',502 if reserved else 503) from None
        finally:
            if reserved:
                try:accounting.finish(connection,session['user_id'],rid)
                except Exception:current_app.logger.warning('Voice refund deferred to recovery')

    @bp.post('/api/learning/voice/sessions/<sid>/transcribe/<int:index>')
    @checked
    def voice_transcribe(sid,index):
        # Raw body avoids Safari/Flask multipart MIME differences. Token is header-only.
        request.max_content_length=voice.MAX_BYTES
        sid=str(UUID(sid));limit('voice-upload',12)
        if not voice.enabled():raise accounting.PracticeError('付费语音转写默认关闭或尚未验证，请使用文字模式',503)
        with connection() as conn:data=voice.session_data(conn,session['user_id'],sid)
        if index!=data['version'] or data['completed']:raise accounting.PracticeError('该题已结束，请恢复会话',409)
        raw=request.get_data(cache=False)
        audio,seconds=voice.normalize_audio(raw)
        digest=hashlib.sha256(raw).hexdigest()
        return paid(sid,index,'audio',digest,lambda _:voice.transcribe(ai_provider,audio),voice.reserve_audio(session['user_id'],seconds))

    @bp.post('/api/learning/voice/sessions/<sid>/feedback/<int:index>')
    @checked
    def voice_feedback(sid,index):
        payload([]);sid=str(UUID(sid))
        if not config.enabled() or os.getenv('LEARNING_VOICE_FEEDBACK_ENABLED','0')!='1':
            raise accounting.PracticeError('付费口语文字反馈默认关闭',503)
        with connection() as conn:data=voice.session_data(conn,session['user_id'],sid)
        if not 0<=index<len(data['answers']):raise accounting.PracticeError('请先保存该题回答',409)
        turn=data['answers'][index]
        return paid(sid,index,'feedback',turn['answer'],lambda selected:voice.analyze(ai_provider,selected,turn['prompt'],turn['answer']),voice.reserve_audio(session['user_id'],0))

    @bp.get('/api/learning/voice/examples/<accent>')
    @checked
    def voice_example(accent):
        if accent not in ('gb','us'):raise accounting.PracticeError('不支持的示例',404)
        return send_from_directory(Path(__file__).parent/'assets'/'voice_audio',accent+'.mp3',mimetype='audio/mpeg',conditional=True)
