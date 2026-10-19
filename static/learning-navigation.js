/* View changes preserve live forms, timers and server-backed module state. */
(() => {
  'use strict';
  const drawer = document.getElementById('learning-drawer');
  const toggle = document.getElementById('learning-menu');
  const main = document.getElementById('learning-main');
  const views = [...document.querySelectorAll('[data-learning-view]')];
  let focusAfterClose = null, ready = false, hasProfile = false;
  function show(name, focus = true) {
    if (!ready) return;
    if (!views.some(view => view.dataset.learningView === name)) name = hasProfile ? 'overview' : 'exams';
    if (document.body.dataset.profileBusy==='true' && name!=='exams') {
      name='exams';document.getElementById('status').textContent='正在保存学习档案，请稍候。';
    }
    if (!hasProfile && name !== 'exams') {
      name = 'exams'; document.getElementById('status').textContent='请先保存学习档案。';
    }
    history.replaceState(null, '', '#' + name);
    focusAfterClose = focus && drawer.open ? document.getElementById('title-' + name) : null;
    views.forEach(view => { view.hidden = view.dataset.learningView !== name; });
    drawer.querySelectorAll('[data-learning-go]').forEach(link => {
      if (link.dataset.learningGo === name) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    const wasOpen = drawer.open;
    if (wasOpen) { main.inert = false; drawer.close(); }
    if (focus && !wasOpen) document.getElementById('title-' + name).focus();
  }
  toggle.onclick = () => { drawer.showModal(); toggle.setAttribute('aria-expanded', 'true'); main.inert = true; };
  document.getElementById('drawer-close').onclick = () => drawer.close();
  drawer.addEventListener('close', () => { main.inert = false; toggle.setAttribute('aria-expanded', 'false'); (focusAfterClose || toggle).focus(); focusAfterClose = null; });
  drawer.addEventListener('click', event => { if (event.target === drawer) { const r = drawer.getBoundingClientRect(); if(event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) drawer.close(); } });
  // Explicit wrapping also covers Safari versions with inconsistent dialog tab order.
  drawer.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const items = [...drawer.querySelectorAll('button,a[href],summary,input:not(:disabled)')].filter(node=>node.getClientRects().length), first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-learning-go]');
    if (!link) return;
    event.preventDefault(); show(link.dataset.learningGo);
  });
  document.addEventListener('learning:navigate', event => { show(event.detail); });
  document.addEventListener('learning:practice-skill', () => show('practice', false));
  document.addEventListener('learning:exam', event => {
    document.getElementById('current-exam').textContent = event.detail.exam.name;
  });
  function links() {
    drawer.querySelectorAll('[data-learning-go]').forEach(link=>{
      if(!ready||(!hasProfile&&link.dataset.learningGo!=='exams'))link.setAttribute('aria-disabled','true');
      else link.removeAttribute('aria-disabled');
    });
  }
  document.addEventListener('learning:profile-ready',event=>{
    ready=true; hasProfile=event.detail.hasProfile; links(); show(hasProfile?'overview':'exams',false);
  });
  document.addEventListener('learning:profile-saved',()=>{ready=hasProfile=true;links();show('overview');});
  document.addEventListener('learning:profile-unavailable',()=>{
    ready=hasProfile=false;views.forEach(view=>{view.hidden=true;});
    document.getElementById('learning-hub').hidden=true;
    drawer.querySelectorAll('[aria-current]').forEach(link=>link.removeAttribute('aria-current'));links();
  });
  window.addEventListener('hashchange', () => show(location.hash.slice(1)));
  views.forEach(view=>{view.hidden=true;});links();
})();

/* Existing bounded APIs: report their actual coverage rather than invent totals. */
(() => {
  'use strict';
  let overview = null, exam = '', attempts = [];
  const skills = {speaking:'口语文字',writing:'写作',listening:'听力文本',reading:'阅读',vocabulary:'词汇',translation:'翻译'};
  function render() {
    if (!overview) return;
    const root = document.getElementById('learning-topics'), summary = document.getElementById('recent-learning-summary');
    root.replaceChildren(); summary.replaceChildren();
    const add = (parent, tag, value, cls) => { const n=document.createElement(tag); n.textContent=value; if(cls)n.className=cls;parent.append(n);return n; };
    const dayOf = date => new Intl.DateTimeFormat('en-CA', {timeZone:overview.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(date));
    const cutoff = new Date(overview.today+'T12:00:00Z'); cutoff.setUTCDate(cutoff.getUTCDate()-27);
    const first = cutoff.toISOString().slice(0,10);
    const inRange = date => { const day=dayOf(date); return day>=first && day<=overview.today; };
    const records = attempts.filter(a=>a.exam_type===exam && a.purpose==='practice' && inRange(a.finished_at||a.created_at)).map(a=>({date:a.finished_at||a.created_at,topic:skills[a.skill]||a.skill,detail: a.status==='completed'?'已完成 1 次专项练习':'尚未完成，不计为已做题'}));
    overview.diagnostic_history.filter(a=>inRange(a.created_at)).forEach(a=>records.push({date:a.created_at,topic:'入门筛查',detail:`${a.result.correct}/${a.result.total} 题正确 · ${a.result.accuracy}%（非正式能力评级）`}));
    records.sort((a,b)=>new Date(b.date)-new Date(a.date));
    const active = overview.active_dates.filter(day=>day>=first && day<=overview.today).length;
    add(summary,'p',active?`最近 28 天有 ${active} 个学习活动日。`:'最近 28 天暂无学习活动。');

    if(records.length){
      const practiceCount=records.filter(r=>r.detail==='已完成 1 次专项练习').length;
      const diagnostics=overview.diagnostic_history.filter(a=>inRange(a.created_at));
      add(summary,'p',`已返回记录中：完成 ${practiceCount} 次专项练习、${diagnostics.length} 次诊断；诊断共 ${diagnostics.reduce((n,a)=>n+a.result.total,0)} 题，正确 ${diagnostics.reduce((n,a)=>n+a.result.correct,0)} 题。`);
    }

    if(!records.length)add(root,'p','最近 28 天暂无可展示的诊断或专项练习明细。');
    records.forEach(record=>{ const card=add(root,'article',''); add(card,'strong',record.topic);add(card,'p',dayOf(record.date)+' · '+record.detail); });
  }
  document.addEventListener('learning:overview',event=>{overview=event.detail.overview;exam=event.detail.exam;render();});
  document.addEventListener('learning:records',event=>{attempts=event.detail;render();});
})();
