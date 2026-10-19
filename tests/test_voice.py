import io
import json
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from uuid import uuid4

try:
    import httpx2 as httpx  # OpenAI 3.x transport dependency.
except ImportError:
    import httpx  # Older supported SDK installations.
import pytest
from openai import OpenAI

import bot
from learning import accounting, voice
from test_training import client, csrf, counts, submit


def start(c):
    return c.post('/api/learning/voice/sessions',json={'session_id':str(uuid4())},headers=csrf(c)).json['session']


def answer(c,s,text='I enjoy the park because it is peaceful.',rid=None):
    return c.post(f'/api/learning/voice/sessions/{s["session_id"]}/answers',headers=csrf(c),
        json=dict(request_id=rid or str(uuid4()),expected_version=s['version'],answer=text,prompt=s['prompt']))


def wav(seconds=1):
    f=io.BytesIO()
    with wave.open(f,'wb') as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*int(16000*seconds))
    return f.getvalue()


def upload(c,s,data=None,index=None):
    return c.post(f'/api/learning/voice/sessions/{s["session_id"]}/transcribe/{s["version"] if index is None else index}',
        data=wav() if data is None else data,content_type='application/octet-stream',headers=csrf(c))


@pytest.fixture
def voice_ai(ai,user,monkeypatch):
    with bot.db_connection() as conn:conn.execute('TRUNCATE learning_voice_budget')
    monkeypatch.setenv('LEARNING_VOICE_ENABLED','1')
    monkeypatch.setenv('LEARNING_VOICE_VERIFIED_MODEL',voice.MODEL)
    monkeypatch.setenv('LEARNING_VOICE_FEEDBACK_ENABLED','1')
    calls=[]
    def transcribe(**kwargs):calls.append(kwargs);return NS(text='I enjoy a peaceful park near my home.')
    ai.client.audio=NS(transcriptions=NS(create=transcribe))
    values={k:'A useful learning suggestion.' for k in voice.FIELDS}
    values['follow_up']='How has this place changed over time?'
    ai.client.responses.create=lambda **kwargs:NS(status='completed',output_text=json.dumps(values))
    return NS(client=ai.client,calls=calls,values=values)


@pytest.mark.parametrize('method,path',[
    ('get','voice'),('post','voice/sessions'),('get','voice/examples/gb'),
    ('get','voice/sessions/'+str(uuid4())),('delete','voice/sessions/'+str(uuid4())),
    ('post','voice/sessions/'+str(uuid4())+'/answers'),
    ('post','voice/sessions/'+str(uuid4())+'/transcribe/0'),
    ('post','voice/sessions/'+str(uuid4())+'/feedback/0')])
def test_voice_auth(method,path):
    assert getattr(bot.app.test_client(),method)('/api/learning/'+path).status_code==401


def test_default_off_and_unverified_fail_closed(user,monkeypatch):
    monkeypatch.delenv('LEARNING_VOICE_ENABLED',raising=False)
    monkeypatch.delenv('LEARNING_VOICE_FEEDBACK_ENABLED',raising=False)
    c=client(user);s=start(c)
    data=c.get('/api/learning/voice').json
    assert not data['transcription_enabled'] and not data['feedback_enabled'] and not data['pronunciation_assessed']
    assert upload(c,s).status_code==503
    monkeypatch.setenv('LEARNING_VOICE_ENABLED','1')
    monkeypatch.setenv('LEARNING_VOICE_VERIFIED_MODEL','wrong-model')
    assert upload(c,s).status_code==503
    assert counts(user)==(0,0)
    assert answer(c,s).status_code==200


def test_conversation_replay_cas_parts_and_account_isolation(user):
    c=client(user);s=start(c);sid=s['session_id'];rid=str(uuid4())
    assert c.post(f'/api/learning/voice/sessions/{sid}/answers',json={}).status_code==403
    first=answer(c,s,rid=rid).json['session']
    assert answer(c,s,rid=rid).json['session']['version']==1
    assert answer(c,s,rid=rid,text='Different answer.').status_code==409
    assert answer(c,s).status_code==409
    parts=[1]
    while not first['completed']:
        parts.append(first['part']);first=answer(c,first).json['session']
    assert parts==[1,1,1,2,3,3,3]
    assert c.get(f'/api/learning/voice/sessions/{sid}').json['session']['completed']
    with bot.db_connection() as conn:
        other=conn.execute("INSERT INTO users(phone,password_hash) VALUES('13900000000','test') RETURNING id").fetchone()[0]
    stranger=client(other)
    assert stranger.get(f'/api/learning/voice/sessions/{sid}').status_code==404
    assert stranger.delete(f'/api/learning/voice/sessions/{sid}',json={},headers=csrf(stranger)).status_code==404
    assert counts(user)==(0,0)


