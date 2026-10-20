'use strict';
const $ = id => document.getElementById(id);
let challenge = '', resetToken = '', verifiedPhone = '', sentPhone = '';
let cooldownUntil = 0, codeExpiresAt = 0;
function message(text, error = false) {
  $('message').textContent = text;
  $('message').dataset.error = String(error);
}
async function api(action, body) {
  const response = await fetch('/auth/recovery/' + action, {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || '服务暂时不可用，请稍后重试');
  return data;
}
function tick() {
  const seconds = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
  $('sendCode').disabled = seconds > 0;
  $('sendCode').textContent = seconds ? `${seconds}秒后重发` : '获取验证码';
  if (codeExpiresAt && !$('verifyForm').hidden) {
    const left = Math.max(0, Math.ceil((codeExpiresAt - Date.now()) / 1000));
    $('expiry').textContent = left ? `验证码剩余 ${Math.floor(left / 60)}分${left % 60}秒有效` : '验证码已过期，请重新获取。';
  }
}
setInterval(tick, 1000);
$('phone').addEventListener('input', () => {challenge = ''; sentPhone = ''; $('code').value = '';});
$('sendCode').onclick = async () => {
  const phone = $('phone').value.trim();
  if (!/^1[3-9][0-9]{9}$/.test(phone)) return message('请输入正确的11位中国大陆注册手机号', true);
  $('sendCode').disabled = true;
  $('phone').readOnly = true;
  challenge = ''; resetToken = '';
  // Keep a local cooldown even on uncertain network outcomes; server limits are authoritative.
  cooldownUntil = Date.now() + 60000;
  try {
    const data = await api('send', {phone});
    challenge = data.challenge; sentPhone = phone;
    codeExpiresAt = Date.now() + data.expires_in * 1000;
    cooldownUntil = Date.now() + data.retry_after * 1000;
    message(data.message); $('code').focus();
  } catch (e) { message(e.message, true); }
  finally { $('phone').readOnly = false; tick(); }
};
$('verifyForm').onsubmit = async event => {
  event.preventDefault();
  if (!challenge || sentPhone !== $('phone').value.trim()) return message('请先获取邮箱验证码', true);
  $('verifyCode').disabled = true; $('phone').readOnly = true; $('code').readOnly = true;
  try {
    const data = await api('verify', {phone: sentPhone, challenge, code: $('code').value.trim()});
    resetToken = data.reset_token; verifiedPhone = sentPhone;
    $('verifyForm').hidden = true; $('resetForm').hidden = false;
    const steps = document.querySelectorAll('.steps li');
    steps[0].removeAttribute('aria-current'); steps[1].setAttribute('aria-current', 'step');
    $('code').value = ''; challenge = '';
    message('身份验证成功，请在5分钟内设置新密码。'); $('password').focus();
  } catch (e) { message(e.message, true); }
  finally { $('verifyCode').disabled = false; $('phone').readOnly = false; $('code').readOnly = false; }
};
$('resetForm').onsubmit = async event => {
  event.preventDefault();
  const password = $('password').value;
  if (password !== $('confirm').value) return message('两次输入的密码不一致', true);
  if (password.length < 10 || password.length > 128 || !/[A-Za-z]/.test(password) || !/[^A-Za-z]/.test(password)) return message('新密码需为10–128位，包含字母及数字或符号', true);
  $('resetPassword').disabled = true;
  try {
    await api('reset', {phone: verifiedPhone, reset_token: resetToken, new_password: password});
    resetToken = ''; $('password').value = $('confirm').value = '';
    $('resetForm').hidden = true; $('success').hidden = false;
    message('密码重置成功');
  } catch (e) { message(e.message, true); }
  finally { $('resetPassword').disabled = false; }
};
$('restart').onclick = () => {
  resetToken = ''; challenge = ''; verifiedPhone = ''; sentPhone = '';
  $('password').value = $('confirm').value = $('code').value = '';
  $('resetForm').hidden = true; $('verifyForm').hidden = false;
  const steps = document.querySelectorAll('.steps li');
  steps[1].removeAttribute('aria-current'); steps[0].setAttribute('aria-current', 'step');
  message('请重新获取邮箱验证码'); $('phone').focus();
};
