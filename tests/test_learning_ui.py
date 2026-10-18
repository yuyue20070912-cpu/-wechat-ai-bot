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


@pytest.mark.parametrize('engine', ['chromium','webkit'])
@pytest.mark.parametrize('width,color', [(390,'light'),(1440,'dark')])
def test_voice_browser_permissions_native_recording_and_fallback(user,engine,width,color,tmp_path):
    """Native recording when exposed; Linux WebKit fallback is not hardware Safari."""
    pw=pytest.importorskip('playwright.sync_api')
    server=make_server('127.0.0.1',0,bot.app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    c=bot.app.test_client()
    with c.session_transaction() as s:s['user_id']=user
    try:
        with pw.sync_playwright() as p:
            bt=getattr(p,engine);chromium=shutil.which('chromium')
            if not Path(bt.executable_path).exists() and not (engine=='chromium' and chromium):
                if os.getenv('CI'):pytest.fail('Required browser missing')
                pytest.skip('Browser not installed')
            browser=bt.launch(env={**os.environ,'TMPDIR':str(tmp_path)},**({'executable_path':chromium,'args':['--no-sandbox']} if engine=='chromium' and chromium else {}))
            context=browser.new_context(viewport={'width':width,'height':844},color_scheme=color)
            context.add_cookies([{'name':'session','value':c.get_cookie('session').value,'url':origin}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.add_init_script('''
              window.micCalls=0;window.denyMic=true;
              window.nativeRecorder=window.MediaRecorder;
              // Permission denial is exercised before construction. Restore capability below.
              if(!window.MediaRecorder)window.MediaRecorder=function(){};
              Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{
                window.micCalls++;
                if(window.denyMic)throw new DOMException('denied','NotAllowedError');
                const ctx=new AudioContext();await ctx.resume();
                const oscillator=ctx.createOscillator(),destination=ctx.createMediaStreamDestination();
                oscillator.connect(destination);oscillator.start();
                window.testMicTrack=destination.stream.getAudioTracks()[0];
                window.testMicContext=ctx;return destination.stream;
              }}});
            ''')
            page.goto(origin+'/learning');page.locator('[data-exam="ielts"]').click()
            page.locator('#voice-start').click();page.locator('#voice-part').filter(has_text='Part 1').wait_for()
            assert page.evaluate('window.micCalls')==0
            assert page.locator('#voice-record').is_disabled()
            page.locator('#voice-consent').check();page.locator('#voice-record').click()
            pw.expect(page.locator('#voice-status')).to_contain_text('麦克风权限被拒绝')
            assert page.evaluate('window.micCalls')==1
            page.locator('#voice-text-mode').click();page.locator('#voice-answer').fill('I like the peaceful park near my home.')
            page.locator('#voice-next').click();pw.expect(page.locator('#voice-part')).to_contain_text('第 2 / 7')
            page.reload();page.locator('[data-exam="ielts"]').click()
            page.locator('#voice-history option').wait_for(state='attached');page.locator('#voice-resume').click()
            pw.expect(page.locator('#voice-turns')).to_contain_text('peaceful park')
            page.locator('#voice-consent').check();page.evaluate('()=>{window.denyMic=false;window.MediaRecorder=window.nativeRecorder;}')
            page.locator('#voice-record').click()
            if page.evaluate('Boolean(window.nativeRecorder)'):
                pw.expect(page.locator('#voice-stop')).to_be_enabled()
                page.wait_for_function('document.querySelector("#voice-clock").textContent.includes("录音 1")')
                page.locator('#voice-stop').click();pw.expect(page.locator('#voice-preview')).to_be_visible()
                page.wait_for_function('document.querySelector("#voice-preview").readyState>=1')
                assert page.evaluate('window.testMicTrack.readyState')=='ended'
                assert page.locator('#voice-preview').evaluate('(a)=>a.error===null')
            else:
                pw.expect(page.locator('#voice-status')).to_contain_text('浏览器或当前连接不支持录音')
                assert page.evaluate('window.micCalls')==0
            assert page.locator('#voice-transcribe').is_disabled()
            # Both accent assets exercise native browser MP3 decoding and user gesture playback.
            for accent in ('gb','us'):
                page.locator('#voice-accent').select_option(accent)
                page.locator('#voice-example').evaluate('(a)=>{a.preload="metadata";a.load();}')
                try:
                    page.wait_for_function('document.querySelector("#voice-example").readyState>=1',timeout=5000)
                except Exception:
                    pytest.fail(str(page.locator('#voice-example').evaluate('(a)=>({src:a.currentSrc,network:a.networkState,ready:a.readyState,error:a.error?.message,canPlay:a.canPlayType("audio/mpeg")})')))
                page.locator('#voice-example').click()
                assert page.locator('#voice-example').evaluate('(a)=>a.error===null')
            page.locator('#voice-clear').click();assert page.locator('#voice-preview').is_hidden()
            page.locator('[data-exam="cet4"]').click()
            page.locator('#voice-listening-options button').first.wait_for()
            page.locator('#voice-listening-options button').first.click()
            page.locator('#mock-material audio').wait_for()
            page.wait_for_function('document.querySelector("#mock-material audio").readyState>=1')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors
            context.close();browser.close()
    finally:server.shutdown();thread.join(timeout=5)


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
            assert page.locator('#practice-section audio').count() == 0
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
