"""Browser integration against a local Flask server and temporary PostgreSQL."""
import shutil
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

import bot


@pytest.mark.parametrize('width,height,color', [(390, 844, 'light'), (1440, 1000, 'light'), (375, 812, 'dark')])
def test_learning_browser(user, width, height, color):
    playwright = pytest.importorskip('playwright.sync_api')
    chromium = shutil.which('chromium') or shutil.which('google-chrome')
    server = make_server('127.0.0.1', 0, bot.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    c = bot.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = user
        s['phone'] = '13800000000'
    try:
        with playwright.sync_playwright() as p:
            if not chromium and not Path(p.chromium.executable_path).exists():
                pytest.skip('Install Chromium to run the browser integration tests')
            browser = p.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
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
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
