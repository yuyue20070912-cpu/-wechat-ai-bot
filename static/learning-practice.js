/* Text-only practice. Render all learner/model content as text, never HTML. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let exam = null, csrf = '', state = null, questions = [], index = 0;
  let busy = false, generation = 0, pending = null;
  function uuid() {
    // crypto.randomUUID is absent in older Safari; getRandomValues works there.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...options});
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(response.status === 401 ? '登录已失效，请返回聊天页面登录。' : value.error || '服务暂不可用，请重试。');
    return value;
  }
  function text(tag, value, parent) {
    const node = document.createElement(tag); node.textContent = value; parent.append(node); return node;
  }
  function renderFeedback(feedback) {
    const root = el('feedback-content'); root.replaceChildren();
    text('p', `参考分 ${feedback.score}/100 · ${feedback.score_label}`, root).className = 'feedback-score';
    text('p', feedback.summary, root);
    for (const [label, key] of [['做得好的地方', 'strengths'], ['下一次可以改进', 'improvements']]) {
      text('h4', label, root);
      const list = document.createElement('ul'); root.append(list);
      feedback[key].forEach(item => text('li', item, list));
    }
    for (const [label, key] of [['表达改写', 'revised_answer'], ['题目参考答案', 'reference_answer'], ['下一步', 'next_step']]) {
      text('h4', label, root); text('p', feedback[key], root);
    }
    el('feedback').hidden = false;
  }
  function lock(value) {
    busy = value;
    document.body.dataset.practiceBusy = String(value);
    el('practice-skill').disabled = value;
    el('next-question').disabled = value;
    el('practice-answer').disabled = value;
    el('analyze-answer').disabled = value || !state || !state.enabled || !questions.length;
    document.querySelectorAll('.exam').forEach(button => {button.disabled = value || document.body.dataset.hubBusy === 'true' || document.body.dataset.mockBusy === 'true';});
    el('answer-form').setAttribute('aria-busy', String(value));
  }
  async function refresh() {
    state = await api('/api/learning/practice');
    const u = state.usage;
    el('practice-usage').textContent = `${state.enabled ? '' : 'AI 反馈暂未开放，可先浏览题目。'}今日调用剩余 ${u.calls_remaining}/${u.daily_call_limit} 次 · 共享消息剩余 ${u.message_remaining} 条 · 每日调用 UTC 00:00 重置`;
    if (!el('practice-skill').options.length) {
      Object.entries(state.skills).forEach(([value, label]) => {
        const option = document.createElement('option'); option.value = value; option.textContent = label;
        el('practice-skill').append(option);
      });
    }
    const history = el('practice-history'); history.replaceChildren();
    if (!state.attempts.length) text('p', '还没有练习记录，开始第一道吧。', history);
    state.attempts.forEach(attempt => {
      const card = document.createElement('article'); card.className = 'history-item';
      card.dataset.requestId = attempt.request_id;
      const status = {pending: '分析中', completed: '已完成', refunded: '未完成，消息额度已退还'}[attempt.status];
      text('p', `${attempt.exam_type.toUpperCase()} · ${attempt.purpose === 'diagnostic' ? '入门筛查 AI 解读' : attempt.purpose === 'mock' ? '模考 AI 参考评分' : state.skills[attempt.skill]} · ${status}`, card);
      text('small', new Date(attempt.created_at).toLocaleString('zh-CN'), card);
      const details = document.createElement('details'); text('summary', '查看我的答案', details); text('p', attempt.answer, details); card.append(details);
      if (attempt.feedback) {
        const button = text('button', '查看反馈', card); button.type = 'button';
        button.onclick = () => { if (!busy) { renderFeedback(attempt.feedback); el('feedback-heading').focus(); } };
        if (attempt.purpose === 'practice') {
          const save = text('button', '加入错题本', card); save.type = 'button';
          save.onclick = async () => {
            save.disabled = true;
            try {
              await api('/api/learning/reviews/save', {method:'POST', headers:{'Content-Type':'application/json', 'X-Learning-CSRF':csrf},
                body:JSON.stringify({request_id:attempt.request_id})});
              save.textContent = '已加入错题本';
              document.dispatchEvent(new CustomEvent('learning:data-changed'));
            } catch (error) { save.textContent = error.message; save.disabled = false; }
          };
        }
      }
      history.append(card);
    });
    lock(busy);
  }
  function showQuestion() {
    const question = questions[index];
    el('question-panel').hidden = !question;
    el('feedback').hidden = true;
    if (!question) return;
    pending = null;
    el('question-title').textContent = question.title + ` · ${index + 1}/${questions.length}`;
    el('question-guidance').textContent = question.guidance;
    el('question-prompt').textContent = question.prompt;
    el('material-text').textContent = question.material;
    el('question-prompt').lang = question.skill === 'translation' ? 'zh-CN' : 'en';
    el('material-text').lang = question.skill === 'translation' ? 'zh-CN' : 'en';
    el('material-label').textContent = question.skill === 'listening' ? '展开转写文本（当前无音频）' : '展开阅读材料';
    el('question-material').hidden = !question.material;
    el('question-material').open = question.skill !== 'listening';
    el('supplemental').hidden = !question.supplemental;
    el('practice-answer').value = '';
    el('practice-status').textContent = '';
    lock(false);
  }
  async function loadQuestions() {
    const version = ++generation;
    el('question-panel').hidden = true; el('feedback').hidden = true;
    el('practice-status').textContent = '正在加载题目…';
    questions = []; el('analyze-answer').disabled = true;
    try {
      const data = await api(`/api/learning/questions?exam=${encodeURIComponent(exam.id)}&skill=${encodeURIComponent(el('practice-skill').value)}`);
      if (version !== generation) return;
      questions = data.questions; index = 0; showQuestion();
    } catch (error) { if (version === generation) el('practice-status').textContent = error.message; }
  }
  document.addEventListener('learning:exam', async event => {
    if (busy) return;
    if (exam && exam.id === event.detail.exam.id) return;
    exam = event.detail.exam; csrf = event.detail.csrf;
    el('practice-section').hidden = false;
    const chosen = exam.id;
    try { await refresh(); if (exam.id === chosen) await loadQuestions(); }
    catch (error) { el('practice-status').textContent = error.message; }
  });
  el('practice-skill').onchange = loadQuestions;
  document.addEventListener('learning:practice-skill', async event => {
    if (busy || !exam || exam.id !== event.detail.exam_type) return;
    el('practice-skill').value = event.detail.skill;
    await loadQuestions();
    el('practice-answer').focus();
  });
  el('next-question').onclick = () => {
    if (busy || !questions.length) return;
    if (el('practice-answer').value.trim() && !confirm('换题会清除尚未提交的答案，继续吗？')) return;
    index = (index + 1) % questions.length; showQuestion();
  };
  el('refresh-practice').onclick = async () => {
    try { await refresh(); if (!questions.length && exam) await loadQuestions(); }
    catch (error) { el('practice-status').textContent = error.message; }
  };
  el('answer-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !questions.length) return;
    const answer = el('practice-answer').value.trim();
    if (!answer) return;
    const question = questions[index];
    if (!pending || pending.answer !== answer || pending.question_id !== question.id) {
      pending = {request_id: uuid(), question_id: question.id, answer};
    }
    lock(true); el('feedback').hidden = true;
    el('practice-status').textContent = 'AI 正在分析，请稍候。离开页面不会取消已经提交的分析。';
    try {
      const data = await api('/api/learning/attempts', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Learning-CSRF': csrf}, body: JSON.stringify(pending)});
      renderFeedback(data.feedback); el('feedback-heading').focus();
      el('practice-status').textContent = data.replayed ? '已恢复上次反馈，没有重复扣费。' : '反馈已保存。';
    } catch (error) {
      el('practice-status').textContent = error.message;
      // Keep the request ID on network ambiguity: retries cannot double-charge.
      try {
        const data = await api('/api/learning/attempts/' + pending.request_id);
        if (data.attempt.feedback) renderFeedback(data.attempt.feedback);
        if (data.attempt.status === 'refunded') pending = null;
      } catch (_) { /* A later history refresh can recover the result. */ }
    } finally {
      try { await refresh(); } catch (_) { /* Preserve the submitted answer and feedback. */ }
      lock(false);
      document.dispatchEvent(new CustomEvent('learning:data-changed'));
    }
  });
  // Safari can restore DOM state from back/forward cache after an account change.
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();
