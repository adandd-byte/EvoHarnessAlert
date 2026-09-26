from __future__ import annotations

import hashlib  # 哈希
import json  # JSON
from dataclasses import dataclass  # 数据类
from datetime import datetime, timezone  # 时间
from typing import Any  # 任意类型

from sqlalchemy.orm import Session  # ORM 会话

from app.core.config import Settings  # 全局配置
from app.core.enums import AlertStatus, AlertType, IncidentStatus, Severity, ToolJobKind, ToolJobStatus  # 枚举
from app.models.entities import AgentRunTrace, AlertEvent, AlertSource, Incident, IncidentNote, ToolJob  # 实体
from app.schemas.dtos import AlertWebhookRequest  # 告警请求 DTO
from app.services.privacy import PrivacySanitizer  # 脱敏器
from app.services.ai import AiClient, PromptTemplates


SEVERITY_ALIASES = {  # 严重级别别名表
    "0": Severity.P0, "p0": Severity.P0, "sev0": Severity.P0, "blocker": Severity.P0,
    "emergency": Severity.P0, "disaster": Severity.P0,
    "1": Severity.P1, "p1": Severity.P1, "critical": Severity.P1, "crit": Severity.P1,
    "fatal": Severity.P1, "high": Severity.P1, "page": Severity.P1,
    "2": Severity.P2, "p2": Severity.P2, "warning": Severity.P2, "warn": Severity.P2,
    "medium": Severity.P2, "major": Severity.P2,
    "3": Severity.P3, "p3": Severity.P3, "info": Severity.P3, "informational": Severity.P3,
    "low": Severity.P3, "minor": Severity.P3, "none": Severity.P3,
}

ALERT_TYPE_ALIASES = {  # 告警类型别名表
    "problem": AlertType.PROBLEM, "issue": AlertType.PROBLEM, "incident": AlertType.PROBLEM, "故障": AlertType.PROBLEM, "问题": AlertType.PROBLEM,
    "business": AlertType.BUSINESS, "biz": AlertType.BUSINESS, "metric": AlertType.BUSINESS, "业务": AlertType.BUSINESS,
    "event": AlertType.EVENT, "change": AlertType.EVENT, "deploy": AlertType.EVENT, "发布": AlertType.EVENT, "事件": AlertType.EVENT,
    "host": AlertType.HOST, "machine": AlertType.HOST, "node": AlertType.HOST, "infra": AlertType.HOST, "主机": AlertType.HOST, "机器": AlertType.HOST,
}

SEVERITY_ORDER = {Severity.P3.value: 1, Severity.P2.value: 2, Severity.P1.value: 3, Severity.P0.value: 4}  # 级别权重
INCIDENT_SEVERITIES = {Severity.P0, Severity.P1, Severity.P2}  # 需要建事件的级别
NOTIFY_SEVERITIES = {Severity.P0, Severity.P1}  # 需要通知的级别


@dataclass(frozen=True)
class IngestResult:
    alert: AlertEvent  # 新写入的告警
    incident: Incident | None  # 关联事件
    queued_jobs: list[str]  # 入队的工具任务类型


