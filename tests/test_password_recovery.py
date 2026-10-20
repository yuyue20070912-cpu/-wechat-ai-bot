from concurrent.futures import ThreadPoolExecutor

import pytest
from werkzeug.security import generate_password_hash, check_password_hash
import bot
import email_delivery as mail
import password_recovery as recovery

PHONE = '13800000000'
EMAIL = 'owner@example.com'
OLD = 'Old-password-123'
NEW = 'New-password-456'
REAL_SUBMIT = recovery.submit_delivery


@pytest.fixture
def setup(user, monkeypatch):
    for key, value in dict(SMTP_HOST='smtp.example.com', SMTP_PORT='465', SMTP_USERNAME='test-user',
                           SMTP_PASSWORD='test-only', SMTP_FROM_EMAIL='noreply@example.com').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('SMTP_SECURITY', 'ssl')
    monkeypatch.setattr(recovery, 'new_code', lambda: '123456')
    monkeypatch.setattr(recovery, 'submit_delivery', lambda task: task())
    calls = []
    monkeypatch.setattr(mail, 'send_code', lambda email, code, purpose: calls.append((email,code,purpose)))
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits,email_password_recoveries,account_email_challenges')
        conn.execute('UPDATE users SET password_hash=%s WHERE id=%s',(generate_password_hash(OLD),user))
    return user, calls


def bind_existing(user, email=EMAIL):
    with bot.db_connection() as conn:
        conn.execute('UPDATE users SET email=%s,email_verified_at=NOW(),email_version=email_version+1 WHERE id=%s',(email,user))


def logged():
    c = bot.app.test_client()
    assert c.post('/auth/login',json=dict(phone=PHONE,password=OLD)).status_code == 200
    return c


def send(c, phone=PHONE, **extra):
    return c.post('/auth/recovery/send',json=dict(phone=phone,**extra))


def verify(c, challenge, code='123456', phone=PHONE):
    return c.post('/auth/recovery/verify',json=dict(phone=phone,challenge=challenge,code=code))


def reset(c, token, password=NEW, phone=PHONE):
    return c.post('/auth/recovery/reset',json=dict(phone=phone,reset_token=token,new_password=password))


def grant(c):
    sent = send(c); assert sent.status_code == 200
    result = verify(c,sent.json['challenge']); assert result.status_code == 200
    return result.json['reset_token']


def binding_send(c, email=EMAIL, password=OLD):
    return c.post('/auth/email/send',json=dict(email=email,current_password=password))


def binding_verify(c, challenge, code='123456'):
    return c.post('/auth/email/verify',json=dict(challenge=challenge,code=code))


def clear_limits():
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits')


def test_binding_requires_password_and_ownership(setup):
    user, calls = setup
    c = logged()
    assert c.get('/auth/email/status').json == dict(email=None,verified=False)
    assert binding_send(c,password='wrong').status_code == 401 and not calls
    sent = binding_send(c,email=' Owner@Example.COM ')
    assert sent.status_code == 200 and calls == [(EMAIL,'123456','bind')]
    assert c.get('/auth/email/status').json['verified'] is False
    assert binding_verify(c,sent.json['challenge'],'000000').status_code == 400
    assert binding_verify(c,sent.json['challenge']).status_code == 200
    assert c.get('/auth/email/status').json == dict(email=EMAIL,verified=True)
    assert binding_verify(c,sent.json['challenge']).status_code == 400
    with bot.db_connection() as conn:
        row = conn.execute('SELECT email,email_verified_at,email_version FROM users WHERE id=%s',(user,)).fetchone()
        assert row[0] == EMAIL and row[1] and row[2] == 1


