"""Live themes and persisted appearance settings against real Flask/PostgreSQL."""
import os
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

import bot
from werkzeug.security import generate_password_hash


@pytest.fixture
def chat_site(user):
    with bot.db_connection() as conn:
        conn.execute('UPDATE users SET password_hash=%s WHERE id=%s', (generate_password_hash('Theme-test-123'), user))
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits')
    c = bot.app.test_client()
    with c.session_transaction() as session:
        session.update(user_id=user, phone='13800000000')
    server = make_server('127.0.0.1', 0, bot.app, threaded=True)
    server.daemon_threads = False
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}', c.get_cookie('session').value
    server.shutdown(); thread.join(timeout=5); server.server_close()


def launch(p, engine):
    browser_type = getattr(p, engine)
    if not Path(browser_type.executable_path).exists():
        if os.getenv('CI'): pytest.fail('CI requires theme test browsers')
        pytest.skip('Install Playwright browsers')
    return browser_type.launch()


def select_theme(page, value):
    if 'open' not in (page.locator('#fishSide').get_attribute('class') or ''):
        page.locator('#sideToggle').click()
    page.locator('#appearance').evaluate('(n)=>n.open=true')
    page.locator(f'input[name="chat-theme"][value="{value}"]').check()


def readable(page):
    ratios = page.evaluate(r"""() => {
      const luminance = color => {
        const rgb = color.match(/[\d.]+/g).slice(0,3).map(Number).map(v => {
          v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
        });
        return rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722;
      };
      return ['body','#chat .from-ai .msg','#chat .from-user .msg','#message',
        '#sendBtn','#newChatBtn','#memberPlan','#orderResult','.member-card.featured'].map(selector => {
        const style = getComputedStyle(document.querySelector(selector));
        const a = luminance(style.color), b = luminance(style.backgroundColor);
        return [selector, (Math.max(a,b)+.05)/(Math.min(a,b)+.05)];
      });
    }""")
    assert all(ratio >= 4.5 for _, ratio in ratios), ratios


@pytest.mark.parametrize('engine,width,height', [('webkit',390,844),('webkit',1440,1000),('chromium',1440,1000)])
def test_chat_theme_system_manual_reload_and_main_features(chat_site, engine, width, height):
    playwright = pytest.importorskip('playwright.sync_api')
    origin, cookie = chat_site
    with playwright.sync_playwright() as p:
        browser = launch(p,engine)
        device = p.devices['iPhone 13'] if width < 720 else {}
        context = browser.new_context(**{**device,'viewport':dict(width=width,height=height),'color_scheme':'dark'})
        context.add_cookies([dict(name='session',value=cookie,url=origin)])
        page=context.new_page(); errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        if width < 720:
            page.add_init_script("Object.defineProperty(navigator,'standalone',{value:true})")
        page.goto(origin)
        playwright.expect(page.locator('#chatArea')).to_be_visible()
        root=page.locator('html')
        playwright.expect(root).to_have_attribute('data-theme','dark')
        assert page.evaluate("localStorage.getItem('fishyeah-chat-theme')") is None
        assert page.locator('#themeBtn, #themeMenu').count() == 0
        assert '改变风格' not in page.locator('body').inner_text()
        assert page.locator('.ui-actions button').count() == 1
        assert page.locator('#fishSide #appearance').count() == 1
        page.route('**/chat',lambda route:route.fulfill(content_type='application/x-ndjson',
            body='{"type":"ready"}\n{"type":"delta","text":"A theme test reply."}\n{"type":"done"}\n'))
        page.get_by_label('输入你的问题').fill('A theme test question.')
        page.get_by_label('发送消息',exact=True).click()
        playwright.expect(page.locator('#chat .from-ai .msg').last).to_have_text('A theme test reply.')
        page.get_by_label('输入你的问题').fill('Unsaved draft stays here.')
        page.evaluate('window.themeDocumentMarker=42')
        for color in ['light','dark']:
            page.emulate_media(color_scheme=color)
            playwright.expect(root).to_have_attribute('data-theme',color)
            assert page.evaluate('window.themeDocumentMarker') == 42
            assert page.get_by_label('输入你的问题').input_value() == 'Unsaved draft stays here.'
            assert page.evaluate('getComputedStyle(document.documentElement).colorScheme') == color
            assert page.locator('meta[name=theme-color]').get_attribute('content') == ('#141416' if color=='dark' else '#f5f5f7')
            if os.getenv('CHAT_THEME_SCREENSHOT_DIR') and engine=='webkit' and width<720:
                folder=Path(os.environ['CHAT_THEME_SCREENSHOT_DIR']);folder.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(folder/f'{width}-{color}-chat.png'))
            page.locator('#sideToggle').click()
            page.locator('#membership').evaluate('(n)=>n.open=true')
            page.locator('#appearance').evaluate('(n)=>n.open=true')
            page.wait_for_function("Math.abs(document.getElementById('fishSide').getBoundingClientRect().left)<1")
            assert page.locator('#appearance summary svg').is_visible()
            readable(page)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if os.getenv('CHAT_THEME_SCREENSHOT_DIR') and engine=='webkit':
                folder=Path(os.environ['CHAT_THEME_SCREENSHOT_DIR']);folder.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(folder/f'{width}-{color}.png'))
            page.locator('#sideToggle').click()
        for choice,opposite in [('light','dark'),('dark','light')]:
            select_theme(page,choice)
            page.emulate_media(color_scheme=opposite)
            playwright.expect(root).to_have_attribute('data-theme',choice)
            page.reload()
            playwright.expect(page.locator('#chatArea')).to_be_visible()
            playwright.expect(root).to_have_attribute('data-theme',choice)
            assert page.evaluate("localStorage.getItem('fishyeah-chat-theme')") == choice
        select_theme(page,'system')
        page.emulate_media(color_scheme='dark')
        playwright.expect(root).to_have_attribute('data-theme','dark')
        page.reload()
        playwright.expect(root).to_have_attribute('data-theme-preference','system')
        page.emulate_media(color_scheme='light')
        playwright.expect(root).to_have_attribute('data-theme','light')
        second=context.new_page();second.goto(origin)
        select_theme(page,'dark')
        playwright.expect(second.locator('html')).to_have_attribute('data-theme','dark')
        # Closing/reopening a page uses the saved choice; no account/payment mutation.
        second.close(); reopened=context.new_page();reopened.goto(origin)
        playwright.expect(reopened.locator('html')).to_have_attribute('data-theme','dark')
        page.locator('#logoutBtn').click()
        playwright.expect(page.locator('#auth')).to_be_visible()
        playwright.expect(root).to_have_attribute('data-theme','dark')
        page.locator('#phone').fill('13800000000');page.locator('#password').fill('Theme-test-123')
        page.locator('#loginBtn').click()
        playwright.expect(page.locator('#chatArea')).to_be_visible()
        playwright.expect(page.locator('#createOrderBtn')).to_have_text('支付尚未开放')
        page.locator('#membership > summary').click()
        page.locator('#ordersBtn').click()
        playwright.expect(page.locator('#orderResult')).to_have_text('暂无订单')
        page.locator('#sideToggle').click();page.locator('#newChatBtn').click()
        playwright.expect(page.locator('#chat')).to_contain_text('你好，我是 fishyeah Chat')
        assert not errors
        context.close();browser.close()


