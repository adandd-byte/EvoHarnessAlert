const $ = (id) => document.getElementById(id);
const TYPE = {PROBLEM:'问题', BUSINESS:'业务', EVENT:'变更', HOST:'主机'};
const STATUS = {SUCCESS:'已返回', TIMEOUT:'查询超时', DENIED:'权限拒绝', OPEN:'待接手', ACKNOWLEDGED:'处理中', RESOLVED:'已关闭', FIRING:'触发中', NEED_MORE_EVIDENCE:'待补充证据', CONFLICT:'证据冲突', ACCESS_DENIED:'权限拒绝', WAITING_APPROVAL:'等待人工审批'};
const TOOL = {metrics:'指标', logs:'日志', traces:'调用链', change:'变更', code:'代码', host:'主机', database:'数据库', dependency:'依赖'};
const state = {dataset:null, live:false, view:'alerts', items:[], selected:null, tab:'evidence', token:'', user:'', notes:{}, local:[]};
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge = value => `<span class="badge ${['P0','P1','P2','P3'].includes(value)?value.toLowerCase():''}">${escapeHtml(STATUS[value] || value)}</span>`;
function notice(message) { $('notice').textContent = message; }
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json', ...(state.token?{Authorization:`Basic ${state.token}`}:{})}});
  if (!response.ok) throw new Error(response.status===401?'账号或密码不正确':response.status===403?'当前账号无操作权限':`请求失败（${response.status}），请检查后端与数据库连接`);
  return response.json();
}
function chosen() { return state.items.find(item => item.id === state.selected); }
function filtered() {
  const term = $('search').value.trim().toLowerCase();
  return state.items.filter(item => (!$('priority').value || item.alert.severity===$('priority').value)
    && (!$('type').value || item.alert.alertType.toUpperCase()===$('type').value)
    && (!term || `${item.id} ${item.alert.title} ${item.alert.labels?.service || ''}`.toLowerCase().includes(term)));
}
function render() {
  const items = filtered();
  if (!items.some(item=>item.id===state.selected)) state.selected=items[0]?.id;
  $('count').textContent=state.items.length;
  $('urgent').textContent=state.items.filter(x=>['P0','P1'].includes(x.alert.severity)).length;
  $('types').textContent=Object.keys(TYPE).map(type=>state.items.filter(x=>x.alert.alertType.toUpperCase()===type).length).join(' / ');
  $('evidenceCount').textContent=state.items.reduce((n,x)=>n+(x.toolSnapshots?.length||0),0);
  $('lastMetricLabel').textContent=state.live?'已关联取证快照':'冻结工具快照';
  $('resultCount').textContent=`${items.length} 条`;
  $('mode').textContent=state.live?'已连接 · 切回模拟':'模拟回放 · 连接';
  $('connection').textContent=state.live?`${state.user} · 后端已连接`:'本地模拟 · 未连接后端';
  $('connect').textContent=state.live?'断开连接':'连接后端';
  $('pageTitle').textContent=state.view==='dataset'?'回放数据集':'告警工作台';
  $('list').innerHTML=items.length?items.map(item=>`<button class="alert-row ${item.id===state.selected?'active':''}" data-id="${escapeHtml(item.id)}" aria-pressed="${item.id===state.selected}"><div class="row-title"><strong>${escapeHtml(item.alert.title)}</strong>${badge(item.alert.severity)}</div><div class="row-meta">${escapeHtml(item.alert.labels?.service || '未标注服务')} · ${escapeHtml(state.view==='dataset'?item.variantLabel:TYPE[item.alert.alertType.toUpperCase()])}</div></button>`).join(''):'<div class="empty">没有匹配的告警</div>';
  renderDetail();
}
function renderDetail() {
  const item=chosen();
  if (!item) { $('detail').innerHTML='<div class="empty">暂无告警详情</div>'; return; }
  const a=item.alert, expected=item.expected;
  const variantSelect=!state.live && item.scenario?`<select class="variant" id="variant" aria-label="回放变体">${state.dataset.cases.filter(x=>x.scenario===item.scenario).map(x=>`<option value="${x.id}" ${x.id===item.id?'selected':''}>${x.variantLabel}</option>`).join('')}</select>`:'';
  let body='';
  if (state.tab==='evidence') {
    body=`<section><div class="section-head"><h3>告警原文</h3>${badge(item.status || a.status?.toUpperCase())}</div><div class="raw">${escapeHtml(a.description || a.title)}</div></section>`;
    body+=`<section class="detail-section"><div class="section-head"><h3>${state.live?'证据记录':'冻结证据快照'}</h3><span class="muted">${item.toolSnapshots?.length || 0} 项</span></div>`;
    body+=(item.toolSnapshots||[]).map(ev=>`<div class="evidence"><div class="evidence-heading"><strong>${escapeHtml(TOOL[ev.tool.split('.')[0]] || ev.tool)}取证</strong>${badge(ev.status)}</div><p>${escapeHtml(ev.result.summary || ev.error)}</p><code>${escapeHtml(ev.id)}</code></div>`).join('') || '<p class="muted">尚无工具取证结果</p>';
    body+='</section>';
    if(expected) body+=`<section class="report"><h3>预期研判 · ${escapeHtml(STATUS[expected.outcome])}</h3><p>${escapeHtml(expected.artifact.facts[0]?.statement || '暂无已核验事实')}</p><p>根因：未确认。${escapeHtml(expected.artifact.gaps.join('；'))}</p><p>处置建议：${escapeHtml(expected.actionProposal)}，须经人工审批。</p><span class="muted">合成样本预期结果 · 非实时 AI 诊断</span></section>`;
    else body+=`<section class="report"><h3>研判摘要</h3><p>${escapeHtml(item.incident?.summary || '等待后端研判或人工补充')}</p><p>${escapeHtml(item.incident?.rootCauseHint || '尚无已确认根因')}</p><p>${escapeHtml(item.incident?.runbookHint || '')}</p></section>`;
  } else if(state.tab==='flow') {
    body=`<h3>${state.live?'后端执行记录':'预期处理路径'}</h3><div class="path">${(expected?.taskPlan || item.trace?.agentSteps?.map(x=>`${x.agent}：${x.action || ''}`) || []).map(step=>`<span>${escapeHtml(step.replace(/^(metrics|logs|traces|change|code|host|database|dependency)/,x=>TOOL[x]))}</span>`).join('')}</div><pre>${escapeHtml(JSON.stringify(expected?{权限:item.authorization,预期成果:expected.artifact,禁止动作:expected.forbiddenActions,恢复验证:expected.verification}:item.trace || {状态:'尚无执行轨迹'},null,2))}</pre>`;
  } else if(state.tab==='notes') {
    const notes=state.notes[item.id] || [];
    body=`<h3>处置记录</h3>${notes.length?notes.map(n=>`<div class="note"><span class="muted">${escapeHtml(n.actor)} · ${escapeHtml(n.createdAt || '')}</span><p>${escapeHtml(n.note)}</p></div>`).join(''):'<p class="muted">暂无处置记录</p>'}<form id="noteForm" class="notes-form"><input id="noteText" required aria-label="处置备注" placeholder="补充排查发现或交接信息"><button ${state.live&&!item.incident?'disabled':''}>添加记录</button></form>`;
  } else body=`<pre>${escapeHtml(JSON.stringify(item,null,2))}</pre>`;
  $('detail').innerHTML=`<div class="detail-top"><span class="case-id">${escapeHtml(item.id)}</span>${variantSelect}</div><h2>${escapeHtml(a.title)}</h2><div class="meta-line">${badge(a.severity)}<span>${escapeHtml(TYPE[a.alertType.toUpperCase()])} / ${escapeHtml(a.alertType.toUpperCase())}</span><span>${escapeHtml(a.labels?.service || '—')}</span><span>${escapeHtml(a.labels?.env || '—')}</span></div><div class="detail-tabs">${[['evidence','证据与报告'],['flow','协作路径'],['notes','处置记录'],['json','原始数据']].map(([key,label])=>`<button data-tab="${key}" class="${state.tab===key?'active':''}">${label}</button>`).join('')}</div>${body}<div class="actions"><button data-action="ack" ${state.live&&!item.incident?'disabled':''}>确认接手</button><button data-action="resolve" ${state.live&&!item.incident?'disabled':''}>标记已关闭</button><button data-action="export">导出当前案例 ↓</button></div>`;
}
function exportJson(data,name) {const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function load() {
  $('refresh').disabled=true;
  try {
    if(state.live) {
      const [alerts,incidents,traces]=await Promise.all([api('/api/admin/alerts'),api('/api/admin/incidents'),api('/api/admin/agent-traces')]);
      state.items=alerts.map(alert=>{const trace=traces.find(x=>x.alertId===alert.id);const incident=incidents.find(x=>x.id===trace?.incidentId || x.lastAlertId===alert.id);return {id:String(alert.id),alert,incident,trace,status:incident?.status,toolSnapshots:[]};});
      notice('实时数据 · 当前后台返回的最近告警；证据内容以实际工具结果为准');
    } else {
      state.items=[...state.local,...state.dataset.cases.filter(x=>state.view==='dataset'||x.variant==='normal')];
      notice('模拟环境 · 文档第 19 章场景；全部数据为合成样本，处置仅保存在本页');
    }
    render();
  } catch(error) {notice(error.message);} finally {$('refresh').disabled=false;}
}
async function action(name) {
  const item=chosen();if(!item)return;
  if(name==='export'){exportJson(item,`${item.id}.json`);return;}
  try {
    if(state.live){const result=await api(`/api/admin/incidents/${item.incident.id}/${name}`,{method:'POST',body:JSON.stringify({actor:state.user})});item.incident=result;item.status=result.status;}
    else item.status=name==='ack'?'ACKNOWLEDGED':'RESOLVED';
    notice(`${state.live?'已更新事件':'模拟状态已更新'}：${STATUS[item.status]}`);renderDetail();
  } catch(error){notice(error.message);}
}
$('list').addEventListener('click',event=>{const row=event.target.closest('[data-id]');if(row){state.selected=row.dataset.id;state.tab='evidence';render();}});
$('detail').addEventListener('change',event=>{if(event.target.id==='variant'){const item=state.dataset.cases.find(x=>x.id===event.target.value);if(!state.items.some(x=>x.id===item.id))state.items=state.items.map(x=>x.scenario===item.scenario?item:x);state.selected=item.id;render();}});
$('detail').addEventListener('click',async event=>{
  const tab=event.target.closest('[data-tab]');const button=event.target.closest('[data-action]');
  if(tab){state.tab=tab.dataset.tab;const item=chosen();if(state.tab==='notes'&&state.live&&item.incident){try {const notes=await api(`/api/admin/incidents/${item.incident.id}/notes`);state.notes[item.id]=notes;}catch(e){notice(e.message);}}renderDetail();}
  if(button){button.disabled=true;try{await action(button.dataset.action);}finally{if(button.isConnected)button.disabled=false;}}
});
$('detail').addEventListener('submit',async event=>{
  if(event.target.id!=='noteForm')return;event.preventDefault();const item=chosen(),note=$('noteText').value.trim();if(!note)return;
  const button=event.target.querySelector('button');button.disabled=true;
  try{const record=state.live?await api(`/api/admin/incidents/${item.incident.id}/notes`,{method:'POST',body:JSON.stringify({actor:state.user,note})}):{actor:'模拟值班人员',note,createdAt:new Date().toLocaleString('zh-CN')};(state.notes[item.id] ||= []).push(record);renderDetail();}catch(e){notice(e.message);button.disabled=false;}
});
['search','priority','type'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',render));
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{state.view=button.dataset.view;if(state.view==='dataset'){state.live=false;state.token='';}document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x===button));load();}));
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$('allTab').onclick=()=>{['search','priority','type'].forEach(id=>$(id).value='');render();};
$('refresh').onclick=load;
$('download').onclick=()=>exportJson(state.dataset,'alert-replay-v1.json');
function connect(){if(state.live){state.live=false;state.token='';load();}else $('loginDialog').showModal();}
$('connect').onclick=connect;$('mode').onclick=connect;
$('loginForm').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('[type=submit]');button.disabled=true;$('loginError').textContent='';
  try{state.token=btoa(String.fromCharCode(...new TextEncoder().encode(`${$('username').value}:${$('password').value}`)));const profile=await api('/api/profile');state.user=profile.username;state.live=true;state.view='alerts';$('loginDialog').close();$('password').value='';await load();}catch(e){state.token='';$('loginError').textContent=e.message;}finally{button.disabled=false;}
});
$('newAlert').onclick=()=>{$('submitHint').textContent=state.live?'将提交至当前后端':'模拟接入 · 不写入数据库，不调用模型';$('alertDialog').showModal();};
$('alertForm').addEventListener('submit',async event=>{event.preventDefault();const text=$('rawInput').value.trim(),service=$('inputService').value.trim();if(!text||!service)return;$('submitAlert').disabled=true;
  const alert={source:'manual',title:text.slice(0,80),description:text,severity:$('inputPriority').value,alertType:$('inputType').value,labels:{service,env:'staging'},annotations:{}};
  try{if(state.live){await api('/api/alerts/webhook',{method:'POST',body:JSON.stringify(alert)});await load();notice('告警已提交至后端');}else{const item={id:`LOCAL-${Date.now()}`,alert,toolSnapshots:[]};state.local.unshift(item);state.selected=item.id;await load();notice('模拟告警已接收，尚无取证结果');}$('alertDialog').close();$('rawInput').value='';}catch(e){$('submitHint').textContent=e.message;}finally{$('submitAlert').disabled=false;}
});
(async()=>{try{const response=await fetch('/fixtures/alert-replay-v1.json');if(!response.ok)throw new Error('测试数据加载失败');state.dataset=await response.json();await load();}catch(e){notice(e.message);$('detail').innerHTML='<div class="empty">数据不可用，请刷新重试</div>';$('refresh').onclick=()=>location.reload();}})();