def test_audio_replay_seconds_feedback_and_restore(user,voice_ai):
    c=client(user);s=start(c);sid=s['session_id']
    result=upload(c,s)
    assert result.status_code==200 and not result.json['feedback']['pronunciation_assessed']
    assert upload(c,s).json['replayed'] and len(voice_ai.calls)==1
    assert upload(c,s,wav(2)).status_code==409
    assert counts(user)==(1,1)
    args=voice_ai.calls[0]
    assert args['model']==voice.MODEL and args['response_format']=='json' and args['language']=='en'
    assert args['file'][0]=='answer.wav' and args['file'][2]=='audio/wav'
    assert c.get(f'/api/learning/voice/sessions/{sid}').json['session']['transcript']==result.json['feedback']['transcript']
    saved=answer(c,s).json['session']
    path=f'/api/learning/voice/sessions/{sid}/feedback/0'
    f=c.post(path,json={},headers=csrf(c))
    assert f.status_code==200 and 'score' not in f.json['feedback'] and f.json['feedback']['text_only']
    assert c.post(path,json={},headers=csrf(c)).json['replayed']
    recovered=c.get(f'/api/learning/voice/sessions/{sid}').json['session']
    assert recovered['answers'][0]['feedback']==f.json['feedback']
    assert recovered['prompt']==voice_ai.values['follow_up']
    assert answer(c,saved).status_code==409  # Never attach an old answer to a new AI question.
    assert answer(c,recovered).json['session']['answers'][1]['prompt']==voice_ai.values['follow_up']
    assert counts(user)==(2,2)
    with bot.db_connection() as conn:
        assert conn.execute('SELECT sum(seconds) FROM learning_voice_budget').fetchone()[0]==2
    history=accounting.history(bot.db_connection,user)
    assert all(h['purpose']=='voice' for h in history)
    # Voice/transcription records never become score trends or ordinary error-book entries.
    assert c.get('/api/learning/overview/ielts').status_code==200
    assert c.post('/api/learning/reviews/ielts/sync',json={},headers=csrf(c)).json['imported']==0
    assert c.delete(f'/api/learning/voice/sessions/{sid}',json={},headers=csrf(c)).json['deleted']
    assert c.get(f'/api/learning/voice/sessions/{sid}').status_code==404
    assert counts(user)==(2,2)
    with bot.db_connection() as conn:
        rows=conn.execute('SELECT answer,feedback FROM learning_attempts WHERE user_id=%s',(user,)).fetchall()
        assert all(a=='[deleted]' and f=={'kind':'voice_deleted'} for a,f in rows)


@pytest.mark.parametrize('seconds',[0.1,120.1,121,200])
def test_audio_duration_limit(seconds):
    with pytest.raises(accounting.PracticeError):voice.normalize_audio(wav(seconds))


@pytest.mark.parametrize('fmt,codec',[('webm','libopus'),('mp4','aac'),('ogg','libopus')])
def test_safari_and_chromium_audio_containers(tmp_path,fmt,codec):
    source=tmp_path/'in.wav';source.write_bytes(wav(1.1));dest=tmp_path/('out.'+fmt)
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',str(source),'-c:a',codec,str(dest)],check=True)
    audio,seconds=voice.normalize_audio(dest.read_bytes())
    assert seconds==2 and audio.startswith(b'RIFF')


def test_malformed_oversized_empty_audio_never_charged(user,voice_ai):
    c=client(user);s=start(c)
    for data,status in [(b'',413),(b'not audio',400),(b'X'*(voice.MAX_BYTES+1),413),(wav(.1),400)]:
        assert upload(c,s,data).status_code==status
    assert not voice_ai.calls and counts(user)==(0,0)


def test_playlist_cannot_reference_other_local_files(tmp_path):
    sample=tmp_path/'private.wav';sample.write_bytes(wav(1))
    with pytest.raises(accounting.PracticeError):
        voice.normalize_audio(('#EXTM3U\n#EXTINF:1,private\n'+str(sample)+'\n').encode())


def test_failed_audio_refund_once_but_budget_retained(user,voice_ai):
    c=client(user);s=start(c)
    def fail(**kwargs):raise RuntimeError('SECRET provider detail never shown')
    voice_ai.client.audio.transcriptions.create=fail
    result=upload(c,s)
    assert result.status_code==502 and b'SECRET' not in result.data
    assert counts(user)==(0,1)
    assert upload(c,s).status_code==409
    accounting.recover(bot.db_connection)
    assert counts(user)==(0,1)
    with bot.db_connection() as conn:assert conn.execute('SELECT sum(seconds) FROM learning_voice_budget').fetchone()[0]==2


