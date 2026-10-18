/* Personalization uses server persistence; no answers or completion state in localStorage. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const skills = {speaking:'口语文字',writing:'写作',listening:'听力文本',reading:'阅读',vocabulary:'词汇',translation:'翻译'};
  let exam = null, csrf = '', generation = 0, busy = false, test = null, report = null, offset = 0, pendingDiagnostic = null, preferences = null;
  const reviewRequests = new Map();
  const text = (tag, value, root) => {const node=document.createElement(tag);node.textContent=value;root.append(node);return node;};
  const uuid = () => {
    const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
    const h=Array.from(b,n=>n.toString(16).padStart(2,'0')).join('');
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
  };
  async function api(path, method='GET', body) {
    const response=await fetch('/api/learning/'+path,{method,cache:'no-store',credentials:'same-origin',
      headers:method==='GET'?{}:{'Content-Type':'application/json','X-Learning-CSRF':csrf},
      body:body===undefined?undefined:JSON.stringify(body)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw Error(response.status===401?'登录已失效，请返回聊天页登录。':data.error||'学习服务暂不可用，请重试。');
    return data;
  }
  function lock(value) {
    busy=value;document.body.dataset.hubBusy=String(value);
    document.querySelectorAll('.exam').forEach(button=>{button.disabled=value||document.body.dataset.voiceBusy==='true'||document.body.dataset.practiceBusy==='true'||document.body.dataset.mockBusy==='true';});
    el('learning-hub').setAttribute('aria-busy',String(value));
  }
  async function action(work, message) {
    if(busy)return;
    lock(true);el('hub-status').textContent='正在保存…';
    try {await work();await load(false);el('hub-status').textContent=message;return true;}
    catch(error){el('hub-status').textContent=error.message;return false;}
    finally{lock(false);}
  }
  function renderTest(value) {
    test=value;pendingDiagnostic=null;el('screening-group').textContent=`入门筛查 · 题组 ${value.group}/${value.groups} · 非完整水平测试`;
    const root=el('diagnostic-questions');root.replaceChildren();
    value.questions.forEach(q=>{
      const group=document.createElement('fieldset');text('legend',`${skills[q.skill]} · ${q.prompt}`,group);
      q.options.forEach((option,index)=>{
        const label=document.createElement('label'), input=document.createElement('input');
        input.type='radio';input.name='diag-'+q.id;input.value=index;input.required=true;
        label.append(input,document.createTextNode(option));group.append(label);
      });root.append(group);
    });
  }
  function renderOverview(data) {
    const root=el('learning-metrics');root.replaceChildren();
    for(const [label,value] of [['学习天数',data.study_days],['已完成任务',data.completed_tasks],['最新诊断正确率',data.diagnostic_accuracy===null?'待诊断':data.diagnostic_accuracy+'%'],['到期复习',data.review.due]]){
      const card=document.createElement('div');text('strong',value,card);text('span',label,card);root.append(card);
    }
    report=data.latest_diagnostic;
    const summary=el('diagnostic-summary');summary.replaceChildren();
    if(report){
      text('p',`本题组观察：${report.result.level} · ${report.result.correct}/${report.result.total} 题正确`,summary);
      text('p','待巩固：'+(report.result.weak_skills.map(s=>skills[s]).join('、')||'继续用专项练习验证'),summary);
      const details=document.createElement('details');text('summary','查看本地判分与解析',details);
      report.result.details.forEach(item=>text('p',`${skills[item.skill]}：${item.correct?'正确':'需复习'}。${item.explanation}`,details));summary.append(details);
    }else{text('p','先用六道题，认识自己的起点。',summary);}
    el('trend-note').textContent=data.notice;
    const diag=el('diagnostic-trend');diag.replaceChildren();text('h4','客观诊断变化',diag);
    if(data.diagnostic_delta!==null)text('p',`较上一次同版本诊断：${data.diagnostic_delta>0?'+':''}${data.diagnostic_delta} 个百分点（仅六题小样本）`,diag);
    if(!data.diagnostic_history.length)text('p','完成诊断后展示客观题正确率；至少两次同版本结果才能计算变化。',diag);
    data.diagnostic_history.forEach(point=>{
      const row=document.createElement('div');row.className='trend-row';
      text('span',new Date(point.created_at).toLocaleDateString('zh-CN'),row);
      const meter=document.createElement('meter');meter.min=0;meter.max=100;meter.value=point.result.accuracy;meter.setAttribute('aria-label','文字诊断正确率');row.append(meter);
      text('span',point.result.accuracy+'%',row);diag.append(row);
    });
    const practice=el('practice-trend');practice.replaceChildren();text('h4','AI 练习参考分（独立观察）',practice);
    if(!data.practice_scores.length)text('p','尚无已完成的 AI 文字练习。诊断 AI 解读不会计入本项。',practice);
    data.practice_scores.forEach(s=>text('p',`${skills[s.skill]} · ${s.count} 次 · 均分 ${s.average}/100 · ${s.change===null?'样本不足，暂不计算变化':`首次到最近 ${s.change>0?'+':''}${s.change} 参考分`}`,practice));
    const calendar=el('activity-days');calendar.replaceChildren();
    for(let i=27;i>=0;i--){
      const date=new Date(data.today+'T12:00:00Z');date.setUTCDate(date.getUTCDate()-i);const day=date.toISOString().slice(0,10);
      const cell=text('span',day.slice(8),calendar), active=data.active_dates.includes(day);
      cell.className=active?'active':'';cell.title=day+(active?' · 有学习活动':' · 暂无学习活动');cell.setAttribute('aria-label',cell.title);
    }
  }
  function renderPlan(plan) {
    const root=el('daily-tasks');root.replaceChildren();
    el('plan-date').textContent=plan?`${plan.date} · ${plan.inputs.timezone} · 今天的安排`:'今日尚无计划。可先完成诊断，再生成。';
    if(!plan)return;
    text('p',plan.notice,root).className='practice-note';
    plan.tasks.forEach(task=>{
      const card=document.createElement('article');card.className='daily-task';card.dataset.taskId=task.id;
      const label=document.createElement('label'), input=document.createElement('input');
      input.type='checkbox';input.checked=task.completed;input.setAttribute('aria-label','完成任务：'+task.title);
      input.onchange=async()=>{
        if(busy){input.checked=task.completed;return;}
        const value=input.checked;input.disabled=true;
        const saved=await action(async()=>{
          await api(`tasks/${exam.id}/${plan.date}/${task.id}`,'PUT',{completed:value,expected_version:task.version});
        },'任务状态已保存，重新登录后仍可恢复。');
        if(!saved)input.checked=task.completed;
        input.disabled=false;
      };
      label.append(input,document.createTextNode(task.title));card.append(label);
      text('small',`${task.minutes} 分钟 · ${task.reason}`,card);
      const go=text('button','去完成',card);go.type='button';go.onclick=()=>{
        if(task.kind==='diagnostic'){el('diagnostic-details').open=true;el('diagnostic-details').scrollIntoView({block:'start'});}
        else if(task.kind==='review'){el('review-heading').scrollIntoView({block:'start'});}
        else document.dispatchEvent(new CustomEvent('learning:practice-skill',{detail:{exam_type:exam.id,skill:task.skill}}));
      };root.append(card);
    });
  }
  function renderReviews(data) {
    const root=el('review-items');root.replaceChildren();
    el('review-count').textContent=`共 ${data.total} 项 · ${data.due} 项到期 · 第 ${Math.floor(offset/30)+1} 页`;
    el('review-prev').disabled=offset===0;el('review-next').disabled=offset+30>=data.total;
    if(!data.items.length)text('p','还没有待巩固项目。诊断错题和 AI 练习记录会在这里汇合。',root);
    data.items.forEach((item,index)=>{
      const card=document.createElement('details');card.className='review-card';card.dataset.itemKey=item.item_key;
      text('summary',`${skills[item.skill]} · ${item.source_type==='diagnostic'?'客观错题':'AI 待巩固'} · ${item.is_due?'现在复习':'下次 '+new Date(item.due_at).toLocaleString('zh-CN')}`,card);
      text('p',item.content.prompt,card);if(item.content.material)text('p',item.content.material,card);
      text('p','原答案：'+item.content.answer,card);text('p',item.content.notice,card).className='practice-note';
      const solution=document.createElement('details');text('summary','查看参考答案与解析',solution);
      text('p',item.content.reference,solution);text('p',item.content.explanation,solution);
      if(item.content.ai_feedback){
        text('h4','已保存的 AI 解析',solution);
        item.content.ai_feedback.improvements.forEach(s=>text('p',s,solution));
        text('p',item.content.ai_feedback.revised_answer,solution);
      }card.append(solution);
      text('p',`复习状态：${{new:'新收录',relearning:'重新学习',reviewing:'复习中',mastered:'已巩固'}[item.state]} · 连续掌握 ${item.repetitions} 次`,card);
      const form=document.createElement('form'), label=text('label','本次复习答案',form), id='review-answer-'+index;
      label.htmlFor=id;
      let answer;
      if(item.source_type==='diagnostic'){
        answer=document.createElement('select');const blank=text('option','请选择',answer);blank.value='';
        item.content.options.forEach((value,i)=>{const option=text('option',value,answer);option.value=i;});
      }else{answer=document.createElement('textarea');answer.rows=3;answer.maxLength=4000;}
      answer.id=id;answer.required=true;form.append(answer);
      let rating=null;
      if(item.source_type==='ai_practice'){
        rating=document.createElement('select');rating.setAttribute('aria-label','自评掌握程度');rating.required=true;
        for(const [value,caption] of [['','请自评掌握程度'],['again','仍未掌握'],['hard','勉强回忆'],['good','基本掌握'],['easy','轻松掌握']]){const option=text('option',caption,rating);option.value=value;}form.append(rating);
      }
      const submit=text('button','提交复习答案',form);submit.type='submit';submit.disabled=!item.is_due;
      form.onsubmit=event=>{
        event.preventDefault();if(busy||!item.is_due)return;
        const value={item_key:item.item_key,expected_version:item.version,answer:answer.value.trim(),self_rating:rating?rating.value:null};
        const signature=JSON.stringify(value);if(!reviewRequests.has(signature))reviewRequests.set(signature,uuid());
        action(()=>api('review-responses','POST',{...value,request_id:reviewRequests.get(signature)}),'复习已记录，下一次时间已安排。');
      };card.append(form);root.append(card);
    });
  }
  async function load(rebuild=false) {
    if(!exam)return;
    const version=++generation, id=exam.id;
    const paths=[`overview/${id}`,`plans/${id}`,`reviews/${id}?offset=${offset}`,'preferences'];
    if(rebuild)paths.push(`diagnostics/${id}`);
    const results=await Promise.all(paths.map(path=>api(path)));
    if(version!==generation)return;
    renderOverview(results[0].overview);renderPlan(results[1].plan);renderReviews(results[2]);
    preferences=results[3].preferences;el('learning-timezone').value=preferences.timezone;
    if(rebuild){renderTest(results[4].test);el('diagnostic-details').open=false;}
    const ai=el('diagnostic-ai');ai.disabled=true;el('diagnostic-ai-result').replaceChildren();
    if(report){
      const result=await api('diagnostic-results/'+report.request_id);
      if(version!==generation)return;
      ai.disabled=!result.ai_enabled||Boolean(result.ai_attempt);
      ai.textContent=result.ai_attempt?'AI 解读已提交 · 可查看历史':'可选 AI 解读 · '+(result.ai_enabled?'消耗 1 条消息':'默认关闭');
      if(result.ai_attempt?.feedback){
        const feedback=result.ai_attempt.feedback, root=el('diagnostic-ai-result');
        text('h4','AI 学习建议（不改写客观正确率）',root);text('p',feedback.summary,root);
        feedback.improvements.forEach(s=>text('p',s,root));text('p',feedback.next_step,root);
      }else if(result.ai_attempt)text('p',result.ai_attempt.status==='pending'?'AI 解读处理中，可稍后刷新。':'AI 解读未完成，消息额度按账本退款；本地诊断仍可使用。',el('diagnostic-ai-result'));
    }
  }
  document.addEventListener('learning:exam',async event=>{
    if(busy)return;
    const changed=!exam||exam.id!==event.detail.exam.id;
    exam=event.detail.exam;csrf=event.detail.csrf;if(changed)offset=0;
    el('learning-hub').hidden=false;el('hub-status').textContent='正在加载个人学习数据…';
    try{await load(changed);el('hub-status').textContent='学习数据已同步。';}catch(error){el('hub-status').textContent=error.message;}
  });
  document.addEventListener('learning:data-changed',async()=>{if(exam&&!busy){try{await load(false);}catch(error){el('hub-status').textContent=error.message;}}});
  el('hub-refresh').onclick=()=>action(()=>Promise.resolve(),'学习数据已同步。');
  el('create-plan').onclick=()=>action(()=>api('plans/'+exam.id,'POST',{}),'今日计划已保存。');
  el('review-sync').onclick=()=>action(()=>api(`reviews/${exam.id}/sync`,'POST',{}),'已导入一批待巩固题，可继续点击导入剩余记录。');
  el('review-prev').onclick=()=>{offset=Math.max(0,offset-30);action(()=>Promise.resolve(),'复习列表已更新。');};
  el('review-next').onclick=()=>{offset+=30;action(()=>Promise.resolve(),'复习列表已更新。');};
  el('diagnostic-form').onsubmit=event=>{
    event.preventDefault();if(!test||busy)return;
    const answers={};new FormData(event.target).forEach((value,key)=>{answers[key.slice(5)]=Number(value);});
    const content={version:test.version,answers}, signature=exam.id+JSON.stringify(content);
    if(!pendingDiagnostic||pendingDiagnostic.signature!==signature)pendingDiagnostic={signature,request_id:uuid()};
    action(async()=>{
      await api('diagnostics/'+exam.id,'POST',{...content,request_id:pendingDiagnostic.request_id});
      pendingDiagnostic=null;event.target.reset();el('diagnostic-details').open=false;
      renderTest((await api('diagnostics/'+exam.id)).test);
    },'诊断已保存，错题已加入复习；本次未调用付费 AI。');
  };
  el('timezone-form').onsubmit=event=>{event.preventDefault();if(preferences)action(()=>api('preferences','PUT',{timezone:el('learning-timezone').value.trim(),expected_version:preferences.version}),'时区已保存，今日计划按新时区恢复。');};
  el('diagnostic-ai').onclick=()=>{if(report)action(()=>api(`diagnostic-results/${report.request_id}/ai`,'POST',{}),'AI 解读已保存，可随时回来查看。');};
})();
