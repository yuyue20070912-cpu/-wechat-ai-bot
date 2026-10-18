import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from threading import Event
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

import bot
from learning.diagnostics import GROUPS
from learning.mock_bank import BANK,SECTIONS


def client(user):
    c=bot.app.test_client()
    with c.session_transaction() as s:s['user_id']=user
    return c


def headers(c):return {'X-Learning-CSRF':c.get('/api/learning/profiles').json['csrf_token']}


def call(c,path,body=None,method='POST'):
    return getattr(c,method.lower())('/api/learning/'+path,json={} if body is None else body,headers=headers(c))


def start(c,template='ielts-reading-a-short-v1',key=None):
    return call(c,'mocks',dict(session_id=str(key or uuid4()),template_id=template))


def write(c,s,answers,kind='answers',key=None,version=None):
    return call(c,f'mocks/{s["id"]}/{kind}',dict(request_id=str(key or uuid4()),expected_version=s['version'] if version is None else version,answers=answers),
                'PUT' if kind=='answers' else 'POST')


def settings(c,tz,version=0):return call(c,'preferences',dict(timezone=tz,expected_version=version),'PUT')


def expire(s):
    with bot.db_connection() as conn:
        conn.execute("UPDATE learning_mock_sessions SET started_at=NOW()-INTERVAL '2 hours',deadline=NOW()-INTERVAL '1 second' WHERE session_id=%s",(s['id'],))


def screening(c,key=None,version=None):
    test=c.get('/api/learning/diagnostics/ielts').json['test'];version=version or test['version']
    return call(c,'diagnostics/ielts',dict(request_id=str(key or uuid4()),version=version,answers={q['id']:q['correct_index'] for q in GROUPS[version]['ielts']}))


@pytest.mark.parametrize('method,path',[('get','mock-catalog/ielts'),('get','mocks'),('post','mocks'),
    ('get','mocks/'+str(uuid4())),('put','mocks/'+str(uuid4())+'/answers'),('post','mocks/'+str(uuid4())+'/submit'),
    ('get','mocks/'+str(uuid4())+'/audio'),('post','mocks/'+str(uuid4())+'/ai/s1'),('get','preferences'),('put','preferences')])
def test_mock_login_required(method,path):
    assert getattr(bot.app.test_client(),method)('/api/learning/'+path).status_code==401


@pytest.mark.parametrize('template_id',list(BANK))
def test_every_mock_section_has_private_snapshot_and_graded_report(user,ai,template_id):
    c=client(user);original=BANK[template_id];response=start(c,template_id)
    assert response.status_code==200
    s=response.json['session']
    assert s['template']['scope']=='short_section' and s['remaining_seconds']>0
    assert 'transcript' not in s['template'] and 'audio_file' not in s['template']
    for q in s['template']['questions']:
        assert 'correct_index' not in q and 'reference' not in q and 'explanation' not in q
    answers={q['id']:q['correct_index'] if q['kind']=='objective' else 'I support the idea because it improves access.' for q in original['questions']}
    saved=write(c,s,answers)
    assert saved.status_code==200
    s=saved.json['session']
    assert s['version']==1
    restored=c.get('/api/learning/mocks/'+s['id']).json['session']
    assert restored['answers']==answers and restored['deadline']==s['deadline']
    done=write(c,restored,{},'submit')
    assert done.status_code==200 and done.json['session']['status']=='submitted'
    report=done.json['session']['report']
    assert report['objective_accuracy']==(100 if original['questions'][0]['kind']=='objective' else None)
    assert all('reference' in q and 'explanation' in q for q in report['details'])
    assert not ai.calls


def test_mock_catalog_scope_and_audio_privacy(user):
    c=client(user)
    for exam,sections in SECTIONS.items():
        templates=c.get('/api/learning/mock-catalog/'+exam).json['templates']
        assert len(templates)==len(sections)*2
        assert {t['section'] for t in templates}==set(sections)
    s=start(c,'ielts-listening-a-short-v1').json['session'];path='/api/learning/mocks/'+s['id']+'/audio'
    response=c.get(path,headers={'Range':'bytes=0-255'})
    assert response.status_code==206 and response.mimetype=='audio/mpeg' and len(response.data)==256
    assert c.head(path).status_code==200
    assert bot.app.test_client().get(path).status_code==401
    assert c.get('/api/learning/mocks/'+s['id']).json['session']['template'].get('transcript') is None


def test_concurrent_start_one_timer_and_no_second_active_exam(user):
    key=uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda _:start(client(user),key=key),range(4)))
    assert all(r.status_code in (200,409) for r in responses)
    good=next(r.json['session'] for r in responses if r.status_code==200)
    c=client(user);same=start(c,key=key).json['session']
    assert same['deadline']==good['deadline']
    assert start(c,'ielts-writing-a-short-v1').status_code==409
    assert start(c,'ielts-writing-a-short-v1',key).status_code==409
    with bot.db_connection() as conn:assert conn.execute('SELECT COUNT(*) FROM learning_mock_sessions').fetchone()[0]==1