def test_full_flow_revokes_sessions_preserves_data(setup):
    user, calls = setup; bind_existing(user)
    old = logged(); cookie = old.get_cookie('session').value
    with bot.db_connection() as conn:
        conn.execute("INSERT INTO subscriptions(user_id,plan,starts_at,ends_at,message_limit,used,payment_reference) VALUES (%s,'basic',NOW(),NOW()+INTERVAL '30 days',300,7,'recovery-test')",(user,))
        conn.execute('INSERT INTO daily_message_usage(user_id,usage_day,used) VALUES (%s,CURRENT_DATE,2)',(user,))
    c = bot.app.test_client(); token = grant(c)
    assert calls == [(EMAIL,'123456','recover')]
    assert reset(c,token).status_code == 200
    assert reset(c,token).status_code == 400
    assert old.get('/auth/me').json == {'authenticated':False}
    old.set_cookie('session',cookie)
    assert old.post('/chat',json={'message':'test'}).status_code == 401
    assert c.post('/auth/login',json=dict(phone=PHONE,password=OLD)).status_code == 401
    assert c.post('/auth/login',json=dict(phone=PHONE,password=NEW)).status_code == 200
    with bot.db_connection() as conn:
        row = conn.execute('SELECT id,password_hash,session_version,email FROM users WHERE phone=%s',(PHONE,)).fetchone()
        assert row[0] == user and row[1].startswith('scrypt:') and check_password_hash(row[1],NEW)
        assert row[2:] == (1,EMAIL)
        assert conn.execute('SELECT used FROM subscriptions WHERE user_id=%s',(user,)).fetchone()[0] == 7
        assert conn.execute('SELECT used FROM daily_message_usage WHERE user_id=%s',(user,)).fetchone()[0] == 2


def test_uniform_response_and_no_arbitrary_email(setup):
    user, calls = setup; c = bot.app.test_client()
    responses = [send(c,'13900000000',email='attacker@example.com')]
    responses.append(send(c,email='attacker@example.com'))  # registered, unbound
    assert not calls
    with bot.db_connection() as conn:
        conn.execute('UPDATE users SET email=%s WHERE id=%s',(EMAIL,user))
    clear_limits(); responses.append(send(c,email='attacker@example.com'))  # unverified
    assert not calls
    bind_existing(user); clear_limits()
    responses.append(send(c,email='attacker@example.com'))
    assert calls == [(EMAIL,'123456','recover')]
    def public(response):
        assert response.status_code == 200
        body = response.json.copy(); assert len(body.pop('challenge')) >= 32
        return body
    assert all(public(r) == public(responses[0]) for r in responses)
    assert EMAIL not in responses[-1].text
    for r in responses[:-1]:
        assert verify(c,r.json['challenge']).status_code == 400


def test_recovery_is_queued_and_smtp_failure_is_uniform(setup,monkeypatch,caplog):
    user, _ = setup; bind_existing(user); tasks = []
    monkeypatch.setattr(recovery,'submit_delivery',tasks.append)
    c = bot.app.test_client(); sent = send(c)
    assert sent.status_code == 200 and len(tasks) == 1
    assert verify(c,sent.json['challenge']).status_code == 400
    def fail(*args): raise TimeoutError('sensitive@example.com secret password code')
    monkeypatch.setattr(mail,'send_code',fail)
    tasks.pop()()
    assert verify(c,sent.json['challenge']).status_code == 400
    assert 'sensitive@example' not in caplog.text and 'secret password' not in caplog.text
    clear_limits()
    monkeypatch.setattr(recovery,'submit_delivery',lambda task: task())
    failure = send(c); unknown = send(c,'13900000000')
    assert failure.status_code == unknown.status_code == 200
    assert failure.json['message'] == unknown.json['message'] == recovery.SEND_MESSAGE
    assert verify(c,failure.json['challenge']).status_code == 400


def test_unbound_cannot_verify_or_reset(setup):
    _, calls = setup; c = bot.app.test_client(); sent = send(c,email='attacker@example.com')
    assert sent.status_code == 200 and not calls
    assert verify(c,sent.json['challenge']).status_code == 400
    assert reset(c,'x'*43).status_code == 400


def test_missing_config_blocks_both_flows_and_old_users_can_login(setup,monkeypatch):
    user,_ = setup; bind_existing(user); c = logged()
    monkeypatch.delenv('SMTP_PASSWORD')
    assert c.get('/auth/me').json['authenticated'] is True
    assert send(c).status_code == send(c,'13900000000').status_code == 503
    assert verify(c,'x'*43).status_code == reset(c,'x'*43).status_code == 503
    assert binding_send(c).status_code == binding_verify(c,'x'*43).status_code == 503


