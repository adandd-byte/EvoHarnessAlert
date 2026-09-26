import {esc, time} from './adapters.js';

export const environmentName = value => ({staging:'预发布环境',production:'生产环境',prod:'生产环境',dev:'开发环境',development:'开发环境'})[value] || value || '未提供';
export const variantName = value => ({normal:'查询正常返回',timeout:'查询超时',conflict:'结果不一致，待核对',tenant_denied:'无权访问其他团队',approval:'变更操作等待审批'})[value] || '演练情况';
export function evidenceName(item, id) {
  const index = item.toolSnapshots.findIndex(e => e.id === id);
  return index < 0 ? '未匹配到证据' : `证据 ${index+1}：${item.toolSnapshots[index].label || '查询记录'}`;
}
export function paymentOverview(item) {
  const context=item.businessContext, a=item.alert, ex=item.expected;
  const list=values=>`<ol class="business-steps">${values.map(v=>`<li>${esc(v)}</li>`).join('')}</ol>`;
  return `<section class="detail-section"><h3>这是什么业务？</h3><p>${esc(context.serviceName)}（${esc(a.labels.service)}）${esc(context.responsibility)}</p><p class="business-flow">${context.flow.map(esc).join(' → ')}</p><p class="muted">虚构团购业务示例；业务步骤不代表全部同步调用。</p></section>
  <div class="metadata"><div><span>数据来源</span><strong>合成演练样本 · 非生产事故</strong></div><div><span>业务运行环境</span><strong>${esc(environmentName(a.labels.env))}（${esc(a.labels.env)}）</strong><small>正式上线前用于验证的环境</small></div><div><span>示例负责团队</span><strong>${esc(context.team)}</strong></div><div><span>演练告警时间</span><strong>${esc(time(a.startsAt))}</strong></div></div>
  <section class="detail-section"><h3>发生了什么？</h3><p>${esc(a.description)}</p><p class="muted">${esc(context.priorityReason)}</p></section>
  <section class="detail-section"><h3>可能影响什么？</h3><p>${esc(context.impact)}</p></section>
  <section class="report"><h3>本案例的预设判断</h3><p class="muted">预先编写的演练判断，不是本次 GPT 分析。</p><div class="fact-label">样本提供的观测，不等于已确认根因</div>${ex.artifact.facts.map(f=>`<p>${esc(f.statement)}</p><div class="evidence-links">${f.evidenceRefs.map(ref=>`<button data-evidence="${esc(ref)}">${esc(evidenceName(item,ref))}</button>`).join('')}</div>`).join('') || '<p>尚无可用观测。</p>'}<div class="fact-label">哪里还不确定？</div>${ex.artifact.gaps.map(g=>`<p>${esc(g)}</p>`).join('')}<p><strong>根因尚未确认，不能据此直接回滚。</strong></p></section>
  <section class="detail-section"><h3>下一步查什么？</h3><p class="muted">以下为预设排查步骤，尚未真实执行。</p>${list(context.nextSteps)}</section>
  <section class="detail-section"><h3>故障处理手册（Runbook）</h3><p class="muted">说明检查顺序、操作条件和恢复标准，不是一条执行命令。</p><p>${esc(context.runbook)}</p><div class="gap-note">${esc(ex.actionProposal)}</div><div class="fact-label">处理后如何确认恢复？</div><p>${esc(context.verification)}</p></section>`;
}
