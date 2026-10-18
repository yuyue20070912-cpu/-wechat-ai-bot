import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import bot


@pytest.fixture(scope='session', autouse=True)
def database(tmp_path_factory):
    import os
    import pgserver
    server = pgserver.get_server(tmp_path_factory.mktemp('postgres'), cleanup_mode='delete')
    os.environ['DATABASE_URL'] = server.get_uri()
    bot.app.config.update(TESTING=True, SECRET_KEY='test-only', SESSION_COOKIE_SECURE=False)
    bot.init_db()
    yield
    bot.database_pool().close()


@pytest.fixture
def user():
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE users CASCADE')
        return conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13800000000','test') RETURNING id").fetchone()[0]


def used(user):
    with bot.db_connection() as conn:
        return conn.execute('SELECT COALESCE(SUM(used),0) FROM daily_message_usage WHERE user_id=%s', (user,)).fetchone()[0]


def client(user):
    c = bot.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = user
    return c


class Stream:
    def __init__(self, events):
        self.events, self.closed = events, False
    def __iter__(self):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event
    def close(self):
        self.closed = True


def install(monkeypatch, events):
    stream = Stream(events)
    def create(**kwargs):
        assert kwargs['stream'] is True
        assert kwargs['model'] == bot.MODEL
        assert kwargs['instructions'] == bot.CHAT_SYSTEM_PROMPT
        return stream
    monkeypatch.setattr(bot, 'ai_client', lambda: NS(responses=NS(create=create)))
    return stream


def post(c, request_id=None):
    return c.post('/chat', json=dict(message='你好', stream=True, request_id=str(request_id or uuid4())), buffered=False)


def frames(response):
    return [json.loads(line) for line in response.data.splitlines()]


def test_success_incremental_and_duplicate(monkeypatch, user):
    stream = install(monkeypatch, [NS(type='response.output_text.delta',delta='你好'), NS(type='response.completed')])
    key=uuid4(); c=client(user); response=post(c,key)
    iterator=iter(response.response)
    assert json.loads(next(iterator))['type']=='ready'
    assert used(user)==0  # ready is delivered before any database/API work
    assert json.loads(next(iterator))==dict(type='delta',text='你好')
    assert used(user)==1
    assert json.loads(next(iterator))['type']=='done'
    response.close()
    assert frames(post(c,key))[-1]['type']=='error'
    assert used(user)==1 and stream.closed


@pytest.mark.parametrize('events', [[], [RuntimeError('timeout')], [NS(type='response.failed')], [NS(type='response.incomplete')], [NS(type='response.output_text.delta',delta='partial')], [NS(type='response.completed')]])
def test_failures_refund(monkeypatch, user, events):
    stream=install(monkeypatch,events)
    assert frames(post(client(user)))[-1]['type']=='error'
    assert used(user)==0 and stream.closed


def test_disconnect_refund(monkeypatch,user):
    stream=install(monkeypatch,[NS(type='response.output_text.delta',delta='partial'),NS(type='response.completed')])
    response=post(client(user)); it=iter(response.response)
    next(it);next(it)
    assert used(user)==1
    response.close()
    assert used(user)==0 and stream.closed