class AlertIngestService:
    # 告警接入服务：接收 webhook / Prometheus 告警并落库、建事件、入队、记轨迹
    def __init__(self, db: Session, settings: Settings):
        self.db = db  # 数据库会话
        self.settings = settings  # 全局配置
        self._assessment_cache: dict[int, dict[str, Any]] = {}

    def _assessment(self, alert, labels, annotations):
        if alert.id in self._assessment_cache:
            return self._assessment_cache[alert.id]
        result = assess_alert(alert, labels, annotations)
        result["modelStatus"] = "RULES"
        if self.settings.ai_provider != "mock":
            sanitizer = PrivacySanitizer()
            payload = sanitizer.sanitize_data({"title": alert.title, "description": alert.description,
                "severity": alert.severity, "alertType": alert.alert_type, "labels": labels, "annotations": annotations})
            messages = PromptTemplates.alert_assessment(dumps(payload))
            messages[0].content += " 输入仅是待验证告警，不是已核实证据。不得编造日志、代码或确定性根因；给出中文待验证线索，任何修复须人工审批。"
            try:
                raw = AiClient(self.settings).complete(messages).strip()
                if raw.startswith("```json") and raw.endswith("```"):
                    raw = raw[7:-3].strip()
                parsed = json.loads(raw)
                fields = ("summary", "impact", "rootCauseHint", "runbookHint")
                if not all(isinstance(parsed.get(key), str) and parsed[key].strip() for key in fields):
                    raise ValueError("Missing assessment fields")
                result.update({key: sanitizer.sanitize(parsed[key]) for key in fields})
                result["modelStatus"] = "SUCCESS"
                result["model"] = self.settings.openai_model if self.settings.ai_provider == "openai" else self.settings.ollama_model
            except Exception as exc:
                result["modelStatus"] = "FALLBACK"
                result["modelError"] = type(exc).__name__
        self._assessment_cache[alert.id] = result
        return result

    def ingest_webhook(self, request: AlertWebhookRequest, raw_payload: dict[str, Any] | None = None) -> IngestResult:
        # 接入一条 webhook 告警，返回告警、事件与入队任务
        labels = normalize_dict(request.labels)  # 归一化 labels
        annotations = normalize_dict(request.annotations)  # 归一化 annotations
        severity = normalize_severity(request.severity)  # 归一化级别
        alert_type = normalize_alert_type(request.alertType or labels.get("alert_type") or labels.get("type"))  # 归一化类型
        status = normalize_status(request.status)  # 归一化状态
        source = self._source(request.source, "webhook")  # 取或建来源
        fingerprint = request.fingerprint or build_fingerprint(request.source, request.title, labels, alert_type.value)  # 生成指纹
        alert = AlertEvent(  # 构造告警实体
            source_id=source.id,  # 来源 ID
            external_id=request.externalId,  # 外部 ID
            fingerprint=fingerprint,  # 指纹
            title=request.title.strip(),  # 标题
            description=request.description.strip(),  # 描述
            labels_json=dumps(labels),  # labels JSON
            annotations_json=dumps(annotations),  # annotations JSON
            severity=severity.value,  # 级别
            alert_type=alert_type.value,  # 类型
            status=status.value,  # 状态
            starts_at=request.startsAt,  # 开始时间
            ends_at=request.endsAt,  # 结束时间
            raw_payload_json=dumps(raw_payload or request.model_dump(mode="json")),  # 原始负载
        )
        self.db.add(alert)  # 落库
        self.db.commit()
        self.db.refresh(alert)
        incident = self._upsert_incident(alert, labels, annotations) if severity in INCIDENT_SEVERITIES else None  # 高优先级建/跟事件
        self._save_trace(alert, incident, labels, annotations)  # 写 Agent 轨迹
        jobs = self._enqueue(alert, incident)  # 入队工具任务
        self.db.commit()
        return IngestResult(alert=alert, incident=incident, queued_jobs=jobs)

    def ingest_prometheus(self, payload: dict[str, Any]) -> list[IngestResult]:
        # 解析 Prometheus payload 并逐条转 webhook 接入
        alerts = payload.get("alerts")  # 告警数组
        if not isinstance(alerts, list) or not alerts:  # 不合法直接抛错
            raise ValueError("Prometheus payload 缺少 alerts 数组")
        results = []
        for item in alerts:  # 逐条转换
            labels = normalize_dict(item.get("labels", {}))  # labels
            annotations = normalize_dict(item.get("annotations", {}))  # annotations
            title = annotations.get("summary") or labels.get("alertname") or "Prometheus 告警"  # 标题
            request = AlertWebhookRequest(  # 组装请求
                source="prometheus",  # 来源
                externalId=item.get("fingerprint", ""),  # 外部 ID
                fingerprint=item.get("fingerprint") or build_fingerprint("prometheus", title, labels, normalize_alert_type(labels.get("alert_type") or labels.get("type")).value),  # 指纹
                title=title,  # 标题
                description=annotations.get("description", ""),  # 描述
                labels=labels,  # labels
                annotations=annotations,  # annotations
                severity=labels.get("severity") or labels.get("priority") or payload.get("severity") or "P2",  # 级别
                alertType=labels.get("alert_type") or labels.get("type") or annotations.get("alert_type") or infer_alert_type(title, labels, annotations).value,  # 类型
                status=item.get("status") or payload.get("status") or "firing",  # 状态
                startsAt=parse_dt(item.get("startsAt")),  # 开始时间
                endsAt=parse_dt(item.get("endsAt")),  # 结束时间
            )
            results.append(self.ingest_webhook(request, item))  # 复用 webhook 接入
        return results

    def _source(self, name: str, kind: str) -> AlertSource:
        # 按名字查询告警来源，不存在则创建
        normalized = name.strip() or "webhook"  # 归一来源名
        source = self.db.query(AlertSource).filter(AlertSource.name == normalized).first()  # 查询
        if source is not None:  # 已存在直接返回
            return source
        source = AlertSource(name=normalized, kind=kind, description=f"{normalized} 告警来源")  # 新建来源
        self.db.add(source)  # 落库
        self.db.commit()
        self.db.refresh(source)
        return source

    def _upsert_incident(self, alert: AlertEvent, labels: dict[str, Any], annotations: dict[str, Any]) -> Incident:
        # 更新或创建事件（incident）：按聚合键幂等
        key = incident_key(alert, labels)  # 生成聚合键
        incident = self.db.query(Incident).filter(Incident.incident_key == key).first()  # 查已有事件
        assessment = self._assessment(alert, labels, annotations)
        if incident is None:  # 无则新建
            incident = Incident(  # 构造事件
                incident_key=key,  # 聚合键
                severity=alert.severity,  # 级别
                alert_type=alert.alert_type,  # 类型
                status=IncidentStatus.OPEN.value,  # 状态
                owner=self._owner(),  # 负责人
                summary=assessment["summary"],  # 摘要
                impact=assessment["impact"],  # 影响
                root_cause_hint=assessment["rootCauseHint"],  # 根因提示
                runbook_hint=assessment["runbookHint"],  # Runbook 提示
                alert_count=1,  # 告警数
                last_alert_id=alert.id,  # 最近告警
            )
            self.db.add(incident)  # 落库
            self.db.commit()
            self.db.refresh(incident)
            return incident
        incident.alert_count += 1  # 告警数累加
        incident.last_alert_id = alert.id  # 更新最近告警
        incident.severity = max_severity(incident.severity, alert.severity)  # 取更高级别
        incident.alert_type = alert.alert_type  # 更新类型
        if alert.status == AlertStatus.RESOLVED.value:  # 告警恢复则关事件
            incident.status = IncidentStatus.RESOLVED.value
            incident.resolved_at = datetime.utcnow()  # 恢复时间
            incident.resolved_by = "告警恢复"  # 恢复者
        elif incident.status == IncidentStatus.RESOLVED.value:  # 旧事件恢复但再告警则重开
            incident.status = IncidentStatus.OPEN.value
            incident.resolved_at = None  # 清空恢复时间
            incident.resolved_by = None  # 清空恢复者
        incident.summary = assessment["summary"]  # 更新摘要
        incident.impact = assessment["impact"]  # 更新影响
        incident.root_cause_hint = assessment["rootCauseHint"]  # 更新根因
        incident.runbook_hint = assessment["runbookHint"]  # 更新 Runbook
        incident.updated_at = datetime.utcnow()  # 更新时间
        self.db.add(incident)
        self.db.commit()
        self.db.refresh(incident)
        return incident

    def _save_trace(self, alert: AlertEvent, incident: Incident | None, labels: dict[str, Any], annotations: dict[str, Any]) -> None:
        # 写一条 Agent 运行轨迹（AgentRunTrace），用于回放和审计
        sanitizer = PrivacySanitizer()  # 脱敏器
        safe_labels = sanitizer.sanitize_data(labels)  # 脱敏 labels
        safe_annotations = sanitizer.sanitize_data(annotations)  # 脱敏 annotations
        safe_payload = sanitizer.sanitize_data(loads(alert.raw_payload_json, {}))  # 脱敏原始负载
        assessment = self._assessment(alert, safe_labels, safe_annotations)
        steps = handling_steps(alert.alert_type)  # 处理步骤
        trace_steps = [  # 组装轨迹步骤
            {"agent": "ModelAssessment", "action": "告警摘要与处置建议", "status": assessment["modelStatus"], "model": assessment.get("model"), "error": assessment.get("modelError")},
            {"agent": "CoordinatorAgent", "action": f"接收 {alert.severity}/{alert.alert_type} 告警并写入黑板", "status": "完成"},
            {"agent": "TriageAgent", "action": f"确认优先级 {alert.severity}，状态 {alert.status}", "status": "完成"},
            {"agent": "CorrelationAgent", "action": f"聚合键 {incident.incident_key if incident else alert.fingerprint}", "status": "完成"},
            *steps,
            {"agent": "EvidenceReportAgent", "action": "生成带证据引用的值班报告草稿", "status": "完成"},
        ]
        self.db.add(AgentRunTrace(  # 写轨迹
            alert_id=alert.id,  # 告警 ID
            incident_id=incident.id if incident else None,  # 事件 ID
            severity=alert.severity,  # 级别
            alert_type=alert.alert_type,  # 类型
            original_payload_json=dumps(safe_payload),  # 原始负载
            agent_steps_json=dumps(trace_steps),  # Agent 步骤
            retrieved_knowledge_json=dumps(assessment["evidence"]),  # 召回知识
            assessment_json=dumps(assessment),  # 研判
        ))

    def _enqueue(self, alert: AlertEvent, incident: Incident | None) -> list[str]:
        # 按级别入队工具任务：台账必入，事件存在入事件更新，P0/P1 入通知
        kinds = [ToolJobKind.LEDGER_WRITE]  # 台账必做
        if incident is not None:  # 有事件则更新
            kinds.append(ToolJobKind.INCIDENT_UPSERT)
        if normalize_severity(alert.severity) in NOTIFY_SEVERITIES and incident is not None:  # P0/P1 且有事则通知
            kinds.append(ToolJobKind.NOTIFICATION_SEND)
        for kind in kinds:  # 逐类建任务
            self.db.add(ToolJob(alert_id=alert.id, incident_id=incident.id if incident else None, kind=kind.value, status=ToolJobStatus.PENDING.value, max_attempts=self.settings.tool_queue_max_attempts, run_after=datetime.utcnow()))
        return [kind.value for kind in kinds]

    def _owner(self) -> str:
        # 取配置的收件人第一个作为事件负责人
        recipients = [item.strip() for item in self.settings.alert_email_to.replace(";", ",").split(",") if item.strip()]  # 拆分配置
        return recipients[0] if recipients else "未分派"


