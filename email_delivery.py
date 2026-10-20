"""SMTP delivery with mandatory TLS; all credentials are server environment values."""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

ENV_KEYS = ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM_EMAIL')


def normalize_email(value):
    # A deliberately bounded ASCII mailbox format; reject header injection and display names.
    if not isinstance(value, str) or len(value) > 254:
        return None
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value):
        return None
    local, domain = value.rsplit('@', 1)
    if (len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in value
            or any(label.startswith('-') or label.endswith('-') or len(label) > 63 for label in domain.split('.'))):
        return None
    return value


def configured():
    return (all(os.getenv(key, '').strip() for key in ENV_KEYS)
            and os.environ['SMTP_PORT'].isdigit() and 1 <= int(os.environ['SMTP_PORT']) <= 65535
            and os.getenv('SMTP_SECURITY', 'ssl') in ('ssl', 'starttls')
            and normalize_email(os.environ['SMTP_FROM_EMAIL']) is not None
            and not any(c in os.environ['SMTP_HOST'] for c in '\r\n'))


def send_code(recipient, code, purpose):
    if not configured() or not normalize_email(recipient) or not re.fullmatch(r'[0-9]{6}', code):
        raise ValueError('Invalid mail configuration or message')
    message = EmailMessage()
    message['From'] = normalize_email(os.environ['SMTP_FROM_EMAIL'])
    message['To'] = recipient
    message['Subject'] = 'fishyeah Chat · ' + ('邮箱绑定验证码' if purpose == 'bind' else '找回密码验证码')
    message['Date'] = formatdate(localtime=False, usegmt=True)
    message['Message-ID'] = make_msgid(domain=message['From'].rsplit('@', 1)[1])
    action = '绑定邮箱' if purpose == 'bind' else '重置密码'
    message.set_content(f'你正在{action}。\n\n验证码：{code}\n\n验证码5分钟内有效，仅可使用一次。请勿向任何人透露。\n如果不是你本人操作，请忽略本邮件。')
    host, port = os.environ['SMTP_HOST'], int(os.environ['SMTP_PORT'])
    context = ssl.create_default_context()
    if os.getenv('SMTP_SECURITY', 'ssl') == 'ssl':
        client = smtplib.SMTP_SSL(host, port, timeout=8, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=8)
    with client:
        if os.getenv('SMTP_SECURITY', 'ssl') == 'starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
        if client.send_message(message):
            raise RuntimeError('Mail delivery refused')
