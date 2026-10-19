"""Real HTTP request lifecycle during document replacement (no mocked fetch)."""
from pathlib import Path
import os
import json
from threading import Event, Lock, Thread

import pytest
from werkzeug.serving import make_server

import bot
from test_learning import client, headers, save


@pytest.mark.parametrize('engine', ['webkit', 'chromium'])
def test_reload_with_pending_learning_reads(user, engine):
    playwright = pytest.importorskip('playwright.sync_api')
    c = client(user)
    assert save(c, headers=headers(c)).status_code == 200
    paths = ['/api/learning/mocks', '/api/learning/questions']
    waiting = {path: Event() for path in paths}
    release = Event()
    mutex = Lock()
    original = bot.app.wsgi_app

    def delayed(environ, start_response):
        path = environ['PATH_INFO']
        hold = False
        with mutex:
            if path in waiting and not waiting[path].is_set():
                waiting[path].set(); hold = True
        if hold:
            assert release.wait(10), 'Test failed to release pending HTTP response'
        return original(environ, start_response)

    server = make_server('127.0.0.1', 0, delayed, threaded=True)
    server.daemon_threads = False  # server_close joins released request handlers.
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            browser_type = getattr(p, engine)
            if not Path(browser_type.executable_path).exists():
                if os.getenv('CI'): pytest.fail('CI requires browser installation')
                pytest.skip('Browser not installed')
            browser = browser_type.launch()
            context = browser.new_context()
            context.add_cookies([dict(name='session', value=c.get_cookie('session').value, url=origin)])
            page = context.new_page()
            errors = []; requests = []
            page.on('pageerror', lambda error: errors.append(dict(message=error.message, stack=error.stack)))
            page.on('requestfailed', lambda r: requests.append(dict(url=r.url, failure=r.failure)))
            page.goto(origin+'/learning')
            for event in waiting.values():
                assert event.wait(5), 'Initial background request not started'
            page.reload(wait_until='commit')
            release.set()
            page.wait_for_function("document.querySelector('#question-title').textContent.length > 0")
            page.locator('#mock-template option').first.wait_for(state='attached')
            assert not errors, dict(errors=errors, requests=requests)
            def unavailable(message):
                return lambda route: route.fulfill(status=503, content_type='application/json',
                    body=json.dumps(dict(error=message)))
            for path, message in [('questions', 'Question read unavailable'), ('mocks', 'Mock history unavailable')]:
                page.route('**/api/learning/'+path+'?*', unavailable(message))
            page.reload()
            playwright.expect(page.locator('#practice-status')).to_have_text('Question read unavailable')
            playwright.expect(page.locator('#mock-status')).to_have_text('Mock history unavailable')
            assert not errors, dict(errors=errors, requests=requests)
            # The expiry callback must return its history refresh promise, so a
            # genuine network failure is handled rather than escaping as pageerror.
            from test_mocks import start, expire
            page.unroute('**/api/learning/mocks?*')
            session = start(c).json['session']
            page.clock.install()
            page.reload()
            playwright.expect(page.locator('#view-overview')).to_be_visible()
            page.evaluate("document.dispatchEvent(new CustomEvent('learning:navigate',{detail:'mocks'}))")
            page.locator('#mock-resume').click()
            page.locator('#mock-questions input').first.wait_for()
            expire(session)
            page.route('**/api/learning/mocks?*', unavailable('Mock history unavailable'))
            page.clock.fast_forward(3600000)
            playwright.expect(page.locator('#mock-status')).to_have_text('计时已结束，待联网后恢复服务器报告；草稿不会延长考试时间。')
            assert not errors, dict(errors=errors, requests=requests)
            context.close(); browser.close()
    finally:
        release.set(); server.shutdown(); thread.join(timeout=5); server.server_close()


@pytest.mark.parametrize('engine', ['webkit', 'chromium'])
def test_pagehide_cancels_startup_reads_before_they_can_fan_out(user, engine):
    playwright = pytest.importorskip('playwright.sync_api')
    c = client(user)
    assert save(c, headers=headers(c)).status_code == 200
    paths = ['/api/learning/practice', '/api/learning/mock-catalog/ielts']
    waiting = {path: Event() for path in paths}
    release = Event()
    original = bot.app.wsgi_app

    def delayed(environ, start_response):
        path = environ['PATH_INFO']
        if path in waiting:
            waiting[path].set()
            assert release.wait(10)
        return original(environ, start_response)

    server = make_server('127.0.0.1', 0, delayed, threaded=True)
    server.daemon_threads = False  # server_close joins released request handlers.
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            browser_type = getattr(p, engine)
            if not Path(browser_type.executable_path).exists():
                if os.getenv('CI'): pytest.fail('CI requires browser installation')
                pytest.skip('Browser not installed')
            browser = browser_type.launch()
            context = browser.new_context()
            context.add_cookies([dict(name='session', value=c.get_cookie('session').value, url=origin)])
            page = context.new_page()
            # Observe actual fetch signals; no mock responses or rejected fetches.
            page.add_init_script("""{
                const original = window.fetch;
                window.startupReads = [];
                window.fetch = (...args) => {
                    if (['/api/learning/practice','/api/learning/mock-catalog/ielts'].includes(args[0]))
                        {
                            const read = {signal:args[1]?.signal, settled:false};
                            startupReads.push(read);
                            return original(...args).then(
                                value => { read.settled=true; return value; },
                                error => { read.settled=true; throw error; });
                        }
                    return original(...args);
                };
            }""")
            errors = []; downstream = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda r: downstream.append(r.url) if '/api/learning/questions?' in r.url or '/api/learning/mocks?' in r.url else None)
            page.goto(origin+'/learning')
            for event in waiting.values(): assert event.wait(5)
            # Keep the old DOM inspectable while exercising the real teardown event.
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
            assert page.evaluate('startupReads.length===2 && startupReads.every(r=>r.signal?.aborted)')
            page.wait_for_function('startupReads.every(r=>r.settled)')
            release.set()
            assert not downstream
            assert not errors
            context.close(); browser.close()
    finally:
        release.set(); server.shutdown(); thread.join(timeout=5); server.server_close()
