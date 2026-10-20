import ssl

import pytest
import email_delivery as mail
import password_recovery as recovery


@pytest.fixture
def smtp_config(monkeypatch):
    for key,value in dict(SMTP_HOST='smtp.example.com',SMTP_PORT='465',SMTP_USERNAME='test-user',
                          SMTP_PASSWORD='test-only-password',SMTP_FROM_EMAIL='noreply@example.com').items():
        monkeypatch.setenv(key,value)
    monkeypatch.setenv('SMTP_SECURITY','ssl')


@pytest.mark.parametrize('security',['ssl','starttls'])
def test_smtp_auth_tls_and_message(smtp_config,monkeypatch,security):
    monkeypatch.setenv('SMTP_SECURITY',security)
    if security == 'starttls': monkeypatch.setenv('SMTP_PORT','587')
    actions=[]
    class FakeSMTP:
        def __init__(self,host,port,timeout,context=None):
            assert host == 'smtp.example.com' and timeout == 8
            assert port == (465 if security == 'ssl' else 587)
            if context: assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            actions.append('connect')
        def __enter__(self): return self
        def __exit__(self,*args): actions.append('close')
        def ehlo(self): actions.append('ehlo')
        def starttls(self,context):
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            actions.append('tls')
        def login(self,username,password):
            assert username == 'test-user' and password == 'test-only-password'
            actions.append('login')
        def send_message(self,message):
            assert message['From'] == 'noreply@example.com' and message['To'] == 'owner@example.com'
            assert '找回密码' in str(message['Subject'])
            assert '012345' in message.get_content() and '5分钟' in message.get_content()
            assert 'test-only-password' not in message.as_string()
            actions.append('send'); return {}
    monkeypatch.setattr(mail.smtplib,'SMTP_SSL',FakeSMTP)
    monkeypatch.setattr(mail.smtplib,'SMTP',FakeSMTP)
    mail.send_code('owner@example.com','012345','recover')
    assert actions.index('login') < actions.index('send')
    if security == 'starttls': assert actions.index('tls') < actions.index('login')


def test_tls_failure_never_authenticates(smtp_config,monkeypatch):
    monkeypatch.setenv('SMTP_SECURITY','starttls'); actions=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def ehlo(self): pass
        def starttls(self,context): raise RuntimeError('TLS unavailable')
        def login(self,*args): actions.append('login')
    monkeypatch.setattr(mail.smtplib,'SMTP',FakeSMTP)
    with pytest.raises(RuntimeError): mail.send_code('owner@example.com','012345','bind')
    assert not actions


def test_recipient_rejection_not_treated_as_sent(smtp_config,monkeypatch):
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def login(self,*args): pass
        def send_message(self,message): return {'owner@example.com':(550,b'refused')}
    monkeypatch.setattr(mail.smtplib,'SMTP_SSL',FakeSMTP)
    with pytest.raises(RuntimeError): mail.send_code('owner@example.com','123456','bind')


@pytest.mark.parametrize('key,value',[('SMTP_PASSWORD',''),('SMTP_PORT','bad'),('SMTP_PORT','65536'),
                                     ('SMTP_FROM_EMAIL','Header\r\nBcc: a@example.com'),('SMTP_SECURITY','none')])
def test_config_is_fail_closed(smtp_config,monkeypatch,key,value):
    monkeypatch.setenv(key,value); assert not mail.configured()
    with pytest.raises(ValueError): mail.send_code('owner@example.com','123456','recover')


def test_codes_six_digits_include_leading_zeros(monkeypatch):
    monkeypatch.setattr(recovery,'randbelow',lambda bound:0)
    assert recovery.new_code() == '000000'
    monkeypatch.setattr(recovery,'randbelow',lambda bound:999999)
    assert recovery.new_code() == '999999'


def test_delivery_queue_is_bounded_and_releases_slots(monkeypatch):
    from threading import BoundedSemaphore
    tasks=[]
    class Pool:
        def submit(self,task): tasks.append(task)
    monkeypatch.setattr(recovery,'_delivery_slots',BoundedSemaphore(1))
    monkeypatch.setattr(recovery,'_delivery_pool',Pool())
    called=[]
    assert recovery.submit_delivery(lambda:called.append(True))
    assert not recovery.submit_delivery(lambda:called.append(False))
    tasks.pop()(); assert called == [True]
    assert recovery.submit_delivery(lambda:None)
    tasks.pop()()