class IncidentService:
    # 事件处理服务：确认、解决、追加备注
    def __init__(self, db: Session):
        self.db = db  # 数据库会话

    def acknowledge(self, incident_id: int, actor: str, note: str = "") -> Incident:
        # 确认接手事件
        incident = self._get(incident_id)  # 取事件
        incident.status = IncidentStatus.ACKNOWLEDGED.value  # 置已确认
        incident.acknowledged_by = actor.strip() or "admin"  # 确认人
        incident.acknowledged_at = datetime.utcnow()  # 确认时间
        incident.updated_at = datetime.utcnow()  # 更新时间
        self.db.add(incident)
        self._note(incident.id, incident.acknowledged_by, note or "已确认接手该事件")  # 写备注
        self.db.commit()
        self.db.refresh(incident)
        return incident

    def resolve(self, incident_id: int, actor: str, note: str = "") -> Incident:
        # 解决事件
        incident = self._get(incident_id)  # 取事件
        incident.status = IncidentStatus.RESOLVED.value  # 置已解决
        incident.resolved_by = actor.strip() or "admin"  # 解决人
        incident.resolved_at = datetime.utcnow()  # 解决时间
        incident.updated_at = datetime.utcnow()  # 更新时间
        self.db.add(incident)
        self._note(incident.id, incident.resolved_by, note or "事件已关闭")  # 写备注
        self.db.commit()
        self.db.refresh(incident)
        return incident

    def add_note(self, incident_id: int, actor: str, note: str) -> IncidentNote:
        # 追加一条处置备注
        self._get(incident_id)  # 校验存在
        record = self._note(incident_id, actor.strip() or "admin", note.strip())  # 写备注
        self.db.commit()
        self.db.refresh(record)
        return record

    def _get(self, incident_id: int) -> Incident:
        # 查询事件，不存在则抛错
        incident = self.db.get(Incident, incident_id)  # 取事件
        if incident is None:  # 缺失
            raise ValueError("事件不存在")
        return incident

    def _note(self, incident_id: int, actor: str, note: str) -> IncidentNote:
        # 新建备注记录
        if not note:  # 备注必填
            raise ValueError("处置记录不能为空")
        record = IncidentNote(incident_id=incident_id, actor=actor, note=note)  # 构造备注
        self.db.add(record)
        return record


