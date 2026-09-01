---
name: alert_triage
description: 用于运维告警定级、影响判断、升级建议和敏感信息处理。
alert_types:
  - PROBLEM
  - BUSINESS
  - EVENT
  - HOST
workflow:
  - 提取 priority/severity、alert_type、service、instance、env、owner、region、traceId 和时间窗口
  - 优先采信外部 P0/P1/P2/P3，再用规则兜底归一告警等级
  - 识别告警类型是 PROBLEM、BUSINESS、EVENT 还是 HOST
  - 标记是否需要升级、是否需要通知值班人员、是否需要创建 Incident
  - 对手机号、邮箱、Token、密码、连接串、内网 IP 等内容做脱敏
  - 明确区分事实、推测和待验证证据
---

告警研判应输出：当前级别、影响服务、影响范围、可能根因、建议验证步骤、止血动作和升级条件。证据不足时明确说明需要验证，不要把推测写成事实。

安全边界：不得泄露敏感字段，不得把未经验证的根因写成确定结论，不得绕过人工审批执行重启、回滚、扩缩容、部署等高风险动作。