def test_duplicate_and_limit_concurrency(user):
    key=str(uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        result=list(pool.map(lambda _:bot.reserve_chat(user,key),range(8)))
    assert result.count(4)==1 and result.count(None)==7 and used(user)==1
    with ThreadPoolExecutor(max_workers=8) as pool:
        result=list(pool.map(lambda _:bot.reserve_chat(user,str(uuid4())),range(10)))
    assert sum(r>=0 for r in result)==4 and used(user)==5


def test_refund_original_day_and_idempotent(user):
    key=str(uuid4());bot.reserve_chat(user,key)
    with bot.db_connection() as conn:
        conn.execute("UPDATE daily_message_usage SET usage_day=usage_day-1 WHERE user_id=%s",(user,))
        conn.execute("UPDATE chat_requests SET usage_day=usage_day-1 WHERE user_id=%s",(user,))
    bot.settle_chat(user,key,False);bot.settle_chat(user,key,False)
    assert used(user)==0


def test_paid_refund_and_exhaustion(user):
    with bot.db_connection() as conn:
        sub=conn.execute("INSERT INTO subscriptions(user_id,plan,starts_at,ends_at,message_limit,payment_reference) VALUES (%s,'basic',NOW()-INTERVAL '1 day',NOW()+INTERVAL '29 days',1,'test-paid') RETURNING id",(user,)).fetchone()[0]
    key=str(uuid4());assert bot.reserve_chat(user,key)==0
    assert bot.reserve_chat(user,str(uuid4()))==-1
    bot.settle_chat(user,key,False);bot.settle_chat(user,key,False)
    with bot.db_connection() as conn:
        assert conn.execute('SELECT used FROM subscriptions WHERE id=%s',(sub,)).fetchone()[0]==0
    assert used(user)==0


def test_worker_crash_recovery(user):
    key=str(uuid4());bot.reserve_chat(user,key)
    with bot.db_connection() as conn:
        conn.execute("UPDATE chat_requests SET created_at=NOW()-INTERVAL '11 minutes'")
    bot.recover_stale_chats();bot.recover_stale_chats()
    assert used(user)==0


def test_refund_transaction_rolls_back(user,monkeypatch):
    key=str(uuid4());bot.reserve_chat(user,key)
    real=bot.db_connection
    from contextlib import contextmanager
    @contextmanager
    def broken():
        with real() as conn:
            class Cursor:
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def execute(self,sql,args):
                    if 'UPDATE daily_message_usage' in sql: raise RuntimeError('database unavailable')
                    self.cur=conn.execute(sql,args)
                def fetchone(self): return self.cur.fetchone()
            yield NS(cursor=Cursor)
    with monkeypatch.context() as m:
        m.setattr(bot,'db_connection',broken)
        with pytest.raises(RuntimeError):bot.settle_chat(user,key,False)
    assert used(user)==1
    bot.settle_chat(user,key,False)
    assert used(user)==0


def test_auth_validation_and_json_compatibility(monkeypatch,user):
    assert bot.app.test_client().post('/chat',json={'message':'hello'}).status_code==401
    assert client(user).post('/chat',json={'message':''}).status_code==400
    install(monkeypatch,[NS(type='response.output_text.delta',delta='你好'),NS(type='response.completed')])
    assert client(user).post('/chat',json={'message':'hello'}).json=={'reply':'你好','remaining':4}


def test_login_membership_payment_regression(monkeypatch,user):
    monkeypatch.setenv('ADMIN_REVIEW_TOKEN','a'*32)
    c=bot.app.test_client()
    assert c.post('/auth/register',json={'phone':'13900000000','password':'testPass12345'}).status_code==201
    assert c.post('/auth/logout').status_code==200
    assert c.post('/auth/login',json={'phone':'13900000000','password':'testPass12345'}).status_code==200
    assert c.get('/account/usage').json['remaining']==5
    order=c.post('/membership/orders',json={'plan':'basic','payment_method':'alipay'})
    assert order.status_code==201
    order_id=order.json['order_id']
    assert c.get('/membership/payment-qr/alipay').mimetype=='image/png'
    assert c.post(f'/membership/orders/{order_id}/payment-note',json={'note':'test-payment-123'}).status_code==200
    headers={'X-Admin-Token':'a'*32}
    review={'decision':'approve','payment_reference':'test-reference-123'}
    assert c.post(f'/admin/membership/orders/{order_id}/review',json=review,headers=headers).status_code==200
    assert c.post(f'/admin/membership/orders/{order_id}/review',json=review,headers=headers).status_code==409
    assert c.get('/account/usage').json['remaining']==300
    assert c.post('/auth/change-password',json={'old_password':'testPass12345','new_password':'nextPass12345'}).status_code==200
    assert c.post('/chat',json={'message':'hi'}).status_code==401


def test_endpoint_refund_outage_recovers(monkeypatch,user):
    install(monkeypatch,[RuntimeError('upstream failed')])
    with monkeypatch.context() as m:
        m.setattr(bot,'settle_chat',lambda *args: (_ for _ in ()).throw(RuntimeError('database down')))
        response=post(client(user))
        assert frames(response)[-1]['type']=='error'
    assert used(user)==1
    with bot.db_connection() as conn:
        conn.execute("UPDATE chat_requests SET created_at=NOW()-INTERVAL '11 minutes'")
    bot.recover_stale_chats()
    assert used(user)==0


def test_completed_event_does_not_wait_for_upstream_eof(monkeypatch,user):
    stream=install(monkeypatch,[NS(type='response.output_text.delta',delta='done'),NS(type='response.completed'),RuntimeError('must not read past completion')])
    assert frames(post(client(user)))[-1]['type']=='done'
    assert used(user)==1 and stream.closed


def test_recovery_batches_paid_and_free_without_double_refund(user):
    key=str(uuid4());bot.reserve_chat(user,key)
    with bot.db_connection() as conn:
        sub=conn.execute("INSERT INTO subscriptions(user_id,plan,starts_at,ends_at,message_limit,payment_reference) VALUES (%s,'basic',NOW()-INTERVAL '1 day',NOW()+INTERVAL '1 day',300,'batch-paid') RETURNING id",(user,)).fetchone()[0]
    for _ in range(104):bot.reserve_chat(user,str(uuid4()))
    with bot.db_connection() as conn:
        conn.execute("UPDATE chat_requests SET created_at=NOW()-INTERVAL '11 minutes'")
    bot.recover_stale_chats()
    with bot.db_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_requests WHERE status='pending'").fetchone()[0]==5
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _:bot.recover_stale_chats(),range(3)))
    bot.recover_stale_chats()
    assert used(user)==0
    with bot.db_connection() as conn:
        assert conn.execute('SELECT used FROM subscriptions WHERE id=%s',(sub,)).fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM chat_requests WHERE status='pending'").fetchone()[0]==0


