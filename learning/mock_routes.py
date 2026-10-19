import hashlib
import hmac
import os
from pathlib import Path
from uuid import UUID

from flask import current_app,jsonify,request,send_from_directory,session

from . import accounting,config,mock_repository as repo
from .mock_bank import BANK,NOTICE,public_template
from . import ielts_audio


def register_mocks(bp,connection,checked,payload,limit,rate_limit,ai_provider,model):
    @bp.get('/api/learning/mock-catalog/<exam>')
    @checked
    def mock_catalog(exam):
        return jsonify(notice=NOTICE,templates=[dict(id=t['id'],section=t['section'],title=t['title'],form=t['form'],duration_seconds=t['duration_seconds'],
            scope=t['scope'],question_count=len(t['questions']),has_audio=public_template(t)['has_audio'],audio_status=(public_template(t).get('audio') or {}).get('status')) for t in BANK.values() if t['exam_type']==exam])

    @bp.get('/api/learning/mocks')
    @checked
    def mock_history():
        exam=request.args.get('exam','ielts');offset=int(request.args.get('offset','0'))
        if exam not in ('ielts','cet4','cet6') or not 0<=offset<=100000:raise ValueError()
        data=repo.history(connection,session['user_id'],exam,offset)
        key=current_app.secret_key
        scope=hmac.new(key.encode() if isinstance(key,str) else key,str(session['user_id']).encode(),hashlib.sha256).hexdigest()
        return jsonify(**data,storage_scope=scope,ai_enabled=config.enabled() and os.getenv('LEARNING_MOCK_AI_ENABLED','0')=='1')

    @bp.post('/api/learning/mocks')
    @checked
    def mock_start():
        data=payload(['session_id','template_id']);limit('mock-start',20)
        sid=str(UUID(data['session_id']))
        if not isinstance(data['template_id'],str):raise ValueError()
        return jsonify(session=repo.start(connection,session['user_id'],sid,data['template_id']))

    @bp.get('/api/learning/mocks/<session_id>')
    @checked
    def mock_get(session_id):
        sid=str(UUID(session_id));data=repo.get(connection,session['user_id'],sid)
        return jsonify(session=data,comparisons=repo.comparisons(connection,session['user_id'],data) if data['status']!='active' else [])

    def save(session_id,kind):
        request.max_content_length=20*1024
        data=payload(['request_id','expected_version','answers'])
        if not rate_limit('learning:mock-write',str(session['user_id']),120,minutes=1):
            raise accounting.PracticeError('答案保存过于频繁，请稍后同步',429)
        return jsonify(repo.write(connection,session['user_id'],str(UUID(session_id)),str(UUID(data['request_id'])),
                                 data['expected_version'],data['answers'],kind))

    @bp.put('/api/learning/mocks/<session_id>/answers')
    @checked
    def mock_save_answers(session_id):return save(session_id,'save')

    @bp.post('/api/learning/mocks/<session_id>/submit')
    @checked
    def mock_submit(session_id):return save(session_id,'submit')

    @bp.get('/learning/audio/ielts/<filename>')
    def ielts_pilot_audio(filename):
        # Public original media only: no session, database, quota or synthesis call.
        asset=next((entry for entry in ielts_audio.assets().values() if entry['filename']==filename),None)
        if asset is None:
            return jsonify(error='固定 MP3 尚未接入或不可用'),404
        response=send_from_directory(ielts_audio.ASSET_ROOT,filename,mimetype='audio/mpeg',conditional=True,max_age=31536000)
        if response.status_code in (200,206,304):
            response.headers['Cache-Control']='public, max-age=31536000, immutable'
        return response

    @bp.get('/api/learning/mocks/<session_id>/audio')
    @checked
    def mock_audio(session_id):
        sid=str(UUID(session_id));data=repo.get(connection,session['user_id'],sid)
        if not data['template']['has_audio']:raise accounting.PracticeError('本分项没有音频',404)
        with connection() as conn:row=repo.fetch(conn,session['user_id'],sid)
        filename=row['snapshot'].get('audio_file')
        if not filename:raise accounting.PracticeError('本题固定 MP3 尚未接入，暂无原有音频可回退',404)
        return send_from_directory(Path(__file__).parent/'assets'/'mock_audio',filename,mimetype='audio/mpeg',conditional=True)

    @bp.post('/api/learning/mocks/<session_id>/ai/<question_id>')
    @checked
    def mock_subjective_ai(session_id,question_id):
        payload([]);sid=str(UUID(session_id));data=repo.get(connection,session['user_id'],sid)
        if data['status']=='active':raise accounting.PracticeError('请先提交模考，再选择 AI 参考评分',409)
        with connection() as conn:row=repo.fetch(conn,session['user_id'],sid)
        q=next((q for q in row['snapshot']['questions'] if q['id']==question_id and q['kind']=='subjective'),None)
        if q is None:raise accounting.PracticeError('主观题不存在',404)
        answer=row['answers'].get(question_id,'').strip()
        if not answer:raise accounting.PracticeError('未作答的主观题不能申请 AI 评分',400)
        if os.getenv('LEARNING_MOCK_AI_ENABLED','0')!='1':return jsonify(error='模考付费 AI 参考评分默认关闭'),503
        question=dict(id='mock-'+sid+'-'+question_id,exam_type=data['exam_type'],skill=data['section'],mock=True,
            prompt=q['prompt'],material=row['snapshot']['material'],reference=q['reference'])
        from .feedback import execute
        return execute(connection,rate_limit,ai_provider,model,question,answer,repo.ai_id(session['user_id'],sid,question_id))