def normalize_severity(value: str | None) -> Severity:
    # 归一化级别，未识别默认 P2
    return SEVERITY_ALIASES.get(str(value or "").strip().lower(), Severity.P2)  # 查询别名表


def normalize_alert_type(value: str | None) -> AlertType:
    # 归一化告警类型，未识别默认 PROBLEM
    return ALERT_TYPE_ALIASES.get(str(value or "").strip().lower(), AlertType.PROBLEM)  # 查询别名表


def infer_alert_type(title: str, labels: dict[str, Any], annotations: dict[str, Any]) -> AlertType:
    # 根据文本关键词推断告警类型
    text = " ".join(str(v) for v in [title, labels.get("category", ""), labels.get("job", ""), annotations.get("description", "")]).lower()  # 拼接文本
    if any(word in text for word in ["host", "node", "disk", "cpu", "memory", "inode", "主机", "机器", "磁盘", "内存"]):  # 主机
        return AlertType.HOST
    if any(word in text for word in ["business", "order", "payment", "checkout", "gmv", "业务", "订单", "支付"]):  # 业务
        return AlertType.BUSINESS
    if any(word in text for word in ["deploy", "release", "change", "event", "发布", "变更"]):  # 事件
        return AlertType.EVENT
    return AlertType.PROBLEM  # 默认问题


