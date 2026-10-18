"""Browser integration against a local Flask server and temporary PostgreSQL."""
import shutil
import os
import json
import re
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

import bot
from learning.diagnostics import TESTS


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
            page.get_by_role('button',name='加入错题本',exact=True).click()
            page.get_by_role('button',name='已加入错题本',exact=True).wait_for()
            page.get_by_label('选择训练').select_option('listening')
            page.locator('#question-title').filter(has_text='辨认变更').wait_for()
            assert not page.locator('#question-material').evaluate('(node) => node.open')
            page.locator('#material-label').click()
            assert page.locator('#material-text').is_visible()
            assert page.locator('audio').count() == 0
            page.locator('#diagnostic-details > summary').click()
            for q in TESTS['cet4']:
                page.locator(f'input[name="diag-{q["id"]}"][value="{(q["correct_index"]+1)%3}"]').check()
            page.locator('#diagnostic-submit').click()
            page.get_by_text('诊断已保存，错题已加入复习；本次未调用付费 AI。',exact=True).wait_for()
            playwright.expect(page.locator('#diagnostic-summary')).to_contain_text('0/6 题正确')
            assert page.locator('#diagnostic-ai').is_disabled()
            page.locator('#create-plan').click()
            page.get_by_text('今日计划已保存。',exact=True).wait_for()
            checkbox=page.locator('#daily-tasks input[type=checkbox]').first
            task_name=checkbox.get_attribute('aria-label')
            checkbox.check()
            page.get_by_text('任务状态已保存，重新登录后仍可恢复。',exact=True).wait_for()
            review_data=page.request.get(origin+'/api/learning/reviews/cet4').json()
            item=next(item for item in review_data['items'] if item['source_type']=='diagnostic')
            card=page.locator('.review-card').filter(has=page.locator('summary',has_text='客观错题')).first
            # The list is sorted by due time/key, as returned by the API.
            card.locator(':scope > summary').click()
            card.get_by_label('本次复习答案').select_option(str(item['content']['options'].index(item['content']['reference'])))
            card.get_by_role('button',name='提交复习答案').click()
            page.get_by_text('复习已记录，下一次时间已安排。',exact=True).wait_for()
            page.reload()
            page.locator('[data-exam="cet4"]').click()
            playwright.expect(page.get_by_role('checkbox',name=task_name,exact=True)).to_be_checked()
            playwright.expect(page.locator('#diagnostic-summary')).to_contain_text('0/6 题正确')
            assert len(ai.calls)==1
            playwright.expect(page.locator('#learning-timezone')).to_have_value('Asia/Shanghai')
            playwright.expect(page.locator('#screening-group')).to_contain_text('题组 2/3')
            page.locator('#learning-timezone').fill('America/New_York')
            page.get_by_role('button',name='保存时区',exact=True).click()
            page.get_by_text('时区已保存，今日计划按新时区恢复。',exact=True).wait_for()
            page.locator('#mock-template').select_option('cet4-reading-a-short-v1')
            page.locator('#mock-start').click()
            page.locator('input[name="mock-r1"][value="1"]').wait_for()
            page.locator('input[name="mock-r1"][value="1"]').check()
            page.get_by_text('答案已同步到服务器。',exact=True).wait_for()
            active=page.request.get(origin+'/api/learning/mocks?exam=cet4').json()['active_id']
            before=page.request.get(origin+'/api/learning/mocks/'+active).json()['session']
            page.reload();page.locator('[data-exam="cet4"]').click()
            playwright.expect(page.locator('#learning-timezone')).to_have_value('America/New_York')
            page.locator('#mock-resume').click()
            playwright.expect(page.locator('input[name="mock-r1"][value="1"]')).to_be_checked()
            assert page.request.get(origin+'/api/learning/mocks/'+active).json()['session']['deadline']==before['deadline']
            page.once('dialog',lambda dialog:dialog.accept())
            page.locator('#mock-submit').click()
            page.get_by_text('已交卷，报告已保存。',exact=True).wait_for()
            playwright.expect(page.locator('#mock-report-content')).to_contain_text('25%')
            page.locator('#mock-template').select_option('cet4-writing-a-short-v1')
            page.locator('#mock-start').click();page.get_by_label('模考答案',exact=True).wait_for()
            context.set_offline(True)
            draft='Libraries help students learn together. I support public funding.'
            page.get_by_label('模考答案',exact=True).fill(draft)
            page.locator('#mock-status').filter(has_text='同步未确认').wait_for()
            context.set_offline(False);page.reload();page.locator('[data-exam="cet4"]').click()
            page.locator('#mock-resume').click()
            playwright.expect(page.get_by_label('模考答案',exact=True)).to_have_value(draft)
            page.locator('#mock-status').filter(has_text=re.compile('已恢复服务器答案|答案已同步到服务器')).wait_for()
            page.once('dialog',lambda dialog:dialog.accept());page.locator('#mock-submit').click()
            page.get_by_text('已交卷，报告已保存。',exact=True).wait_for()
            assert page.locator('#mock-report-content button').is_disabled()
            assert len(ai.calls)==1  # mock lifecycle and recovery did not trigger paid grading
            page.locator('#mock-template').select_option('cet4-listening-a-short-v1')
            page.locator('#mock-start').click()
            page.locator('#mock-material audio').wait_for()
            page.wait_for_function('document.querySelector("#mock-material audio")?.readyState >= 1')
            assert page.locator('#mock-material audio').evaluate('(audio)=>audio.error===null')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