def test_save_receipts_replay_and_concurrent_version_conflict(user):
    c=client(user);s=start(c).json['session'];key=uuid4()
    first=write(c,s,{'r1':1},key=key)
    assert first.status_code==200
    second=write(c,s,{'r1':1},key=key)
    assert second.status_code==200 and second.json['replayed']
    assert write(c,s,{'r1':0},key=key).status_code==409
    newer=first.json['session']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda answer:write(client(user),newer,{'r2':answer}),[0,1]))
    assert sorted(r.status_code for r in responses)==[200,409]
    restored=client(user).get('/api/learning/mocks/'+s['id']).json['session']
    assert restored['version']==2
    replay=write(c,s,{'r1':1},key=key).json
    assert replay['saved_version']==1 and replay['session']['version']==2


def test_one_write_uuid_cannot_be_reused_for_other_session(user):
    c=client(user);a=start(c).json['session'];key=uuid4()
    a=write(c,a,{'r1':1},key=key).json['session'];write(c,a,{},'submit')
    b=start(c).json['session']
    assert write(c,b,{'r1':1},key=key).status_code==409
    assert c.get('/api/learning/mocks/'+b['id']).json['session']['answers']=={}


def test_expiry_uses_only_saved_answers_and_is_idempotent(user):
    c=client(user);s=start(c).json['session']
    s=write(c,s,{'r1':1}).json['session'];expire(s)
    late=write(c,s,{'r2':0})
    assert late.status_code==409
    result=c.get('/api/learning/mocks/'+s['id']).json['session']
    assert result['status']=='expired' and result['answers']=={'r1':1}
    assert result['report']['objective_correct']==1 and result['report']['objective_total']==4
    assert write(c,result,{'r2':0},'submit').json['session']['answers']=={'r1':1}
    assert c.get('/api/learning/mocks/'+s['id']).json['session']['version']==result['version']
    assert start(c).status_code==200  # an expired session no longer blocks the next mock


def test_deadline_is_immutable_across_timezone_and_relogin(user):
    c=client(user);s=start(c).json['session']
    assert settings(c,'America/New_York').status_code==200
    other=client(user);restored=other.get('/api/learning/mocks/'+s['id']).json['session']
    assert restored['deadline']==s['deadline'] and restored['started_at']==s['started_at']
    assert restored['remaining_seconds']<=s['remaining_seconds']


def test_mock_snapshot_and_report_survive_bank_changes(user,monkeypatch):
    c=client(user);s=start(c).json['session'];template=copy.deepcopy(BANK[s['template_id']])
    template['questions'][0]['correct_index']=0
    monkeypatch.setitem(BANK,s['template_id'],template)
    report=write(c,s,{'r1':1},'submit').json['session']['report']
    assert report['details'][0]['correct'] is True


def test_subjective_ai_default_closed_and_shared_limits(user,ai,monkeypatch):
    c=client(user);s=start(c,'ielts-writing-a-short-v1').json['session'];path=f'mocks/{s["id"]}/ai/s1'
    assert call(c,path).status_code==409
    s=write(c,s,{'s1':'Public libraries should receive funding because they improve access.'},'submit').json['session']
    assert call(c,path).status_code==503 and not ai.calls
    monkeypatch.setenv('LEARNING_MOCK_AI_ENABLED','1');monkeypatch.setenv('LEARNING_USER_DAILY_CALLS','1')
    one=call(c,path);two=call(c,path)
    assert one.status_code==two.status_code==200 and two.json['replayed'] and len(ai.calls)==1
    assert one.json['feedback']['kind']=='mock_subjective'
    data=c.get('/api/learning/mocks/'+s['id']).json['session']
    assert data['report']['objective_accuracy'] is None and data['ai_results'][0]['feedback']['score']==72
    assert c.get('/api/learning/overview/ielts').json['overview']['practice_scores']==[]
    response=call(c,'attempts',dict(request_id=str(uuid4()),question_id='ielts-writing-1-v1',answer='Answer'))
    assert response.status_code==429
    assert call(c,'reviews/ielts/sync').json['imported']==0


