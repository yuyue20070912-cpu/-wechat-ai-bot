import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

import bot
from learning import accounting, reviews
from learning.diagnostics import TESTS, TEST_VERSION, GROUPS
from learning.questions import QUESTIONS
from learning_fakes import FEEDBACK


def client(user):
    c = bot.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = user
        s['phone'] = '13800000000'
    return c


def headers(c):
    return {'X-Learning-CSRF': c.get('/api/learning/profiles').json['csrf_token']}


def post(c, path, body=None):
    return c.post('/api/learning/' + path, json={} if body is None else body, headers=headers(c))


def diagnose(c, exam='ielts', correct=True, request_id=None):
    version=c.get('/api/learning/diagnostics/'+exam).json['test']['version']
    answers = {q['id']: q['correct_index'] if correct else (q['correct_index']+1)%3 for q in GROUPS[version][exam]}
    return post(c, 'diagnostics/' + exam, dict(request_id=str(request_id or uuid4()), version=version, answers=answers))


def plan(c, exam='ielts'):
    response = post(c, 'plans/' + exam)
    assert response.status_code == 200
    return response.json['plan']


def patch_task(c, p, task=None, completed=True, version=0):
    return c.put(f'/api/learning/tasks/{p["exam_type"]}/{p["date"]}/{task or p["tasks"][0]["id"]}',
                 json=dict(completed=completed, expected_version=version), headers=headers(c))


def review_body(item, answer=None, rating=None, request_id=None):
    return dict(request_id=str(request_id or uuid4()), item_key=item['item_key'], expected_version=item['version'],
                answer=answer or '0', self_rating=rating)


def items(c, exam='ielts'):
    return c.get('/api/learning/reviews/' + exam).json['items']


def paid_practice(c, question='ielts-writing-1-v1'):
    return post(c, 'attempts', dict(request_id=str(uuid4()), question_id=question, answer='I learn through practice.'))


@pytest.mark.parametrize('method,path', [('get','diagnostics/ielts'),('post','diagnostics/ielts'),
    ('get','diagnostic-results/'+str(uuid4())),('post','diagnostic-results/'+str(uuid4())+'/ai'),
    ('get','plans/ielts'),('post','plans/ielts'),('put','tasks/ielts/2026-10-18/diagnostic'),
    ('get','overview/ielts'),('get','reviews/ielts'),('post','reviews/ielts/sync'),
    ('post','reviews/save'),('post','review-responses')])
def test_personalization_login_required(method,path):
    response = getattr(bot.app.test_client(), method)('/api/learning/'+path)
    assert response.status_code == 401 and response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('exam', ['ielts','cet4','cet6'])
def test_exam_specific_diagnostic_no_paid_calls(user, ai, exam):
    c = client(user)
    catalog = c.get('/api/learning/diagnostics/' + exam).json['test']
    assert len(catalog['questions']) == 5
    assert all(q['skill'] != 'listening' for q in catalog['questions'])
    assert all('correct_index' not in q and 'explanation' not in q for q in catalog['questions'])
    response = diagnose(c, exam)
    assert response.status_code == 200
    result = response.json['diagnostic']['result']
    assert result['accuracy'] == 100 and result['correct'] == 5 and result['weak_skills'] == []
    assert result['method'] == '本地客观判分，不调用付费 AI'
    assert not ai.calls and items(c, exam) == []
    overview = c.get('/api/learning/overview/' + exam).json['overview']
    assert overview['study_days'] == 1 and overview['diagnostic_accuracy'] == 100
    assert overview['practice_scores'] == [] and overview['diagnostic_delta'] is None


def test_diagnostic_replay_and_concurrency(user):
    key = uuid4()
    payload=dict(request_id=str(key),version=TEST_VERSION,answers={q['id']:(q['correct_index']+1)%3 for q in TESTS['ielts']})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: post(client(user),'diagnostics/ielts',payload), range(4)))
    assert all(r.status_code == 200 for r in results)
    assert sum(not r.json['replayed'] for r in results) == 1
    c = client(user)
    assert len(items(c)) == 5
    assert diagnose(c, correct=True, request_id=key).status_code == 409
    with bot.db_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_diagnostics').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM learning_user_daily_usage').fetchone()[0] == 0