def normalize_status(value: str | None) -> AlertStatus:
    # 归一化告警状态，恢复关键词之外默认 FIRING
    return AlertStatus.RESOLVED if str(value or "").strip().lower() in {"resolved", "resolve", "ok", "closed"} else AlertStatus.FIRING


def normalize_dict(value: Any) -> dict[str, Any]:
    # 确保输入是字典
    return value if isinstance(value, dict) else {}


def parse_dt(value: Any) -> datetime | None:
    # 解析时间字符串为 UTC datetime，失败返回 None
    if not value:  # 空值
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def build_fingerprint(source: str, title: str, labels: dict[str, Any], alert_type: str = "PROBLEM") -> str:
    # 基于来源+标题+类型+稳定 labels 生成去重指纹
    stable = {key: labels.get(key) for key in sorted(labels) if key not in {"pod", "instance", "timestamp"}}  # 过滤易变字段
    raw = json.dumps({"source": source, "title": title, "type": alert_type, "labels": stable}, ensure_ascii=False, sort_keys=True)  # 规范化序列化
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]  # 取 sha256 前 32 位


def incident_key(alert: AlertEvent, labels: dict[str, Any]) -> str:
    # 生成事件聚合键：类型+服务+指纹
    service = labels.get("service") or labels.get("job") or labels.get("app") or labels.get("namespace") or "default"  # 服务名
    return f"{alert.alert_type}:{service}:{alert.fingerprint}"


def max_severity(left: str, right: str) -> str:
    # 取两者中更高的级别
    return left if SEVERITY_ORDER.get(left, 0) >= SEVERITY_ORDER.get(right, 0) else right  # 比较权重


def handling_steps(alert_type: str) -> list[dict[str, str]]:
    # 按告警类型返回排障处理步骤（供轨迹展示）
    if alert_type == AlertType.PROBLEM.value:  # 问题类
        return [
            {"agent": "LogQueryAgent", "action": "按告警时间窗口查询错误日志和关键 trace", "status": "待接入工具"},
            {"agent": "CodeAnalysisAgent", "action": "根据日志关键字、接口、模块名定位相关代码", "status": "待接入工具"},
            {"agent": "MetricsAgent", "action": "查看服务大盘、错误率、延迟、饱和度和依赖指标", "status": "待接入工具"},
            {"agent": "KnowledgeAgent", "action": "检索企业知识库、历史故障和 Runbook", "status": "完成"},
        ]
    if alert_type == AlertType.BUSINESS.value:  # 业务类
        return [
            {"agent": "BusinessMetricAgent", "action": "核对业务指标口径、环比/同比和上下游漏斗", "status": "待接入工具"},
            {"agent": "DataQualityAgent", "action": "检查数据延迟、埋点变更和统计任务状态", "status": "待接入工具"},
            {"agent": "KnowledgeAgent", "action": "检索业务名词、指标口径和历史波动说明", "status": "完成"},
        ]
    if alert_type == AlertType.EVENT.value:  # 事件类
        return [
            {"agent": "ChangeReviewAgent", "action": "关联发布、配置变更、扩缩容和定时任务", "status": "待接入工具"},
            {"agent": "ImpactAgent", "action": "确认事件是否引发服务指标或业务指标异常", "status": "待接入工具"},
        ]
    return [  # 主机类（默认兜底）
        {"agent": "HostDiagnosticAgent", "action": "检查 CPU、内存、磁盘、网络、进程和容器状态", "status": "待接入工具"},
        {"agent": "MetricsAgent", "action": "查看主机和服务大盘确认影响范围", "status": "待接入工具"},
        {"agent": "KnowledgeAgent", "action": "检索基础设施 Runbook 和历史主机故障", "status": "完成"},
    ]


