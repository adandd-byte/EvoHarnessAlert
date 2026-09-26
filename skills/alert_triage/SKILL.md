---
name: alert_triage
description: 用于把原始告警归一化为标准等级，判断影响范围、识别告警类型，并产出包含定级、影响、可能根因、验证步骤、止血与升级条件的研判结论。全程对敏感信息脱敏。
alert_types:
  - PROBLEM
  - BUSINESS
  - EVENT
  - HOST
workflow:
  - 提取并统一告警等级（优先采信外部 P0/P1/P2/P3，缺失时用规则兜底归一）
  - 识别告警类型是 PROBLEM、BUSINESS、EVENT 还是 HOST
  - 提取服务、实例、城市/区域、环境、Owner、traceId 和时间窗口
  - 判断是否需要升级、是否需要通知值班、是否需要创建 Incident
  - 对手机号、邮箱、Token、密码、连接串、内网 IP 等内容做脱敏
  - 明确区分事实、推测与待验证证据，只输出可执行的研判结论
---

# 技能：告警研判（alert_triage）

面向 SRE、运维和值班工程师。输入一条原始告警，输出一份结构化、可执行的研判结论。

## 1. 输入

| 字段 | 说明 | 是否必填 |
| --- | --- | --- |
| 原始告警标题/描述 | 告警主体内容 | 是 |
| labels | 服务名、实例、环境、城市、Owner、traceId 等标签 | 否 |
| annotations | summary、description、root_cause、runbook 等注解 | 否 |
| 外部优先级 | 上游传入的 severity/priority | 否 |

## 2. 分级标准（归一化）

优先采信外部等级；外部缺省或不可信时，按下列规则兜底：

| 等级 | 判定特征 |
| --- | --- |
| P0 | 核心链路全局不可用、重大资损、跨服务系统性故障，需立即拉应急 |
| P1 | 核心服务不可用或重要指标异常，需立即通知值班并进入 Incident |
| P2 | 单点或次要功能异常，值班窗口内处理并跟踪 |
| P3 | 记录台账、趋势观察，无需立即动作 |

## 3. 处理步骤

1. **归一等级**：解析外部 severity/priority → 映射到 P0–P3；缺失时按分级标准兜底。
2. **识别类型**：按关键词与上下文判定 PROBLEM / BUSINESS / EVENT / HOST。
3. **抽取上下文**：service、instance/pod、env、city/region、owner、traceId、时间窗口。
4. **决策分支**：
   - P0/P1 → 高优，推荐升级 & 通知 & 创建 Incident；
   - P2 → 进入事件跟踪；
   - P3 → 仅台账记录。
5. **脱敏**：对敏感字段执行脱敏，替换为占位符并提示。
6. **输出**：按第 4 节 JSON 结构输出。

## 4. 输出结构（JSON）

```json
{
  "severity": "P1",
  "alertType": "PROBLEM",
  "summary": "一句话结论",
  "impact": "影响范围（服务/实例/用户/业务指标）",
  "rootCauseHint": "可能根因（措辞用“可能”“建议验证”，不写确定结论）",
  "verificationSteps": ["第1步", "第2步"],
  "immediateActions": ["止血动作1", "止血动作2"],
  "escalationCondition": "满足何种条件时升级/通知",
  "evidence": [{"source": "告警原文", "strength": "strong"}]
}
```

## 5. 脱敏规则

- 手机号、邮箱、Token、密码、连接串、内网 IP、密钥一律替换为 `***` 并在结论中提示“已脱敏”。
- 不把未验证的根因写成确定结论；不确定处标注“待验证”。
- 证据不足时明确列出需要补充的日志 / 指标 / 代码 / 知识库证据。

## 6. 安全边界

不得泄露敏感字段，不得把推测写成事实，不得绕过人工审批执行重启、回滚、扩缩容、部署等高风险动作。P0/P1 的止血动作必须附带负责人升级与人工确认点。