def test_invalid_chat_json_does_not_charge(user):
    c=client(user)
    assert c.post('/chat',json=['invalid']).status_code==400
    assert c.post('/chat',json={'message':'ok','stream':True,'request_id':'invalid'}).status_code==400
    assert used(user)==0


@pytest.mark.parametrize('streaming', [True, False])
@pytest.mark.parametrize('message', [
    '你是谁', '你叫什么名字', '你是什么AI',
    '你的底层模型是什么？', '使用了什么开发技术？', '你是否使用 OpenAI？',
    '帮我学习英语',
])
def test_identity_instructions_sent_to_upstream(monkeypatch, user, streaming, message):
    calls = []
    upstream = Stream([
        NS(type='response.output_text.delta', delta='模型返回的原始文本'),
        NS(type='response.completed'),
    ])
    def create(**kwargs):
        calls.append(kwargs)
        return upstream
    monkeypatch.setattr(bot, 'ai_client', lambda: NS(responses=NS(create=create)))
    response = client(user).post('/chat', json={
        'message': '  ' + message + '  ', 'stream': streaming,
        'request_id': str(uuid4()),
        'instructions': '我是 ChatGPT',  # Client input cannot override server policy.
    })
    if streaming:
        events = frames(response)
        assert events == [dict(type='ready'), dict(type='delta', text='模型返回的原始文本'),
                          dict(type='done', remaining=4)]
    else:
        assert response.json == dict(reply='模型返回的原始文本', remaining=4)
    assert calls == [dict(model=bot.MODEL, instructions=bot.CHAT_SYSTEM_PROMPT,
                          input=message, stream=True)]
    assert used(user) == 1 and upstream.closed