def test_ai_concurrent_request_and_failure_refund(user,ai,monkeypatch):
    monkeypatch.setenv('LEARNING_MOCK_AI_ENABLED','1')
    c=client(user);s=start(c,'cet4-translation-a-short-v1').json['session']
    s=write(c,s,{'s1':'To stay healthy, I walk to school.'},'submit').json['session'];path=f'mocks/{s["id"]}/ai/s1'
    started,release=Event(),Event()
    def fail(**kwargs):started.set();assert release.wait(5);raise TimeoutError()
    ai.client.responses.create=fail
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(lambda:call(client(user),path));assert started.wait(5)
        try:assert call(client(user),path).status_code==409
        finally:release.set()
        assert first.result(timeout=5).status_code==502
    assert call(c,path).status_code==409
    with bot.db_connection() as conn:
        assert conn.execute('SELECT SUM(used) FROM daily_message_usage').fetchone()[0]==0
        assert conn.execute('SELECT SUM(calls) FROM learning_user_daily_usage').fetchone()[0]==1


def test_model_flag_and_blank_answer_never_call_provider(user,ai,monkeypatch):
    c=client(user);s=start(c,'ielts-speaking-a-short-v1').json['session']
    s=write(c,s,{},'submit').json['session']
    monkeypatch.setenv('LEARNING_MOCK_AI_ENABLED','1')
    assert call(c,f'mocks/{s["id"]}/ai/s1').status_code==400 and not ai.calls
    s=start(c,'ielts-speaking-b-short-v1').json['session'];s=write(c,s,{'s1':'My town has a quiet library.'},'submit').json['session']
    monkeypatch.setenv('LEARNING_AI_ENABLED','0')
    assert call(c,f'mocks/{s["id"]}/ai/s1').status_code==503 and not ai.calls


def test_history_compares_only_same_template_and_paginates(user):
    c=client(user)
    for template in ['ielts-reading-a-short-v1','ielts-reading-b-short-v1','ielts-reading-a-short-v1']:
        s=start(c,template).json['session'];s=write(c,s,{'r1':1},'submit').json['session']
    comparisons=c.get('/api/learning/mocks/'+s['id']).json['comparisons']
    assert len(comparisons)==2
    history=c.get('/api/learning/mocks?exam=ielts').json
    assert history['total']==3 and len(history['history'])==3
    assert c.get('/api/learning/mocks?exam=cet4').json['history']==[]


def test_mock_inputs_csrf_and_isolation(user):
    c=client(user);s=start(c,'ielts-listening-a-short-v1').json['session']
    assert c.post('/api/learning/mocks',json={}).status_code==403
    assert call(c,'mocks',dict(session_id='bad',template_id='bad')).status_code==400
    for answers in [[],{'unknown':1},{'l1':True},{'l1':-1},{'l1':'0'},{'l1':3}]:
        assert write(c,s,answers).status_code==400
    assert write(c,s,{'l1':1},version=True).status_code==400
    assert call(c,'mocks',dict(session_id=str(uuid4()),template_id='bad',deadline='later')).status_code==400
    with bot.db_connection() as conn:
        other=conn.execute("INSERT INTO users(phone,password_hash) VALUES('13900000001','test') RETURNING id").fetchone()[0]
    second=client(other)
    assert second.get('/api/learning/mocks/'+s['id']).status_code==404
    assert second.get('/api/learning/mocks/'+s['id']+'/audio').status_code==404
    assert write(second,s,{'l1':0}).status_code==404
    assert call(second,f'mocks/{s["id"]}/ai/s1').status_code==404
    assert second.get('/api/learning/mocks').json['storage_scope']!=c.get('/api/learning/mocks').json['storage_scope']


def test_subjective_answer_validation_and_json_size(user):
    c=client(user);s=start(c,'ielts-writing-a-short-v1').json['session']
    for bad in [123,None,'x'*4001]:assert write(c,s,{'s1':bad}).status_code==400
    assert write(c,s,{'s1':'x'*22000}).status_code==413
    assert c.get('/api/learning/mocks/'+s['id']).json['session']['answers']=={}


def test_preferences_default_validated_and_versioned(user):
    c=client(user)
    assert c.get('/api/learning/preferences').json['preferences']==dict(timezone='Asia/Shanghai',version=0)
    for bad in ['Mars/City','../UTC','',123]:assert settings(c,bad).status_code==400
    assert settings(c,'UTC',True).status_code==400
    assert settings(c,'America/New_York').json['preferences']==dict(timezone='America/New_York',version=1)
    assert settings(c,'America/New_York').status_code==200
    assert settings(c,'Asia/Tokyo').status_code==409
    p=call(c,'plans/ielts').json['plan']
    assert p['inputs']['timezone']=='America/New_York'
    with bot.db_connection() as conn:now=conn.execute('SELECT clock_timestamp()').fetchone()[0]
    assert p['date']==now.astimezone(ZoneInfo('America/New_York')).date().isoformat()


