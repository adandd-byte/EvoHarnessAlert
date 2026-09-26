import {state,scope,currentFilters,items,selected,isLive,filteredItems,PAGE_SIZE} from './state.js';
import {environmentName, variantName, paymentOverview} from './business.js';
import {TYPE,STATUS,TOOL,esc,time} from './adapters.js';
export const $ = id => document.getElementById(id);
export const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
export const icons = () => window.lucide?.createIcons();
const badge = value => `<span class="badge ${['P0','P1','P2','P3','OPEN','ACKNOWLEDGED','RESOLVED','SUCCESS','TIMEOUT','DENIED'].includes(value)?value.toLowerCase():''}">${esc(STATUS[value] || value)}</span>`;
const empty = (heading, body, retry=false) => `<div class="empty">${icon('inbox')}<strong>${esc(heading)}</strong><p>${esc(body)}</p>${retry?'<button data-retry>重新加载</button>':''}</div>`;
export function notify(text, error=false) { $('notice').textContent=text; $('notice').className=error?'notice-error':''; }
export function render() {
  const dataset=state.view==='dataset', f=currentFilters(), list=filteredItems();
  f.page=Math.max(1,Math.min(f.page,Math.ceil(list.length/PAGE_SIZE)||1));
  const page=list.slice((f.page-1)*PAGE_SIZE,f.page*PAGE_SIZE);
  if(!list.some(x=>x.id===f.selected)) f.selected=page[0]?.id;
  const heading=dataset?'回放数据集':'告警工作台';
  $('pageTitle').textContent=heading; $('breadcrumb').textContent=heading;
  $('pageSubtitle').textContent=dataset?'20 个告警场景 · 5 类回放变体 · 可追溯的冻结证据':'关注异常，追踪证据，闭环处置。';
  $('listTitle').textContent=dataset?'场景样本':'告警队列';
  $('resultCount').textContent=`${list.length} 条${list.length!==items().length?` / 共 ${items().length} 条`:''}`;
  $('environment').textContent=isLive()?'后端接入':'演练数据';
  $('connection').textContent=state.connected?'断开后端':'连接后端';
  $('connect').classList.toggle('connected',state.connected);
  $('modeSwitch').hidden=dataset; $('status').hidden=dataset; $('variantFilter').hidden=!dataset;
  $('footerInfo').textContent=dataset?'合成数据 · 未经人工验证':isLive()?'后端事实记录 · 缺失信息不作推断':'模拟记录仅在本页有效';
  for(const id of ['search','priority','type','status']) $(id).value=f[id];
  $('variantFilter').value=f.variant;
  document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===state.view);b.setAttribute('aria-current',b.dataset.view===state.view?'page':'false');});
  document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('active',b.dataset.mode===state.mode);b.setAttribute('aria-pressed',String(b.dataset.mode===state.mode));});
  const all=items(), urgent=all.filter(x=>['P0','P1'].includes(x.alert.severity)&&x.status!=='RESOLVED').length;
  const stats=dataset?[['database','回放样本',all.length,'全部为合成样本'],['layers','基础场景',new Set(all.map(x=>x.scenario)).size,'覆盖四类告警'],['git-branch','回放变体',new Set(all.map(x=>x.variant)).size,'含超时、冲突、权限'],['shield-check','人工审核',all.filter(x=>x.humanReviewed).length,'不等同于生产验证']]:[['bell-ring','加载告警',all.length,'当前加载范围'],['circle-alert','待处理 P0 / P1',urgent,'最高与高优先级'],['activity','处置中',all.filter(x=>x.status==='ACKNOWLEDGED').length,'已确认接手'],['circle-check','已关闭',all.filter(x=>x.status==='RESOLVED').length,'处置状态统计']];
  $('stats').innerHTML=stats.map(([i,label,n,sub],idx)=>`<div class="stat"><div class="stat-icon stat-${idx}">${icon(i)}</div><div><span>${label}</span><strong>${state.loading?'—':n}</strong><small>${sub}</small></div></div>`).join('');
  $('workbench').classList.toggle('expanded',state.expanded); $('workbench').classList.toggle('mobile-detail',state.mobileDetail); $('workbench').classList.toggle('dataset',dataset);
  document.body.classList.toggle('showing-detail',state.mobileDetail);
  if(state.loading) $('table').innerHTML=`<div class="skeleton" aria-label="正在加载告警">${Array.from({length:8},()=>'<div></div>').join('')}</div>`;
  else if(state.error && (isLive() || !state.dataset.length)) $('table').innerHTML=empty('数据加载失败',state.error,true);
  else if(!page.length) $('table').innerHTML=empty(all.length?'没有符合筛选的结果':'暂无告警',all.length?'调整筛选条件或重置后查看。':'接入告警后会显示在这里。');
  else {
    const columns=dataset?[['severity','优先级'],['title','场景 / 样本'],['type','类型'],['','变体'],['','预期结果'],['','审核']]:[['severity','优先级'],['title','告警 / 服务'],['type','类型'],['','环境'],['status','处置状态']];
    $('table').innerHTML=`<table><thead><tr>${columns.map(([key,label])=>`<th>${key?`<button data-sort="${key}" aria-label="按${label}排序">${label}${icon(f.sort===key?(f.direction===1?'arrow-up':'arrow-down'):'arrow-up-down')}</button>`:label}</th>`).join('')}</tr></thead><tbody>${page.map(x=>`<tr class="alert-row ${f.selected===x.id?'selected':''}"><td>${badge(x.alert.severity)}</td><td class="title-cell"><button data-id="${esc(x.id)}" aria-label="查看 ${esc(x.alert.title)}"><strong>${esc(x.alert.title)}</strong><span>${esc(dataset?`场景 ${String(x.scenario).padStart(2,'0')} · ${variantName(x.variant)}`:x.alert.labels?.service || '未标注服务')}</span></button></td><td><span class="type-label">${esc(TYPE[x.alert.alertType.toUpperCase()] || x.alert.alertType)}</span></td>${dataset?`<td>${esc(variantName(x.variant))}</td><td>${esc(STATUS[x.expected?.outcome] || '未提供')}</td><td><span class="review-state">${x.humanReviewed?'已审核':'未审核'}</span></td>`:`<td class="env-cell">${esc(environmentName(x.alert.labels?.env))}</td><td>${badge(x.status)}</td>`}</tr>`).join('')}</tbody></table>`;
  }
  $('pagination').innerHTML=`<span>每页 ${PAGE_SIZE} 条 · ${list.length?((f.page-1)*PAGE_SIZE+1):0}–${Math.min(f.page*PAGE_SIZE,list.length)} / ${list.length}</span><div><button class="icon-button" data-page="-1" aria-label="上一页" title="上一页" ${f.page===1?'disabled':''}>${icon('chevron-left')}</button><span>${f.page} / ${Math.max(1,Math.ceil(list.length/PAGE_SIZE))}</span><button class="icon-button" data-page="1" aria-label="下一页" title="下一页" ${f.page*PAGE_SIZE>=list.length?'disabled':''}>${icon('chevron-right')}</button></div>`;
  renderDetail(); icons();
}
export function renderDetail() {
  const x=selected();
  if(!x || state.loading || (state.error&&isLive())) { $('detail').innerHTML=empty('选择一条告警','查看研判结果与处置记录');icons();return; }
  const a=x.alert, ex=x.expected, live=isLive(), dataset=scope()==='dataset';
  const disabled=state.busy || dataset || (live&&!x.incident) || x.status==='RESOLVED';
  const reason=dataset?'样本只读，载入演练工作台后可模拟处置':live&&!x.incident?'未关联事件（Incident），无法接手、关闭或追加记录':live?'后端事件处置': '模拟操作仅保存在本页，刷新后重置';
  let body='';
  if(state.tab==='overview') {
    body=`<div class="detail-section"><div class="section-label">${icon('file-text')}告警原文</div><p class="raw-text">${esc(a.description || a.title)}</p></div><div class="metadata"><div><span>服务</span><strong>${esc(a.labels?.service || '未提供')}</strong></div><div><span>环境</span><strong>${esc(environmentName(a.labels?.env))}</strong></div><div><span>负责人</span><strong>${esc(x.incident?.owner || '未提供')}</strong></div><div><span>触发时间</span><strong>${esc(time(a.startsAt))}</strong></div></div>`;
    if(ex) body+=`<div class="report"><div class="section-label">${icon('scan-search')}本案例的预设判断 <span class="badge">${esc(STATUS[ex.outcome])}</span></div><p>根因尚未确认，需由值班人员进一步核验。</p><div class="fact-label">已记录事实</div>${ex.artifact.facts.map(f=>`<p>${esc(f.statement)}<span class="reference">${esc(f.evidenceRefs.join(' · '))}</span></p>`).join('') || '<p class="muted">暂无可用事实</p>'}<div class="fact-label">待验证假设</div><p>${esc(ex.artifact.hypotheses.map(h=>typeof h==='string'?h:JSON.stringify(h)).join('；') || '未提供')}</p><div class="fact-label">证据缺口</div><p>${esc(ex.artifact.gaps.join('；') || '无已记录缺口')}</p></div><div class="detail-section"><div class="section-label">${icon('book-open')}故障处理手册（Runbook）</div><p>${esc(ex.actionProposal)}</p><div class="approval-note">${icon('shield-check')}执行前须人工审批，本页不会执行自动修复。</div></div>`;
    else body+=`<div class="report"><div class="section-label">${icon('scan-search')}研判概览</div><p>${esc(x.incident?.summary || '尚无后端研判结果')}</p><div class="fact-label">影响范围</div><p>${esc(x.incident?.impact || '未提供')}</p><div class="fact-label">根因线索 · 待验证</div><p>${esc(x.incident?.rootCauseHint || '未提供')}</p><div class="fact-label">故障处理手册（Runbook）</div><p>${esc(x.incident?.runbookHint || '未提供')}</p></div>`;
    if(!live && x.businessContext) body=paymentOverview(x);
  } else if(state.tab==='evidence') {
    body=`<div class="tab-heading"><h3>${live?'后端证据':'冻结证据快照'}</h3><span class="muted">${x.toolSnapshots.length} 项</span></div>`;
    body+=x.toolSnapshots.map((ev,i)=>`<details class="evidence" data-evidence-id="${esc(ev.id)}"><summary><span class="evidence-number">${String(i+1).padStart(2,'0')}</span><div><strong>${esc(ev.label || TOOL[ev.tool.split('.')[0]] || ev.tool)}</strong><span>${esc(ev.result?.summary || ev.error || '无返回结果')}</span></div>${badge(ev.status==='MISSING'?'待补充':ev.status)}${icon('chevron-down')}</summary><div class="evidence-body"><div class="fact-label">引用编号</div><code>${esc(ev.id)}</code><div class="fact-label">时间窗口</div><p>${esc(time(ev.request?.from))} → ${esc(time(ev.request?.to))}</p><div class="fact-label">来源</div><p>${esc(ev.source)}</p><pre>${esc(JSON.stringify(ev.result || {},null,2))}</pre></div></details>`).join('') || empty('暂无工具证据',live?'当前接口未返回取证快照，不生成模拟证据。':'此告警尚未附带取证结果。');
    if(ex?.artifact.gaps.length) body+=`<div class="gap-note"><strong>缺失 / 冲突证据</strong><p>${esc(ex.artifact.gaps.join('；'))}</p></div>`;
  } else if(state.tab==='flow') {
    const steps=x.businessContext?.nextSteps || ex?.taskPlan || x.trace?.agentSteps || [];
    body=`<div class="tab-heading"><h3>${ex?'预期流程 · 非实际执行':'后端协作轨迹'}</h3><span class="muted">${steps.length} 步</span></div><ol class="timeline">${steps.map(s=>`<li><span class="timeline-dot"></span>${typeof s==='string'?`<strong>${esc(s)}</strong>`:`<strong>${esc(s.agent || s.name || 'Agent 协作记录')}</strong><pre>${esc(JSON.stringify(s,null,2))}</pre>`}</li>`).join('')}</ol>${!steps.length?empty('暂无执行轨迹','仅展示后端实际返回的 Agent 步骤。'):''}`;
  } else if(state.tab==='notes') {
    const notes=state.notes.get(`${scope()}:${x.id}`);
    body=`<div class="tab-heading"><h3>处置记录</h3><span class="muted">${live?'后端审计记录':'本页临时记录'}</span></div>${notes?.loading?'<div class="skeleton"><div></div><div></div></div>':notes?.error?`<p class="error-text">${esc(notes.error)}</p><button data-action="reload-notes">重试</button>`:(notes?.rows || []).map(n=>`<article class="note"><div><strong>${esc(n.actor)}</strong><span>${esc(time(n.createdAt))}</span></div><p>${esc(n.note)}</p></article>`).join('') || '<div class="muted no-notes">尚无处置记录</div>'}<form id="noteForm"><label for="noteText">补充排查发现或交接信息</label><textarea id="noteText" rows="3" required ${disabled?'disabled':''}></textarea><button class="primary" ${disabled?'disabled':''}>${icon('plus')}添加记录</button></form>`;
  } else body=`<div class="tab-heading"><h3>原始数据</h3><button data-action="export" title="下载当前样本 JSON">${icon('download')}JSON</button></div><pre class="json">${esc(JSON.stringify(x,null,2))}</pre>`;
  $('detail').innerHTML=`<div class="detail-fixed"><div class="detail-kicker"><button class="icon-button mobile-back" data-action="back" aria-label="返回列表">${icon('arrow-left')}</button><span>${esc(x.scenario ? `场景 ${String(x.scenario).padStart(2,'0')} · ${variantName(x.variant)}` : live ? `告警编号 ${x.id}` : '手动录入告警')}</span><span class="detail-origin">${live?'后端告警':'模拟样本'}</span><button class="icon-button expand" data-action="expand" title="${state.expanded?'恢复分栏':'展开详情'}" aria-label="${state.expanded?'恢复分栏':'展开详情'}">${icon(state.expanded?'minimize-2':'maximize-2')}</button></div><h2>${esc(a.title)}</h2><div class="detail-meta">${badge(a.severity)}${badge(x.status)}<span>${esc(TYPE[a.alertType.toUpperCase()] || a.alertType)} / ${esc(a.alertType.toUpperCase())}</span></div><div class="detail-actions">${dataset?`<button class="primary" data-action="load">${icon('corner-down-left')}载入演练工作台</button>`:`<button class="primary" data-action="ack" ${disabled||x.status==='ACKNOWLEDGED'?'disabled':''}>${icon('user-check')}确认接手</button><button data-action="resolve" ${disabled?'disabled':''}>${icon('circle-check')}关闭事件</button>`}<button class="icon-button" data-action="export" title="下载当前案例" aria-label="下载当前案例">${icon('download')}</button></div><p class="action-reason">${reason}</p>${!live&&x.scenario?`<label class="variant-label">演练情况<select id="variant" aria-label="回放变体选择">${state.dataset.filter(v=>v.scenario===x.scenario).map(v=>`<option value="${esc(v.id)}" ${v.id===x.id?'selected':''}>${esc(variantName(v.variant))}</option>`).join('')}</select></label>`:''}<div class="detail-tabs" role="tablist">${[['overview','研判概览'],['evidence','证据'],['flow','协作记录'],['notes','处置记录'],['json','原始数据']].map(([k,label])=>`<button role="tab" aria-selected="${state.tab===k}" data-tab="${k}" class="${state.tab===k?'active':''}">${label}</button>`).join('')}</div></div><div class="detail-body" role="tabpanel">${body}</div>`;
  icons();
}
