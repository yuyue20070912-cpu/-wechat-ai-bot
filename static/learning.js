/* Account profiles are authoritative. Unsaved per-exam drafts live only in this page. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const drafts = new Map();
  let exams = [], profiles = [], levels = {}, csrf = '', selected = null, active = null;
  let busy = false, checking = false, needsReload = false;
  const moduleBusy = () => ['practiceBusy','hubBusy','mockBusy'].some(key => document.body.dataset[key] === 'true');
  const dispatch = (name, detail) => document.dispatchEvent(new CustomEvent(name, {detail}));
  function expired() {
    needsReload = true; profiles = []; drafts.clear(); selected = active = null;
    el('profile-section').hidden = true; el('profile-form').reset();
    el('target-score').value=''; el('current-exam').textContent='尚未登录';
    dispatch('learning:profile-unavailable');
    el('status').textContent = '登录已失效，请返回聊天页面登录，再重新加载学习中心。';
    el('retry').hidden = false;
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {cache:'no-store', credentials:'same-origin', ...options});
    if (response.status === 401) { expired(); throw Error('请先返回聊天页面登录，再进入 AI 伴学。'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || '服务暂不可用，请重试。');
    return data;
  }
  function capture() {
    if (selected) drafts.set(selected.id, {score:el('target-score').value, level:el('current-level').value});
  }
  function choose(exam, focus = true) {
    capture(); selected = exam;
    document.querySelectorAll('.exam').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.exam === exam.id)));
    const profile = profiles.find(item => item.exam_type === exam.id), draft = drafts.get(exam.id);
    el('profile-heading').textContent = exam.name + ' · 学习档案';
    el('profile-description').textContent = '设定你的目标，记录目前的水平。保存成功后应用目标并进入总览。';
    const input = el('target-score');
    input.min = exam.score_step; input.max = exam.max_score; input.step = exam.score_step;
    input.value = draft ? draft.score : profile ? profile.target_score : '';
    input.placeholder = exam.id === 'ielts' ? '例如 6.5' : '例如 500';
    el('score-help').textContent = `${exam.score_step}–${exam.max_score} 分，步长 ${exam.score_step} 分`;
    el('current-level').value = draft ? draft.level : profile ? profile.current_level : 'not_assessed';
    el('saved-at').textContent = profile ? '上次保存：' + new Date(profile.updated_at).toLocaleString('zh-CN') : '尚未建立档案';
    el('save-status').textContent = ''; el('profile-section').hidden = false;
    if (focus) el('profile-heading').focus();
  }
  function complete(profile) {
    const exam = exams.find(item => item.id === profile.exam_type), score = profile.target_score;
    return exam && typeof score === 'number' && Number.isFinite(score) && score >= exam.score_step &&
      score <= exam.max_score && score % exam.score_step === 0 && Object.hasOwn(levels, profile.current_level);
  }
  function activate(exam) {
    active = exam;
    dispatch('learning:exam', {exam, csrf});
  }
  function lock(value) {
    busy = value; document.body.dataset.profileBusy=String(value);
    el('save').disabled = value; el('target-score').disabled = value; el('current-level').disabled = value;
    el('profile-form').setAttribute('aria-busy', String(value));
    document.querySelectorAll('.exam').forEach(button => {
      button.disabled = value || moduleBusy();
    });
  }
  async function load() {
    el('retry').hidden = true; el('status').textContent = '正在加载学习中心…';
    dispatch('learning:profile-unavailable'); el('exams').setAttribute('aria-busy', 'true');
    try {
      const [catalog, archive] = await Promise.all([api('/api/learning/exams'), api('/api/learning/profiles')]);
      exams = catalog.exams; levels = catalog.levels; csrf = archive.csrf_token;
      profiles = archive.profiles.filter(complete);
      el('exams').replaceChildren(); el('current-level').replaceChildren();
      Object.entries(levels).forEach(([value, label]) => {
        const option=document.createElement('option'); option.value=value; option.textContent=label; el('current-level').append(option);
      });
      exams.forEach((exam, index) => {
        const button=document.createElement('button'); button.type='button'; button.className='exam';
        button.dataset.exam=exam.id; button.setAttribute('aria-pressed','false'); button.setAttribute('aria-controls','profile-section');
        for (const [tag, className, text] of [
          ['span','icon',['◎','IV','VI'][index]],['span','arrow','↗'],['strong','',exam.name],['p','',exam.subtitle],['span','skills',exam.skills.join(' · ')]
        ]) { const node=document.createElement(tag); node.className=className; node.textContent=text; button.append(node); }
        button.onclick=() => { if(!busy)choose(exam); }; el('exams').append(button);
      });
      // Old servers/legacy accounts need no migration: fall back to their most recently saved complete profile.
      const latest=profiles.find(profile=>profile.exam_type===archive.active_exam_type) ||
        profiles.slice().sort((a,b)=>(Date.parse(b.updated_at)||0)-(Date.parse(a.updated_at)||0))[0];
      const initial=exams.find(exam=>exam.id===latest?.exam_type) || exams[0];
      choose(initial,false);
      dispatch('learning:profile-ready',{hasProfile:Boolean(latest)});
      if(latest)activate(initial);
      else el('current-exam').textContent='尚未保存学习目标';
      el('status').textContent=latest ? `当前目标：${initial.name}。可在「考试选择」修改完整档案。` : '首次使用 · 建立学习档案';
    } catch(error) {
      el('status').textContent=error.message; el('retry').hidden=false;
    } finally { el('exams').setAttribute('aria-busy','false'); }
  }
  el('profile-form').addEventListener('submit', async event => {
    event.preventDefault(); if(!selected||busy||needsReload)return;
    if(moduleBusy()){el('save-status').textContent='请等待当前学习操作完成后再保存档案。';return;}
    capture(); const exam=selected, draft={...drafts.get(exam.id)};
    lock(true); el('save-status').textContent='正在保存…';
    try {
      // A new login rotates the existing session CSRF token. Never write an old account's draft into a new account.
      const archive=await api('/api/learning/profiles');
      if(archive.csrf_token!==csrf) { needsReload=true; window.location.replace('/learning'); return; }
      if(moduleBusy())throw Error('当前学习操作仍在处理中，请完成后再保存档案。');
      const data=await api('/api/learning/profiles/'+exam.id, {
        method:'PUT',headers:{'Content-Type':'application/json','X-Learning-CSRF':csrf},
        body:JSON.stringify({target_score:Number(draft.score),current_level:draft.level})
      });
      if(!data.profile || !complete(data.profile) || data.profile.exam_type!==exam.id)throw Error('保存结果未能确认，请重试或重新加载档案。');
      profiles=profiles.filter(item=>item.exam_type!==exam.id).concat(data.profile);
      drafts.set(exam.id,{score:String(data.profile.target_score),level:data.profile.current_level});
      el('saved-at').textContent='上次保存：'+new Date(data.profile.updated_at).toLocaleString('zh-CN');
      el('save-status').textContent=''; el('status').textContent='学习档案已保存。';
      // Existing modules ignore exam changes while writing. Wait for their locks before notifying them.
      if(moduleBusy()) {
        el('save-status').textContent='档案已保存，正在等待当前学习操作完成…';
        const idle=await new Promise(resolve=>{
          const finish=value=>{observer.disconnect();clearTimeout(timer);resolve(value);};
          const observer=new MutationObserver(()=>{if(!moduleBusy())finish(true);});
          const timer=setTimeout(()=>finish(false),12000);
          observer.observe(document.body,{attributes:true,attributeFilter:['data-practice-busy','data-hub-busy','data-mock-busy']});
          if(!moduleBusy())finish(true);
        });
        if(!idle){window.location.replace('/learning');return;}
      }
      el('save-status').textContent=''; lock(false);
      dispatch('learning:profile-saved'); activate(exam);
    } catch(error) {
      if(!needsReload) {
        dispatch('learning:navigate','exams'); el('save-status').textContent=error.message; el('save-status').focus();
      }
    } finally { lock(false); }
  });
  async function checkSession() {
    if(checking||busy||!csrf||document.visibilityState==='hidden')return;
    checking=true;
    try {
      const archive=await api('/api/learning/profiles');
      if(needsReload||archive.csrf_token!==csrf)window.location.replace('/learning');
    } catch(_) { /* 401 already locks private views; a network error must not erase a draft. */ }
    finally {checking=false;}
  }
  window.addEventListener('focus',checkSession);
  document.addEventListener('visibilitychange',checkSession);
  el('retry').onclick=()=>{if(needsReload)window.location.reload();else load();};
  load();
})();