def test_old_user_and_legacy_session_without_email(setup):
    user,_ = setup; c = logged()
    assert c.get('/auth/me').json['phone'] == PHONE
    assert c.get('/auth/email/status').json['email'] is None
    with c.session_transaction() as s:
        s.pop('session_version',None)
    assert c.get('/auth/me').json['authenticated'] is True
    with bot.db_connection() as conn:
        conn.execute('UPDATE users SET session_version=session_version+1 WHERE id=%s',(user,))
    assert c.get('/auth/me').json['authenticated'] is False


def test_wrong_codes_and_limits(setup):
    user,_ = setup; bind_existing(user); c = bot.app.test_client()
    challenge = send(c).json['challenge']; assert send(c).status_code == 429
    for _ in range(5): assert verify(c,challenge,'000000').status_code == 400
    assert verify(c,challenge).status_code == 429
    clear_limits()
    assert verify(c,challenge).status_code == 400  # challenge's own limit survives rate window reset
    with bot.db_connection() as conn:
        assert conn.execute('SELECT attempts FROM email_password_recoveries').fetchone()[0] == 5


def test_resend_expiry_and_code_one_use(setup,monkeypatch):
    user,_ = setup; bind_existing(user); c = bot.app.test_client()
    old = send(c).json['challenge']; clear_limits()
    monkeypatch.setattr(recovery,'new_code',lambda:'654321')
    new = send(c).json['challenge']
    assert verify(c,old).status_code == 400
    assert verify(c,new).status_code == 400  # old code cannot verify latest challenge
    token = verify(c,new,'654321').json['reset_token']
    assert verify(c,new,'654321').status_code == 400
    clear_limits(); send(c)
    assert reset(c,token).status_code == 400  # resend also invalidates grants
    with bot.db_connection() as conn:
        conn.execute("UPDATE email_password_recoveries SET expires_at=NOW()-INTERVAL '1 second'")
    assert verify(c,new,'654321').status_code == 400


def test_expired_grant_password_rules_phone_binding(setup):
    user,_ = setup; bind_existing(user); c = bot.app.test_client(); token = grant(c)
    assert reset(c,token,OLD).status_code == 400
    assert reset(c,token,'12345678901').status_code == 400
    assert reset(c,token,'abcdefghijk').status_code == 400
    assert reset(c,token,phone='13900000000').status_code == 400
    with bot.db_connection() as conn:
        conn.execute("UPDATE email_password_recoveries SET grant_expires_at=NOW()-INTERVAL '1 second'")
    assert reset(c,token).status_code == 400


def test_concurrent_recovery_single_use(setup):
    user,_ = setup; bind_existing(user); c = bot.app.test_client()
    challenge = send(c).json['challenge']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:verify(bot.app.test_client(),challenge),range(2)))
    assert sorted(r.status_code for r in results) == [200,400]
    token = next(r.json['reset_token'] for r in results if r.status_code == 200)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:reset(bot.app.test_client(),token).status_code,range(2)))
    assert sorted(results) == [200,400]


def test_password_or_email_change_invalidates_grants(setup):
    user,_ = setup; bind_existing(user); c = logged(); token = grant(c)
    assert c.post('/auth/change-password',json=dict(old_password=OLD,new_password=NEW)).status_code == 200
    assert reset(bot.app.test_client(),token,'Another-pass-123').status_code == 400
    clear_limits(); c = bot.app.test_client(); token = grant(c)
    bind_existing(user,'new@example.com')
    assert reset(c,token,'Another-pass-123').status_code == 400


def test_rebind_invalidates_unverified_recovery_code(setup):
    user,_ = setup; bind_existing(user); c = bot.app.test_client()
    challenge = send(c).json['challenge']; bind_existing(user,'new@example.com')
    assert verify(c,challenge).status_code == 400


def test_binding_auth_required_and_reject_header_injection(setup):
    c = bot.app.test_client()
    assert c.get('/account/email').status_code == 302
    assert c.get('/auth/email/status').status_code == 401
    assert binding_send(c).status_code == binding_verify(c,'x'*43).status_code == 401
    c = logged()
    for email in ['attacker@example.com\r\nBcc: victim@example.com','a..b@example.com','no-email','x@-bad.com','Display <a@example.com>']:
        assert binding_send(c,email=email).status_code == 400