def test_bad_diagnostic_fields_csrf_and_unknown_exam(user):
    c = client(user)
    assert c.post('/api/learning/diagnostics/ielts',json={}).status_code == 403
    assert c.post('/api/learning/plans/ielts',json={},headers={**headers(c),'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert post(c,'diagnostics/unknown').status_code == 404
    good = dict(request_id=str(uuid4()), version=TEST_VERSION, answers={q['id']:q['correct_index'] for q in TESTS['ielts']})
    for bad in [{}, [], {**good,'user_id':42}, {**good,'request_id':[]}, {**good,'answers':[]},
                {**good,'answers':{}}, {**good,'answers':{**good['answers'],'iv':True}},
                {**good,'answers':{**good['answers'],'iv':3}}]:
        assert post(c,'diagnostics/ielts',bad).status_code == 400
    assert post(c,'diagnostics/ielts',{**good,'version':'old'}).status_code == 409
    assert c.get('/api/learning/reviews/ielts?offset=-1').status_code == 400


def test_daily_plan_uses_goal_level_diagnostic_and_ai_weakness(user, ai):
    c = client(user)
    c.put('/api/learning/profiles/ielts', json=dict(target_score=7.5,current_level='beginner'),headers=headers(c))
    diagnose(c, correct=False)
    feedback = dict(FEEDBACK, score=40)
    original = ai.client.responses.create
    def create(**kwargs):
        response = original(**kwargs); response.output_text=json.dumps(feedback); return response
    ai.client.responses.create = create
    assert paid_practice(c, 'ielts-vocabulary-1-v1').status_code == 200
    p = plan(c)
    assert p['inputs']['profile'] == dict(target_score=7.5,current_level='beginner')
    assert p['inputs']['diagnostic_id']
    assert p['inputs']['ranked_skills'][0] == 'vocabulary'
    assert len(p['tasks']) == 4 and any(t['id']=='review' for t in p['tasks'])
    assert all(t['minutes']==8 for t in p['tasks'] if t['kind']=='practice')
    assert len(ai.calls) == 1


def test_plan_concurrent_creation_and_snapshot(user):
    with ThreadPoolExecutor(max_workers=5) as pool:
        plans = list(pool.map(lambda _: plan(client(user)), range(5)))
    assert all(p == plans[0] for p in plans)
    c=client(user); before=plans[0]
    diagnose(c)
    assert plan(c) == before
    assert c.get('/api/learning/plans/cet4').json['plan'] is None
    with bot.db_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_tasks').fetchone()[0] == 3


def test_tasks_persist_after_real_logout_login_and_restore_versions(user):
    c=client(user); p=plan(c)
    with bot.db_connection() as conn:
        conn.execute('UPDATE users SET password_hash=%s WHERE id=%s',(generate_password_hash('testPass12345'),user))
    response=patch_task(c,p)
    assert response.status_code==200
    assert patch_task(c,p).status_code==200  # identical lost-response retry
    assert patch_task(c,p,completed=False).status_code==409
    c.post('/auth/logout')
    assert c.get('/api/learning/plans/ielts').status_code==401
    assert c.post('/auth/login',json=dict(phone='13800000000',password='testPass12345')).status_code==200
    restored=plan(c)
    task=next(t for t in restored['tasks'] if t['id']==p['tasks'][0]['id'])
    assert task['completed'] and task['version']==1
    assert patch_task(c,restored,completed=False,version=1).status_code==200
    assert c.get('/api/learning/overview/ielts').json['overview']['completed_tasks']==0


def test_concurrent_task_updates_are_idempotent(user):
    p=plan(client(user))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda _:patch_task(client(user),p),range(4)))
    assert all(r.status_code==200 for r in responses)
    with bot.db_connection() as conn:
        assert conn.execute('SELECT SUM(version) FROM learning_tasks').fetchone()[0]==1