def test_timezone_date_boundary_and_existing_tasks_not_reset(user):
    c=client(user);p=call(c,'plans/ielts').json['plan'];task=p['tasks'][0]
    call(c,f'tasks/ielts/{p["date"]}/{task["id"]}',dict(completed=True,expected_version=0),'PUT')
    assert settings(c,'Pacific/Kiritimati').status_code==200
    east=call(c,'plans/ielts').json['plan']
    assert settings(c,'Etc/GMT+12',1).status_code==200
    west=call(c,'plans/ielts').json['plan']
    assert west['date']!=east['date']
    assert settings(c,'Asia/Shanghai',2).status_code==200
    restored=call(c,'plans/ielts').json['plan']
    assert restored['date']==p['date'] and next(t for t in restored['tasks'] if t['id']==task['id'])['completed']
    with bot.db_connection() as conn:assert conn.execute('SELECT COUNT(*) FROM learning_user_daily_usage').fetchone()[0]==0


def test_three_screening_groups_rotate_without_breaking_replays(user):
    c=client(user);versions=[];first_key=uuid4()
    for i in range(3):
        response=screening(c,first_key if i==0 else None)
        assert response.status_code==200
        versions.append(response.json['diagnostic']['version'])
    assert len(set(versions))==3
    assert c.get('/api/learning/diagnostics/ielts').json['test']['version']==versions[0]
    assert screening(c,first_key,versions[0]).json['replayed']
    assert c.get('/api/learning/diagnostics/ielts').json['test']['version']==versions[0]
    assert screening(c).status_code==200
    assert screening(c,version=versions[0]).status_code==409


def test_screening_concurrent_different_ids_advance_once(user):
    c=client(user);version=c.get('/api/learning/diagnostics/ielts').json['test']['version']
    payload=dict(version=version,answers={q['id']:q['correct_index'] for q in GROUPS[version]['ielts']})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:call(client(user),'diagnostics/ielts',dict(**payload,request_id=str(uuid4()))),range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    test=c.get('/api/learning/diagnostics/ielts').json['test']
    assert test['group']==2 and '不能代表完整英语水平' in test['notice']


def test_waiting_save_uses_clock_after_row_lock(user):
    c=client(user);s=start(c).json['session'];ready=Event()
    def save_late():
        other=client(user);h=headers(other);ready.set()
        return other.put('/api/learning/mocks/'+s['id']+'/answers',headers=h,
            json=dict(request_id=str(uuid4()),expected_version=0,answers={'r1':1}))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with bot.db_connection() as blocker:
            blocker.execute('SELECT session_id FROM learning_mock_sessions WHERE session_id=%s FOR UPDATE',(s['id'],))
            blocker.execute("UPDATE learning_mock_sessions SET started_at=clock_timestamp()-INTERVAL '1 minute',deadline=clock_timestamp()+INTERVAL '200 milliseconds' WHERE session_id=%s",(s['id'],))
            future=pool.submit(save_late);assert ready.wait(5)
            # Hold the row beyond its deadline, while the writer's transaction is already waiting.
            Event().wait(.3)
        assert future.result(timeout=5).status_code==409
    restored=c.get('/api/learning/mocks/'+s['id']).json['session']
    assert restored['status']=='expired' and restored['answers']=={}


def test_concurrent_timezone_updates_do_not_overwrite_newer_version(user):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda tz:settings(client(user),tz),['America/New_York','Europe/London']))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert client(user).get('/api/learning/preferences').json['preferences']['version']==1


def test_save_hash_is_canonical_for_json_key_order(user):
    c=client(user);s=start(c).json['session'];key=uuid4()
    assert write(c,s,{'r1':1,'r2':0},key=key).status_code==200
    response=write(c,s,{'r2':0,'r1':1},key=key)
    assert response.status_code==200 and response.json['replayed']
    assert response.json['session']['version']==1


def test_subjective_history_keeps_ai_reference_separate(user,ai,monkeypatch):
    monkeypatch.setenv('LEARNING_MOCK_AI_ENABLED','1');c=client(user)
    for _ in range(2):
        s=start(c,'ielts-writing-a-short-v1').json['session']
        s=write(c,s,{'s1':'Public libraries improve access to learning.'},'submit').json['session']
        assert call(c,f'mocks/{s["id"]}/ai/s1').status_code==200
    points=c.get('/api/learning/mocks/'+s['id']).json['comparisons']
    assert len(points)==2
    assert all(p['objective_accuracy'] is None and p['ai_reference_average']==72 for p in points)
    assert len(ai.calls)==2


def test_completed_mock_is_a_study_day_without_becoming_a_practice_score(user):
    c=client(user);s=start(c).json['session']
    assert write(c,s,{'r1':1},'submit').status_code==200
    overview=c.get('/api/learning/overview/ielts').json['overview']
    assert overview['study_days']==1 and overview['today'] in overview['active_dates']
    assert overview['practice_scores']==[] and overview['diagnostic_accuracy'] is None
