import {ApiClient} from './api.js';
import {state,scope,currentFilters,selected,isLive} from './state.js';
import {demoItem,liveItems,download,STATUS} from './adapters.js';
import {$,render,renderDetail,notify,icons} from './views.js';
const api=new ApiClient();
let generation=0, notesGeneration=0, resolveTarget=null, intakeMode='demo';
const message=()=>notify(scope()==='dataset'?'100 条合成回放样本，预期结论不代表生产验证结果。':isLive()?'后端数据 · 仅展示接口返回的事实与研判记录。':'模拟回放 · 处置仅保存在本页，不会向后端提交操作。');
function showDialog(id) { $(id).showModal(); icons(); }
async function load() {
  const version=++generation;
  state.loading=true;state.error='';render();
  try {
    if(!state.dataset.length) {
      const response=await fetch('/fixtures/alert-replay-v1.json');
      if(!response.ok) throw new Error('样本加载失败，请重试');
      const data=await response.json();
      state.dataset=data.cases.map(demoItem);
      state.demoCache=new Map(state.dataset.map(x=>[x.id,demoItem(x)]));
      state.demo=[...state.demoCache.values()].filter(x=>x.variant==='normal');
    }
    if(isLive()) {
      if(!state.connected) throw new Error('尚未连接后端，请先连接账号');
      const data=await api.snapshot();
      if(version!==generation)return;
      state.live=liveItems(data);
    }
    if(version!==generation)return;
    message();
  } catch(e) {if(version===generation){state.error=e.message;notify(e.message,true);}}
  finally {if(version===generation){state.loading=false;render();}}
}
function switchView(view) { ++notesGeneration;state.view=view;state.expanded=false;state.mobileDetail=false;state.tab='overview';message();render(); }
async function loadNotes() {
  const x=selected();if(!isLive()||!x?.incident)return;
  const key=`live:${x.id}`, version=++notesGeneration;
  state.notes.set(key,{loading:true,rows:[]});renderDetail();
  try { const rows=await api.request(`/api/admin/incidents/${x.incident.id}/notes`);if(version===notesGeneration)state.notes.set(key,{rows}); }
  catch(e){if(version===notesGeneration)state.notes.set(key,{error:e.message,rows:[]});}
  if(version===notesGeneration&&scope()==='live'&&selected()?.id===x.id&&state.tab==='notes')renderDetail();
}
async function updateIncident(action, target, note='') {
  const {item, live, key}=target;
  state.busy=true;renderDetail();
  try {
    if(live) {
      const result=await api.request(`/api/admin/incidents/${item.incident.id}/${action}`,{method:'POST',body:JSON.stringify({actor:api.user,note:note || undefined})});
      for(const x of state.live.filter(x=>x.incident?.id===result.id)){x.incident=result;x.status=result.status;}
      state.notes.delete(key);
    } else {
      item.status=action==='ack'?'ACKNOWLEDGED':'RESOLVED';
      const rows=state.notes.get(key)?.rows || [];
      rows.push({actor:'模拟值班人员',note:note || '已确认接手',createdAt:new Date().toISOString()});state.notes.set(key,{rows});
    }
    notify(`${live?'后端事件':'本页模拟事件'}已更新：${STATUS[item.status]}`);
  } finally {state.busy=false;render();}
}
function target() {return {item:selected(),live:isLive(),key:`${scope()}:${selected().id}`};}
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.evidence){
    state.tab='evidence';renderDetail();
    const row=[...document.querySelectorAll('[data-evidence-id]')].find(x=>x.dataset.evidenceId===button.dataset.evidence);
    if(row){row.open=true;row.querySelector('summary').focus();row.scrollIntoView({block:'nearest'});}
    return;
  }
  if(button.hasAttribute('data-close'))button.closest('dialog').close();
  if(button.dataset.view)switchView(button.dataset.view);
  if(button.dataset.mode){
    if(button.dataset.mode==='live'&&!state.connected){showDialog('loginDialog');return;}
    ++notesGeneration;state.mode=button.dataset.mode;state.tab='overview';state.mobileDetail=false;await load();
  }
  if(button.dataset.id){++notesGeneration;currentFilters().selected=button.dataset.id;state.tab='overview';state.mobileDetail=true;render();}
  if(button.dataset.sort){const f=currentFilters();f.direction=f.sort===button.dataset.sort?-f.direction:1;f.sort=button.dataset.sort;render();}
  if(button.dataset.page){currentFilters().page+=Number(button.dataset.page);render();}
  if(button.hasAttribute('data-retry'))await load();
  if(button.dataset.tab){state.tab=button.dataset.tab;renderDetail();if(state.tab==='notes')await loadNotes();}
  const action=button.dataset.action;if(!action)return;
  const x=selected();if(!x)return;
  if(action==='back'){state.mobileDetail=false;render();}
  if(action==='expand'){state.expanded=!state.expanded;render();}
  if(action==='export')download(x,`${x.id}.json`);
  if(action==='reload-notes')await loadNotes();
  if(action==='load'){
    if(!state.demo.some(v=>v.id===x.id))state.demo.unshift(state.demoCache.get(x.id));
    Object.assign(state.filters.demo,{search:'',priority:'',type:'',status:'',variant:'',page:1});
    state.mode='demo';state.filters.demo.selected=x.id;switchView('alerts');state.mobileDetail=true;render();
  }
  if(action==='ack'){try{await updateIncident('ack',target());}catch(e){notify(e.message,true);}}
  if(action==='resolve'){
    resolveTarget=target();$('resolveNote').value='';$('resolveError').textContent='';
    $('resolveHint').textContent=`${isLive()?'将关闭后端事件':'仅关闭本页模拟事件'}：${x.alert.title}`;showDialog('resolveDialog');
  }
});
document.addEventListener('change',event=>{
  if(event.target.id!=='variant')return;
  const x=state.dataset.find(x=>x.id===event.target.value);
  if(scope()==='demo'&&!state.demo.some(v=>v.id===x.id))state.demo=state.demo.map(v=>v.id===currentFilters().selected?state.demoCache.get(x.id):v);
  currentFilters().selected=x.id;state.tab='overview';render();
});
for(const id of ['search','priority','type','status','variantFilter']) $(id).addEventListener(id==='search'?'input':'change',()=>{const f=currentFilters();f[id==='variantFilter'?'variant':id]=$(id).value;f.page=1;render();});
$('reset').onclick=()=>{Object.assign(currentFilters(),{search:'',priority:'',type:'',status:'',variant:'',page:1});render();};
$('collapse').onclick=()=>document.body.classList.toggle('collapsed');
$('refresh').onclick=load;
$('download').onclick=()=>download({schemaVersion:'AlertReplayV1',cases:state.dataset.map(({status,origin,...x})=>x)},'alert-replay-v1.json');
$('connect').onclick=()=>{
  if(!state.connected){$('loginError').textContent='';showDialog('loginDialog');return;}
  ++generation;++notesGeneration;api.disconnect();state.connected=false;state.mode='demo';state.live=[];state.loading=false;state.error='';state.filters.live.selected=null;state.notes.forEach((_,key)=>{if(key.startsWith('live:'))state.notes.delete(key);});message();render();
};
$('loginForm').onsubmit=async event=>{
  event.preventDefault();const b=event.target.querySelector('[type=submit]');b.disabled=true;b.textContent='正在连接…';$('loginError').textContent='';
  try {const data=await api.login($('username').value,$('password').value);state.live=liveItems(data);state.connected=true;state.mode='live';state.view='alerts';state.error='';state.loading=false;state.mobileDetail=false;state.tab='overview';$('loginDialog').close();message();render();}
  catch(e){api.disconnect();$('loginError').textContent=e.message;}
  finally{b.disabled=false;b.textContent='连接工作空间';$('password').value='';}
};
$('newAlert').onclick=()=>{intakeMode=isLive()?'live':'demo';$('submitHint').textContent=intakeMode==='live'?'目标：当前后端。提交后将写入业务数据库并触发后端处理。':'目标：本页模拟环境。不调用模型、不写入后端数据库。';$('submitError').textContent='';showDialog('alertDialog');};
$('alertForm').onsubmit=async event=>{
  event.preventDefault();const text=$('rawInput').value.trim(),service=$('inputService').value.trim();if(!text||!service)return;
  const b=$('submitAlert');b.disabled=true;b.textContent='正在提交…';$('submitError').textContent='';
  const alert={source:'manual',title:text.slice(0,80),description:text,severity:$('inputPriority').value,alertType:$('inputType').value,labels:{service,env:'staging'},annotations:{}};
  try{
    if(intakeMode==='live'){
      const result=await api.request('/api/alerts/webhook',{method:'POST',body:JSON.stringify(alert)});
      Object.assign(state.filters.live,{search:'',priority:'',type:'',status:'',page:1,selected:String(result.alertId)});
      await load();
    } else {
      const x={id:`LOCAL-${Date.now()}`,alert,status:'OPEN',toolSnapshots:[],origin:'demo'};state.demo.unshift(x);
      Object.assign(state.filters.demo,{search:'',priority:'',type:'',status:'',page:1,selected:x.id});
      state.mode='demo';state.view='alerts';state.mobileDetail=true;render();
    }
    $('alertDialog').close();$('rawInput').value='';notify(intakeMode==='live'?'告警已提交至后端。':'模拟告警已接收，尚无取证结果。');
  }catch(e){$('submitError').textContent=e.message;}finally{b.disabled=false;b.textContent='提交告警';}
};
$('resolveForm').onsubmit=async event=>{
  event.preventDefault();const b=event.target.querySelector('[type=submit]');b.disabled=true;$('resolveError').textContent='';
  try{await updateIncident('resolve',resolveTarget,$('resolveNote').value.trim());$('resolveDialog').close();}
  catch(e){$('resolveError').textContent=e.message;}finally{b.disabled=false;}
};
$('detail').addEventListener('submit',async event=>{
  if(event.target.id!=='noteForm')return;event.preventDefault();const t=target(),text=$('noteText').value.trim();if(!text)return;
  const b=event.target.querySelector('button');b.disabled=true;
  try{
    const record=t.live?await api.request(`/api/admin/incidents/${t.item.incident.id}/notes`,{method:'POST',body:JSON.stringify({actor:api.user,note:text})}):{actor:'模拟值班人员',note:text,createdAt:new Date().toISOString()};
    const rows=state.notes.get(t.key)?.rows || [];rows.push(record);state.notes.set(t.key,{rows});
    if(`${scope()}:${selected()?.id}`===t.key)renderDetail();
    notify('处置记录已保存');
  }catch(e){notify(e.message,true);b.disabled=false;}
});
document.addEventListener('keydown',event=>{
  if(event.target.matches('[role=tab]')&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
    event.preventDefault();const tabs=[...document.querySelectorAll('[role=tab]')],index=tabs.indexOf(event.target);
    const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    tabs[next].click();document.querySelectorAll('[role=tab]')[next].focus();
  }
});
icons();load();