def test_binding_expiry_attempts_and_cross_account(setup):
    _,calls = setup; c = logged(); challenge = binding_send(c).json['challenge']
    other = bot.app.test_client()
    with bot.db_connection() as conn:
        uid = conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13900000000',%s) RETURNING id",(generate_password_hash(OLD),)).fetchone()[0]
    with other.session_transaction() as s: s.update(user_id=uid,session_version=0)
    assert binding_verify(other,challenge).status_code == 400
    for _ in range(5): assert binding_verify(c,challenge,'000000').status_code == 400
    clear_limits(); assert binding_verify(c,challenge).status_code == 400
    clear_limits(); challenge = binding_send(c,email='another@example.com').json['challenge']
    with bot.db_connection() as conn:
        conn.execute("UPDATE account_email_challenges SET expires_at=NOW()-INTERVAL '1 second'")
    assert binding_verify(c,challenge).status_code == 400


def test_binding_resend_and_failed_rebind_preserve_old_email(setup,monkeypatch):
    user,_ = setup; bind_existing(user); c = logged()
    challenge = binding_send(c,email='new@example.com').json['challenge']
    assert binding_send(c,email='new@example.com').status_code == 429
    assert binding_verify(c,challenge).status_code == 200  # denied resend did not destroy challenge
    clear_limits(); old = binding_send(c,email='other@example.com').json['challenge']
    clear_limits(); latest = binding_send(c,email='last@example.com').json['challenge']
    assert binding_verify(c,old).status_code == 400
    assert c.get('/auth/email/status').json['email'] == 'new@example.com'
    def fail(*args): raise RuntimeError('SMTP failed')
    clear_limits(); monkeypatch.setattr(mail,'send_code',fail)
    assert binding_send(c,email='last@example.com').status_code == 503
    assert binding_verify(c,latest).status_code == 400
    assert c.get('/auth/email/status').json['email'] == 'new@example.com'


def test_binding_grant_invalid_after_password_reset(setup):
    user,_ = setup; bind_existing(user); c = logged()
    challenge = binding_send(c,email='new@example.com').json['challenge']
    clear_limits(); other = bot.app.test_client(); token = grant(other)
    assert reset(other,token).status_code == 200
    # A new login cannot reuse proof of the previous password.
    assert c.post('/auth/login',json=dict(phone=PHONE,password=NEW)).status_code == 401  # clears stale cookie
    assert c.post('/auth/login',json=dict(phone=PHONE,password=NEW)).status_code == 200
    assert binding_verify(c,challenge).status_code == 400


def test_concurrent_binding_is_single_use(setup):
    c = logged(); challenge = binding_send(c).json['challenge']; cookie = c.get_cookie('session').value
    def verify_one(_):
        client = bot.app.test_client(); client.set_cookie('session',cookie)
        return binding_verify(client,challenge).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(verify_one,range(2))) == [200,400]


def test_mailbox_cannot_be_bound_to_two_accounts(setup):
    c = logged(); sent = binding_send(c)
    with bot.db_connection() as conn:
        conn.execute("INSERT INTO users(phone,password_hash,email,email_verified_at) VALUES ('13900000000','test',%s,NOW())",(EMAIL,))
    assert binding_verify(c,sent.json['challenge']).status_code == 409
    assert c.get('/auth/email/status').json['verified'] is False


def test_hashes_are_keyed_and_purpose_bound(setup):
    user,_ = setup; bind_existing(user); c = bot.app.test_client(); challenge = send(c).json['challenge']
    with bot.db_connection() as conn:
        row = conn.execute('SELECT challenge_hash,code_hash,grant_hash FROM email_password_recoveries').fetchone()
    assert row[0] != challenge and row[1] != '123456' and row[2] is None
    with bot.app.app_context():
        assert row[1] == recovery.code_digest('recover',challenge,EMAIL,'123456')
        assert row[1] != recovery.code_digest('bind',challenge,EMAIL,'123456')
        assert row[1] != recovery.code_digest('recover',challenge,'attacker@example.com','123456')
    token = verify(c,challenge).json['reset_token']
    with bot.db_connection() as conn:
        row = conn.execute('SELECT code_hash,grant_hash FROM email_password_recoveries').fetchone()
    assert row == (None,recovery.digest(token))


