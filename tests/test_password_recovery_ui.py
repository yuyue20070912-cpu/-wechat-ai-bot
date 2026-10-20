"""Email binding and recovery on iPhone/WebKit and desktop/Chromium."""
import os
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server
import bot
import email_delivery as mail
import password_recovery as recovery


@pytest.fixture
def recovery_site(user,monkeypatch):
    from werkzeug.security import generate_password_hash
    for key,value in dict(SMTP_HOST='smtp.example.com',SMTP_PORT='465',SMTP_USERNAME='test-only',
                          SMTP_PASSWORD='test-only',SMTP_FROM_EMAIL='noreply@example.com').items():
        monkeypatch.setenv(key,value)
    monkeypatch.setenv('SMTP_SECURITY','ssl')
    monkeypatch.setattr(recovery,'new_code',lambda:'123456')
    monkeypatch.setattr(recovery,'submit_delivery',lambda task:task())
    monkeypatch.setattr(mail,'send_code',lambda *args:None)
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits,email_password_recoveries,account_email_challenges')
        conn.execute('UPDATE users SET password_hash=%s WHERE id=%s',(generate_password_hash('Old-password-123'),user))
    server=make_server('127.0.0.1',0,bot.app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown(); thread.join(timeout=5); server.server_close()


@pytest.mark.parametrize('engine,width,theme',[('webkit',390,'light'),('webkit',390,'dark'),('chromium',1440,'light'),('chromium',1440,'dark')])
def test_binding_and_recovery_flow(recovery_site,engine,width,theme,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser_type=getattr(p,engine)
        if not Path(browser_type.executable_path).exists():
            if os.getenv('CI'): pytest.fail('CI requires recovery browsers')
            pytest.skip('Install Playwright browsers')
        browser=browser_type.launch()
        device=p.devices['iPhone 13'] if width<720 else {}
        context=browser.new_context(**{**device,'viewport':dict(width=width,height=844),'color_scheme':theme})
        page=context.new_page(); errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(recovery_site)
        page.locator('#phone').fill('13800000000'); page.locator('#password').fill('Old-password-123')
        page.locator('#loginBtn').click(); expect(page.locator('#chatArea')).to_be_visible()
        page.locator('#sideToggle').click()
        page.locator('#security').evaluate('(el)=>el.open=true')
        page.get_by_role('link',name='绑定找回密码邮箱').click()
        expect(page).to_have_url(recovery_site+'/account/email')
        expect(page.locator('#emailStatus')).to_contain_text('尚未绑定')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.get_by_label('邮箱地址',exact=True).fill('owner@example.com')
        page.get_by_label('当前密码',exact=True).fill('wrong')
        page.get_by_role('button',name='发送邮箱验证码',exact=True).click()
        expect(page.locator('#message')).to_contain_text('当前密码不正确')
        page.get_by_label('当前密码',exact=True).fill('Old-password-123')
        page.get_by_role('button',name='发送邮箱验证码',exact=True).click()
        expect(page.locator('#confirmForm')).to_be_visible()
        assert page.locator('#currentPassword').input_value()==''
        page.get_by_label('邮箱验证码',exact=True).fill('000000')
        page.get_by_role('button',name='验证并绑定邮箱',exact=True).click()
        expect(page.locator('#message')).to_contain_text('验证码不正确')
        page.get_by_label('邮箱验证码',exact=True).fill('123456')
        page.screenshot(path=str(tmp_path/f'binding-{engine}-{theme}.png'),full_page=True)
        page.get_by_role('button',name='验证并绑定邮箱',exact=True).click()
        expect(page.locator('#emailStatus')).to_contain_text('已验证绑定邮箱：owner@example.com')
        page.reload(); expect(page.locator('#emailStatus')).to_contain_text('owner@example.com')
        with bot.db_connection() as conn:
            # Advance the shared mailbox frequency window, without a real one-minute wait.
            conn.execute("UPDATE auth_rate_limits SET reset_at=NOW()-INTERVAL '1 second'")
        context.clear_cookies(); page.goto(recovery_site)
        page.get_by_role('link',name='忘记密码？').click()
        expect(page).to_have_url(recovery_site+'/forgot-password')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('input[type=email]').count()==0
        page.get_by_label('注册手机号',exact=True).fill('13800000000')
        page.get_by_role('button',name='获取验证码',exact=True).click()
        expect(page.locator('#message')).to_contain_text('若该账号已绑定并验证邮箱')
        expect(page.locator('#sendCode')).to_be_disabled()
        page.get_by_label('邮箱验证码',exact=True).fill('000000')
        page.get_by_role('button',name='验证并继续').click()
        expect(page.locator('#message')).to_contain_text('验证码不正确')
        expect(page.locator('#resetForm')).to_be_hidden()
        page.get_by_label('邮箱验证码',exact=True).fill('123456')
        page.get_by_role('button',name='验证并继续').click()
        expect(page.locator('#resetForm')).to_be_visible()
        page.get_by_label('新密码',exact=True).fill('New-password-456')
        page.get_by_label('确认新密码',exact=True).fill('New-password-789')
        page.get_by_role('button',name='重置密码',exact=True).click()
        expect(page.locator('#message')).to_contain_text('两次输入')
        page.get_by_label('确认新密码',exact=True).fill('New-password-456')
        page.screenshot(path=str(tmp_path/f'recovery-{engine}-{theme}.png'),full_page=True)
        page.get_by_role('button',name='重置密码',exact=True).click()
        expect(page.locator('#success')).to_be_visible()
        assert not errors
        context.close(); browser.close()


def test_unconfigured_ui(recovery_site,monkeypatch):
    from playwright.sync_api import sync_playwright,expect
    monkeypatch.delenv('SMTP_PASSWORD')
    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists(): pytest.skip('Install Playwright browsers')
        browser=p.chromium.launch(); page=browser.new_page()
        page.goto(recovery_site+'/forgot-password')
        page.get_by_label('注册手机号',exact=True).fill('13800000000')
        page.get_by_role('button',name='获取验证码',exact=True).click()
        expect(page.locator('#message')).to_contain_text('尚未配置完成')
        page.get_by_label('邮箱验证码',exact=True).fill('123456')
        page.get_by_role('button',name='验证并继续').click()
        expect(page.locator('#message')).to_contain_text('请先获取邮箱验证码')
        expect(page.locator('#resetForm')).to_be_hidden()
        browser.close()
