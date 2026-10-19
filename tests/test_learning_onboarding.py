"""Account-backed onboarding and navigation, exercised against real Flask/PostgreSQL."""
import os
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

import bot
from learning.catalog import LEVELS
from test_learning import client, save, headers


@pytest.mark.parametrize('engine,width,height,color', [('webkit',390,844,'light'),('webkit',375,812,'dark'),('chromium',1440,1000,'light')])
def test_onboarding_profiles_navigation_and_account_changes(user, engine, width, height, color):
    playwright=pytest.importorskip('playwright.sync_api')
    server=make_server('127.0.0.1',0,bot.app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    c=client(user);c.get('/api/learning/profiles')
    try:
        with playwright.sync_playwright() as p:
            browser_type=getattr(p,engine)
            if not Path(browser_type.executable_path).exists():
                if os.getenv('CI'):pytest.fail('CI requires browser installation')
                pytest.skip('Install browser to run onboarding UI tests')
            browser=browser_type.launch()
            device=p.devices['iPhone 13'] if width<720 else {}
            options={**device,'viewport':{'width':width,'height':height},'color_scheme':color}
            context=browser.new_context(**options)
            context.add_cookies([dict(name='session',value=c.get_cookie('session').value,url=origin)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            def navigate(name):
                page.locator('#learning-menu').click()
                page.locator(f'#learning-drawer [data-learning-go="{name}"]').click()
                playwright.expect(page.locator('#title-'+name)).to_be_focused()
            def screenshot(name):
                if os.getenv('LEARNING_ONBOARDING_SCREENSHOT_DIR') and engine=='webkit':
                    folder=Path(os.environ['LEARNING_ONBOARDING_SCREENSHOT_DIR']);folder.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(folder/f'iphone-{color}-{name}.png'),full_page=name!='menu')
            page.goto(origin+'/learning#overview')
            page.get_by_label('目标分数').wait_for()
            assert page.locator('[data-learning-view]:visible').count()==1
            assert page.locator('#view-exams').is_visible()
            assert not page.locator('#view-overview').is_visible()
            assert not page.locator('#practice-section').is_visible()
            assert page.locator('#current-level').evaluate('(n)=>Object.fromEntries([...n.options].map(o=>[o.value,o.textContent]))')==LEVELS
            assert page.get_by_label('目标分数').get_attribute('step')=='0.5'
            assert page.get_by_label('目标分数').get_attribute('max')=='9'
            page.get_by_label('目标分数').fill('6.5');page.get_by_label('当前水平').select_option('advanced')
            screenshot('onboarding')
            page.locator('[data-exam="cet4"]').click()
            assert page.locator('#view-exams').is_visible()
            assert page.get_by_label('目标分数').get_attribute('step')=='1'
            assert page.get_by_label('目标分数').get_attribute('max')=='710'
            page.get_by_label('目标分数').fill('520');page.get_by_label('当前水平').select_option('intermediate')
            page.locator('[data-exam="ielts"]').click()
            assert page.get_by_label('目标分数').input_value()=='6.5'
            assert page.get_by_label('当前水平').input_value()=='advanced'
            page.locator('[data-exam="cet4"]').click()
            assert page.get_by_label('目标分数').input_value()=='520'
            assert page.get_by_label('当前水平').input_value()=='intermediate'
            assert c.get('/api/learning/profiles').json['profiles']==[]
            # A forged/direct hash cannot bypass first-use setup.
            page.evaluate("location.hash='practice'")
            playwright.expect(page.locator('#view-exams')).to_be_visible()
            page.evaluate("document.body.dataset.practiceBusy='true'")
            page.locator('#save').click()
            playwright.expect(page.locator('#save-status')).to_contain_text('请等待当前学习操作')
            assert c.get('/api/learning/profiles').json['profiles']==[]
            page.evaluate("document.body.dataset.practiceBusy='false'")
            def fail(route):route.fulfill(status=503,content_type='application/json',body='{"error":"暂时无法保存档案"}')
            page.route('**/api/learning/profiles/cet4',fail)
            page.locator('#save').click()
            playwright.expect(page.locator('#save-status')).to_have_text('暂时无法保存档案')
            assert page.locator('#view-exams').is_visible()
            assert page.get_by_label('目标分数').input_value()=='520'
            assert c.get('/api/learning/profiles').json['active_exam_type'] is None
            page.unroute('**/api/learning/profiles/cet4',fail)
            def finish_after_background_write(route):
                response=route.fetch()
                page.evaluate("document.body.dataset.hubBusy='true'")
                route.fulfill(response=response)
            page.route('**/api/learning/profiles/cet4',finish_after_background_write)
            page.locator('#save').click()
            playwright.expect(page.locator('#save-status')).to_contain_text('正在等待当前学习操作完成')
            assert page.locator('#view-exams').is_visible()
            page.evaluate("document.body.dataset.hubBusy='false'")
            playwright.expect(page.locator('#view-overview')).to_be_visible()
            page.unroute('**/api/learning/profiles/cet4',finish_after_background_write)
            playwright.expect(page.locator('#current-exam')).to_contain_text('四级')
            page.locator('#learning-metrics strong').first.wait_for()
            assert c.get('/api/learning/profiles').json['active_exam_type']=='cet4'
            screenshot('overview')
            page.locator('#learning-menu').click()
            links=page.locator('#learning-drawer nav a')
            assert links.all_text_contents()[:6]==['学习总览','考试选择','专项练习','模拟考试','智能错题本','学习记录']
            assert links.count()==9
            assert page.locator('#learning-drawer svg').count()==links.count()
            for icon in page.locator('#learning-drawer .nav-icon').all():
                assert icon.get_attribute('aria-hidden')=='true'
                assert icon.get_attribute('focusable')=='false'
                assert icon.evaluate('(n)=>getComputedStyle(n).width')=='20px'
            selected=page.locator('#learning-drawer [aria-current=page] .nav-icon')
            assert selected.evaluate('(n)=>getComputedStyle(n).color')=='rgb(23, 61, 128)'
            neutral=page.locator('#learning-drawer [data-learning-go=exams] .nav-icon')
            assert neutral.evaluate('(n)=>getComputedStyle(n).color')!='rgb(23, 61, 128)'
            screenshot('menu')
            page.keyboard.press('Escape')
            playwright.expect(page.locator('#learning-menu')).to_be_focused()
            navigate('practice')
            assert not page.locator('#exams').is_visible()
            assert not page.locator('#profile-form').is_visible()
            page.locator('#question-panel').wait_for()
            page.get_by_label('我的答案').fill('This practice draft survives visiting exam settings.')
            navigate('exams')
            assert not page.locator('#practice-section').is_visible()
            # Clicking an unsaved exam is an editor selection, not a persisted active target.
            page.locator('[data-exam="ielts"]').click()
            assert page.get_by_label('目标分数').input_value()=='6.5'
            playwright.expect(page.locator('#current-exam')).to_contain_text('四级')
            navigate('practice')
            assert page.get_by_label('我的答案').input_value()=='This practice draft survives visiting exam settings.'
            navigate('exams')
            page.locator('#save').click()
            playwright.expect(page.locator('#view-overview')).to_be_visible()
            playwright.expect(page.locator('#current-exam')).to_contain_text('雅思')
            assert len(c.get('/api/learning/profiles').json['profiles'])==2
            # Browser storage and a stale/deep link do not select the current goal.
            page.evaluate("localStorage.clear();sessionStorage.clear()")
            page.goto(origin+'/learning#practice');page.reload()
            playwright.expect(page.locator('#view-overview')).to_be_visible()
            playwright.expect(page.locator('#current-exam')).to_contain_text('雅思')
            # An independent login/device uses the same server account data.
            other_client=client(user);other_client.get('/api/learning/profiles')
            second=browser.new_context(**options)
            second.add_cookies([dict(name='session',value=other_client.get_cookie('session').value,url=origin)])
            other_page=second.new_page();other_page.goto(origin+'/learning')
            playwright.expect(other_page.locator('#view-overview')).to_be_visible()
            playwright.expect(other_page.locator('#current-exam')).to_contain_text('雅思')
            second.close()
            for view in ['overview','exams','practice','mocks','reviews','history','tasks','diagnostic']:
                navigate(view)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            # Rotating the session to another account must not post the previous account's draft.
            navigate('exams');page.get_by_label('目标分数').fill('8')
            with bot.db_connection() as conn:
                other_user=conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13900000003','test') RETURNING id").fetchone()[0]
            new_client=client(other_user);new_client.get('/api/learning/profiles')
            context.add_cookies([dict(name='session',value=new_client.get_cookie('session').value,url=origin)])
            page.locator('#save').click()
            playwright.expect(page.locator('#view-exams')).to_be_visible()
            playwright.expect(page.get_by_label('目标分数')).to_have_value('')
            assert new_client.get('/api/learning/profiles').json['profiles']==[]
            assert len(c.get('/api/learning/profiles').json['profiles'])==2
            # Logout locks private views and does not retain an editor for another account.
            page.request.post(origin+'/auth/logout')
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            playwright.expect(page.locator('#status')).to_contain_text('登录已失效')
            assert page.locator('[data-learning-view]:visible').count()==0
            # An archive outage is not treated as an empty/new account; retry recovers the saved target.
            recovery=browser.new_context(**options)
            recovery.add_cookies([dict(name='session',value=c.get_cookie('session').value,url=origin)])
            recovery_page=recovery.new_page()
            def outage(route):route.fulfill(status=503,content_type='application/json',body='{"error":"学习档案暂不可用"}')
            recovery_page.route('**/api/learning/profiles',outage)
            recovery_page.goto(origin+'/learning')
            playwright.expect(recovery_page.locator('#retry')).to_be_visible()
            assert recovery_page.locator('[data-learning-view]:visible').count()==0
            recovery_page.unroute('**/api/learning/profiles',outage)
            recovery_page.locator('#retry').click()
            playwright.expect(recovery_page.locator('#view-overview')).to_be_visible()
            playwright.expect(recovery_page.locator('#current-exam')).to_contain_text('雅思')
            recovery.close()
            assert not errors
            context.close();browser.close()
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()


@pytest.mark.parametrize('engine', ['webkit', 'chromium'])
def test_rapid_exam_saves_reload_and_password_login(user, engine):
    from werkzeug.security import generate_password_hash
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits')
    playwright = pytest.importorskip('playwright.sync_api')
    with bot.db_connection() as conn:
        conn.execute('UPDATE users SET password_hash=%s WHERE id=%s',
                     (generate_password_hash('Test-password-123'), user))
    server = make_server('127.0.0.1', 0, bot.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            browser_type = getattr(p, engine)
            if not Path(browser_type.executable_path).exists():
                if os.getenv('CI'): pytest.fail('CI requires browser installation')
                pytest.skip('Install browser to run onboarding UI tests')
            browser = browser_type.launch()
            context = browser.new_context()
            page = context.new_page()
            credentials = dict(phone='13800000000', password='Test-password-123')
            assert page.request.post(origin+'/auth/login', data=credentials).ok
            csrf = page.request.get(origin+'/api/learning/profiles').json()['csrf_token']
            # No sleeps between saves; wait only for each successful response.
            for exam, score in [('ielts', 7), ('cet4', 520), ('cet6', 600)]:
                response = page.request.put(origin+'/api/learning/profiles/'+exam,
                    data=dict(target_score=score, current_level='advanced'),
                    headers={'X-Learning-CSRF': csrf})
                assert response.ok
            for relogin in [False, True]:
                if relogin:
                    assert page.request.post(origin+'/auth/logout').ok
                    assert page.request.get(origin+'/api/learning/profiles').status == 401
                    assert page.request.post(origin+'/auth/login', data=credentials).ok
                page.goto(origin+'/learning'); page.reload()
                playwright.expect(page.locator('#view-overview')).to_be_visible()
                playwright.expect(page.locator('#current-exam')).to_contain_text('六级')
                archive = page.request.get(origin+'/api/learning/profiles').json()
                assert archive['active_exam_type'] == 'cet6'
                assert len(archive['profiles']) == 3
            browser.close()
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close()