def test_schema_is_additive_idempotent(setup):
    user,_ = setup
    with bot.db_connection() as conn:
        before = conn.execute('SELECT id,phone,password_hash,session_version FROM users WHERE id=%s',(user,)).fetchone()
        with conn.cursor() as cur:
            recovery.create_schema(cur); recovery.create_schema(cur)
        assert conn.execute('SELECT id,phone,password_hash,session_version FROM users WHERE id=%s',(user,)).fetchone() == before


def test_daily_ip_and_recipient_limits(setup):
    user,calls = setup; bind_existing(user); c = bot.app.test_client()
    for _ in range(10): bot.rate_limit('email:recovery:day',PHONE,10,1440)
    assert send(c).status_code == 429
    clear_limits()
    for _ in range(20): bot.rate_limit('email:send:ip','127.0.0.1',20)
    assert send(c,'13900000000').status_code == 429
    clear_limits()
    for _ in range(10): bot.rate_limit('email:recipient:day',EMAIL,10,1440)
    limited = send(c); assert limited.status_code == 200 and not calls
    assert verify(c,limited.json['challenge']).status_code == 400


@pytest.mark.parametrize('payload',[[], 'bad', 1, None, {'phone':[]}])
def test_malformed_json(setup,payload):
    c = bot.app.test_client()
    for path in ('send','verify','reset'):
        assert c.post('/auth/recovery/'+path,json=payload).status_code == 400
    c = logged()
    for path in ('send','verify'):
        assert c.post('/auth/email/'+path,json=payload).status_code == 400


def test_stale_queued_jobs_do_not_send(setup,monkeypatch):
    user,calls = setup; bind_existing(user); jobs=[]
    monkeypatch.setattr(recovery,'submit_delivery',jobs.append)
    c = bot.app.test_client(); old = send(c).json['challenge']
    clear_limits(); latest = send(c).json['challenge']
    jobs[0](); assert not calls
    bind_existing(user,'changed@example.com')
    jobs[1](); assert not calls
    assert verify(c,old).status_code == verify(c,latest).status_code == 400


def test_binding_proof_invalid_after_password_change(setup):
    c = logged(); challenge = binding_send(c).json['challenge']
    assert c.post('/auth/change-password',json=dict(old_password=OLD,new_password=NEW)).status_code == 200
    assert c.post('/auth/login',json=dict(phone=PHONE,password=NEW)).status_code == 200
    assert binding_verify(c,challenge).status_code == 400
    assert binding_send(c,password=OLD).status_code == 401


def test_queue_full_has_same_public_response(setup,monkeypatch):
    user,calls = setup; bind_existing(user)
    monkeypatch.setattr(recovery,'submit_delivery',lambda task:False)
    c = bot.app.test_client(); bound = send(c); unknown = send(c,'13900000000')
    assert bound.status_code == unknown.status_code == 200
    assert bound.json['message'] == unknown.json['message']
    assert not calls and verify(c,bound.json['challenge']).status_code == 400



def test_real_worker_sends_without_request_context(setup,monkeypatch):
    from threading import Event
    user,calls = setup; bind_existing(user)
    started, release, done = Event(), Event(), Event()
    def slow_mail(email,code,purpose):
        started.set()
        assert release.wait(5)
        calls.append((email,code,purpose))
    monkeypatch.setattr(mail,'send_code',slow_mail)
    def submit(task):
        def run():
            try: task()
            finally: done.set()
        return REAL_SUBMIT(run)
    monkeypatch.setattr(recovery,'submit_delivery',submit)
    c = bot.app.test_client()
    try:
        response = send(c)
        assert response.status_code == 200 and started.wait(5)
        assert not done.is_set()  # response returned while SMTP is still waiting
        assert verify(c,response.json['challenge']).status_code == 400
    finally:
        release.set()
        assert done.wait(5)
    assert calls == [(EMAIL,'123456','recover')]
    assert verify(c,response.json['challenge']).status_code == 200
