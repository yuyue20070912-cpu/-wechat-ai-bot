"""Browser integration against a local Flask server and temporary PostgreSQL."""
import shutil
import os
import json
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

import bot


@pytest.mark.parametrize('width,height,color', [(390, 844, 'light'), (1440, 1000, 'light'), (375, 812, 'dark')])
@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
def test_learning_browser(user, ai, width, height, color, engine):
    playwright = pytest.importorskip('playwright.sync_api')
    chromium = shutil.which('chromium') or shutil.which('google-chrome')
    server = make_server('127.0.0.1', 0, bot.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    c = bot.app.test_client()
    original_create = ai.client.responses.create
    def create(**kwargs):
        result = original_create(**kwargs)
        feedback = json.loads(result.output_text)
        feedback['summary'] = '<img src=x onerror="window.unsafeFeedback=true">'
        result.output_text = json.dumps(feedback)
        return result
    ai.client.responses.create = create
    with c.session_transaction() as s:
        s['user_id'] = user
        s['phone'] = '13800000000'
    try:
        with playwright.sync_playwright() as p:
            browser_type = getattr(p, engine)
            if engine == 'webkit' and not Path(browser_type.executable_path).exists():
                if os.getenv('CI'):
                    pytest.fail('CI requires the WebKit browser installation')
                pytest.skip('Install WebKit to run Safari engine coverage')
            if engine == 'chromium' and not chromium and not Path(p.chromium.executable_path).exists():
                pytest.skip('Install Chromium to run the browser integration tests')
            browser = browser_type.launch(**({'executable_path': chromium, 'args': ['--no-sandbox']} if engine == 'chromium' else {}))
            context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=color)
            context.add_cookies([{'name': 'session', 'value': c.get_cookie('session').value, 'url': origin}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/')
            page.locator('#sideToggle').click()
            page.get_by_role('link', name='✦ AI 伴学 →').click()
            page.get_by_role('button', name='IELTS 雅思', exact=False).wait_for()
            assert page.locator('.exam').count() == 3
            page.locator('[data-exam="ielts"]').click()
            page.get_by_label('目标分数').fill('6.5')
            page.get_by_label('当前水平').select_option('intermediate')
            page.locator('#save').click()
            page.get_by_text('学习档案已保存。', exact=True).wait_for()
            page.reload()
            page.locator('[data-exam="ielts"]').click()
            assert page.get_by_label('目标分数').input_value() == '6.5'
            page.locator('[data-exam="cet4"]').click()
            assert page.get_by_label('目标分数').input_value() == ''
            page.get_by_label('目标分数').fill('500')
            page.locator('#save').click()
            page.get_by_text('学习档案已保存。', exact=True).wait_for()
            page.get_by_label('选择训练').select_option('writing')
            page.locator('#question-title').filter(has_text='写一段').wait_for()
            page.get_by_label('我的答案').fill('I believe teamwork helps students learn from one another.')
            page.get_by_role('button', name='提交并获取 AI 反馈').click()
            page.get_by_text('反馈已保存。', exact=True).wait_for()
            assert page.locator('#feedback-content').inner_text().find('72/100') >= 0
            assert page.locator('#feedback-content img').count() == 0
            assert not page.evaluate('Boolean(window.unsafeFeedback)')
            page.get_by_role('button', name='提交并获取 AI 反馈').click()
            page.get_by_text('已恢复上次反馈，没有重复扣费。', exact=True).wait_for()
            assert len(ai.calls) == 1
            page.locator('.history > summary').click()
            playwright.expect(page.locator('#practice-history')).to_contain_text('已完成')
            page.get_by_label('选择训练').select_option('listening')
            page.locator('#question-title').filter(has_text='辨认变更').wait_for()
            assert not page.locator('#question-material').evaluate('(node) => node.open')
            page.locator('#material-label').click()
            assert page.locator('#material-text').is_visible()
            assert page.locator('audio').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
