/* Independent learning UI. Profile data stays on the server, never localStorage. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let exams = [], profiles = [], csrf = '', selected = null, busy = false;
  async function api(path, options = {}) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin', ...options});
    if (response.status === 401) {
      el('profile-section').hidden = true;
      throw Error('请先返回聊天页面登录，再进入 AI 伴学。');
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || '服务暂不可用，请重试。');
    return data;
  }
  function choose(exam, focus = true) {
    selected = exam;
    document.querySelectorAll('.exam').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.exam === exam.id)));
    const profile = profiles.find(item => item.exam_type === exam.id);
    el('profile-heading').textContent = exam.name + ' · 学习档案';
    el('profile-description').textContent = '设定你的目标，记录目前的水平。你可以随时回来调整。';
    const input = el('target-score');
    input.min = exam.score_step; input.max = exam.max_score; input.step = exam.score_step;
    input.value = profile ? profile.target_score : '';
    input.placeholder = exam.id === 'ielts' ? '例如 6.5' : '例如 500';
    el('score-help').textContent = `${exam.score_step}–${exam.max_score} 分，步长 ${exam.score_step} 分`;
    el('current-level').value = profile ? profile.current_level : 'not_assessed';
    el('saved-at').textContent = profile ? '上次保存：' + new Date(profile.updated_at).toLocaleString('zh-CN') : '尚未建立档案';
    el('save-status').textContent = '';
    el('profile-section').hidden = false;
    if (focus) el('profile-heading').focus();
  }
  async function load() {
    el('retry').hidden = true;
    el('status').textContent = '正在加载学习中心…';
    el('exams').setAttribute('aria-busy', 'true');
    try {
      const [catalog, archive] = await Promise.all([api('/api/learning/exams'), api('/api/learning/profiles')]);
      exams = catalog.exams; profiles = archive.profiles; csrf = archive.csrf_token;
      el('exams').replaceChildren(); el('current-level').replaceChildren();
      Object.entries(catalog.levels).forEach(([value, label]) => {
        const option = document.createElement('option'); option.value = value; option.textContent = label;
        el('current-level').append(option);
      });
      exams.forEach((exam, index) => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'exam';
        button.dataset.exam = exam.id; button.setAttribute('aria-pressed', 'false');
        button.setAttribute('aria-controls', 'profile-section');
        for (const [tag, className, text] of [
          ['span', 'icon', ['◎', 'IV', 'VI'][index]], ['span', 'arrow', '↗'],
          ['strong', '', exam.name], ['p', '', exam.subtitle], ['span', 'skills', exam.skills.join(' · ')]
        ]) {
          const node = document.createElement(tag); node.className = className; node.textContent = text; button.append(node);
        }
        button.onclick = () => { if (!busy) choose(exam); };
        el('exams').append(button);
      });
      el('status').textContent = profiles.length ? `已保存 ${profiles.length} 份考试档案。选择考试查看或调整。` : '选择一场考试，建立你的第一份学习档案。';
    } catch (error) {
      el('status').textContent = error.message;
      el('retry').hidden = false;
    } finally { el('exams').setAttribute('aria-busy', 'false'); }
  }
  el('profile-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!selected || busy) return;
    busy = true; el('save').disabled = true;
    el('save-status').textContent = '正在保存…';
    try {
      const data = await api('/api/learning/profiles/' + selected.id, {
        method: 'PUT', headers: {'Content-Type': 'application/json', 'X-Learning-CSRF': csrf},
        body: JSON.stringify({target_score: Number(el('target-score').value), current_level: el('current-level').value})
      });
      profiles = profiles.filter(item => item.exam_type !== selected.id).concat(data.profile);
      choose(selected, false);
      el('save-status').textContent = '学习档案已保存。';
      el('status').textContent = `已保存 ${profiles.length} 份考试档案。选择考试查看或调整。`;
    } catch (error) { el('save-status').textContent = error.message; }
    finally { busy = false; el('save').disabled = false; }
  });
  el('retry').onclick = load;
  load();
})();