def test_objective_review_schedule_and_replay(user):
    c=client(user); diagnose(c,correct=False)
    item=items(c)[0]
    # Use reference from the saved mistake for a new correct objective answer.
    correct=str(item['content']['options'].index(item['content']['reference']))
    payload=review_body(item,correct)
    response=post(c,'review-responses',payload)
    assert response.status_code==200 and response.json['correct'] is True
    assert response.json['item']['interval_days']==1 and response.json['item']['version']==1
    assert post(c,'review-responses',payload).json['replayed'] is True
    assert post(c,'review-responses',{**payload,'answer':str((int(correct)+1)%3)}).status_code==409
    assert post(c,'review-responses',review_body(response.json['item'],correct)).status_code==409  # not due
    with bot.db_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_review_events').fetchone()[0]==1


def test_objective_review_wrong_answer_returns_in_ten_minutes(user):
    c=client(user); diagnose(c,correct=False); item=items(c)[0]
    correct=item['content']['options'].index(item['content']['reference'])
    response=post(c,'review-responses',review_body(item,str((correct+1)%3)))
    assert response.json['correct'] is False
    changed=response.json['item']
    assert changed['state']=='relearning' and changed['interval_days']==0 and changed['repetitions']==0
    with bot.db_connection() as conn:
        minutes=conn.execute('SELECT EXTRACT(EPOCH FROM due_at-NOW())/60 FROM learning_review_items WHERE item_key=%s',(item['item_key'],)).fetchone()[0]
        assert 9 <= minutes <= 10


def test_review_concurrent_writes_do_not_advance_twice(user):
    c=client(user); diagnose(c,correct=False); item=items(c)[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:post(client(user),'review-responses',review_body(item)),range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    with bot.db_connection() as conn:
        assert conn.execute('SELECT SUM(version) FROM learning_review_items').fetchone()[0]==1


def test_same_review_uuid_for_different_items_rolls_back_loser(user):
    c=client(user); diagnose(c,correct=False); batch=items(c); key=uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda item:post(client(user),'review-responses',review_body(item,request_id=key)),batch[:2]))
    assert sorted(r.status_code for r in responses)==[200,409]
    with bot.db_connection() as conn:
        assert conn.execute('SELECT SUM(version) FROM learning_review_items').fetchone()[0]==1


def test_ai_mistake_snapshot_manual_save_and_self_rating(user,ai):
    c=client(user); response=paid_practice(c); key=response.json['request_id']
    assert items(c)==[]  # 72/100 is not automatically classified as a mistake
    assert post(c,'reviews/save',dict(request_id=key)).json['created'] is True
    assert post(c,'reviews/save',dict(request_id=key)).json['created'] is False
    item=items(c)[0]
    assert item['content']['ai_feedback']==response.json['feedback']
    response=post(c,'review-responses',review_body(item,'I can explain this more clearly.','easy'))
    assert response.status_code==200 and response.json['correct'] is None
    assert response.json['item']['interval_days']==4  # ceil(1 * 2.5 * 1.3)
    assert len(ai.calls)==1  # reviewing a saved AI analysis does not call AI


def test_auto_mistake_is_atomic_with_feedback(user,ai,monkeypatch):
    c=client(user); original=ai.client.responses.create
    def create(**kwargs):
        response=original(**kwargs);response.output_text=json.dumps(dict(FEEDBACK,score=35));return response
    ai.client.responses.create=create
    assert paid_practice(c).status_code==200
    assert len(items(c))==1
    with monkeypatch.context() as patch:
        patch.setattr(reviews,'capture_attempt',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError()))
        assert paid_practice(c).status_code==502
    with bot.db_connection() as conn:
        assert conn.execute('SELECT SUM(used) FROM daily_message_usage').fetchone()[0]==1
    assert len(items(c))==1


def test_legacy_mistake_import_is_bounded_and_preserves_schedule(user):
    c=client(user)
    with bot.db_connection() as conn:
        for _ in range(105):
            conn.execute('''INSERT INTO learning_attempts(user_id,request_id,question_id,exam_type,skill,answer,status,usage_day,model,feedback,finished_at)
                VALUES (%s,%s,'ielts-writing-1-v1','ielts','writing','answer','completed',CURRENT_DATE,'test',%s,NOW())''',
                (user,str(uuid4()),json.dumps(dict(FEEDBACK,score=40))))
    assert post(c,'reviews/ielts/sync').json['imported']==100
    first=items(c)[0]; response=post(c,'review-responses',review_body(first,'Better answer','good'))
    assert response.status_code==200
    assert post(c,'reviews/ielts/sync').json['imported']==5
    assert post(c,'reviews/ielts/sync').json['imported']==0
    assert c.get('/api/learning/reviews/ielts').json['total']==105
    with bot.db_connection() as conn:
        assert conn.execute('SELECT version FROM learning_review_items WHERE item_key=%s',(first['item_key'],)).fetchone()[0]==1


