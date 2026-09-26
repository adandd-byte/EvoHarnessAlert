export const TYPE = {PROBLEM:'问题',BUSINESS:'业务',EVENT:'事件',HOST:'主机'};
export const STATUS = {OPEN:'待接手',ACKNOWLEDGED:'处理中',RESOLVED:'已关闭',FIRING:'触发中',SUCCESS:'已返回',TIMEOUT:'查询超时',DENIED:'权限拒绝',NEED_MORE_EVIDENCE:'待补充证据',CONFLICT:'证据冲突',ACCESS_DENIED:'权限拒绝',WAITING_APPROVAL:'等待审批'};
export const TOOL = {metrics:'监控指标',logs:'日志',traces:'调用链路',change:'发布变更',code:'代码',host:'主机',database:'数据库',dependency:'服务依赖'};
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const time = value => value ? (Number.isNaN(Date.parse(value)) ? String(value) : new Date(value).toLocaleString('zh-CN',{hour12:false})) : '未提供';
export function demoItem(value) { return {...structuredClone(value),status:'OPEN',origin:'demo'}; }
export function liveItems([alerts, incidents, traces]) {
  if (![alerts,incidents,traces].every(Array.isArray)) throw new Error('后端数据结构不符合预期');
  return alerts.map(alert => {
    const trace = traces.find(x => x.alertId === alert.id);
    const incident = incidents.find(x => trace?.incidentId === x.id || x.lastAlertId === alert.id);
    return {id:String(alert.id),alert,trace,incident,status:incident?.status || (alert.status?.toUpperCase() === 'RESOLVED' ? 'RESOLVED':'OPEN'),toolSnapshots:[],origin:'live'};
  });
}
export function download(data, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url),1000);
}
