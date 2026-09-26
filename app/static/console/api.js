export class ApiClient {
  constructor() { this.token = ''; this.user = ''; }
  async request(path, options = {}, token = this.token) {
    let response;
    try { response = await fetch(path, {...options, headers: {'Content-Type':'application/json', ...(token ? {Authorization:`Basic ${token}`} : {})}}); }
    catch (error) { if (error.name === 'AbortError') throw error; throw new Error('后端连接中断，请检查服务后重试'); }
    if (!response.ok) throw new Error(response.status === 401 ? '认证失败（401），请重新连接账号' : response.status === 403 ? '权限不足（403），请联系管理员授权' : `请求失败（${response.status}），请检查后端服务后重试`);
    try { return await response.json(); } catch { throw new Error('后端未返回有效 JSON，请确认当前站点已启动 API 服务'); }
  }
  async snapshot(token = this.token) {
    return Promise.all(['alerts','incidents','agent-traces'].map(name => this.request(`/api/admin/${name}`, {}, token)));
  }
  async login(username, password) {
    const token = btoa(String.fromCharCode(...new TextEncoder().encode(`${username}:${password}`)));
    const profile = await this.request('/api/profile', {}, token);
    const data = await this.snapshot(token);
    this.token = token; this.user = profile.username;
    return data;
  }
  disconnect() { this.token = ''; this.user = ''; }
}
