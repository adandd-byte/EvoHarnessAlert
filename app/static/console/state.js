const filters = () => ({search:'',priority:'',type:'',status:'',variant:'',page:1,sort:'severity',direction:1,selected:null});
export const state = {
  mode:'demo', view:'alerts', dataset:[], demo:[], demoCache:new Map(), live:[], notes:new Map(),
  filters:{demo:filters(),live:filters(),dataset:filters()}, tab:'overview',
  expanded:false,mobileDetail:false,loading:true,error:'',busy:false,connected:false,
};
export const scope = () => state.view === 'dataset' ? 'dataset' : state.mode;
export const currentFilters = () => state.filters[scope()];
export const items = () => scope() === 'dataset' ? state.dataset : state[state.mode];
export const selected = () => items().find(item => item.id === currentFilters().selected);
export const isLive = () => scope() === 'live';
export const PAGE_SIZE = 10;
export function filteredItems() {
  const f = currentFilters(), q = f.search.trim().toLowerCase();
  return items().filter(x => (!f.priority || x.alert.severity === f.priority) && (!f.type || x.alert.alertType.toUpperCase() === f.type) && (!f.status || x.status === f.status) && (!f.variant || x.variant === f.variant) && (!q || `${x.id} ${x.alert.title} ${x.alert.labels?.service || ''}`.toLowerCase().includes(q))).sort((a,b) => {
    const value = x => ({severity:x.alert.severity,title:x.alert.title,type:x.alert.alertType,service:x.alert.labels?.service || '',status:x.status})[f.sort] || x.id;
    return String(value(a)).localeCompare(String(value(b)), 'zh-CN') * f.direction;
  });
}