@pytest.mark.parametrize('env,value',[
    ('LEARNING_VOICE_USER_SECONDS','1'),('LEARNING_VOICE_GLOBAL_SECONDS','1'),
    ('LEARNING_VOICE_CONCURRENCY','0'),('LEARNING_VOICE_GLOBAL_SECONDS','bad')])
def test_voice_budgets_rollback(user,voice_ai,monkeypatch,env,value):
    monkeypatch.setenv(env,value);c=client(user);s=start(c)
    assert upload(c,s,wav(2)).status_code in (429,503)
    assert not voice_ai.calls and counts(user)==(0,0)


@pytest.mark.parametrize('guard',[('LEARNING_VOICE_CONCURRENCY','1'),('LEARNING_VOICE_GLOBAL_SECONDS','1')])
def test_concurrent_upload_dedupe_and_global_slot(user,voice_ai,monkeypatch,guard):
    monkeypatch.setenv(*guard)
    with bot.db_connection() as conn:
        other=conn.execute("INSERT INTO users(phone,password_hash) VALUES('13900000000','test') RETURNING id").fetchone()[0]
    c=client(user);s=start(c);c2=client(other);s2=start(c2)
    entered,release=Event(),Event();original=voice_ai.client.audio.transcriptions.create
    def wait(**kwargs):entered.set();assert release.wait(10);return original(**kwargs)
    voice_ai.client.audio.transcriptions.create=wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(upload,c,s)
        assert entered.wait(5)
        try:
            assert upload(client(user),s).status_code==409
            assert submit(client(user)).status_code==409
            assert upload(c2,s2).status_code==429
            assert c.delete(f'/api/learning/voice/sessions/{s["session_id"]}',json={},headers=csrf(c)).status_code==409
            assert counts(other)==(0,0)
        finally:release.set()
        assert future.result().status_code==200
    assert counts(user)==(1,1) and len(voice_ai.calls)==1


def test_abandoned_voice_refund_keeps_seconds_and_call_budget(user,voice_ai):
    c=client(user);s=start(c);rid=str(uuid4())
    q=dict(id='voice-audio-'+s['session_id']+'-0',exam_type='ielts',skill='speaking')
    accounting.reserve(bot.db_connection,user,rid,q,'hash',voice.MODEL,(10,200),5,voice.reserve_audio(user,2))
    with bot.db_connection() as conn:
        conn.execute("UPDATE learning_attempts SET created_at=NOW()-INTERVAL '6 minutes' WHERE user_id=%s",(user,))
    assert accounting.recover(bot.db_connection)==1 and accounting.recover(bot.db_connection)==0
    assert not accounting.finish(bot.db_connection,user,rid,dict(transcript='too late'))
    assert counts(user)==(0,1)
    with bot.db_connection() as conn:
        assert conn.execute('SELECT sum(seconds) FROM learning_voice_budget').fetchone()[0]==4


def test_invalid_feedback_refunds_and_never_exposes_provider_output(user,voice_ai):
    c=client(user);s=start(c);answer(c,s)
    voice_ai.client.responses.create=lambda **kwargs:NS(status='completed',output_text='SECRET invalid output')
    response=c.post(f'/api/learning/voice/sessions/{s["session_id"]}/feedback/0',json={},headers=csrf(c))
    assert response.status_code==502 and b'SECRET' not in response.data and counts(user)==(0,1)


def test_actual_sdk_multipart_shape_without_paid_call():
    requests=[]
    def respond(req):
        requests.append(req);return httpx.Response(200,json={'text':'A language takes practice in this place.'})
    sdk=OpenAI(api_key='test-not-real',http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    result=voice.transcribe(lambda:sdk,wav())
    req=requests[0]
    assert req.url.path=='/v1/audio/transcriptions'
    assert req.headers['content-type'].startswith('multipart/form-data;')
    assert voice.MODEL.encode() in req.content and b'answer.wav' in req.content and b'RIFF' in req.content
    assert result['pronunciation_assessed'] is False


@pytest.mark.parametrize('accent',['gb','us'])
def test_audio_example_auth_range_and_no_budget(user,accent):
    c=client(user);r=c.get('/api/learning/voice/examples/'+accent,headers={'Range':'bytes=0-127'})
    assert r.status_code==206 and len(r.data)==128 and r.mimetype=='audio/mpeg'
    assert r.headers['Cache-Control']=='no-store' and counts(user)==(0,0)


def test_feedback_schema_rejects_score_or_invalid_fields(voice_ai):
    voice_ai.client.responses.create=lambda **kwargs:NS(status='completed',output_text=json.dumps(dict(voice_ai.values,pronunciation_score=9)))
    with pytest.raises(ValueError):voice.analyze(lambda:voice_ai.client,'test','prompt','answer')