def test_review_math_is_bounded_and_relearning_resets():
    interval,ease,reps=0,2.5,0
    for expected in (1,3,8):
        interval,ease,reps,state,_=reviews.schedule(interval,ease,reps,'good')
        assert interval==expected
    assert state=='mastered'
    assert reviews.schedule(365,3,50,'easy')[0]==365
    assert reviews.schedule(100,1.3,5,'again')[:4]==(0,1.3,0,'relearning')


def test_cross_user_isolation_for_reports_plans_reviews_and_tasks(user,ai):
    c=client(user); diag=diagnose(c,correct=False).json['diagnostic']; p=plan(c); item=items(c)[0]
    attempt=paid_practice(c).json['request_id']
    with bot.db_connection() as conn:
        other=conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13900000001','test') RETURNING id").fetchone()[0]
    second=client(other)
    assert second.get('/api/learning/diagnostic-results/'+diag['request_id']).status_code==404
    assert post(second,'diagnostic-results/'+diag['request_id']+'/ai').status_code==404
    assert patch_task(second,p).status_code==404
    assert items(second)==[]
    assert post(second,'review-responses',review_body(item)).status_code==404
    assert post(second,'reviews/save',dict(request_id=attempt)).status_code==404
    overview=second.get('/api/learning/overview/ielts').json['overview']
    assert overview['study_days']==0 and overview['practice_scores']==[] and overview['diagnostic_accuracy'] is None


def test_optional_ai_diagnostic_requires_both_flags_and_replays(user,ai,monkeypatch):
    c=client(user); diag=diagnose(c).json['diagnostic']; path='diagnostic-results/'+diag['request_id']+'/ai'
    assert post(c,path).status_code==503 and not ai.calls
    monkeypatch.setenv('LEARNING_DIAGNOSTIC_AI_ENABLED','1')
    monkeypatch.setenv('LEARNING_AI_ENABLED','0')
    assert post(c,path).status_code==503 and not ai.calls
    monkeypatch.setenv('LEARNING_AI_ENABLED','1')
    first=post(c,path); second=post(c,path)
    assert first.status_code==second.status_code==200
    assert second.json['replayed'] is True and len(ai.calls)==1
    assert first.json['feedback']['kind']=='diagnostic_interpretation'
    overview=c.get('/api/learning/overview/ielts').json['overview']
    assert overview['diagnostic_accuracy']==100 and overview['practice_scores']==[]
    assert items(c)==[]


def test_ai_diagnostic_failure_refunds_and_consumes_shared_budget(user,ai,monkeypatch):
    monkeypatch.setenv('LEARNING_DIAGNOSTIC_AI_ENABLED','1')
    monkeypatch.setenv('LEARNING_USER_DAILY_CALLS','1')
    c=client(user); diag=diagnose(c).json['diagnostic']; path='diagnostic-results/'+diag['request_id']+'/ai'
    ai.client.responses.create=lambda **kwargs:(_ for _ in ()).throw(TimeoutError())
    assert post(c,path).status_code==502
    assert post(c,path).status_code==409
    assert paid_practice(c).status_code==429
    with bot.db_connection() as conn:
        assert conn.execute('SELECT SUM(used) FROM daily_message_usage').fetchone()[0]==0
        assert conn.execute('SELECT SUM(calls) FROM learning_user_daily_usage').fetchone()[0]==1


