'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let challenge = '', expiresAt = 0, cooldownUntil = 0, busy = false;
  function message(text, error = false) {
    $('message').textContent = text; $('message').dataset.error = String(error);
  }
  async function api(action, body) {
    const response = await fetch('/auth/email/' + action, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin',
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || '邮箱服务暂时不可用，请稍后重试');
    return data;
  }
  function showStatus(email) {
    $('emailStatus').textContent = email ? `已验证绑定邮箱：${email}` : '尚未绑定邮箱。绑定后可通过邮箱找回密码。';
  }
  api('status').then(data => showStatus(data.email)).catch(e => message(e.message, true));
  function tick() {
    const seconds = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
    $('sendBinding').disabled = busy || seconds > 0;
    $('sendBinding').textContent = seconds ? `${seconds}秒后可重新发送` : '发送邮箱验证码';
    if (challenge) {
      const left = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      $('bindingExpiry').textContent = left ? `验证码剩余 ${Math.floor(left / 60)}分${left % 60}秒有效` : '验证码已过期，请重新输入当前密码并获取。';
    }
  }
  setInterval(tick, 1000);
  $('bindForm').onsubmit = async event => {
    event.preventDefault();
    if (busy || Date.now() < cooldownUntil) return;
    busy = true; tick(); challenge = '';
    $('email').readOnly = $('currentPassword').readOnly = true;
    try {
      const data = await api('send', {email: $('email').value.trim(), current_password: $('currentPassword').value});
      challenge = data.challenge; expiresAt = Date.now() + data.expires_in * 1000;
      cooldownUntil = Date.now() + data.retry_after * 1000;
      $('bindForm').hidden = true; $('confirmForm').hidden = false;
      message(data.message); $('bindingCode').focus();
    } catch (e) { message(e.message, true); }
    finally {
      $('currentPassword').value = '';
      $('email').readOnly = $('currentPassword').readOnly = false;
      busy = false; tick();
    }
  };
  $('confirmForm').onsubmit = async event => {
    event.preventDefault();
    if (!challenge || busy) return;
    busy = true; $('confirmBinding').disabled = $('editEmail').disabled = true;
    try {
      const data = await api('verify', {challenge, code: $('bindingCode').value.trim()});
      challenge = ''; $('bindingCode').value = ''; $('confirmForm').hidden = true;
      $('bindForm').hidden = false; showStatus(data.email); message(data.message);
    } catch (e) { message(e.message, true); }
    finally { busy = false; $('confirmBinding').disabled = $('editEmail').disabled = false; tick(); }
  };
  $('editEmail').onclick = () => {
    challenge = ''; $('bindingCode').value = ''; $('currentPassword').value = '';
    $('confirmForm').hidden = true; $('bindForm').hidden = false;
    message('请重新输入当前密码并获取验证码。'); $('email').focus(); tick();
  };
})();
