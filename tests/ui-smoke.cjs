// Run with Node and Playwright installed. All requests are intercepted locally.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.TEST_PYTHON || path.join(root, '.venv/bin/python');
const html = execFileSync(python, ['-c', 'import bot; print(bot.home())'], { cwd: root, encoding: 'utf8' });

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
  try {
    for (const viewport of [{ width: 390, height: 844 }, { width: 375, height: 667 }, { width: 1280, height: 900 }]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      let chatCount = 0;
      let delayedReply;
      let delayChat = false;
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        const respond = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
        if (url.pathname.startsWith('/static/')) {
          return route.fulfill({ path: path.join(root, url.pathname) });
        }
        if (url.pathname === '/app-icon') return route.fulfill({ path: path.join(root, 'C8671EEC-8C80-4721-B2CD-732CAE507F1C.png') });
        if (url.pathname === '/auth/me') return respond({ authenticated: false });
        if (url.pathname === '/auth/login') return respond({ phone: '13800000000' });
        if (url.pathname === '/auth/logout') return respond({ ok: true });
        if (url.pathname === '/account/usage') return respond({ plan: 'free', remaining: 4, limit: 5 });
        if (url.pathname === '/membership/plans') return respond({ payments_enabled: false, plans: [] });
        if (url.pathname === '/chat') {
          chatCount++;
          if (delayChat) {
            delayedReply = () => respond({ reply: '上一账号的私密回复', remaining: 3 });
            return;
          }
          return respond({ reply: '测试回复 <script>window.xss=true</script>', remaining: 4 });
        }
        throw Error('Unexpected request: ' + url.pathname);
      });
      await page.goto('https://fishyeah.test');
      if (process.env.UI_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.UI_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.UI_SCREENSHOT_DIR, `auth-${viewport.width}.png`) });
      }
      await page.fill('#phone', '13800000000');
      await page.fill('#password', 'safe-password123');
      await page.click('#loginBtn');
      await page.waitForSelector('#chatArea:not(.hidden)');
      assert.equal(await page.locator('#createOrderBtn').isDisabled(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.fill('#message', '你好');
      await page.click('#sendBtn');
      await page.waitForFunction(() => document.getElementById('chat').textContent.includes('测试回复'));
      assert.equal(chatCount, 1);
      assert.equal(await page.evaluate(() => window.xss), undefined);
      await page.click('#newChatBtn');
      assert.equal(await page.locator('#chat').textContent(), '你好，我是 fishyeah Chat。有什么可以帮你？');
      delayChat = true;
      await page.fill('#message', '延迟请求');
      await page.click('#sendBtn');
      await page.waitForFunction(() => document.getElementById('sendBtn').disabled);
      assert.equal(await page.locator('#newChatBtn').isDisabled(), true);
      await page.click('#sideToggle');
      await page.click('#logoutBtn');
      await page.waitForSelector('#auth:not(.hidden)');
      assert.ok(delayedReply);
      const received=page.waitForResponse(r => new URL(r.url()).pathname === "/chat");
      await delayedReply();
      await (await received).finished();
      await page.waitForTimeout(150);
      await page.waitForFunction(() => !document.getElementById('chat').textContent.includes('延迟请求'));
      assert.equal(await page.locator('#chat').textContent(), '你好，我是 fishyeah Chat。有什么可以帮你？');
      assert.deepEqual(errors, []);
      console.log(`UI smoke passed: ${viewport.width}x${viewport.height}`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