def assess_alert(alert: AlertEvent, labels: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    # 对告警生成研判：摘要、影响、根因提示、Runbook 提示、证据
    service = labels.get("service") or labels.get("job") or labels.get("app") or "未知服务"  # 服务
    instance = labels.get("instance") or labels.get("pod") or labels.get("host") or "未知实例"  # 实例
    type_name = {"PROBLEM": "问题", "BUSINESS": "业务", "EVENT": "事件", "HOST": "主机"}.get(alert.alert_type, alert.alert_type)  # 中文类型
    summary = f"{service} 触发 {alert.severity} {type_name}告警：{alert.title}，当前状态 {alert.status}。"  # 摘要
    if alert.severity == Severity.P0.value:  # P0 影响
        impact = "最高优先级，可能存在全局不可用、核心链路中断或重大资损风险，需要立即拉起应急响应。"
    elif alert.severity == Severity.P1.value:  # P1 影响
        impact = "高优先级，可能影响核心服务或重要业务指标，需要立即通知值班人员。"
    elif alert.severity == Severity.P2.value:  # P2 影响
        impact = "中优先级，需要进入事件跟踪并在值班窗口内处理。"
    else:  # 其余影响
        impact = "低优先级，记录台账并用于趋势观察。"
    root = annotations.get("root_cause") or root_hint(alert.alert_type, instance)  # 根因提示
    runbook = annotations.get("runbook") or runbook_hint(alert.alert_type)  # Runbook 提示
    evidence = [  # 证据链
        {"source": "告警原文", "quote": alert.title, "strength": "strong"},
        {"source": "labels", "quote": json.dumps(labels, ensure_ascii=False, default=str), "strength": "medium"},
        {"source": "annotations", "quote": json.dumps(annotations, ensure_ascii=False, default=str), "strength": "medium"},
    ]
    return {"summary": summary, "impact": impact, "rootCauseHint": root, "runbookHint": runbook, "evidence": evidence}  # 组装研判


def root_hint(alert_type: str, instance: str) -> str:
    # 按类型返回根因排查建议
    return {
        AlertType.PROBLEM.value: f"建议从 {instance} 在告警时间窗口内的错误日志、trace、近期代码变更和依赖异常开始验证。",
        AlertType.BUSINESS.value: "建议先确认指标口径、数据延迟、上下游漏斗、活动配置和最近业务变更。",
        AlertType.EVENT.value: "建议关联发布、配置变更、定时任务、容量调整和外部依赖事件。",
        AlertType.HOST.value: f"建议检查 {instance} 的 CPU、内存、磁盘、网络、进程、容器重启和节点健康。",
    }.get(alert_type, "建议补充日志、指标、代码和知识库证据后再判断根因。")


def runbook_hint(alert_type: str) -> str:
    # 按类型返回处理流程建议
    return {
        AlertType.PROBLEM.value: "Problem 流程：查日志 -> 看代码 -> 查大盘 -> 检索知识库/历史故障 -> 生成证据链报告。",
        AlertType.BUSINESS.value: "Business 流程：确认指标异常 -> 查数据链路 -> 对齐业务口径 -> 联系业务 Owner -> 记录影响范围。",
        AlertType.EVENT.value: "Event 流程：关联变更 -> 验证影响 -> 必要时回滚或暂停变更 -> 更新事件记录。",
        AlertType.HOST.value: "Host 流程：检查资源水位 -> 定位异常进程/容器 -> 摘除或迁移流量 -> 执行基础设施 Runbook。",
    }.get(alert_type, "按日志、指标、代码、知识库四类证据补齐研判。")


def dumps(value: Any) -> str:
    # 安全 JSON 序列化（处理非序列化对象、保留中文）
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(raw: str, default: Any) -> Any:
    # 安全 JSON 反序列化，失败返回默认值
    try:
        return json.loads(raw or "")
    except Exception:
        return default