def test_ai_diagnostic_concurrency_is_one_call(user,ai,monkeypatch):
    monkeypatch.setenv('LEARNING_DIAGNOSTIC_AI_ENABLED','1')
    c=client(user); diag=diagnose(c).json['diagnostic']; path='diagnostic-results/'+diag['request_id']+'/ai'
    started,release=Event(),Event(); original=ai.client.responses.create
    def create(**kwargs):
        started.set();assert release.wait(5);return original(**kwargs)
    ai.client.responses.create=create
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(lambda:post(client(user),path));assert started.wait(5)
        try: assert post(client(user),path).status_code==409
        finally: release.set()
        assert first.result(timeout=5).status_code==200
    assert len(ai.calls)==1


def test_insights_use_all_attempts_and_separate_score_series(user):
    c=client(user); diagnose(c,correct=False); diagnose(c,correct=True)
    with bot.db_connection() as conn:
        for i in range(25):
            conn.execute('''INSERT INTO learning_attempts(user_id,request_id,question_id,exam_type,skill,answer,status,usage_day,model,feedback,finished_at)
                VALUES (%s,%s,'ielts-writing-1-v1','ielts','writing','answer','completed',CURRENT_DATE,'test',%s,NOW()-(%s * INTERVAL '1 day'))''',
                (user,str(uuid4()),json.dumps(dict(FEEDBACK,score=50+i)),24-i))
    result=c.get('/api/learning/overview/ielts').json['overview']
    assert result['diagnostic_accuracy']==100 and result['diagnostic_delta'] is None  # Different screening groups are not directly comparable
    assert result['study_days']==25
    assert result['practice_scores'][0]['count']==25 and result['practice_scores'][0]['change']==24
    assert len(result['active_dates'])==25


def test_invalid_task_and_review_fields(user):
    c=client(user); p=plan(c)
    assert patch_task(c,p,completed=1).status_code==400
    assert patch_task(c,p,version=True).status_code==400
    for bad in [[],{},dict(request_id=str(uuid4()),item_key='x',expected_version=-1,answer='a',self_rating=None)]:
        assert post(c,'review-responses',bad).status_code==400
    assert post(c,'reviews/save',dict(request_id='bad')).status_code==400


def test_local_diagnostic_request_rate_is_bounded(user):
    c=client(user)
    for _ in range(20): assert diagnose(c).status_code==200
    assert diagnose(c).status_code==429


def test_legacy_diagnostic_replay_keeps_saved_result(user, monkeypatch):
    from learning import diagnostic_repository
    c = client(user)
    key = str(uuid4())
    old_questions = GROUPS['entry-v1']['ielts']
    payload = dict(request_id=key, version='entry-v1',
                   answers={q['id']: q['correct_index'] for q in old_questions})
    # Simulate a result saved before the new five-question groups were released.
    with monkeypatch.context() as old:
        old.setattr(diagnostic_repository, 'GROUP_VERSIONS', ('entry-v1', 'entry-v1-b', 'entry-v1-c'))
        saved = post(c, 'diagnostics/ielts', payload)
    assert saved.status_code == 200
    replay = post(c, 'diagnostics/ielts', payload)
    assert replay.status_code == 200 and replay.json['replayed']
    assert replay.json['diagnostic'] == saved.json['diagnostic']
    assert replay.json['diagnostic']['result']['total'] == 6
    next_test = c.get('/api/learning/diagnostics/ielts').json['test']
    assert next_test['version'] == 'entry-v2-b'
    assert len(next_test['questions']) == 5
    assert all(q['skill'] != 'listening' for q in next_test['questions'])


def test_plan_ignores_legacy_listening_weakness(user):
    from learning import diagnostic_repository
    from unittest.mock import patch
    c = client(user)
    with patch.object(diagnostic_repository, 'GROUP_VERSIONS', ('entry-v1',)):
        questions = GROUPS['entry-v1']['ielts']
        response = post(c, 'diagnostics/ielts', dict(request_id=str(uuid4()), version='entry-v1',
            answers={q['id']: (q['correct_index']+1)%3 if q['skill']=='listening' else q['correct_index'] for q in questions}))
    assert response.status_code == 200
    p = plan(c)
    assert all(t.get('skill') != 'listening' for t in p['tasks'])
    # Generating the new plan must not rewrite the saved diagnostic.
    assert c.get('/api/learning/diagnostic-results/'+response.json['diagnostic']['request_id']).json['diagnostic']['result']['weak_skills'] == ['listening']