@pytest.mark.parametrize('engine',['webkit','chromium'])
def test_chat_theme_unavailable_storage_and_legacy_safari_listener(chat_site,engine):
    playwright=pytest.importorskip('playwright.sync_api');origin,cookie=chat_site
    with playwright.sync_playwright() as p:
        browser=launch(p,engine);context=browser.new_context(color_scheme='dark')
        context.add_cookies([dict(name='session',value=cookie,url=origin)])
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script("""{
          Storage.prototype.getItem=Storage.prototype.setItem=()=>{throw new DOMException('Unavailable','SecurityError');};
          const match=window.matchMedia.bind(window);
          window.matchMedia=query=>{const result=match(query);Object.defineProperty(result,'addEventListener',{value:undefined});return result;};
        }""")
        page.goto(origin)
        playwright.expect(page.locator('#chatArea')).to_be_visible()
        playwright.expect(page.locator('html')).to_have_attribute('data-theme','dark')
        page.emulate_media(color_scheme='light')
        playwright.expect(page.locator('html')).to_have_attribute('data-theme','light')
        select_theme(page,'dark');page.emulate_media(color_scheme='light')
        playwright.expect(page.locator('html')).to_have_attribute('data-theme','dark')
        select_theme(page,'system')
        playwright.expect(page.locator('html')).to_have_attribute('data-theme','light')
        assert not errors
        context.close();browser.close()


def test_chat_theme_legacy_migration_and_new_choice_priority(chat_site):
    playwright=pytest.importorskip('playwright.sync_api');origin,_=chat_site
    with playwright.sync_playwright() as p:
        browser=launch(p,'webkit')
        for legacy,expected in [({'theme':'dark'},'dark'),({'fishyeah-theme':'light'},'light'),
            ({'theme':'auto'},'system'),({'darkMode':'true'},'dark'),({'darkMode':'false'},'light'),
            ({'theme':'garbage'},'system'),({'theme':'dark','fishyeah-chat-theme':'light'},'light')]:
            context=browser.new_context(color_scheme='dark');page=context.new_page()
            # Set old preferences once, before loading the new implementation.
            page.goto(origin+'/health')
            page.evaluate('(values)=>Object.entries(values).forEach(([k,v])=>localStorage.setItem(k,v))',legacy)
            page.goto(origin)
            playwright.expect(page.locator('html')).to_have_attribute('data-theme-preference',expected)
            assert page.locator('#auth').is_visible()
            page.reload()
            playwright.expect(page.locator('html')).to_have_attribute('data-theme-preference',expected)
            if expected != 'system':
                assert page.evaluate("localStorage.getItem('fishyeah-chat-theme')") == expected
            context.close()
        browser.close()
